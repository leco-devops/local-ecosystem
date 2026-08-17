"""Knowledge corpus for LEco DevOps RAG — discovery, chunking, scrubbing, indexing.

This module owns the *static* half of the platform's own knowledge: the
repository's markdown documentation and the Claude plugin skill bodies.  It
turns those files into heading-aligned chunks, redacts anything that looks like
a credential **before** the text is ever persisted, and keeps a cheap JSON
index under ``ecosystem-stack/config/generated/``.

Design rules that are load-bearing:

* **No new dependencies.**  The dashboard image ships flask/docker/requests/
  PyYAML and nothing else, so everything here is stdlib.
* **Scrub at the earliest point.**  A secret that never enters the index cannot
  leak into a prompt later.  ``scrub_text`` also runs again on live state in
  ``ai_rag`` — defence in depth, because live state comes from Docker, not from
  this module.
* **Staleness by mtime.**  ``load_index()`` re-stats the source files on every
  call (~120 stats, sub-millisecond) and rebuilds when anything moved.  Editing
  a doc must be visible without restarting the dashboard.
* **Embeddings are opt-in and local-only.**  Vectors are built only when an
  operator explicitly asks for them via ``/api/ai/rag/reindex`` and only from a
  local Ollama.  The corpus is never shipped to a cloud embedding endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))

GENERATED_DIR = PROJECT_ROOT / "ecosystem-stack" / "config" / "generated"
INDEX_FILE = GENERATED_DIR / "ai-rag-index.json"
EMBEDDINGS_FILE = GENERATED_DIR / "ai-rag-embeddings.json"

INDEX_VERSION = 1

# ---------------------------------------------------------------------------
# What goes into the corpus
# ---------------------------------------------------------------------------
#
# Explicit, ordered source groups rather than one recursive sweep: the corpus is
# the platform's *documentation*, not its source tree, and being explicit is what
# keeps config files (which hold credentials) structurally out of scope.

@dataclass(frozen=True)
class SourceGroup:
    """One family of corpus files."""

    kind: str          # short label carried onto every chunk ("doc", "help", "skill", "root")
    label: str         # human phrasing for the status endpoint
    root: str          # repo-relative directory, "" for repo root
    pattern: str       # glob relative to ``root``; "" when ``files`` is used
    files: tuple[str, ...] = ()


SOURCE_GROUPS: tuple[SourceGroup, ...] = (
    SourceGroup(kind="doc", label="Repository documentation (docs/)", root="docs", pattern="**/*.md"),
    SourceGroup(
        kind="root",
        label="Top-level guides",
        root="",
        pattern="",
        files=("README.md", "START_HERE.md", "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md"),
    ),
    SourceGroup(
        kind="skill",
        label="Claude plugin skills (tools/claude-plugin/skills/)",
        root="tools/claude-plugin/skills",
        pattern="**/*.md",
    ),
)

# Anything matching these is refused even if a future source group would reach
# it.  Matched against the POSIX repo-relative path, case-insensitively.
EXCLUDED_PATH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(^|/)config/ai-providers\.ya?ml$",
        r"(^|/)config/ui-credentials\.ya?ml$",
        r"(^|/)config/leco-registry\.ya?ml$",
        r"(^|/)\.env($|\.)",
        r"(^|/)\.dev\.vars($|\.)",
        r"^certs/",
        r"(^|/)certs/",
        r"\.(pem|key|crt|p12|pfx|jks|keystore)$",
        r"(^|/)id_(rsa|ed25519|ecdsa|dsa)",
        r"(^|/)\.git/",
        r"(^|/)node_modules/",
        r"(^|/)__pycache__/",
        r"(^|/)\.local-eco-backups/",
        r"(^|/)ui-login-registry\.json$",
        r"(^|/)ui-credentials",
        r"secrets?\.(ya?ml|json|txt|env)$",
        r"credentials?\.(ya?ml|json|txt|env)$",
    )
)

MAX_FILE_BYTES = 512 * 1024  # a markdown doc bigger than this is not prose

# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------

# Field names whose *value* is always a credential.
_STRONG_SECRET_FIELDS = (
    "password", "passwd", "pwd", "secret", "api_key", "apikey", "api-key",
    "access_key", "accesskey", "private_key", "privatekey", "client_secret",
    "auth_token", "access_token", "refresh_token", "bearer_token", "session_token",
    "credential", "credentials", "token", "passphrase", "auth", "authorization",
    "cloud_api_key", "webhook_secret", "signing_key", "encryption_key",
)

# Field names that are only a credential when the value *looks* like one.
# "keys" appears all over this repo's routing docs as a plain structural word.
_WEAK_SECRET_FIELDS = ("key", "keys", "cert", "salt", "hash", "seed")

_REDACTED = "[REDACTED]"

# `name: value`, `name = value`, `name="value"` in prose, YAML, .env and JSON.
_STRONG_ASSIGN_RE = re.compile(
    r"(?P<lead>(?<![\w-])(?:[\w.-]*(?:%s))\s*[\"']?\s*[:=]\s*)(?P<q>[\"']?)(?P<val>[^\s\"',}\]]+)"
    % "|".join(re.escape(f) for f in _STRONG_SECRET_FIELDS),
    re.IGNORECASE,
)
_WEAK_ASSIGN_RE = re.compile(
    r"(?P<lead>(?<![\w-])(?:[\w.-]*(?:%s))\s*[\"']?\s*[:=]\s*)(?P<q>[\"']?)(?P<val>[^\s\"',}\]]+)"
    % "|".join(re.escape(f) for f in _WEAK_SECRET_FIELDS),
    re.IGNORECASE,
)

# Vendor token shapes — redacted wherever they appear, assignment or not.
_TOKEN_SHAPE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bsk-(?:proj-|or-v1-|live-|test-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{5,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}"),
)

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Placeholders that are documentation, not credentials.  Redacting these makes
# the docs worse without making anything safer.
_PLACEHOLDER_RE = re.compile(
    r"^(?:[\"']?)(?:"
    # `${VAR` has no closing brace here because the value capture stops at `}`.
    r"|-|~|null|none|true|false|yes|no|\.\.\.|\*+|x+|<[^>]*>?|\{\{[^}]*\}?\}?|\$\{[^}]*\}?|\$[A-Z_]+"
    r"|your[-_ ]?\w*|my[-_ ]?\w*|example\w*|changeme|redacted|\[redacted\]|placeholder"
    r"|sk-\.\.\.|sk-xxx+|\.{3}|\d{1,4}"
    r")(?:[\"']?)$",
    re.IGNORECASE,
)

_HIGH_ENTROPY_RE = re.compile(r"^[A-Za-z0-9+/=_\-]{20,}$")


def _looks_like_secret_value(value: str) -> bool:
    """True when a bare value has the shape of a real credential."""
    v = value.strip().strip("\"'")
    if not v or _PLACEHOLDER_RE.match(v):
        return False
    if any(rx.search(v) for rx in _TOKEN_SHAPE_RES):
        return True
    if not _HIGH_ENTROPY_RE.match(v):
        return False
    # Reject things that are merely long words or paths.
    classes = sum(
        (
            1 if any(c.islower() for c in v) else 0,
            1 if any(c.isupper() for c in v) else 0,
            1 if any(c.isdigit() for c in v) else 0,
        )
    )
    return classes >= 2


def is_secret_field_name(name: str) -> bool:
    """True when a config/state *key* name implies its value is a credential."""
    low = str(name).lower()
    return any(frag in low for frag in _STRONG_SECRET_FIELDS)


def scrub_text(text: str) -> str:
    """Redact credential-shaped material from a block of text.

    Runs on every corpus chunk at index time and again on every live-state blob
    at query time, so no path into a prompt bypasses it.
    """
    if not text:
        return text
    out = _PEM_RE.sub("-----BEGIN PRIVATE KEY----- %s -----END PRIVATE KEY-----" % _REDACTED, text)

    def _strong(m: re.Match[str]) -> str:
        val = m.group("val")
        if _PLACEHOLDER_RE.match(val.strip()):
            return m.group(0)
        return f"{m.group('lead')}{m.group('q')}{_REDACTED}"

    def _weak(m: re.Match[str]) -> str:
        val = m.group("val")
        if not _looks_like_secret_value(val):
            return m.group(0)
        return f"{m.group('lead')}{m.group('q')}{_REDACTED}"

    out = _STRONG_ASSIGN_RE.sub(_strong, out)
    out = _WEAK_ASSIGN_RE.sub(_weak, out)
    for rx in _TOKEN_SHAPE_RES:
        out = rx.sub(_REDACTED, out)
    return out


def scrub_structure(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact a JSON-ish structure (live state from Docker et al.).

    Keys whose name implies a credential lose their value outright; every string
    still goes through :func:`scrub_text` in case a token is embedded in prose.
    """
    if _depth > 12:
        return "[depth-limited]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if is_secret_field_name(k):
                out[k] = _REDACTED
            else:
                out[k] = scrub_structure(v, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub_structure(v, _depth=_depth + 1) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    """Resolved repository root the corpus is confined to."""
    return PROJECT_ROOT.resolve()


def is_excluded(rel_path: str) -> bool:
    """True when a repo-relative path must never enter the corpus."""
    rel = rel_path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return any(rx.search(rel) for rx in EXCLUDED_PATH_PATTERNS)


def safe_relative_path(candidate: Path) -> str | None:
    """Repo-relative POSIX path, or None when the file escapes the repo root.

    Resolves symlinks first, so a symlinked doc pointing at ``/etc/shadow``
    fails the containment check rather than being read.
    """
    root = repo_root()
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return None
    return rel.as_posix()


def iter_source_files() -> Iterator[tuple[str, Path, str]]:
    """Yield ``(kind, absolute_path, repo_relative_path)`` for every corpus file."""
    root = repo_root()
    seen: set[str] = set()
    for group in SOURCE_GROUPS:
        base = (root / group.root) if group.root else root
        candidates: Iterable[Path]
        if group.files:
            candidates = [base / name for name in group.files]
        else:
            try:
                candidates = sorted(base.glob(group.pattern))
            except OSError:
                continue
        for path in candidates:
            if not path.is_file():
                continue
            rel = safe_relative_path(path)
            if rel is None or rel in seen or is_excluded(rel):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            seen.add(rel)
            yield group.kind, path, rel


# ---------------------------------------------------------------------------
# Markdown chunking
# ---------------------------------------------------------------------------

MIN_CHUNK_CHARS = 700       # below this a section absorbs the ones that follow it
TARGET_CHUNK_CHARS = 1800
MAX_CHUNK_CHARS = 2800
ABSORB_FLOOR_CHARS = 160    # a chunk this small is a stray heading, never a passage

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


@dataclass
class Section:
    level: int
    title: str
    trail: list[str]
    lines: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n".join(self.lines).strip("\n")


def split_sections(text: str, *, doc_title: str) -> list[Section]:
    """Split markdown into heading-delimited sections, honouring code fences."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    current = Section(level=0, title=doc_title, trail=[doc_title])
    in_fence = False
    fence_marker = ""

    for line in text.splitlines():
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            current.lines.append(line)
            continue
        if in_fence:
            current.lines.append(line)
            continue

        head = _HEADING_RE.match(line)
        if not head:
            current.lines.append(line)
            continue

        if current.body.strip() or current.level > 0:
            sections.append(current)
        level = len(head.group(1))
        title = head.group(2).strip() or f"(section {len(sections) + 1})"
        while stack and stack[-1][0] >= level:
            stack.pop()
        trail = [doc_title] + [t for _, t in stack] + [title]
        stack.append((level, title))
        current = Section(level=level, title=title, trail=trail)

    if current.body.strip() or not sections:
        sections.append(current)
    return [s for s in sections if s.body.strip() or s.title.strip()]


def _split_oversized(section: Section) -> list[tuple[list[str], str]]:
    """Split one very long section at paragraph boundaries.

    Returns ``(trail, body)`` pairs; the trail gains a ``part n/m`` suffix so a
    citation still tells the reader which slice they are looking at.
    """
    parts: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", section.body):
        if buf and len(buf) + len(para) + 2 > TARGET_CHUNK_CHARS:
            parts.append(buf.strip())
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        parts.append(buf.strip())

    # A long markdown table or list has no blank lines, so paragraph splitting
    # returns one giant part. Fall back to line boundaries for those.
    exploded: list[str] = []
    for part in parts:
        if len(part) <= MAX_CHUNK_CHARS:
            exploded.append(part)
            continue
        line_buf = ""
        for line in part.splitlines():
            if line_buf and len(line_buf) + len(line) + 1 > TARGET_CHUNK_CHARS:
                exploded.append(line_buf.strip())
                line_buf = line
            else:
                line_buf = f"{line_buf}\n{line}" if line_buf else line
        if line_buf.strip():
            exploded.append(line_buf.strip())
    parts = [p for p in exploded if p.strip()]

    if len(parts) <= 1:
        return [(list(section.trail), section.body)]
    total = len(parts)
    return [
        (list(section.trail) + [f"part {i + 1}/{total}"], body)
        for i, body in enumerate(parts)
    ]


def chunk_markdown(text: str, *, rel_path: str, kind: str) -> list[dict[str, Any]]:
    """Heading-aligned chunks carrying source path and heading trail.

    Sections shorter than ``MIN_CHUNK_CHARS`` absorb the sections that follow
    them until the chunk is worth retrieving; oversized sections are split at
    paragraph boundaries.  Boundaries are therefore always at headings, so a
    retrieved passage never begins mid-thought.
    """
    doc_title = _document_title(text, rel_path)
    sections = split_sections(text, doc_title=doc_title)

    chunks: list[dict[str, Any]] = []
    i = 0
    while i < len(sections):
        sec = sections[i]
        if len(sec.body) > MAX_CHUNK_CHARS:
            for trail, body in _split_oversized(sec):
                chunks.append(_make_chunk(rel_path, kind, trail, body, doc_title))
            i += 1
            continue

        trail = list(sec.trail)
        body = sec.body
        titles = [sec.title]
        j = i + 1
        frontier = sec.level
        while len(body) < MIN_CHUNK_CHARS and j < len(sections):
            nxt = sections[j]
            # Only descend. Absorbing a sibling would merge two unrelated topics
            # under the first one's heading, mislabelling the citation and hiding
            # the second topic. The one exception is a section with essentially
            # no body — a bare parent heading whose content is the section below.
            if nxt.level <= frontier and len(body) >= ABSORB_FLOOR_CHARS:
                break
            if len(body) + len(nxt.body) > MAX_CHUNK_CHARS:
                break
            heading_line = "#" * max(1, nxt.level) + " " + nxt.title
            body = f"{body}\n\n{heading_line}\n\n{nxt.body}".strip()
            titles.append(nxt.title)
            frontier = nxt.level
            j += 1
        chunks.append(_make_chunk(rel_path, kind, trail, body, doc_title, headings=titles))
        i = max(j, i + 1)

    return _absorb_stragglers([c for c in chunks if c["text"].strip()])


def _absorb_stragglers(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold sub-``ABSORB_FLOOR_CHARS`` chunks into a neighbour.

    A near-empty chunk is almost always a parent heading whose content lives in
    the section below it. Left alone it is retrieval poison: BM25 length
    normalization makes a three-character body with a keyword-rich heading score
    higher than the page that actually answers the question.
    """
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        if len(chunk["text"]) >= ABSORB_FLOOR_CHARS:
            out.append(chunk)
            continue
        if out and len(out[-1]["text"]) + len(chunk["text"]) <= MAX_CHUNK_CHARS + ABSORB_FLOOR_CHARS:
            out[-1]["text"] = f"{out[-1]['text']}\n\n{chunk['heading']}\n\n{chunk['text']}".strip()
            out[-1]["headings"] = out[-1]["headings"] + chunk["headings"]
        elif chunk["text"].strip():
            out.append(chunk)
    if len(out) > 1 and len(out[0]["text"]) < ABSORB_FLOOR_CHARS:
        head = out.pop(0)
        out[0]["text"] = f"{head['heading']}\n\n{head['text']}\n\n{out[0]['text']}".strip()
        out[0]["headings"] = head["headings"] + out[0]["headings"]
    return out


def _document_title(text: str, rel_path: str) -> str:
    for line in text.splitlines()[:40]:
        head = _HEADING_RE.match(line)
        if head and len(head.group(1)) == 1:
            return head.group(2).strip()
    return Path(rel_path).stem.replace("-", " ").replace("_", " ")


def _make_chunk(
    rel_path: str,
    kind: str,
    trail: list[str],
    body: str,
    doc_title: str,
    headings: list[str] | None = None,
) -> dict[str, Any]:
    # The document title is trail[0]; an H1 with the same text would repeat it.
    clean_trail: list[str] = []
    for t in trail:
        if t and t.strip() and (not clean_trail or clean_trail[-1] != t):
            clean_trail.append(t)
    return {
        "path": rel_path,
        "kind": kind,
        "doc_title": doc_title,
        "heading": clean_trail[-1] if clean_trail else doc_title,
        "trail": clean_trail,
        # Every heading whose text ended up in this chunk. The trail names where
        # the chunk *starts*; this names everything it covers, so absorbed
        # sections stay findable and citable.
        "headings": [h for h in (headings or [clean_trail[-1] if clean_trail else doc_title]) if h and h.strip()],
        # Scrubbed here, at the earliest possible point: a credential that never
        # reaches the index cannot reach a prompt.
        "text": scrub_text(body).strip(),
    }


# ---------------------------------------------------------------------------
# Index build / load
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {"index": None, "signature": None}


def _file_signature() -> dict[str, list[float]]:
    """``{rel_path: [mtime, size]}`` for every corpus file — the staleness key."""
    sig: dict[str, list[float]] = {}
    for _kind, path, rel in iter_source_files():
        try:
            st = path.stat()
        except OSError:
            continue
        sig[rel] = [round(st.st_mtime, 3), st.st_size]
    return sig


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_index(*, persist: bool = True) -> dict[str, Any]:
    """Read every corpus file, chunk it, and return (optionally persist) the index."""
    t0 = time.perf_counter()
    chunks: list[dict[str, Any]] = []
    files: dict[str, list[float]] = {}
    per_group: dict[str, int] = {}
    skipped: list[str] = []

    for kind, path, rel in iter_source_files():
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            st = path.stat()
        except OSError as exc:
            skipped.append(f"{rel}: {exc}")
            continue
        files[rel] = [round(st.st_mtime, 3), st.st_size]
        made = chunk_markdown(raw, rel_path=rel, kind=kind)
        for c in made:
            c["id"] = len(chunks)
            chunks.append(c)
        per_group[kind] = per_group.get(kind, 0) + len(made)

    index = {
        "version": INDEX_VERSION,
        "built_at": _now(),
        "build_ms": int((time.perf_counter() - t0) * 1000),
        "root": str(repo_root()),
        "files": files,
        "file_count": len(files),
        "chunk_count": len(chunks),
        "chunks_by_kind": per_group,
        "skipped": skipped[:20],
        "chunk_signature": _chunk_signature(chunks),
        "chunks": chunks,
    }
    if persist:
        _write_index(index)
    with _LOCK:
        _CACHE["index"] = index
        _CACHE["signature"] = files
        _CACHE.pop("postings", None)
    return index


def _chunk_signature(chunks: list[dict[str, Any]]) -> str:
    """Stable digest of chunk identity — embeddings are only valid for a match."""
    h = hashlib.sha256()
    for c in chunks:
        h.update(c["path"].encode())
        h.update(b"\x00")
        h.update(" > ".join(c["trail"]).encode())
        h.update(b"\x00")
        h.update(str(len(c["text"])).encode())
        h.update(b"\n")
    return h.hexdigest()[:32]


def _write_index(index: dict[str, Any]) -> None:
    try:
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        tmp = INDEX_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        tmp.replace(INDEX_FILE)
    except OSError:
        # A read-only generated dir must degrade to an in-memory index, not a 500.
        pass


def _read_index() -> dict[str, Any] | None:
    try:
        raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != INDEX_VERSION:
        return None
    if not isinstance(raw.get("chunks"), list):
        return None
    return raw


def load_index(*, allow_rebuild: bool = True) -> tuple[dict[str, Any], bool]:
    """Return ``(index, rebuilt)``, rebuilding when any source file changed.

    Staleness is detected purely by mtime+size, so a doc edit is picked up on the
    next question without restarting the dashboard.
    """
    current = _file_signature()
    with _LOCK:
        cached = _CACHE.get("index")
        if cached is not None and _CACHE.get("signature") == current:
            return cached, False

    disk = _read_index()
    if disk is not None and disk.get("files") == current:
        with _LOCK:
            _CACHE["index"] = disk
            _CACHE["signature"] = current
            _CACHE.pop("postings", None)
        return disk, False

    if not allow_rebuild:
        if disk is not None:
            return disk, False
        return {
            "version": INDEX_VERSION, "built_at": None, "files": {}, "file_count": 0,
            "chunk_count": 0, "chunks_by_kind": {}, "chunks": [], "chunk_signature": "",
            "root": str(repo_root()), "skipped": [],
        }, False

    return build_index(), True


def index_status() -> dict[str, Any]:
    """Metadata about the persisted index without forcing a rebuild."""
    disk = _read_index()
    current = _file_signature()
    stale = disk is None or disk.get("files") != current
    emb = embeddings_status(disk)
    return {
        "built_at": (disk or {}).get("built_at"),
        "build_ms": (disk or {}).get("build_ms"),
        "chunk_count": (disk or {}).get("chunk_count", 0),
        "file_count": (disk or {}).get("file_count", 0),
        "chunks_by_kind": (disk or {}).get("chunks_by_kind", {}),
        "index_path": str(INDEX_FILE),
        "index_present": disk is not None,
        "stale": bool(stale),
        "source_files_on_disk": len(current),
        "sources": [
            {"kind": g.kind, "label": g.label, "root": g.root or ".", "pattern": g.pattern or ", ".join(g.files)}
            for g in SOURCE_GROUPS
        ],
        "embeddings": emb,
    }


# ---------------------------------------------------------------------------
# Optional local embeddings (Ollama) — opt-in, never a startup dependency
# ---------------------------------------------------------------------------

DEFAULT_EMBED_MODEL = os.getenv("LECO_RAG_EMBED_MODEL", "nomic-embed-text")


def ollama_base_url() -> str:
    """Base URL of the local Ollama, honouring the saved AI provider config."""
    env = (os.getenv("LECO_RAG_EMBED_BASE_URL") or "").strip()
    if env:
        return env.rstrip("/")
    try:
        from ai_config import get_provider_config

        cfg = get_provider_config()
        base = ((cfg.get("providers") or {}).get("ollama") or {}).get("base_url") or ""
        if base:
            return str(base).rstrip("/")
    except Exception:
        pass
    return "http://ollama:11434"


def embed_texts(
    texts: list[str],
    *,
    model: str = "",
    timeout: int = 120,
    deadline_seconds: float = 0.0,
) -> tuple[list[list[float]], str]:
    """Embed via the local Ollama. Returns ``(vectors, error)``; never raises.

    This is the only network call this module can make, it only ever targets a
    local Ollama, and nothing calls it unless an operator asked for embeddings.

    ``deadline_seconds`` bounds the whole batch. Ollama will happily serve
    ``/api/embeddings`` from a 7B *generative* model at ~12 s per chunk, which
    turns a reindex into a multi-hour hang; the deadline turns that into an
    honest error naming a real embedding model instead.
    """
    import requests  # local import: keeps import cost off the dashboard boot path

    mdl = model or DEFAULT_EMBED_MODEL
    base = ollama_base_url()
    started = time.perf_counter()
    out: list[list[float]] = []
    for text in texts:
        if deadline_seconds and (time.perf_counter() - started) > deadline_seconds:
            return [], (
                f"embedding with '{mdl}' exceeded {deadline_seconds:.0f}s after "
                f"{len(out)}/{len(texts)} chunks — use a dedicated embedding model "
                f"(ollama pull {DEFAULT_EMBED_MODEL})"
            )
        try:
            r = requests.post(
                f"{base}/api/embeddings",
                json={"model": mdl, "prompt": text[:8000]},
                timeout=timeout,
            )
        except Exception as exc:
            return [], f"{base}/api/embeddings unreachable: {exc}"
        if r.status_code != 200:
            return [], f"{base}/api/embeddings HTTP {r.status_code}: {r.text[:200]}"
        try:
            vec = r.json().get("embedding") or []
        except ValueError:
            return [], "Ollama returned a non-JSON embedding response"
        if not vec:
            return [], f"model '{mdl}' returned an empty embedding (is it pulled?)"
        out.append([float(x) for x in vec])
    return out, ""


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm <= 0:
        return vec
    return [x / norm for x in vec]


# A full corpus embed must finish inside a request. With nomic-embed-text this
# is ~1 minute for ~900 chunks; anything slower is the wrong model.
EMBED_BUILD_DEADLINE_SECONDS = float(os.getenv("LECO_RAG_EMBED_DEADLINE", "600"))


def build_embeddings(index: dict[str, Any], *, model: str = "") -> dict[str, Any]:
    """Embed every chunk and persist normalized float32 vectors beside the index."""
    chunks = index.get("chunks") or []
    if not chunks:
        return {"ok": False, "error": "index has no chunks"}
    texts = [f"{' > '.join(c['trail'])}\n\n{c['text']}" for c in chunks]
    t0 = time.perf_counter()
    vectors, err = embed_texts(texts, model=model, deadline_seconds=EMBED_BUILD_DEADLINE_SECONDS)
    if err:
        return {"ok": False, "error": err, "model": model or DEFAULT_EMBED_MODEL}
    dim = len(vectors[0])
    flat = array("f")
    for vec in vectors:
        if len(vec) != dim:
            return {"ok": False, "error": "embedding model returned inconsistent dimensions"}
        flat.extend(_normalize(vec))
    payload = {
        "model": model or DEFAULT_EMBED_MODEL,
        "dim": dim,
        "count": len(vectors),
        "chunk_signature": index.get("chunk_signature", ""),
        "built_at": _now(),
        "build_ms": int((time.perf_counter() - t0) * 1000),
        "data": base64.b64encode(flat.tobytes()).decode("ascii"),
    }
    try:
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        tmp = EMBEDDINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(EMBEDDINGS_FILE)
    except OSError as exc:
        return {"ok": False, "error": f"could not persist embeddings: {exc}"}
    with _LOCK:
        _CACHE.pop("vectors", None)
    return {
        "ok": True, "model": payload["model"], "dim": dim, "count": len(vectors),
        "build_ms": payload["build_ms"],
    }


def drop_embeddings() -> bool:
    """Remove persisted vectors — the way an operator turns the upgrade back off."""
    with _LOCK:
        _CACHE.pop("vectors", None)
    try:
        EMBEDDINGS_FILE.unlink()
        return True
    except OSError:
        return False


def _read_embeddings_meta() -> dict[str, Any] | None:
    try:
        raw = json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def embeddings_status(index: dict[str, Any] | None = None) -> dict[str, Any]:
    """Whether persisted vectors exist and still match the current chunk set."""
    meta = _read_embeddings_meta()
    if meta is None:
        return {"available": False, "active": False, "reason": "no embeddings built (opt-in)"}
    idx = index if index is not None else _read_index()
    sig = (idx or {}).get("chunk_signature", "")
    matches = bool(sig) and meta.get("chunk_signature") == sig
    return {
        "available": True,
        "active": matches,
        "model": meta.get("model"),
        "dim": meta.get("dim"),
        "count": meta.get("count"),
        "built_at": meta.get("built_at"),
        "reason": "" if matches else "vectors are stale for the current chunk set — reindex with embeddings",
    }


def load_vectors(index: dict[str, Any]) -> tuple[list[list[float]] | None, dict[str, Any]]:
    """Normalized per-chunk vectors when they match this index, else ``(None, meta)``."""
    meta = _read_embeddings_meta()
    if meta is None:
        return None, {"active": False, "reason": "no embeddings built"}
    if meta.get("chunk_signature") != index.get("chunk_signature"):
        return None, {"active": False, "reason": "embeddings stale for current chunks"}
    with _LOCK:
        cached = _CACHE.get("vectors")
        if cached and cached.get("signature") == meta.get("chunk_signature"):
            return cached["vectors"], {"active": True, "model": meta.get("model"), "dim": meta.get("dim")}
    try:
        flat = array("f")
        flat.frombytes(base64.b64decode(meta["data"]))
    except (KeyError, ValueError, TypeError):
        return None, {"active": False, "reason": "embedding payload is unreadable"}
    dim = int(meta.get("dim") or 0)
    count = int(meta.get("count") or 0)
    if dim <= 0 or count <= 0 or len(flat) != dim * count:
        return None, {"active": False, "reason": "embedding payload size mismatch"}
    vectors = [list(flat[i * dim:(i + 1) * dim]) for i in range(count)]
    with _LOCK:
        _CACHE["vectors"] = {"signature": meta.get("chunk_signature"), "vectors": vectors}
    return vectors, {"active": True, "model": meta.get("model"), "dim": dim}


def reset_cache() -> None:
    """Drop the in-process caches (tests, and after an explicit reindex)."""
    with _LOCK:
        _CACHE.clear()
