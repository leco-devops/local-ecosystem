"""RAG over LEco DevOps' own knowledge: corpus, retrieval, routing, safety.

The load-bearing invariant here is the mirror of ``test_ai_config``: a
credential must never reach a model. ``test_planted_secret_*`` plant fake
secrets in a doc and in live state and assert they are absent from the
assembled prompt — the only text that is ever sent to a provider.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

FAKE_KEY = "sk-ant-api03-ZZTOPSECRETZZ0123456789abcdefghijKLMNOP"
FAKE_PASSWORD = "hunter2-do-not-leak-9Q7x"


# ---------------------------------------------------------------------------
# Fixtures — a throwaway repo so nothing depends on the real docs tree
# ---------------------------------------------------------------------------

def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def fake_repo(tmp_path):
    """A miniature repo with docs, a skill, and a credential file."""
    _write(tmp_path, "README.md", "# LEco DevOps\n\nA local platform on `*.lh` hostnames.\n")
    _write(
        tmp_path,
        "docs/TRAEFIK.md",
        "# Traefik routing\n\n"
        "## 502 Bad Gateway\n\n"
        "A hosted app returns 502 when its container is not attached to `lh-network`.\n"
        "Attach it with `docker network connect lh-network <container>` and restart Traefik.\n"
        "This is by far the most common cause of a bad gateway on this platform.\n\n"
        "## TLS certificates\n\n"
        "Run `certs/generate-certs.sh` to mint a certificate covering every `*.lh` hostname.\n"
        "Traefik mounts the certs directory on start, so restart it afterwards.\n",
    )
    _write(
        tmp_path,
        "docs/help/onboarding.md",
        "# Onboarding an app\n\n"
        "## Workspace parent\n\n"
        "Use a `wsp:` path to onboard a repository that lives beside this one.\n"
        "The registration wizard materializes it under `hosting/app-available/`.\n"
        "Nothing is copied; a symlink points at the source tree.\n",
    )
    _write(
        tmp_path,
        "tools/claude-plugin/skills/logs/SKILL.md",
        "# Reading logs\n\nUse `leco_app_logs` with a slug to tail a hosted app.\n",
    )
    # Credential files that must never be indexed.
    _write(tmp_path, "config/ai-providers.yaml", f"providers:\n  anthropic:\n    api_key: {FAKE_KEY}\n")
    _write(tmp_path, "config/ui-credentials.yaml", f"n8n:\n  password: {FAKE_PASSWORD}\n")
    _write(tmp_path, ".env", f"DASHBOARD_CONTROL_TOKEN={FAKE_KEY}\n")
    return tmp_path


@pytest.fixture()
def corpus(fake_repo, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PROJECT_ROOT", str(fake_repo))
    import ai_corpus as mod

    importlib.reload(mod)
    mod.reset_cache()
    yield mod
    importlib.reload(mod)


@pytest.fixture()
def rag(corpus):
    import ai_rag as mod

    importlib.reload(mod)
    mod._POSTINGS.clear()
    yield mod
    mod._POSTINGS.clear()


# ---------------------------------------------------------------------------
# Corpus discovery and confinement
# ---------------------------------------------------------------------------

def test_corpus_collects_docs_help_skills_and_root_guides(corpus):
    rels = {rel for _kind, _path, rel in corpus.iter_source_files()}
    assert "README.md" in rels
    assert "docs/TRAEFIK.md" in rels
    assert "docs/help/onboarding.md" in rels
    assert "tools/claude-plugin/skills/logs/SKILL.md" in rels


def test_corpus_refuses_credential_files(corpus):
    rels = {rel for _kind, _path, rel in corpus.iter_source_files()}
    assert not any(r.startswith("config/") for r in rels)
    assert ".env" not in rels
    for bad in ("config/ai-providers.yaml", "config/ui-credentials.yaml", ".env", ".dev.vars",
                "certs/wildcard.lh-key.pem", "docs/../config/ai-providers.yaml"):
        assert corpus.is_excluded(bad), bad


def test_corpus_is_confined_to_the_repo_root(corpus, tmp_path):
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("# outside\n", encoding="utf-8")
    assert corpus.safe_relative_path(outside) is None
    assert corpus.safe_relative_path(Path(corpus.repo_root()) / ".." / "outside-secret.md") is None
    assert corpus.safe_relative_path(Path(corpus.repo_root()) / "README.md") == "README.md"


def test_symlink_escaping_the_repo_is_rejected(corpus, tmp_path):
    target = tmp_path.parent / "escape-target.md"
    target.write_text("# escape\n", encoding="utf-8")
    link = Path(corpus.repo_root()) / "docs" / "escape.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert corpus.safe_relative_path(link) is None
    assert "docs/escape.md" not in {rel for _k, _p, rel in corpus.iter_source_files()}


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def test_chunks_carry_source_path_and_heading_trail(corpus):
    index = corpus.build_index(persist=False)
    traefik = [c for c in index["chunks"] if c["path"] == "docs/TRAEFIK.md"]
    assert traefik
    for chunk in traefik:
        assert chunk["path"] == "docs/TRAEFIK.md"
        assert chunk["trail"][0] == "Traefik routing"
        assert chunk["heading"]
    assert any("502 Bad Gateway" in c["trail"] + c["headings"] for c in traefik)
    assert any("TLS certificates" in c["trail"] + c["headings"] for c in traefik)


def test_headings_inside_code_fences_do_not_split_sections(corpus):
    text = "# Doc\n\n## Real heading\n\n```sh\n# not a heading\necho hi\n```\n\nBody.\n"
    chunks = corpus.chunk_markdown(text, rel_path="docs/x.md", kind="doc")
    titles = {t for c in chunks for t in c["trail"] + c["headings"]}
    assert "not a heading" not in titles
    assert "Real heading" in titles


def test_oversized_table_section_is_split_without_stray_remainders(corpus):
    table = "\n".join(f"| row-{i} | value-{i} | note about row {i} |" for i in range(400))
    chunks = corpus.chunk_markdown(f"# Doc\n\n## Big table\n\n{table}\n", rel_path="docs/t.md", kind="doc")
    assert len(chunks) > 1
    assert all(len(c["text"]) <= corpus.MAX_CHUNK_CHARS for c in chunks)
    # A three-character leftover chunk is retrieval poison; there must be none.
    assert all(len(c["text"]) >= corpus.ABSORB_FLOOR_CHARS for c in chunks)
    assert any("part 1/" in " ".join(c["trail"]) for c in chunks)


def test_a_substantial_section_never_swallows_its_sibling(corpus):
    """A section with real content must not absorb the next top-level topic."""
    text = (
        "# Doc\n\n" + ("Intro prose. " * 60) + "\n\n"
        "## Alpha\n\n" + ("Alpha content. " * 60) + "\n\n"
        "## Beta\n\n" + ("Beta content. " * 60) + "\n"
    )
    chunks = corpus.chunk_markdown(text, rel_path="docs/s.md", kind="doc")
    assert [c for c in chunks if "Alpha" in c["trail"]]
    assert [c for c in chunks if "Beta" in c["trail"]]


def test_a_stray_heading_absorbs_forward_but_discloses_what_it_covers(corpus):
    """A near-empty section may merge forward — the citation must say so."""
    text = "# Doc\n\n## Alpha\n\nTiny.\n\n## Beta\n\n" + ("Beta content. " * 40) + "\n"
    chunks = corpus.chunk_markdown(text, rel_path="docs/s.md", kind="doc")
    merged = [c for c in chunks if "Beta" in c["headings"]]
    assert merged, "Beta's content must remain findable by heading"
    assert all(len(c["text"]) >= corpus.ABSORB_FLOOR_CHARS for c in chunks)


# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------

def test_scrub_text_redacts_assignments_and_token_shapes(corpus):
    out = corpus.scrub_text(
        f"api_key: {FAKE_KEY}\n"
        f"DB_PASSWORD={FAKE_PASSWORD}\n"
        f'{{"client_secret": "{FAKE_KEY}"}}\n'
        f"A bare token {FAKE_KEY} in prose.\n"
    )
    assert FAKE_KEY not in out
    assert FAKE_PASSWORD not in out
    assert out.count("[REDACTED]") >= 4


def test_scrub_text_leaves_documentation_placeholders_alone(corpus):
    text = "api_key: <your-key-here>\npassword: changeme\nkeys: [routers, services]\ntoken: ${CONTROL_TOKEN}\n"
    out = corpus.scrub_text(text)
    assert "<your-key-here>" in out
    assert "changeme" in out
    assert "[routers, services]" in out          # a structural "keys:" list survives
    assert "${CONTROL_TOKEN}" in out


def test_scrub_structure_redacts_by_key_name(corpus):
    payload = {"provider": "anthropic", "api_key": FAKE_KEY,
               "nested": [{"password": FAKE_PASSWORD, "host": "n8n.lh"}]}
    out = corpus.scrub_structure(payload)
    assert out["api_key"] == "[REDACTED]"
    assert out["nested"][0]["password"] == "[REDACTED]"
    assert out["nested"][0]["host"] == "n8n.lh"
    assert FAKE_KEY not in json.dumps(out)


def test_planted_secret_in_a_doc_never_reaches_a_prompt(corpus, rag):
    """The end-to-end guarantee: a secret in an indexed doc cannot be prompted."""
    _write(
        Path(corpus.repo_root()),
        "docs/LEAKY.md",
        "# Provider setup\n\n## Anthropic\n\n"
        f"Set `api_key: {FAKE_KEY}` in config and a `password: {FAKE_PASSWORD}` for the UI.\n"
        f"You can also export ANTHROPIC_API_KEY={FAKE_KEY} before starting.\n"
        "Repeat this until the provider connects, then restart the dashboard service.\n",
    )
    corpus.reset_cache()
    rag._POSTINGS.clear()

    index = corpus.build_index(persist=False)
    all_text = json.dumps(index["chunks"])
    assert FAKE_KEY not in all_text
    assert FAKE_PASSWORD not in all_text

    passages, _ = rag.retrieve("anthropic provider api key setup", top_k=6,
                               use_embeddings=False, index=index)
    assert passages, "the leaky doc should be retrievable — scrubbed, not excluded"
    ctx = rag.assemble_prompt("what is the anthropic api key", passages, [])
    assert FAKE_KEY not in ctx.user_prompt
    assert FAKE_PASSWORD not in ctx.user_prompt
    assert "[REDACTED]" in ctx.user_prompt


def test_planted_secret_in_live_state_never_reaches_a_prompt(corpus, rag):
    """Live state comes from Docker, not from the index, so it is scrubbed again."""
    plan = rag.LivePlan(sources=["ai_config"], mode="explicit",
                        reasons={"ai_config": "test"})

    def leaky(_plan):
        return rag._as_json_block({
            "provider": "anthropic",
            "api_key": FAKE_KEY,
            "env": [f"DASHBOARD_CONTROL_TOKEN={FAKE_KEY}"],
            "note": f"raw token {FAKE_KEY} pasted into a log line",
        })[0], {}

    rag.COLLECTORS["ai_config"] = leaky
    blobs = rag.collect_live(plan)
    assert blobs and blobs[0].ok
    ctx = rag.assemble_prompt("which provider am I using", [], blobs)
    assert FAKE_KEY not in ctx.user_prompt
    assert "[REDACTED]" in ctx.user_prompt


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def test_bm25_ranks_the_relevant_section_first(corpus, rag):
    index = corpus.build_index(persist=False)
    top, meta = rag.retrieve("why does my app return 502", top_k=3, use_embeddings=False, index=index)
    assert top[0].path == "docs/TRAEFIK.md"
    assert "502" in top[0].citation()
    assert meta["method"] == "bm25"
    assert meta["chunks_searched"] == index["chunk_count"]


def test_heading_and_path_bonus_beats_a_body_only_mention(corpus, rag):
    index = corpus.build_index(persist=False)
    top, _ = rag.retrieve("tls certificates", top_k=3, use_embeddings=False, index=index)
    assert "TLS certificates" in top[0].citation()


def test_plural_query_matches_singular_heading(corpus, rag):
    index = corpus.build_index(persist=False)
    top, _ = rag.retrieve("how do I mint certificates", top_k=3, use_embeddings=False, index=index)
    assert any("TLS certificates" in p.citation() for p in top)


def test_retrieval_returns_nothing_gracefully_for_gibberish(corpus, rag):
    index = corpus.build_index(persist=False)
    top, meta = rag.retrieve("zzzqqxx nonexistentterm", top_k=5, use_embeddings=False, index=index)
    assert top == []
    assert meta["chunks_matched"] == 0


# ---------------------------------------------------------------------------
# Index persistence and staleness
# ---------------------------------------------------------------------------

def test_index_persists_under_generated_and_reloads_without_rebuild(corpus):
    corpus.build_index()
    assert corpus.INDEX_FILE.is_file()
    assert corpus.INDEX_FILE.parent.name == "generated"
    corpus.reset_cache()
    index, rebuilt = corpus.load_index()
    assert rebuilt is False
    assert index["chunk_count"] > 0


def test_editing_a_doc_rebuilds_the_index_without_a_restart(corpus):
    corpus.build_index()
    corpus.reset_cache()
    _index, rebuilt = corpus.load_index()
    assert rebuilt is False

    doc = Path(corpus.repo_root()) / "docs" / "TRAEFIK.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\n## Brand new section\n\nFresh content here.\n",
                   encoding="utf-8")
    import os
    os.utime(doc, (doc.stat().st_atime, doc.stat().st_mtime + 10))

    index, rebuilt = corpus.load_index()
    assert rebuilt is True
    assert any("Brand new section" in (c["trail"] + c["headings"]) for c in index["chunks"])


# ---------------------------------------------------------------------------
# Live-source routing
# ---------------------------------------------------------------------------

def test_docs_only_question_plans_no_app_or_log_sources(rag, monkeypatch):
    monkeypatch.setattr(rag, "known_app_slugs", lambda: ["myapp"])
    plan = rag.plan_live_sources("how do I onboard an app from the workspace parent")
    assert plan.app_slug == ""
    assert "app_logs" not in plan.sources
    assert "app_snapshot" not in plan.sources
    assert "service_logs" not in plan.sources


def test_app_failure_question_plans_snapshot_routes_and_logs(rag, monkeypatch):
    monkeypatch.setattr(rag, "known_app_slugs", lambda: ["myapp", "other"])
    plan = rag.plan_live_sources("why is myapp 502ing")
    assert plan.app_slug == "myapp"
    assert {"app_snapshot", "app_logs", "traefik_routes"} <= set(plan.sources)
    # Every planned source must carry a human-readable justification.
    assert all(plan.reasons.get(s) for s in plan.sources)


def test_named_stack_service_routes_to_service_logs(rag, monkeypatch):
    monkeypatch.setattr(rag, "known_app_slugs", lambda: [])
    plan = rag.plan_live_sources("show me traefik errors in the logs")
    assert plan.service_container == "traefik"
    assert "service_logs" in plan.sources


def test_live_can_be_disabled_or_pinned_by_the_caller(rag, monkeypatch):
    monkeypatch.setattr(rag, "known_app_slugs", lambda: [])
    assert rag.plan_live_sources("is everything running", enabled=False).sources == []
    plan = rag.plan_live_sources("anything", requested=["stack_status", "bogus_source"])
    assert plan.sources == ["stack_status"]


def test_a_failing_collector_degrades_to_a_note(rag):
    def boom(_plan):
        raise RuntimeError("docker socket unavailable")

    rag.COLLECTORS["stack_status"] = boom
    blobs = rag.collect_live(rag.LivePlan(sources=["stack_status"], mode="explicit"))
    assert blobs[0].ok is False
    assert "docker socket" in blobs[0].error
    ctx = rag.assemble_prompt("q", [], blobs)
    assert "unavailable" in ctx.user_prompt


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def test_system_prompt_forbids_ungrounded_answers(rag):
    sp = rag.SYSTEM_PROMPT
    assert "ONLY from the CONTEXT" in sp
    assert "does not cover the question" in sp
    assert "read-only" in sp.lower()


def test_context_is_capped_and_truncation_is_reported(rag):
    passages = [
        rag.Passage(chunk_id=i, path=f"docs/d{i}.md", heading=f"H{i}", trail=["Doc", f"H{i}"],
                    kind="doc", text="x" * 1500, score=10.0 - i)
        for i in range(8)
    ]
    ctx = rag.assemble_prompt("q", passages, [], max_context_chars=4000)
    assert ctx.used_chars <= 4000
    assert ctx.truncated is True
    assert ctx.omitted_passages > 0
    assert "truncated" in ctx.user_prompt
    assert len(ctx.passages) < len(passages)


def test_live_state_cannot_crowd_out_documentation(corpus, rag):
    index = corpus.build_index(persist=False)
    passages, _ = rag.retrieve("502", top_k=4, use_embeddings=False, index=index)
    noisy = [rag.LiveBlob(source="app_logs", label="logs", why="test", text="LOGLINE " * 4000)]
    ctx = rag.assemble_prompt("q", passages, noisy, max_context_chars=8000)
    assert ctx.passages, "documentation must survive a chatty log"
    assert "## DOCUMENTATION" in ctx.user_prompt


def test_prompt_numbers_sources_so_the_model_can_cite_them(corpus, rag):
    index = corpus.build_index(persist=False)
    passages, _ = rag.retrieve("502 lh-network", top_k=3, use_embeddings=False, index=index)
    ctx = rag.assemble_prompt("why 502", passages, [])
    for i, p in enumerate(ctx.passages, start=1):
        assert f"### [{i}] {p.path}" in ctx.user_prompt


# ---------------------------------------------------------------------------
# Answer path
# ---------------------------------------------------------------------------

def test_ask_without_a_provider_still_returns_context(corpus, rag, monkeypatch):
    corpus.build_index()
    monkeypatch.setattr(rag, "_build_provider",
                        lambda model="": (None, {"provider": "none", "providers": {}}, "No AI provider is selected."))
    monkeypatch.setattr(rag, "known_app_slugs", lambda: [])
    out = rag.ask("why does my app return 502", live_enabled=False, use_embeddings=False)
    assert out["ok"] is True
    assert out["answered_by"] == "retrieval-only"
    assert out["sources"]
    assert out["sources"][0]["path"] == "docs/TRAEFIK.md"
    assert "lh-network" in out["answer"]
    assert out["provider"]["configured"] is False
    assert "nothing left this machine" in out["provider"]["note"]


class _FakeProvider:
    """Minimal stand-in for ai_provider.AIProvider (JSON-mode, like the real ones)."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.seen: dict[str, str] = {}

    def analyze(self, system_prompt, user_prompt, *, model=None, stream=False):
        self.seen = {"system": system_prompt, "user": user_prompt}
        if stream:
            raw = json.dumps(self.payload)
            return iter(
                [type("C", (), {"type": "token", "text": raw[i:i + 17], "data": None})()
                 for i in range(0, len(raw), 17)]
                + [type("C", (), {"type": "done", "text": raw, "data": self.payload})()]
            )
        return type("R", (), {"ok": True, "data": self.payload, "raw_text": json.dumps(self.payload),
                              "error": "", "model": "fake-1", "provider": "fake"})()


def test_ask_with_a_provider_returns_answer_and_names_the_destination(corpus, rag, monkeypatch):
    corpus.build_index()
    fake = _FakeProvider({"answer": "Attach the container to `lh-network` [1].",
                          "context_sufficient": True, "sources_used": [1], "missing": ""})
    cfg = {"provider": "anthropic", "default_model": "claude-x",
           "providers": {"anthropic": {"api_key": FAKE_KEY, "default_model": "claude-x"}}}
    monkeypatch.setattr(rag, "_build_provider", lambda model="": (fake, cfg, ""))
    monkeypatch.setattr(rag, "known_app_slugs", lambda: [])

    out = rag.ask("why does my app return 502", live_enabled=False, use_embeddings=False)
    assert out["answered_by"] == "llm"
    assert out["answer"].startswith("Attach the container")
    assert out["context_sufficient"] is True
    assert out["provider"]["name"] == "anthropic"
    assert out["provider"]["locality"] == "cloud"
    assert "api.anthropic.com" in out["provider"]["destination"]
    # The stored key is never echoed back and never reaches the prompt.
    assert FAKE_KEY not in json.dumps(out)
    assert FAKE_KEY not in fake.seen["user"] + fake.seen["system"]


def test_stream_emits_sources_before_tokens_and_a_final_done(corpus, rag, monkeypatch):
    corpus.build_index()
    fake = _FakeProvider({"answer": "Line one.\nLine two with a \"quote\".",
                          "context_sufficient": False, "sources_used": [], "missing": "app logs"})
    monkeypatch.setattr(rag, "_build_provider",
                        lambda model="": (fake, {"provider": "ollama", "providers": {"ollama": {}}}, ""))
    monkeypatch.setattr(rag, "known_app_slugs", lambda: [])

    events = list(rag.ask_stream("why 502", live_enabled=False, use_embeddings=False))
    types = [e["type"] for e in events]
    assert types[0] == "status"
    assert types.index("sources") < types.index("token")
    assert types[-1] == "done"
    streamed = "".join(e["text"] for e in events if e["type"] == "token")
    assert streamed == 'Line one.\nLine two with a "quote".'
    done = events[-1]["data"]
    assert done["answered_by"] == "llm"
    assert done["context_sufficient"] is False
    assert done["missing"] == "app logs"
    assert done["provider"]["locality"] == "local"


def test_stream_emits_the_answer_even_when_the_provider_does_not_stream_prose(corpus, rag, monkeypatch):
    """A provider that returns the body in one piece must still reach the client."""
    corpus.build_index()

    class OneShot:
        def analyze(self, *a, **kw):
            body = json.dumps({"answer": "All at once.", "context_sufficient": True})
            return iter([type("C", (), {"type": "done", "text": body, "data": None})()])

    monkeypatch.setattr(rag, "_build_provider",
                        lambda model="": (OneShot(), {"provider": "ollama", "providers": {"ollama": {}}}, ""))
    monkeypatch.setattr(rag, "known_app_slugs", lambda: [])
    events = list(rag.ask_stream("why 502", live_enabled=False, use_embeddings=False))
    assert "".join(e["text"] for e in events if e["type"] == "token") == "All at once."
    assert events[-1]["data"]["answered_by"] == "llm"


def test_stream_error_with_no_schema_output_is_not_labelled_an_llm_answer(corpus, rag, monkeypatch):
    """A hybrid provider's local scaffolding must not be passed off as the answer."""
    corpus.build_index()

    class Broken:
        def analyze(self, *a, **kw):
            return iter([
                type("C", (), {"type": "token", "text": "local summary noise", "data": None})(),
                type("C", (), {"type": "error", "text": "Anthropic HTTP 404", "data": None})(),
            ])

    monkeypatch.setattr(rag, "_build_provider",
                        lambda model="": (Broken(), {"provider": "hybrid", "providers": {"hybrid": {}}}, ""))
    monkeypatch.setattr(rag, "known_app_slugs", lambda: [])
    events = list(rag.ask_stream("why 502", live_enabled=False, use_embeddings=False))
    done = events[-1]["data"]
    assert done["answered_by"] == "retrieval-only"
    assert done["provider_error"] == "Anthropic HTTP 404"
    assert "local summary noise" not in done["answer"]
    assert done["sources"], "the user still gets the retrieved passages"


def test_answer_extractor_decodes_escapes_incrementally(rag):
    ex = rag.AnswerExtractor()
    raw = '{"context_sufficient": true, "answer": "a\\nb \\"c\\" \\u00e9", "sources_used": [1]}'
    pieces = [ex.feed(raw[i:i + 5]) for i in range(0, len(raw), 5)]
    assert "".join(pieces) == 'a\nb "c" é'
    text, parsed = ex.final()
    assert text == 'a\nb "c" é'
    assert parsed["sources_used"] == [1]


def test_answer_extractor_falls_back_when_the_model_ignores_the_schema(rag):
    ex = rag.AnswerExtractor()
    ex.feed("Just some prose, no JSON at all.")
    text, parsed = ex.final()
    assert text == "Just some prose, no JSON at all."
    assert parsed == {}


def test_provider_failure_degrades_to_retrieval_not_an_error(corpus, rag, monkeypatch):
    corpus.build_index()

    class Boom:
        def analyze(self, *a, **kw):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(rag, "_build_provider",
                        lambda model="": (Boom(), {"provider": "ollama", "providers": {"ollama": {}}}, ""))
    monkeypatch.setattr(rag, "known_app_slugs", lambda: [])
    out = rag.ask("why 502", live_enabled=False, use_embeddings=False)
    assert out["ok"] is True
    assert out["answered_by"] == "retrieval-only"
    assert "connection refused" in out["provider_error"]


def test_ask_rejects_an_empty_question(rag):
    assert rag.ask("   ")["ok"] is False


# ---------------------------------------------------------------------------
# Status / reindex
# ---------------------------------------------------------------------------

def test_status_reports_chunks_freshness_embeddings_and_provider(corpus, rag, monkeypatch):
    corpus.build_index()
    monkeypatch.setattr(rag, "_build_provider",
                        lambda model="": (None, {"provider": "none", "providers": {}}, "not configured"))
    st = rag.status()
    assert st["index"]["chunk_count"] > 0
    assert st["index"]["stale"] is False
    assert st["index"]["built_at"]
    assert st["index"]["embeddings"]["active"] is False
    assert st["provider"]["configured"] is False
    assert any(s["source"] == "app_logs" for s in st["live_sources"])


def test_reindex_rebuilds_and_leaves_embeddings_alone_by_default(corpus, rag):
    out = rag.reindex()
    assert out["ok"] is True
    assert out["chunk_count"] > 0
    assert out["embeddings"]["changed"] is False
    assert out["embeddings"]["active"] is False


def test_reindex_with_embeddings_reports_failure_without_breaking_retrieval(corpus, rag, monkeypatch):
    monkeypatch.setattr(corpus, "embed_texts", lambda *a, **kw: ([], "ollama unreachable"))
    out = rag.reindex(embeddings=True)
    assert out["ok"] is True
    assert out["chunk_count"] > 0
    assert "ollama unreachable" in out["warning"]
    index, _ = corpus.load_index()
    passages, _ = rag.retrieve("502", top_k=3, use_embeddings=False, index=index)
    assert passages


def test_embeddings_roundtrip_when_ollama_answers(corpus, rag, monkeypatch):
    """Vectors persist, load back normalized, and fuse with the lexical score."""
    def fake_embed(texts, model="", timeout=120, deadline_seconds=0.0):
        # Deterministic 4-dim vector so the test needs no network.
        return [[float(len(t) % 7), 1.0, 0.5, float(len(t) % 3)] for t in texts], ""

    monkeypatch.setattr(corpus, "embed_texts", fake_embed)
    out = rag.reindex(embeddings=True)
    assert out["embeddings"]["ok"] is True
    assert out["embeddings"]["dim"] == 4

    index, _ = corpus.load_index()
    vectors, meta = corpus.load_vectors(index)
    assert meta["active"] is True
    assert len(vectors) == index["chunk_count"]
    assert abs(sum(x * x for x in vectors[0]) - 1.0) < 1e-5   # normalized at store time

    monkeypatch.setattr(rag.ai_corpus, "embed_texts", fake_embed)
    passages, rmeta = rag.retrieve("502 bad gateway", top_k=3, index=index)
    assert rmeta["method"] == "bm25+embeddings"
    assert passages

    assert corpus.drop_embeddings() is True
    assert corpus.embeddings_status()["active"] is False
