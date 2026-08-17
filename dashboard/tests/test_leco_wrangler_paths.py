"""Wrangler config discovery/parsing across TOML, JSON and JSONC."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from leco_wrangler_paths import (  # noqa: E402
    WRANGLER_PATHS,
    enumerate_wrangler_workers,
    is_wrangler_config_name,
    is_wrangler_pages_config,
    list_wrangler_config_files,
    list_wrangler_pages_config_files,
    loads_jsonc,
    pick_primary_wrangler_config,
    read_pages_build_output_dir,
    read_wrangler_config,
    resolve_pages_asset_dir,
    runtime_id_from_wrangler_relpath,
    strip_json_trailing_commas,
    strip_jsonc_comments,
    wrangler_config_format,
)

REPO_SAMPLE_MONOREPO = ROOT / "hosting" / "samples" / "sample-cf-multi-wrangler-monorepo"
REPO_SAMPLE_SINGLE = ROOT / "hosting" / "samples" / "sample-wrangler-local-cf"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# JSONC stripping
# ---------------------------------------------------------------------------


def test_strip_line_comment_but_not_url_inside_string():
    src = '{"a": "https://example.com/x", // trailing note\n "b": 1}'
    assert json.loads(strip_jsonc_comments(src)) == {"a": "https://example.com/x", "b": 1}


def test_double_slash_anywhere_inside_a_string_survives():
    src = '{"a": "x // y", "b": "/* not a comment */", "c": "//"}'
    assert loads_jsonc(src) == {"a": "x // y", "b": "/* not a comment */", "c": "//"}


def test_escaped_quote_does_not_end_the_string():
    src = r'{"a": "he said \"// hi\"", "b": 2}'
    assert loads_jsonc(src) == {"a": 'he said "// hi"', "b": 2}


def test_escaped_backslash_before_closing_quote():
    # The string ends at the quote after the escaped backslash; the // that follows IS a comment.
    src = r'{"a": "back\\", "b": 3} // done'
    assert loads_jsonc(src) == {"a": "back\\", "b": 3}


def test_block_comment_spanning_lines_and_line_numbers_preserved():
    src = '{\n/* one\n   two\n   three */\n"a": 1\n}'
    stripped = strip_jsonc_comments(src)
    assert stripped.count("\n") == src.count("\n")
    assert json.loads(stripped) == {"a": 1}


def test_block_comment_inline_between_tokens():
    assert loads_jsonc('{"a": /* mid */ 1}') == {"a": 1}


def test_unterminated_block_comment_does_not_hang_or_raise_in_stripper():
    assert strip_jsonc_comments('{"a": 1} /* never closed') .strip() == '{"a": 1}'


def test_trailing_comma_in_object_and_array():
    src = '{"a": [1, 2, 3,], "b": {"c": 1,},}'
    assert loads_jsonc(src) == {"a": [1, 2, 3], "b": {"c": 1}}


def test_trailing_comma_after_a_comment():
    src = '{\n"a": 1, // note\n}'
    assert loads_jsonc(src) == {"a": 1}


def test_comma_inside_string_before_brace_is_kept():
    src = '{"a": "x,]", "b": "y,}"}'
    assert strip_json_trailing_commas(src) == src
    assert loads_jsonc(src) == {"a": "x,]", "b": "y,}"}


def test_plain_json_with_no_comments_round_trips_unchanged():
    src = json.dumps({"name": "w", "vars": {"A": "1"}, "list": [1, 2]}, indent=2)
    assert strip_jsonc_comments(src) == src
    assert strip_json_trailing_commas(src) == src
    assert loads_jsonc(src) == json.loads(src)


def test_loads_jsonc_raises_on_genuinely_broken_json():
    with pytest.raises(json.JSONDecodeError):
        loads_jsonc('{"a": ')


# ---------------------------------------------------------------------------
# Name classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("wrangler.toml", True),
        ("wrangler.jsonc", True),
        ("wrangler.json", True),
        ("wrangler.api.toml", True),
        ("wrangler.api.jsonc", True),
        ("wrangler.pages.json", True),
        ("wrangler-config.test.ts", False),
        ("wrangler.yaml", False),
        ("package.json", False),
    ],
)
def test_is_wrangler_config_name(name, expected):
    assert is_wrangler_config_name(name) is expected


def test_wrangler_config_format():
    assert wrangler_config_format("wrangler.api.jsonc") == "jsonc"
    assert wrangler_config_format("wrangler.toml") == "toml"
    assert wrangler_config_format("wrangler.json") == "json"
    assert wrangler_config_format("nope.yaml") == "unknown"


def test_pages_configs_identified_in_all_formats():
    for name in ("wrangler.pages.toml", "wrangler.pages.jsonc", "wrangler.pages.json"):
        assert is_wrangler_pages_config(name)
    assert not is_wrangler_pages_config("wrangler.jsonc")


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("wrangler.toml", "worker"),
        ("infra/wrangler.api.toml", "api"),
        ("infra/wrangler.onboarding-email.toml", "onboarding-email"),
        ("infra/wrangler.onboarding_email.toml", "onboarding-email"),
        ("wrangler.jsonc", "worker"),
        ("workers/core-router/wrangler.jsonc", "worker"),
        ("infra/wrangler.api.jsonc", "api"),
        ("infra/wrangler.api.json", "api"),
    ],
)
def test_runtime_id_from_wrangler_relpath(rel, expected):
    assert runtime_id_from_wrangler_relpath(Path(rel)) == expected


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovers_jsonc_monorepo(tmp_path):
    for wid in ("core-router", "svc-auth", "mod-cache"):
        _write(tmp_path / "workers" / wid / "wrangler.jsonc", '{"name": "%s"}' % wid)
    # Directories without a config must not appear.
    (tmp_path / "workers" / "svc-empty").mkdir(parents=True)
    found = [p.as_posix() for p in list_wrangler_config_files(tmp_path)]
    assert found == [
        "workers/core-router/wrangler.jsonc",
        "workers/mod-cache/wrangler.jsonc",
        "workers/svc-auth/wrangler.jsonc",
    ]


def test_root_config_outranks_nested_one(tmp_path):
    _write(tmp_path / "wrangler.toml", 'name = "root"\n')
    _write(tmp_path / "workers" / "a" / "wrangler.toml", 'name = "a"\n')
    assert pick_primary_wrangler_config(tmp_path) == Path("wrangler.toml")


def test_toml_preferred_over_jsonc_over_json_for_the_same_slot(tmp_path):
    _write(tmp_path / "wrangler.json", '{"name": "j"}')
    _write(tmp_path / "wrangler.jsonc", '{"name": "jc"}')
    _write(tmp_path / "wrangler.toml", 'name = "t"\n')
    assert [p.as_posix() for p in list_wrangler_config_files(tmp_path)] == [
        "wrangler.toml",
        "wrangler.jsonc",
        "wrangler.json",
    ]


def test_ordering_is_stable_across_runs(tmp_path):
    _write(tmp_path / "wrangler.jsonc", '{"name": "a"}')
    _write(tmp_path / "wrangler.json", '{"name": "b"}')
    _write(tmp_path / "infra" / "wrangler.api.jsonc", '{"name": "api"}')
    first = list_wrangler_config_files(tmp_path)
    for _ in range(5):
        assert list_wrangler_config_files(tmp_path) == first


def test_api_config_still_ranks_first(tmp_path):
    _write(tmp_path / "infra" / "wrangler.api.jsonc", '{"name": "api"}')
    _write(tmp_path / "infra" / "wrangler.onboarding.jsonc", '{"name": "onb"}')
    assert pick_primary_wrangler_config(tmp_path) == Path("infra/wrangler.api.jsonc")


def test_pages_configs_excluded_from_worker_list_and_sorted_separately(tmp_path):
    _write(tmp_path / "wrangler.jsonc", '{"name": "w"}')
    _write(tmp_path / "wrangler.pages.jsonc", '{"pages_build_output_dir": "dist"}')
    workers = [p.as_posix() for p in list_wrangler_config_files(tmp_path)]
    pages = [p.as_posix() for p in list_wrangler_pages_config_files(tmp_path)]
    assert workers == ["wrangler.jsonc"]
    assert pages == ["wrangler.pages.jsonc"]


def test_prune_dirs_and_max_depth_still_apply(tmp_path):
    _write(tmp_path / "node_modules" / "pkg" / "wrangler.jsonc", "{}")
    _write(tmp_path / "dist" / "wrangler.jsonc", "{}")
    _write(tmp_path / "a/b/c/d/e/f/g" / "wrangler.jsonc", "{}")
    _write(tmp_path / "a" / "wrangler.jsonc", '{"name": "a"}')
    found = [p.as_posix() for p in list_wrangler_config_files(tmp_path)]
    assert found == ["a/wrangler.jsonc"]
    assert list_wrangler_config_files(tmp_path, max_depth=8)


def test_wrangler_paths_keeps_toml_entries_first():
    assert WRANGLER_PATHS[:2] == (Path("wrangler.toml"), Path("cloudflare") / "wrangler.toml")
    assert Path("wrangler.jsonc") in WRANGLER_PATHS


# ---------------------------------------------------------------------------
# Repo fixtures: TOML behaviour must not move
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REPO_SAMPLE_MONOREPO.is_dir(), reason="sample fixture not present")
def test_repo_toml_monorepo_ordering_unchanged():
    assert [p.as_posix() for p in list_wrangler_config_files(REPO_SAMPLE_MONOREPO)] == [
        "infra/wrangler.api.toml",
        "infra/wrangler.onboarding-email.toml",
        "infra/wrangler.onboarding-mobile.toml",
        "infra/wrangler.onboarding.toml",
    ]
    assert [p.as_posix() for p in list_wrangler_pages_config_files(REPO_SAMPLE_MONOREPO)] == [
        "infra/wrangler.pages.toml"
    ]


@pytest.mark.skipif(not REPO_SAMPLE_SINGLE.is_dir(), reason="sample fixture not present")
def test_repo_single_toml_app_unchanged():
    assert pick_primary_wrangler_config(REPO_SAMPLE_SINGLE) == Path("wrangler.toml")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_TOML_CONFIG = """
name = "api-worker"
main = "src/index.ts"
compatibility_date = "2026-08-01"
compatibility_flags = ["nodejs_compat"]

[vars]
ENVIRONMENT = "development"

[[kv_namespaces]]
binding = "KV_HOT"
id = "abc"

[[r2_buckets]]
binding = "R2_MEDIA"
bucket_name = "media"

[[d1_databases]]
binding = "D1_CONTROL"
database_name = "control"

[[services]]
binding = "AUTH"
service = "svc-auth"

[[durable_objects.bindings]]
name = "ROOM"
class_name = "Room"

[[queues.producers]]
binding = "Q_USAGE"
queue = "usage"
"""

_JSONC_CONFIG = """
{
  // the same worker, modern format
  "name": "api-worker",
  "main": "src/index.ts",
  "compatibility_date": "2026-08-01",
  "compatibility_flags": ["nodejs_compat"],
  "vars": { "ENVIRONMENT": "development" },
  "kv_namespaces": [{ "binding": "KV_HOT", "id": "abc" }],
  "r2_buckets": [{ "binding": "R2_MEDIA", "bucket_name": "media" }],
  "d1_databases": [{ "binding": "D1_CONTROL", "database_name": "control" }],
  "services": [{ "binding": "AUTH", "service": "svc-auth" }],
  "durable_objects": { "bindings": [{ "name": "ROOM", "class_name": "Room" }] },
  "queues": { "producers": [{ "binding": "Q_USAGE", "queue": "usage" }] },
}
"""


def _binding_names(rows):
    return [r.get("binding") or r.get("name") for r in rows]


@pytest.mark.parametrize(
    "filename,text",
    [
        ("wrangler.toml", _TOML_CONFIG),
        ("wrangler.jsonc", _JSONC_CONFIG),
        ("wrangler.json", json.dumps(json.loads(strip_json_trailing_commas(
            strip_jsonc_comments(_JSONC_CONFIG))))),
    ],
)
def test_parser_normalizes_all_three_formats_identically(tmp_path, filename, text):
    cfg = read_wrangler_config(_write(tmp_path / filename, text))
    assert cfg["error"] is None
    assert cfg["format"] == filename.rsplit(".", 1)[1]
    assert cfg["path"].endswith(filename)
    assert cfg["name"] == "api-worker"
    assert cfg["main"] == "src/index.ts"
    assert cfg["compatibility_date"] == "2026-08-01"
    assert cfg["compatibility_flags"] == ["nodejs_compat"]
    assert cfg["vars"] == {"ENVIRONMENT": "development"}
    assert _binding_names(cfg["kv_namespaces"]) == ["KV_HOT"]
    assert _binding_names(cfg["r2_buckets"]) == ["R2_MEDIA"]
    assert _binding_names(cfg["d1_databases"]) == ["D1_CONTROL"]
    assert _binding_names(cfg["services"]) == ["AUTH"]
    assert _binding_names(cfg["durable_objects"]) == ["ROOM"]
    assert _binding_names(cfg["queues"]["producers"]) == ["Q_USAGE"]
    assert cfg["queues"]["consumers"] == []


def test_parser_never_raises_on_broken_jsonc(tmp_path):
    cfg = read_wrangler_config(_write(tmp_path / "wrangler.jsonc", '{"name": "x", '))
    assert cfg["name"] is None
    assert cfg["kv_namespaces"] == []
    assert "parse error" in (cfg["error"] or "")


def test_parser_never_raises_on_broken_toml(tmp_path):
    cfg = read_wrangler_config(_write(tmp_path / "wrangler.toml", 'name = "x"\n[[[bad\n'))
    assert "parse error" in (cfg["error"] or "")
    # Regex fallback still recovers the worker name.
    assert cfg["name"] == "x"


def test_parser_never_raises_on_missing_file(tmp_path):
    cfg = read_wrangler_config(tmp_path / "wrangler.jsonc")
    assert cfg["error"] and cfg["name"] is None


def test_parser_tolerates_a_non_object_document(tmp_path):
    cfg = read_wrangler_config(_write(tmp_path / "wrangler.json", "[1, 2]"))
    assert cfg["error"] is None
    assert cfg["name"] is None
    assert cfg["raw"] == {}


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def test_enumerate_reports_only_dirs_that_actually_have_a_config(tmp_path):
    for wid in ("core-router", "svc-auth"):
        _write(tmp_path / "workers" / wid / "wrangler.jsonc", '{"name": "%s"}' % wid)
    (tmp_path / "workers" / "svc-empty").mkdir(parents=True)
    (tmp_path / "workers" / "mod-empty").mkdir(parents=True)

    entries = enumerate_wrangler_workers(tmp_path)
    assert [e["config"] for e in entries] == [
        "workers/core-router/wrangler.jsonc",
        "workers/svc-auth/wrangler.jsonc",
    ]
    assert [e["dir"] for e in entries] == ["workers/core-router", "workers/svc-auth"]
    assert [e["name"] for e in entries] == ["core-router", "svc-auth"]
    assert [e["format"] for e in entries] == ["jsonc", "jsonc"]
    assert [e["runtime_id"] for e in entries] == ["core-router", "svc-auth"]


def test_enumerate_survives_one_broken_config(tmp_path):
    _write(tmp_path / "workers" / "a" / "wrangler.jsonc", '{"name": "a"}')
    _write(tmp_path / "workers" / "b" / "wrangler.jsonc", "{ broken")
    _write(tmp_path / "workers" / "c" / "wrangler.jsonc", '{"name": "c"}')
    entries = enumerate_wrangler_workers(tmp_path)
    assert len(entries) == 3
    broken = [e for e in entries if e["config"].endswith("b/wrangler.jsonc")][0]
    assert broken["error"] and broken["name"] is None
    # Falls back to the directory name so the caller still gets a usable runtime id.
    assert broken["runtime_id"] == "b"
    assert [e["runtime_id"] for e in entries] == ["a", "b", "c"]


def test_enumerate_keeps_legacy_ids_for_toml_layouts(tmp_path):
    _write(tmp_path / "wrangler.toml", 'name = "single"\n')
    assert [e["runtime_id"] for e in enumerate_wrangler_workers(tmp_path)] == ["worker"]

    other = tmp_path / "mono"
    _write(other / "infra" / "wrangler.api.toml", 'name = "api-worker"\n')
    _write(other / "infra" / "wrangler.onboarding.toml", 'name = "onb"\n')
    assert [e["runtime_id"] for e in enumerate_wrangler_workers(other)] == ["api", "onboarding"]


def test_enumerate_deduplicates_colliding_runtime_ids(tmp_path):
    _write(tmp_path / "a" / "wrangler.jsonc", '{"name": "dup"}')
    _write(tmp_path / "b" / "wrangler.jsonc", '{"name": "dup"}')
    assert [e["runtime_id"] for e in enumerate_wrangler_workers(tmp_path)] == ["dup", "dup-2"]


# ---------------------------------------------------------------------------
# Pages output dir
# ---------------------------------------------------------------------------


def test_pages_build_output_dir_toml_and_json(tmp_path):
    t = _write(tmp_path / "wrangler.pages.toml", 'pages_build_output_dir = "dist"\n')
    j = _write(tmp_path / "wrangler.pages.jsonc", '{\n// out\n"pages_build_output_dir": "dist",\n}')
    assert read_pages_build_output_dir(t) == "dist"
    assert read_pages_build_output_dir(j) == "dist"


def test_pages_output_dir_falls_back_to_regex_on_broken_toml(tmp_path):
    p = _write(tmp_path / "wrangler.pages.toml", 'pages_build_output_dir = "dist"\n[[[bad\n')
    assert read_pages_build_output_dir(p) == "dist"


def test_resolve_pages_asset_dir(tmp_path):
    _write(tmp_path / "wrangler.pages.jsonc", '{"pages_build_output_dir": "dist"}')
    (tmp_path / "dist").mkdir()
    assert resolve_pages_asset_dir(tmp_path, Path("wrangler.pages.jsonc")) == (
        tmp_path / "dist"
    ).resolve()
