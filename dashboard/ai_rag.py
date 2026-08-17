"""Retrieval-augmented answering over LEco DevOps' own knowledge.

An external model (Eden AI, Claude, Gemini, OpenRouter, a local Ollama…) knows
nothing about this platform's conventions and nothing at all about *this*
machine.  This module supplies both halves of that missing context:

* **Static** — heading-aligned chunks of the repository's own documentation,
  retrieved with a BM25F-style lexical scorer implemented here (see
  :func:`retrieve`).  Lexical retrieval was chosen deliberately: the corpus is a
  couple of thousand chunks of highly technical prose full of rare identifiers
  (``lh-network``, ``502``, ``leco.app.yaml``, ``ecosystem-stack.sh``) which is
  exactly the regime where term matching beats a small embedding model, and it
  adds no dependency, no network call and no cold-start cost.  Local Ollama
  embeddings can be layered on top as an opt-in reranker.
* **Live** — this machine's current state, fetched at query time (never
  persisted) and only for the sources a question actually needs.  The routing
  from question to live sources is a declarative table (:data:`LIVE_RULES`), not
  keyword tests scattered through the retrieval loop.

Safety invariants:

* Corpus text is scrubbed at index time (``ai_corpus.scrub_text``); live state is
  scrubbed again on the way out of every collector.  No path reaches
  :func:`assemble_prompt` unscrubbed.
* The system prompt forbids answering outside the supplied context.
* Nothing here performs a control action.  It reads, it never writes.
* The response names the provider and its destination host, so a user can see
  which cloud their logs were sent to.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import ai_corpus
from ai_corpus import scrub_structure, scrub_text

# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

DEFAULT_TOP_K = 6
MAX_TOP_K = 20
DEFAULT_MAX_CONTEXT_CHARS = 24_000
MAX_CONTEXT_CHARS_CEILING = 120_000
LIVE_CONTEXT_SHARE = 0.45          # live state may claim at most this much of the budget
MAX_LIVE_BLOB_CHARS = 6_000        # per live source
MAX_LOG_LINES = 120


# ---------------------------------------------------------------------------
# Lexical retrieval (BM25F-style)
# ---------------------------------------------------------------------------

BM25_K1 = 1.4
BM25_B = 0.72

# Heading and path matches are scored as *flat* bonuses rather than folded into
# term frequency. Folding them in gets them length-normalized away exactly when
# they matter most: a short, precisely-titled page (docs/help/09-502-routing.md)
# would otherwise lose a "502" question to a long page that merely says "502" a
# lot. A term in the title of the page is evidence about the page, not about a
# passage, so it must not be divided by passage length.
HEADING_BONUS = 1.3
PATH_BONUS = 1.0
COVERAGE_WEIGHT = 1.6   # reward chunks that cover *more distinct* query terms

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._\-/][a-z0-9]+)*")

STOPWORDS = frozenset("""
a an and are as at be been being but by can cant could did do does doing done dont for from
get gets had has have having he her here hers him his how i if in into is it its itd itll
just me my no nor not of off on once only or other our ours out over own same she should so
some such than that the their theirs them then there these they this those through to too
under until up us very was we were what when where which while who whom why will with would
you your yours about after again all also am any because before below between both during
each few more most now once ours than too via want wants need needs like use used using
would mean means happen happens go goes going make makes made know tell show shows please
thing things one two way ways lot see look looks give gives
return returns returned returning
""".split())


def _singular(tok: str) -> str:
    """Conservative singular form, or "" when the token is already singular.

    Not a stemmer — just enough plural folding that "certificates" matches a
    heading that says "certificate" and "routes" matches "route". Applied
    identically to documents and queries, so it can only add matches, never
    change what a term means.
    """
    if len(tok) < 4 or not tok.isalpha() or not tok.endswith("s") or tok.endswith("ss"):
        return ""
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith(("sses", "shes", "ches", "xes", "zes")):
        return tok[:-2]
    return tok[:-1]


def tokenize(text: str) -> list[str]:
    """Lowercase tokens plus the sub-parts of dotted/hyphenated identifiers.

    ``ecosystem-stack.sh`` yields ``ecosystem-stack.sh``, ``ecosystem``,
    ``stack``, ``sh`` so a question phrased in prose still hits a chunk that
    only ever writes the identifier form (and vice versa).
    """
    out: list[str] = []

    def emit(tok: str) -> None:
        if tok in STOPWORDS or (len(tok) < 2 and not tok.isdigit()):
            return
        out.append(tok)
        base = _singular(tok)
        if base and base not in STOPWORDS:
            out.append(base)

    for m in _TOKEN_RE.finditer(text.lower()):
        tok = m.group(0)
        if tok in STOPWORDS:
            continue
        emit(tok)
        if any(sep in tok for sep in "._-/"):
            for part in re.split(r"[._\-/]+", tok):
                emit(part)
    return out


_POSTINGS_LOCK = threading.RLock()
_POSTINGS: dict[str, Any] = {}


def _build_postings(index: dict[str, Any]) -> dict[str, Any]:
    """Inverted index over the chunk set. Rebuilt in-process, never persisted.

    Keeping postings out of the index file keeps that file small and readable;
    tokenizing ~1.5 MB of markdown takes a fraction of a second and is cached
    for the lifetime of the chunk signature.
    """
    chunks: list[dict[str, Any]] = index.get("chunks") or []
    postings: dict[str, list[tuple[int, float]]] = {}
    heading_idx: dict[str, set[int]] = {}
    path_idx: dict[str, set[int]] = {}
    lengths: list[float] = []
    for cid, chunk in enumerate(chunks):
        tf: dict[str, float] = {}
        for tok in tokenize(chunk.get("text", "")):
            tf[tok] = tf.get(tok, 0.0) + 1.0
        lengths.append(float(sum(tf.values())) or 1.0)
        for tok, weight in tf.items():
            postings.setdefault(tok, []).append((cid, weight))
        headings = list(chunk.get("trail") or []) + list(chunk.get("headings") or [])
        for tok in set(tokenize(" ".join(headings))):
            heading_idx.setdefault(tok, set()).add(cid)
        for tok in set(tokenize(chunk.get("path", ""))):
            path_idx.setdefault(tok, set()).add(cid)
    total = len(chunks)
    avgdl = (sum(lengths) / total) if total else 1.0
    return {
        "signature": index.get("chunk_signature", ""),
        "postings": postings,
        "heading": heading_idx,
        "path": path_idx,
        "lengths": lengths,
        "avgdl": avgdl or 1.0,
        "n": total,
    }


def _postings_for(index: dict[str, Any]) -> dict[str, Any]:
    sig = index.get("chunk_signature", "")
    with _POSTINGS_LOCK:
        cached = _POSTINGS.get("data")
        if cached and cached.get("signature") == sig and sig:
            return cached
    built = _build_postings(index)
    with _POSTINGS_LOCK:
        _POSTINGS["data"] = built
    return built


def bm25_scores(query: str, index: dict[str, Any]) -> dict[int, float]:
    """BM25 score per chunk id, plus a distinct-term coverage bonus."""
    idx = _postings_for(index)
    n = idx["n"]
    if not n:
        return {}
    postings = idx["postings"]
    lengths = idx["lengths"]
    avgdl = idx["avgdl"]

    terms = tokenize(query)
    if not terms:
        return {}
    distinct = sorted(set(terms))

    heading_idx = idx["heading"]
    path_idx = idx["path"]

    scores: dict[int, float] = {}
    hits: dict[int, set[str]] = {}
    idfs: dict[str, float] = {}
    for term in distinct:
        plist = postings.get(term, ())
        heads = heading_idx.get(term, ())
        paths = path_idx.get(term, ())
        if not plist and not heads and not paths:
            continue
        # Document frequency spans all three fields so a term that only ever
        # appears in file names still gets a sane rarity estimate.
        df = len({cid for cid, _ in plist} | set(heads) | set(paths))
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        idfs[term] = idf
        for cid, tf in plist:
            dl = lengths[cid]
            denom = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl)
            scores[cid] = scores.get(cid, 0.0) + idf * (tf * (BM25_K1 + 1.0)) / denom
            hits.setdefault(cid, set()).add(term)
        for cid in heads:
            scores[cid] = scores.get(cid, 0.0) + HEADING_BONUS * idf
            hits.setdefault(cid, set()).add(term)
        for cid in paths:
            scores[cid] = scores.get(cid, 0.0) + PATH_BONUS * idf
            hits.setdefault(cid, set()).add(term)

    if not scores:
        return {}
    # Coverage bonus: a chunk that mentions four of the five query terms is far
    # more likely to be the answer than one that repeats a single rare term.
    mean_idf = sum(idfs.values()) / max(1, len(idfs))
    matchable = len(idfs) or 1
    for cid, matched in hits.items():
        scores[cid] += COVERAGE_WEIGHT * mean_idf * (len(matched) / matchable)
    return scores


def _cosine_scores(query: str, index: dict[str, Any]) -> tuple[dict[int, float], dict[str, Any]]:
    """Optional local-embedding similarity. Returns ``({}, meta)`` when inactive."""
    vectors, meta = ai_corpus.load_vectors(index)
    if vectors is None:
        return {}, meta
    qvecs, err = ai_corpus.embed_texts([query], model=str(meta.get("model") or ""))
    if err or not qvecs:
        return {}, {"active": False, "reason": err or "query embedding failed"}
    q = qvecs[0]
    norm = sum(x * x for x in q) ** 0.5 or 1.0
    q = [x / norm for x in q]
    out: dict[int, float] = {}
    for cid, vec in enumerate(vectors):
        if len(vec) != len(q):
            continue
        out[cid] = sum(a * b for a, b in zip(q, vec))
    return out, meta


@dataclass
class Passage:
    """One retrieved static chunk with its citation."""

    chunk_id: int
    path: str
    heading: str
    trail: list[str]
    kind: str
    text: str
    score: float
    headings: list[str] = field(default_factory=list)
    lexical_score: float = 0.0
    vector_score: float = 0.0

    def citation(self) -> str:
        """Heading trail, naming any extra sections the chunk also covers."""
        base = " > ".join(self.trail) if self.trail else self.heading
        extra = [h for h in self.headings if h and h != self.heading and h not in self.trail]
        if extra:
            return f"{base} (also covers: {', '.join(extra[:4])})"
        return base

    def to_dict(self, n: int) -> dict[str, Any]:
        return {
            "n": n,
            "type": "doc",
            "path": self.path,
            "heading": self.heading,
            "trail": self.trail,
            "headings": self.headings,
            "kind": self.kind,
            "score": round(self.score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "vector_score": round(self.vector_score, 4),
            "chars": len(self.text),
        }


def retrieve(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    use_embeddings: bool = True,
    index: dict[str, Any] | None = None,
) -> tuple[list[Passage], dict[str, Any]]:
    """Top-``k`` passages for a question, plus retrieval metadata.

    Lexical scoring always runs.  When persisted local embeddings match the
    current chunk set and ``use_embeddings`` is on, the two score sets are
    max-normalized and fused 0.65/0.35 in favour of the lexical signal — the
    embeddings act as a tie-breaker for paraphrased questions, never as a
    replacement the feature depends on.
    """
    if index is None:
        index, rebuilt = ai_corpus.load_index()
    else:
        rebuilt = False
    chunks: list[dict[str, Any]] = index.get("chunks") or []

    lex = bm25_scores(question, index)
    method = "bm25"
    vec_meta: dict[str, Any] = {"active": False, "reason": "not requested"}
    vec: dict[int, float] = {}
    if use_embeddings:
        try:
            vec, vec_meta = _cosine_scores(question, index)
        except Exception as exc:            # an embedding hiccup must never break retrieval
            vec, vec_meta = {}, {"active": False, "reason": str(exc)[:200]}

    if vec:
        method = "bm25+embeddings"
        lex_max = max(lex.values()) if lex else 1.0
        vec_max = max(vec.values()) if vec else 1.0
        fused: dict[int, float] = {}
        for cid in set(lex) | set(vec):
            fused[cid] = 0.65 * (lex.get(cid, 0.0) / (lex_max or 1.0)) + 0.35 * (
                max(0.0, vec.get(cid, 0.0)) / (vec_max or 1.0)
            )
        ranked = fused
    else:
        ranked = lex

    top = sorted(ranked.items(), key=lambda kv: kv[1], reverse=True)[: max(1, min(top_k, MAX_TOP_K))]
    passages: list[Passage] = []
    for cid, score in top:
        if cid >= len(chunks):
            continue
        c = chunks[cid]
        passages.append(
            Passage(
                chunk_id=cid,
                path=c.get("path", ""),
                heading=c.get("heading", ""),
                trail=list(c.get("trail") or []),
                headings=list(c.get("headings") or []),
                kind=c.get("kind", "doc"),
                text=c.get("text", ""),
                score=float(score),
                lexical_score=float(lex.get(cid, 0.0)),
                vector_score=float(vec.get(cid, 0.0)),
            )
        )
    meta = {
        "method": method,
        "chunks_searched": len(chunks),
        "chunks_matched": len(ranked),
        "top_k": len(passages),
        "index_built_at": index.get("built_at"),
        "index_rebuilt": rebuilt,
        "embeddings": vec_meta,
    }
    return passages, meta


# ---------------------------------------------------------------------------
# Live state: an explicit question → source routing table
# ---------------------------------------------------------------------------

# A rule whose only trigger is ALWAYS fires whenever its scope is satisfied —
# an app-scoped source needs no keyword once the question has named the app.
ALWAYS = "*"


@dataclass(frozen=True)
class LiveRule:
    """One live source and the shape of question that justifies fetching it.

    ``scope`` is ``"global"`` for machine-wide state and ``"app"`` for state that
    only makes sense once a hosted-app slug has been identified in the question.
    """

    source: str
    label: str
    why: str
    scope: str
    triggers: tuple[str, ...]

    def matches(self, question: str) -> str:
        """Return the trigger phrase that fired, or "" when the rule is silent."""
        for pattern in self.triggers:
            if pattern == ALWAYS:
                return "(app named in the question)"
            m = re.search(pattern, question, re.IGNORECASE)
            if m:
                return m.group(0)
        return ""


LIVE_RULES: tuple[LiveRule, ...] = (
    LiveRule(
        source="stack_status",
        label="Ecosystem stack status (monitor.collect_overview)",
        why="the question asks about what is running, healthy, or resource-hungry right now",
        scope="global",
        triggers=(
            r"\b(status|running|health(y|check)?|unhealthy|up|down|stopped|crash\w*|restart\w*)\b",
            r"\b(overview|uptime|cpu|memory|ram|disk|container\w*)\b",
            r"\b(is|are)\s+\w+\s+(running|up|down|ok|healthy)\b",
        ),
    ),
    LiveRule(
        source="services",
        label="Managed service inventory (monitor.list_managed_services)",
        why="the question refers to the stack's service list",
        scope="global",
        triggers=(r"\b(services?|which service|service list|managed service)\b",),
    ),
    LiveRule(
        source="control_targets",
        label="Control targets and allowed actions (control.list_targets)",
        why="the question is about starting, stopping, or otherwise controlling something",
        scope="global",
        triggers=(
            r"\b(start|stop|restart|pause|unpause|recreate|reset|remove|deploy)\b",
            r"\b(control|action|target)s?\b",
        ),
    ),
    LiveRule(
        source="hosted_apps",
        label="Hosted app inventory + manifests (hosted_apps.list_hosted_apps)",
        why="the question concerns hosted apps registered on this machine",
        scope="global",
        triggers=(
            r"\b(hosted|apps?|app list|onboard\w*|registered|registry|manifest|slug)\b",
            r"\bleco\.app\.ya?ml\b",
        ),
    ),
    LiveRule(
        source="traefik_routes",
        label="Traefik routers and hosted-app hints (hosted_offboard.traefik_routes_with_hosted_hints)",
        why="the question involves edge routing, hostnames, or an HTTP gateway error",
        scope="global",
        triggers=(
            r"\b(traefik|route[rs]?|routing|proxy|edge|gateway)\b",
            r"(?<!\d)(50[234]|404)(?!\d)|bad gateway|gateway timeout",
            r"\b(hostname|host rule|\.lh|tls|ssl|certificate)\b",
        ),
    ),
    LiveRule(
        source="cloudflare_local",
        label="Cloudflare-local adapters (monitor.collect_cloudflare_local_status)",
        why="the question is about the local Cloudflare adapters",
        scope="global",
        triggers=(r"\b(cloudflare|wrangler|workers?|kv|r2|d1|cf-local|cf local)\b",),
    ),
    LiveRule(
        source="ai_config",
        label="AI provider configuration, keys masked (ai_config.config_for_ui)",
        why="the question is about which AI provider or model this dashboard uses",
        scope="global",
        triggers=(
            r"\b(provider|model|ollama|airllm|openrouter|gemini|anthropic|eden)\b",
            r"\bai (config\w*|settings?|setup)\b",
        ),
    ),
    LiveRule(
        source="service_logs",
        label="Recent logs for a named stack service (monitor.collect_service_logs)",
        why="the question asks to inspect logs of a stack service",
        scope="global",
        triggers=(r"\b(logs?|log lines?|stderr|stdout|traceback|stack ?trace|exception|errors?)\b",),
    ),
    LiveRule(
        source="app_snapshot",
        label="Runtime snapshot for the named app (hosted_apps.snapshot_for_slug)",
        why="the question names a hosted app, so its own runtime state is the evidence",
        scope="app",
        triggers=(ALWAYS,),  # naming an app is itself the trigger for its snapshot
    ),
    LiveRule(
        source="app_logs",
        label="Recent container logs for the named app (hosted_apps.logs_for_slug)",
        why="the question asks why a named app misbehaves, which needs its logs",
        scope="app",
        triggers=(
            r"\b(logs?|errors?|exception|traceback|crash\w*|fail\w*|broken|debug|why)\b",
            r"(?<!\d)(50[234]|404)(?!\d)|\b(timeout|refused|unreachable)\b",
        ),
    ),
)

LIVE_RULES_BY_SOURCE = {r.source: r for r in LIVE_RULES}

# Stack containers a question may name directly, mapped to the container the log
# collector understands.
KNOWN_SERVICE_CONTAINERS: dict[str, str] = {
    "traefik": "traefik",
    "dashboard": "service-dashboard",
    "service-dashboard": "service-dashboard",
    "leco devops": "service-dashboard",
    "ollama": "ollama",
    "airllm": "airllm",
    "open webui": "open-webui",
    "open-webui": "open-webui",
    "openwebui": "open-webui",
    "n8n": "n8n",
    "postgres": "postgres",
    "postgresql": "postgres",
    "mcp": "leco-mcp",
    "leco-mcp": "leco-mcp",
    "update catalog": "leco-update-catalog",
    "update-catalog": "leco-update-catalog",
    "minio": "minio",
    "valkey": "valkey",
}


def known_app_slugs() -> list[str]:
    """Slugs registered on this machine (cheap YAML read, no Docker calls)."""
    try:
        from leco_control import load_leco_registry_entries

        return [str(e.get("id") or "").strip() for e in load_leco_registry_entries() if e.get("id")]
    except Exception:
        return []


def _detect_slug(question: str, explicit: str = "") -> str:
    if explicit.strip():
        return explicit.strip()
    low = question.lower()
    best = ""
    for slug in known_app_slugs():
        if not slug:
            continue
        if re.search(rf"(?<![\w-]){re.escape(slug.lower())}(?![\w-])", low):
            if len(slug) > len(best):
                best = slug
    return best


def _detect_service_container(question: str) -> str:
    low = question.lower()
    for name, container in KNOWN_SERVICE_CONTAINERS.items():
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", low):
            return container
    return ""


@dataclass
class LivePlan:
    """Which live sources a question justifies, and why."""

    sources: list[str] = field(default_factory=list)
    app_slug: str = ""
    service_container: str = ""
    reasons: dict[str, str] = field(default_factory=dict)
    triggers: dict[str, str] = field(default_factory=dict)
    mode: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "app_slug": self.app_slug,
            "service_container": self.service_container,
            "planned": [
                {
                    "source": s,
                    "label": LIVE_RULES_BY_SOURCE[s].label if s in LIVE_RULES_BY_SOURCE else s,
                    "why": self.reasons.get(s, ""),
                    "trigger": self.triggers.get(s, ""),
                }
                for s in self.sources
            ],
        }


def plan_live_sources(
    question: str,
    *,
    explicit_app: str = "",
    requested: list[str] | None = None,
    enabled: bool = True,
) -> LivePlan:
    """Decide which live sources this question needs.

    ``requested`` (a list of source ids from the caller) overrides the rules
    entirely; ``enabled=False`` turns live state off.  Otherwise every rule in
    :data:`LIVE_RULES` is evaluated against the question text and the ones that
    fire — with the phrase that fired them — become the plan.
    """
    slug = _detect_slug(question, explicit_app)
    container = _detect_service_container(question)

    if not enabled:
        return LivePlan(mode="disabled", app_slug=slug, service_container=container)

    if requested is not None:
        valid = [s for s in requested if s in LIVE_RULES_BY_SOURCE]
        return LivePlan(
            sources=valid,
            app_slug=slug,
            service_container=container,
            reasons={s: "requested by caller" for s in valid},
            mode="explicit",
        )

    plan = LivePlan(app_slug=slug, service_container=container, mode="auto")
    for rule in LIVE_RULES:
        if rule.scope == "app" and not slug:
            continue
        if rule.source == "service_logs" and (slug or not container):
            # An app-scoped question gets app logs; stack-service logs need a
            # named stack container, otherwise "logs" is too vague to act on.
            continue
        fired = rule.matches(question)
        if not fired:
            continue
        plan.sources.append(rule.source)
        plan.reasons[rule.source] = rule.why
        plan.triggers[rule.source] = fired
    return plan


# ---------------------------------------------------------------------------
# Live collectors — heavy imports stay local to each collector
# ---------------------------------------------------------------------------

def _trim(text: str, limit: int = MAX_LIVE_BLOB_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n… [truncated]", True


def _as_json_block(value: Any, limit: int = MAX_LIVE_BLOB_CHARS) -> tuple[str, bool]:
    safe = scrub_structure(value)
    try:
        text = json.dumps(safe, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = scrub_text(str(safe))
    return _trim(text, limit)


def _collect_stack_status(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from monitor import collect_overview

    data = collect_overview()
    summary = {
        "generated_at": data.get("generated_at"),
        "system": data.get("system"),
        "services": [
            {
                "name": s.get("name"),
                "container": s.get("container"),
                "status": s.get("status"),
                "running": (s.get("container_info") or {}).get("running"),
                "state": (s.get("container_info") or {}).get("status"),
                "restart_count": (s.get("container_info") or {}).get("restart_count"),
                "url_checks": [
                    {"url": u.get("url"), "ok": u.get("ok"), "code": u.get("status_code"), "note": u.get("note")}
                    for u in (s.get("url_checks") or [])
                ][:6],
            }
            for s in (data.get("services") or [])
        ],
        "docker": data.get("docker"),
    }
    return _as_json_block(summary)[0], {}


def _collect_services(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from monitor import list_managed_services

    return _as_json_block(list_managed_services())[0], {}


def _collect_control_targets(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from control import list_targets

    data = list_targets()
    slim = [
        {
            "id": t.get("id"),
            "label": t.get("label"),
            "group": t.get("group"),
            "container": t.get("container"),
            "actions": t.get("actions"),
            "status": (t.get("runtime") or {}).get("status"),
        }
        for t in (data.get("targets") or [])
    ]
    return _as_json_block({"token_required": data.get("token_required"), "targets": slim})[0], {}


def _collect_hosted_apps(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from hosted_apps import list_hosted_apps

    data = list_hosted_apps()
    slim = [
        {
            "id": a.get("id"),
            "label": a.get("label"),
            "runtime": a.get("runtime"),
            "routes": a.get("routes"),
            "main_url": a.get("main_url"),
            "health_urls": a.get("health_urls"),
            "application_version": a.get("application_version"),
            "pending_registration": a.get("pending_registration"),
        }
        for a in (data.get("apps") or [])
    ]
    return _as_json_block({"app_count": len(slim), "apps": slim})[0], {}


def _collect_traefik_routes(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from hosted_offboard import traefik_routes_with_hosted_hints

    return _as_json_block(traefik_routes_with_hosted_hints())[0], {}


def _collect_cloudflare_local(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from monitor import collect_cloudflare_local_status

    return _as_json_block(collect_cloudflare_local_status())[0], {}


def _collect_ai_config(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    # config_for_ui() already masks keys; scrub_structure is belt and braces.
    from ai_config import config_for_ui

    return _as_json_block(config_for_ui(), limit=3000)[0], {}


def _collect_service_logs(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from monitor import collect_service_logs

    container = plan.service_container or "traefik"
    data = collect_service_logs(service_container=container, tail=MAX_LOG_LINES, since_seconds=3600)
    lines = [
        f"{e.get('level', '')}\t{e.get('message', e.get('line', ''))}"
        for e in (data.get("entries") or data.get("logs") or [])
    ]
    body = "\n".join(lines[-MAX_LOG_LINES:]) if lines else json.dumps(data, default=str)[:2000]
    return _trim(scrub_text(f"container: {container}\n{body}"))[0], {"container": container}


def _collect_app_snapshot(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from hosted_apps import snapshot_for_slug

    data = snapshot_for_slug(plan.app_slug)
    return _as_json_block(data)[0], {"app": plan.app_slug}


def _collect_app_logs(plan: LivePlan) -> tuple[str, dict[str, Any]]:
    from hosted_apps import logs_for_slug

    data = logs_for_slug(plan.app_slug, tail=MAX_LOG_LINES, since_seconds=3600)
    log = str(data.get("log") or "")
    if not log and data.get("error"):
        log = f"(no logs: {data['error']})"
    lines = log.splitlines()[-MAX_LOG_LINES:]
    return _trim(scrub_text(f"app: {plan.app_slug}\n" + "\n".join(lines)))[0], {"app": plan.app_slug}


COLLECTORS: dict[str, Callable[[LivePlan], tuple[str, dict[str, Any]]]] = {
    "stack_status": _collect_stack_status,
    "services": _collect_services,
    "control_targets": _collect_control_targets,
    "hosted_apps": _collect_hosted_apps,
    "traefik_routes": _collect_traefik_routes,
    "cloudflare_local": _collect_cloudflare_local,
    "ai_config": _collect_ai_config,
    "service_logs": _collect_service_logs,
    "app_snapshot": _collect_app_snapshot,
    "app_logs": _collect_app_logs,
}


@dataclass
class LiveBlob:
    source: str
    label: str
    why: str
    text: str
    ok: bool = True
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "label": self.label,
            "why": self.why,
            "ok": self.ok,
            "error": self.error,
            "chars": len(self.text),
            **self.extra,
        }


def collect_live(plan: LivePlan) -> list[LiveBlob]:
    """Run every planned collector. A failing source degrades to a note, not a 500."""
    blobs: list[LiveBlob] = []
    for source in plan.sources:
        rule = LIVE_RULES_BY_SOURCE.get(source)
        fn = COLLECTORS.get(source)
        if fn is None or rule is None:
            continue
        t0 = time.perf_counter()
        try:
            text, extra = fn(plan)
            blobs.append(
                LiveBlob(
                    source=source,
                    label=rule.label,
                    why=plan.reasons.get(source, rule.why),
                    text=text,
                    extra={**extra, "ms": int((time.perf_counter() - t0) * 1000)},
                )
            )
        except Exception as exc:
            blobs.append(
                LiveBlob(
                    source=source, label=rule.label, why=plan.reasons.get(source, rule.why),
                    text="", ok=False, error=str(exc)[:300],
                    extra={"ms": int((time.perf_counter() - t0) * 1000)},
                )
            )
    return blobs


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the LEco DevOps assistant. LEco DevOps is a local platform that runs \
applications behind a Traefik edge router on *.lh hostnames, orchestrated by \
ecosystem-stack scripts and operated through a dashboard, a `leco-devops` CLI, \
and an MCP server.

You answer questions about this platform and about the specific machine the \
context was captured from.

GROUNDING RULES — these override everything else:
1. Answer ONLY from the CONTEXT block below. Do not use outside knowledge about \
   other tools, other platforms, or how you imagine this one might work.
2. If the context does not cover the question, say so plainly in one sentence \
   and name what is missing. Do not guess, do not invent file paths, command \
   names, flags, hostnames, or config keys that are not in the context.
3. Cite the numbered sources you used, like [2] or [1][4], inline in the answer.
4. DOCUMENTATION sources describe how the platform is designed. LIVE STATE \
   sources describe this machine right now. When they disagree, trust LIVE \
   STATE for what *is* and DOCUMENTATION for what *should be*, and say which.
5. Values shown as [REDACTED] are credentials that were deliberately withheld. \
   Never speculate about them and never ask the user to paste one.
6. You are read-only. You may explain which command an operator would run, but \
   you are not performing any action.

Answer in GitHub-flavoured markdown, concise and operational. Prefer concrete \
commands, paths and hostnames that appear in the context.

Reply with a single JSON object and nothing else:
{
  "answer": "<your markdown answer, with inline [n] citations>",
  "context_sufficient": <true if the context genuinely answered the question, else false>,
  "sources_used": [<the source numbers you actually used>],
  "missing": "<if context_sufficient is false, what information would be needed>"
}
"""


@dataclass
class AssembledContext:
    system_prompt: str
    user_prompt: str
    passages: list[Passage]
    live: list[LiveBlob]
    used_chars: int
    truncated: bool
    omitted_passages: int
    omitted_live: int


def assemble_prompt(
    question: str,
    passages: list[Passage],
    live: list[LiveBlob],
    *,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> AssembledContext:
    """Build the grounded prompt within a hard character budget.

    Live state is packed first (it is the most specific evidence and the most
    perishable) but may claim no more than ``LIVE_CONTEXT_SHARE`` of the budget,
    so a chatty log can never crowd out the documentation that explains it.
    """
    budget = max(2_000, min(int(max_context_chars), MAX_CONTEXT_CHARS_CEILING))
    live_budget = int(budget * LIVE_CONTEXT_SHARE)

    parts: list[str] = []
    used = 0
    truncated = False
    omitted_live = 0
    kept_live: list[LiveBlob] = []

    if live:
        parts.append("## LIVE STATE (this machine, captured just now)\n")
        for blob in live:
            if not blob.ok or not blob.text:
                kept_live.append(blob)
                parts.append(f"### live:{blob.source} — {blob.label}\n(unavailable: {blob.error or 'no data'})\n")
                continue
            body = scrub_text(blob.text)
            header = f"### live:{blob.source} — {blob.label}\n"
            cost = len(header) + len(body) + 8
            if used + cost > live_budget:
                remaining = live_budget - used - len(header) - 32
                if remaining < 400:
                    omitted_live += 1
                    truncated = True
                    continue
                body = body[:remaining] + "\n… [truncated]"
                cost = len(header) + len(body) + 8
                truncated = True
            parts.append(f"{header}```\n{body}\n```\n")
            used += cost
            kept_live.append(blob)

    kept: list[Passage] = []
    omitted = 0
    if passages:
        parts.append("\n## DOCUMENTATION (LEco DevOps repository)\n")
        for i, p in enumerate(passages, start=1):
            header = f"### [{i}] {p.path} — {p.citation()}\n"
            body = scrub_text(p.text)
            cost = len(header) + len(body) + 2
            if used + cost > budget:
                remaining = budget - used - len(header) - 32
                if remaining < 300:
                    omitted += 1
                    truncated = True
                    continue
                body = body[:remaining] + "\n… [truncated]"
                cost = len(header) + len(body) + 2
                truncated = True
            parts.append(f"{header}{body}\n")
            used += cost
            kept.append(p)

    context = "".join(parts) if parts else "(no context was retrieved)"
    notice = (
        "\nNOTE: the context above was truncated to fit a size limit; some detail "
        "was omitted. Say so if the answer feels incomplete.\n"
        if truncated
        else ""
    )
    user_prompt = (
        f"CONTEXT\n=======\n{context}{notice}\n"
        f"QUESTION\n========\n{question.strip()}\n\n"
        "Answer strictly from the context above, citing the [n] sources you used."
    )
    return AssembledContext(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        passages=kept,
        live=kept_live,
        used_chars=used,
        truncated=truncated,
        omitted_passages=omitted,
        omitted_live=omitted_live,
    )


# ---------------------------------------------------------------------------
# Provider plumbing
# ---------------------------------------------------------------------------

# Where each provider sends the prompt — surfaced verbatim so a user asking
# about their own logs can see which network the text crossed.
_PROVIDER_DESTINATIONS = {
    "ollama": ("local", "this machine (Ollama)"),
    "airllm": ("local", "this machine (AirLLM)"),
    "openai": ("cloud", "api.openai.com"),
    "anthropic": ("cloud", "api.anthropic.com"),
    "google": ("cloud", "generativelanguage.googleapis.com"),
    "hybrid": ("mixed", "local model for summarisation, cloud model for the answer"),
}


def provider_info(cfg: dict[str, Any], provider: Any, model: str = "") -> dict[str, Any]:
    """Honest description of where a question will be (or was) sent."""
    name = str(cfg.get("provider") or "none")
    pcfg = (cfg.get("providers") or {}).get(name) or {}
    locality, destination = _PROVIDER_DESTINATIONS.get(name, ("unknown", ""))
    if name == "openai-compatible":
        base = str(pcfg.get("base_url") or "")
        preset = str(pcfg.get("preset") or "")
        host = re.sub(r"^https?://", "", base).split("/")[0]
        locality = "local" if re.match(r"^(localhost|127\.|host\.docker\.internal|[\w-]+\.lh)", host or "") else "cloud"
        destination = host or preset or "an OpenAI-compatible endpoint"
    if name == "hybrid":
        destination = (
            f"{pcfg.get('local_provider', 'ollama')} (local) then "
            f"{pcfg.get('cloud_provider', 'openai')} (cloud)"
        )
    resolved_model = model or pcfg.get("default_model") or cfg.get("default_model") or ""
    return {
        "configured": provider is not None,
        "name": name,
        "model": resolved_model,
        "locality": locality,
        "destination": destination,
        "note": (
            "Your question and the retrieved context — including any live logs — are sent to "
            f"{destination}." if provider is not None and destination else
            "No AI provider is configured; nothing left this machine."
        ),
    }


def _build_provider(model_override: str = "") -> tuple[Any, dict[str, Any], str]:
    """``(provider_or_None, config, error)`` from the *saved* provider settings."""
    try:
        from ai_config import get_provider_config
        from ai_provider import create_provider
    except Exception as exc:
        return None, {"provider": "none", "providers": {}}, f"AI modules unavailable: {exc}"
    cfg = get_provider_config()
    try:
        provider = create_provider(cfg)
    except Exception as exc:
        return None, cfg, str(exc)
    if provider is None:
        name = cfg.get("provider", "none")
        if name == "none":
            return None, cfg, "No AI provider is selected. Choose one in AI configuration."
        return None, cfg, f"Provider '{name}' is selected but not fully configured (missing key or base URL)."
    return provider, cfg, ""


class AnswerExtractor:
    """Pull the ``answer`` string out of a JSON reply as it streams in.

    Every provider in this codebase forces JSON output mode on ``analyze()``, so
    the model replies with a JSON object.  For a chat-style feature the user
    wants prose token by token, not raw JSON — this decodes the ``answer``
    string incrementally, including escape sequences, and falls back to the raw
    text if the model ignored the schema.
    """

    _ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", '"': '"', "\\": "\\", "/": "/"}
    _START_RE = re.compile(r'"answer"\s*:\s*"')

    def __init__(self) -> None:
        self.buf = ""
        self.started = False
        self.finished = False
        self.pos = 0
        self.text = ""

    def feed(self, delta: str) -> str:
        """Append raw model output; return the newly decoded prose (may be "")."""
        self.buf += delta
        if self.finished:
            return ""
        if not self.started:
            m = self._START_RE.search(self.buf)
            if not m:
                return ""
            self.started = True
            self.pos = m.end()
        out: list[str] = []
        i = self.pos
        buf = self.buf
        while i < len(buf):
            ch = buf[i]
            if ch == "\\":
                if i + 1 >= len(buf):
                    break
                esc = buf[i + 1]
                if esc == "u":
                    if i + 6 > len(buf):
                        break
                    try:
                        out.append(chr(int(buf[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                out.append(self._ESCAPES.get(esc, esc))
                i += 2
                continue
            if ch == '"':
                self.finished = True
                i += 1
                break
            out.append(ch)
            i += 1
        self.pos = i
        piece = "".join(out)
        self.text += piece
        return piece

    def final(self) -> tuple[str, dict[str, Any]]:
        """``(answer_text, parsed_json_or_empty)`` once the stream is complete."""
        parsed: dict[str, Any] = {}
        try:
            candidate = json.loads(self.buf)
            if isinstance(candidate, dict):
                parsed = candidate
        except (ValueError, TypeError):
            m = re.search(r"\{.*\}", self.buf, re.DOTALL)
            if m:
                try:
                    candidate = json.loads(m.group(0))
                    if isinstance(candidate, dict):
                        parsed = candidate
                except (ValueError, TypeError):
                    parsed = {}
        if parsed.get("answer"):
            return str(parsed["answer"]), parsed
        if self.text.strip():
            return self.text, parsed
        # The model ignored the schema entirely — better a raw answer than none.
        return self.buf.strip(), parsed


def _retrieval_only_answer(ctx: AssembledContext, reason: str) -> str:
    """A useful reply with no LLM: the passages themselves, plainly labelled."""
    lines = [
        f"**No AI provider answered this** — {reason}",
        "",
        "Here are the most relevant passages retrieved from this platform's own documentation"
        + (" and live state" if ctx.live else "")
        + ":",
        "",
    ]
    for i, p in enumerate(ctx.passages, start=1):
        snippet = p.text.strip()
        if len(snippet) > 900:
            snippet = snippet[:900].rstrip() + " …"
        lines.append(f"### [{i}] {p.path} — {p.citation()}")
        lines.append("")
        lines.append(snippet)
        lines.append("")
    for blob in ctx.live:
        if not blob.ok:
            continue
        lines.append(f"### live:{blob.source} — {blob.label}")
        lines.append("")
        lines.append("```")
        lines.append(blob.text[:1200])
        lines.append("```")
        lines.append("")
    if not ctx.passages and not ctx.live:
        lines.append("_Nothing matched. Try naming a file, service, hostname or error code._")
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepare(
    question: str,
    *,
    top_k: int,
    live_enabled: bool,
    live_requested: list[str] | None,
    app: str,
    max_context_chars: int,
    use_embeddings: bool,
) -> tuple[AssembledContext, LivePlan, dict[str, Any]]:
    passages, rmeta = retrieve(question, top_k=top_k, use_embeddings=use_embeddings)
    plan = plan_live_sources(question, explicit_app=app, requested=live_requested, enabled=live_enabled)
    blobs = collect_live(plan)
    ctx = assemble_prompt(question, passages, blobs, max_context_chars=max_context_chars)
    return ctx, plan, rmeta


def _base_payload(
    question: str,
    ctx: AssembledContext,
    plan: LivePlan,
    rmeta: dict[str, Any],
    pinfo: dict[str, Any],
    include_prompt: bool = False,
) -> dict[str, Any]:
    return {
        # Opt-in echo of exactly what would be sent. Safe by construction — this
        # is the scrubbed text — and it lets a user verify what left the machine.
        "prompt_preview": (
            {"system": ctx.system_prompt, "user": ctx.user_prompt} if include_prompt else None
        ),
        "ok": True,
        "question": question,
        "generated_at": _now(),
        "provider": pinfo,
        "sources": [p.to_dict(i) for i, p in enumerate(ctx.passages, start=1)],
        "live_sources": [b.to_dict() for b in ctx.live],
        "live_plan": plan.to_dict(),
        "retrieval": rmeta,
        "context": {
            "chars": ctx.used_chars,
            "truncated": ctx.truncated,
            "omitted_passages": ctx.omitted_passages,
            "omitted_live_sources": ctx.omitted_live,
            "note": (
                "Context was truncated to fit the character budget; some retrieved "
                "material was left out."
                if ctx.truncated else ""
            ),
        },
    }


def ask(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    live_enabled: bool = True,
    live_requested: list[str] | None = None,
    app: str = "",
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    use_embeddings: bool = True,
    model: str = "",
    retrieval_only: bool = False,
    include_prompt: bool = False,
) -> dict[str, Any]:
    """Answer a question about LEco DevOps, grounded in docs + live state.

    Returns the retrieved context even when no provider is configured, so the
    endpoint is useful (and testable) on a fresh machine with no network.
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "question is required"}

    ctx, plan, rmeta = _prepare(
        question, top_k=top_k, live_enabled=live_enabled, live_requested=live_requested,
        app=app, max_context_chars=max_context_chars, use_embeddings=use_embeddings,
    )

    provider, cfg, perr = (None, {"provider": "none", "providers": {}}, "retrieval-only requested")
    if not retrieval_only:
        provider, cfg, perr = _build_provider(model)
    pinfo = provider_info(cfg, provider, model)
    payload = _base_payload(question, ctx, plan, rmeta, pinfo, include_prompt)

    if provider is None:
        payload.update(
            answer=_retrieval_only_answer(ctx, perr),
            answered_by="retrieval-only",
            context_sufficient=None,
            provider_error=perr,
        )
        return payload

    t0 = time.perf_counter()
    try:
        result = provider.analyze(ctx.system_prompt, ctx.user_prompt, model=model or None, stream=False)
    except Exception as exc:
        payload.update(
            answer=_retrieval_only_answer(ctx, f"the provider call failed: {exc}"),
            answered_by="retrieval-only",
            context_sufficient=None,
            provider_error=str(exc)[:400],
        )
        return payload

    data = getattr(result, "data", {}) or {}
    raw = getattr(result, "raw_text", "") or ""
    answer = str(data.get("answer") or "").strip()
    if not answer and raw.strip():
        ex = AnswerExtractor()
        ex.feed(raw)
        answer, parsed = ex.final()
        data = data or parsed
    if not answer:
        payload.update(
            answer=_retrieval_only_answer(ctx, f"the provider returned no usable answer ({getattr(result, 'error', '')})"),
            answered_by="retrieval-only",
            context_sufficient=None,
            provider_error=str(getattr(result, "error", ""))[:400],
        )
        return payload

    payload.update(
        answer=answer,
        answered_by="llm",
        context_sufficient=data.get("context_sufficient"),
        sources_used=data.get("sources_used") or [],
        missing=str(data.get("missing") or ""),
        elapsed_seconds=round(time.perf_counter() - t0, 3),
    )
    payload["provider"]["model"] = getattr(result, "model", "") or pinfo.get("model", "")
    return payload


def ask_stream(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    live_enabled: bool = True,
    live_requested: list[str] | None = None,
    app: str = "",
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    use_embeddings: bool = True,
    model: str = "",
    include_prompt: bool = False,
) -> Iterator[dict[str, Any]]:
    """NDJSON-shaped events: ``status`` → ``sources`` → ``token``* → ``done``.

    Mirrors ``/api/leco/ai-analyze/stream``: every event is ``{"type", "text",
    "data"}``.  Sources are emitted *before* the first token so a UI can render
    citations while the answer is still arriving.
    """
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "text": "question is required"}
        yield {"type": "done", "data": {"ok": False, "error": "question is required"}}
        return

    yield {"type": "status", "text": "Retrieving documentation and live state…"}
    ctx, plan, rmeta = _prepare(
        question, top_k=top_k, live_enabled=live_enabled, live_requested=live_requested,
        app=app, max_context_chars=max_context_chars, use_embeddings=use_embeddings,
    )
    provider, cfg, perr = _build_provider(model)
    pinfo = provider_info(cfg, provider, model)
    payload = _base_payload(question, ctx, plan, rmeta, pinfo, include_prompt)
    yield {"type": "sources", "data": {k: payload[k] for k in ("sources", "live_sources", "live_plan", "retrieval", "context", "provider")}}

    if provider is None:
        answer = _retrieval_only_answer(ctx, perr)
        yield {"type": "token", "text": answer}
        payload.update(answer=answer, answered_by="retrieval-only", context_sufficient=None, provider_error=perr)
        yield {"type": "done", "data": payload}
        return

    yield {"type": "status", "text": f"Asking {pinfo['name']} ({pinfo['destination']})…"}
    t0 = time.perf_counter()
    try:
        stream = provider.analyze(ctx.system_prompt, ctx.user_prompt, model=model or None, stream=True)
    except Exception as exc:
        answer = _retrieval_only_answer(ctx, f"the provider call failed: {exc}")
        yield {"type": "token", "text": answer}
        payload.update(answer=answer, answered_by="retrieval-only", provider_error=str(exc)[:400])
        yield {"type": "done", "data": payload}
        return

    # Google (and any future provider without token streaming) returns a plain
    # result even when stream=True — handle both shapes.
    if not hasattr(stream, "__iter__"):
        data = getattr(stream, "data", {}) or {}
        answer = str(data.get("answer") or getattr(stream, "raw_text", "") or "").strip()
        if not answer:
            answer = _retrieval_only_answer(ctx, f"the provider returned no usable answer ({getattr(stream, 'error', '')})")
            payload.update(answered_by="retrieval-only")
        else:
            payload.update(answered_by="llm", context_sufficient=data.get("context_sufficient"))
        yield {"type": "token", "text": answer}
        payload.update(answer=answer, elapsed_seconds=round(time.perf_counter() - t0, 3))
        yield {"type": "done", "data": payload}
        return

    extractor = AnswerExtractor()
    error_text = ""
    emitted = 0
    for chunk in stream:
        ctype = getattr(chunk, "type", "")
        if ctype == "token":
            piece = extractor.feed(getattr(chunk, "text", "") or "")
            if piece:
                emitted += len(piece)
                yield {"type": "token", "text": piece}
        elif ctype == "error":
            error_text = getattr(chunk, "text", "") or "provider error"
            yield {"type": "error", "text": error_text}
        elif ctype == "done":
            full = getattr(chunk, "text", "") or ""
            if full and len(full) > len(extractor.buf):
                piece = extractor.feed(full[len(extractor.buf):])
                if piece:
                    emitted += len(piece)
                    yield {"type": "token", "text": piece}

    answer, parsed = extractor.final()
    # A provider error with no schema-shaped output is a failure, even if the
    # buffer holds some stray text (a hybrid provider's local summary, say).
    # Calling that an LLM answer would present scaffolding as an answer.
    if error_text and not extractor.started:
        answer = ""
    if not answer.strip():
        answer = _retrieval_only_answer(ctx, error_text or "the provider returned no usable answer")
        yield {"type": "token", "text": answer}
        payload.update(answered_by="retrieval-only", provider_error=error_text)
    else:
        if emitted < len(answer):
            # Nothing (or only part) streamed as prose — the provider returned the
            # body in one piece. The client must still receive the whole answer.
            yield {"type": "token", "text": answer[emitted:]}
        payload.update(
            answered_by="llm",
            context_sufficient=parsed.get("context_sufficient"),
            sources_used=parsed.get("sources_used") or [],
            missing=str(parsed.get("missing") or ""),
        )
    payload.update(answer=answer, elapsed_seconds=round(time.perf_counter() - t0, 3))
    yield {"type": "done", "data": payload}


# ---------------------------------------------------------------------------
# Status / reindex
# ---------------------------------------------------------------------------

def status() -> dict[str, Any]:
    """Index size, freshness, embedding state, and whether a provider is set."""
    idx = ai_corpus.index_status()
    provider, cfg, perr = _build_provider()
    pinfo = provider_info(cfg, provider)
    return {
        "ok": True,
        "generated_at": _now(),
        "index": idx,
        "retrieval": {
            "lexical": "bm25 (k1=%.2f, b=%.2f) + flat heading/path bonuses (x%.1f / x%.1f idf)" % (
                BM25_K1, BM25_B, HEADING_BONUS, PATH_BONUS
            ),
            "embeddings_active": bool(idx.get("embeddings", {}).get("active")),
            "embeddings_note": idx.get("embeddings", {}).get("reason", ""),
            "default_top_k": DEFAULT_TOP_K,
            "max_context_chars": DEFAULT_MAX_CONTEXT_CHARS,
        },
        "provider": {**pinfo, "error": perr},
        "live_sources": [
            {"source": r.source, "label": r.label, "scope": r.scope, "why": r.why}
            for r in LIVE_RULES
        ],
    }


def reindex(*, embeddings: bool | None = None, embed_model: str = "") -> dict[str, Any]:
    """Rebuild the static index; optionally (re)build or drop local embeddings."""
    t0 = time.perf_counter()
    ai_corpus.reset_cache()
    with _POSTINGS_LOCK:
        _POSTINGS.clear()
    index = ai_corpus.build_index()
    out: dict[str, Any] = {
        "ok": True,
        "generated_at": _now(),
        "chunk_count": index.get("chunk_count", 0),
        "file_count": index.get("file_count", 0),
        "chunks_by_kind": index.get("chunks_by_kind", {}),
        "skipped": index.get("skipped", []),
        "index_path": str(ai_corpus.INDEX_FILE),
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "embeddings": {"changed": False, **ai_corpus.embeddings_status(index)},
    }
    if embeddings is True:
        res = ai_corpus.build_embeddings(index, model=embed_model)
        out["embeddings"] = {"changed": True, **res, **ai_corpus.embeddings_status(index)}
        if not res.get("ok"):
            out["warning"] = (
                "Static index rebuilt, but embeddings failed: %s. Lexical retrieval is unaffected."
                % res.get("error", "")
            )
    elif embeddings is False:
        dropped = ai_corpus.drop_embeddings()
        out["embeddings"] = {"changed": dropped, "available": False, "active": False,
                             "reason": "embeddings removed on request"}
    return out
