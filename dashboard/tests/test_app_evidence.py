"""Evidence collection, compose-merge confinement, and URL classification.

These are the facts an agent uses instead of guessing, so the tests care most about two
properties: a parsed value always names its source, and anything unparseable becomes an
explicit unknown rather than a plausible default.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

_DASH = Path(__file__).resolve().parents[1]
if str(_DASH) not in sys.path:
    sys.path.insert(0, str(_DASH))

import app_evidence  # noqa: E402
from app_evidence import (  # noqa: E402
    _confine,
    _load_yaml_tolerant,
    _merge_directives,
    _san_matches,
    attribute_ports,
    classify_url_result,
    collect_compose_evidence,
    collect_entry_points,
    collect_evidence,
    collect_overlay_evidence,
    parse_js_port_table,
    parse_port_entry,
    validate_compose_merge,
)


# --------------------------------------------------------------------- port pairs


def test_short_syntax_splits_published_from_target():
    assert parse_port_entry("18787:8787") == {
        "published": 18787,
        "target": 8787,
        "protocol": "tcp",
        "host_ip": None,
        "mode": None,
        "raw": "18787:8787",
        "syntax": "short",
    }


def test_bare_port_is_a_container_port_with_no_publish():
    row = parse_port_entry("8787")
    assert row["target"] == 8787
    assert row["published"] is None


def test_host_ip_and_protocol_are_kept():
    row = parse_port_entry("127.0.0.1:18081:80/udp")
    assert (row["host_ip"], row["published"], row["target"], row["protocol"]) == (
        "127.0.0.1",
        18081,
        80,
        "udp",
    )


def test_ipv6_host_is_not_mistaken_for_a_port():
    row = parse_port_entry("[::1]:18081:80")
    assert row["host_ip"] == "::1"
    assert (row["published"], row["target"]) == (18081, 80)


def test_long_syntax_is_read_as_written():
    row = parse_port_entry({"target": 8787, "published": "18787", "protocol": "tcp",
                            "mode": "ingress"})
    assert (row["published"], row["target"], row["mode"], row["syntax"]) == (
        18787,
        8787,
        "ingress",
        "long",
    )


def test_port_ranges_survive_as_strings_rather_than_becoming_wrong_integers():
    row = parse_port_entry("8000-8005:8000-8005")
    assert row["published"] == "8000-8005"
    assert row["target"] == "8000-8005"


# ------------------------------------------------------------------ merge directives


def test_override_and_reset_are_reported_with_their_different_effects():
    rows = _merge_directives(
        "services:\n  a:\n    ports: !override\n      - '1:1'\n  b:\n    ports: !reset\n"
    )
    by_key = {(r["key"], r["directive"]): r for r in rows}
    assert ("ports", "!override") in by_key
    assert ("ports", "!reset") in by_key
    assert "clears" in by_key[("ports", "!reset")]["effect"]
    assert "replaces" in by_key[("ports", "!override")]["effect"]


def test_overlay_with_merge_tags_still_parses(tmp_path: Path):
    overlay = tmp_path / "docker-compose.leco-hosting.yml"
    overlay.write_text(
        textwrap.dedent(
            """\
            services:
              edge:
                networks: [default, lh-network]
                ports: !override
                  - '18787:8787'
            networks:
              lh-network:
                external: true
            """
        ),
        encoding="utf-8",
    )
    data, err = _load_yaml_tolerant(overlay)
    assert err is None
    assert data["services"]["edge"]["ports"] == ["18787:8787"]


def test_tolerant_loader_still_refuses_python_object_tags(tmp_path: Path):
    bad = tmp_path / "evil.yml"
    bad.write_text("a: !!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    data, err = _load_yaml_tolerant(bad)
    assert data is None
    assert err and "invalid YAML" in err


def test_reset_in_a_hosting_overlay_is_raised_as_an_unknown(tmp_path: Path):
    app = tmp_path / "app"
    (app / "infra" / "docker").mkdir(parents=True)
    (app / "infra" / "docker" / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n    container_name: web-1\n    ports: ['80:80']\n",
        encoding="utf-8",
    )
    slot = tmp_path / "slot"
    slot.mkdir()
    (slot / "docker-compose.leco-hosting.yml").write_text(
        "services:\n  web:\n    ports: !reset\n      - '18080:80'\n", encoding="utf-8"
    )
    out = collect_evidence(app, hosting_dir=slot)
    assert any("!reset" in u for u in out["unknowns"])
    assert out["overlays"]["count"] == 1


# ---------------------------------------------------------------- declared ports


TOPOLOGY = """\
export const FRONT_DOOR_PORT = 8787;

// A comment with { name: 'not-real', port: 1 } that must not be parsed.
export const WORKERS = [
  { name: 'svc-auth', port: 8791, inspectorPort: 9291, tier: 'service' },
  { name: 'app-www', port: 8788, inspectorPort: 9288, tier: 'surface' },
  { name: 'core-router', port: FRONT_DOOR_PORT, inspectorPort: 9287, tier: 'ingress' },
  { name: 'mystery', port: computePort(), tier: 'unknown' },
];
"""


def test_literal_ports_are_read_and_const_references_are_resolved():
    parsed = parse_js_port_table(TOPOLOGY)
    by_name = {e["name"]: e for e in parsed["entries"]}
    assert by_name["svc-auth"]["port"] == 8791
    assert by_name["core-router"]["port"] == 8787
    assert by_name["core-router"]["resolved_from"] == {"port": "FRONT_DOOR_PORT"}
    assert by_name["svc-auth"]["inspector_port"] == 9291


def test_a_computed_port_is_reported_unresolved_rather_than_guessed():
    parsed = parse_js_port_table(TOPOLOGY)
    assert "mystery" not in {e["name"] for e in parsed["entries"]}
    assert [u["name"] for u in parsed["unresolved"]] == ["mystery"]


def test_commented_out_entries_are_not_collected():
    parsed = parse_js_port_table(TOPOLOGY)
    assert "not-real" not in {e["name"] for e in parsed["entries"]}


# ------------------------------------------------------------------- attribution


def _compose_fixture(root: Path) -> None:
    (root / "infra" / "docker").mkdir(parents=True)
    (root / "infra" / "docker" / "docker-compose.yml").write_text(
        textwrap.dedent(
            """\
            name: demo
            services:
              edge:
                build: {context: ../.., dockerfile: infra/docker/Dockerfile}
                container_name: bf-edge
                ports:
                  - '8787:8787'
                  - '8788:8788'
              origin:
                image: nginx:1.27-alpine
                container_name: bf-origin
                ports: ['8081:80']
            """
        ),
        encoding="utf-8",
    )
    (root / "infra" / "dev").mkdir(parents=True)
    (root / "infra" / "dev" / "topology.mjs").write_text(TOPOLOGY, encoding="utf-8")


def test_container_ports_are_attributed_to_the_declared_owner(tmp_path: Path):
    _compose_fixture(tmp_path)
    out = collect_evidence(tmp_path)
    owners = {
        row["container_port"]: (row["owner"], row["owner_source"])
        for row in out["port_attribution"]["attributed"]
    }
    assert owners[8787] == ("core-router", "infra/dev/topology.mjs")
    assert owners[8788] == ("app-www", "infra/dev/topology.mjs")


def test_a_port_with_no_declared_owner_becomes_a_named_unknown(tmp_path: Path):
    _compose_fixture(tmp_path)
    out = collect_evidence(tmp_path)
    unattributed = {r["container_port"] for r in out["port_attribution"]["unattributed"]}
    assert unattributed == {80}
    assert any("no declared owner" in u and "bf-origin:80" in u for u in out["unknowns"])


def test_attribution_never_pairs_by_position():
    compose = {
        "files": [
            {
                "file": "docker-compose.yml",
                "services": [
                    {
                        "service_name": "edge",
                        "container_name": "bf-edge",
                        "port_pairs": [{"published": 1, "target": 9999}],
                    }
                ],
            }
        ]
    }
    declared = {"sources": [{"file": "t.mjs", "entries": [{"name": "first", "port": 1234}]}]}
    joined = attribute_ports(compose, declared)
    assert joined["attributed"] == []
    assert joined["unattributed"][0]["container_port"] == 9999


# ------------------------------------------------------------------ compose facts


def test_compose_evidence_reports_service_container_and_pairs(tmp_path: Path):
    _compose_fixture(tmp_path)
    compose = collect_compose_evidence(tmp_path)
    assert compose["count"] == 1
    services = {s["service_name"]: s for s in compose["files"][0]["services"]}
    assert services["edge"]["container_name"] == "bf-edge"
    assert services["edge"]["build"]["dockerfile"] == "infra/docker/Dockerfile"
    assert [(p["published"], p["target"]) for p in services["edge"]["port_pairs"]] == [
        (8787, 8787),
        (8788, 8788),
    ]
    assert services["origin"]["image"] == "nginx:1.27-alpine"


def test_a_service_without_a_container_name_is_flagged(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n    ports: ['80:80']\n", encoding="utf-8"
    )
    out = collect_evidence(tmp_path)
    assert any("declares no container_name" in u for u in out["unknowns"])


def test_no_compose_is_an_unknown_not_a_silence(tmp_path: Path):
    out = collect_evidence(tmp_path)
    assert out["summary"]["compose_files"] == 0
    assert any("No compose file was found" in u for u in out["unknowns"])
    assert any("No declared port table" in u for u in out["unknowns"])


def test_overlay_evidence_only_reports_leco_owned_files(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.leco-hosting.yml").write_text(
        "services:\n  a:\n    networks: [lh-network]\n", encoding="utf-8"
    )
    rows = collect_overlay_evidence(tmp_path)
    assert [f["file"] for f in rows["files"]] == ["docker-compose.leco-hosting.yml"]


# ------------------------------------------------------------------ entry points


def test_scripts_and_the_files_they_name_are_resolved(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "engines": {"node": ">=22.0.0"},
                "scripts": {
                    "dev": "node infra/dev/dev.mjs",
                    "control": "node infra/dev/control/server.mjs",
                    "test": "vitest run",
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "infra" / "dev").mkdir(parents=True)
    (tmp_path / "infra" / "dev" / "dev.mjs").write_text("//\n", encoding="utf-8")

    out = collect_entry_points(tmp_path)
    by_script = {r["script"]: r for r in out["referenced_files"]}
    # ``.mjs`` counts, and a script name outside any allowlist ("control") is still followed.
    assert by_script["dev"]["files"] == [{"file": "infra/dev/dev.mjs", "exists": True}]
    assert by_script["control"]["files"] == [
        {"file": "infra/dev/control/server.mjs", "exists": False}
    ]
    assert "test" not in by_script  # names no file
    assert out["engines"] == {"node": ">=22.0.0"}


def test_a_script_naming_a_missing_file_is_an_unknown(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"dev": "node missing.mjs"}}), encoding="utf-8"
    )
    out = collect_evidence(tmp_path)
    assert any("missing.mjs" in u and "does not exist" in u for u in out["unknowns"])


# ------------------------------------------------------------------- confinement


def test_confine_rejects_parent_traversal(tmp_path: Path):
    base = tmp_path / "slot"
    base.mkdir()
    (tmp_path / "secret.yml").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.\."):
        _confine(base, "../secret.yml", [tmp_path])


def test_confine_rejects_a_symlink_that_escapes_every_allowed_root(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    base = tmp_path / "roots" / "slot"
    base.mkdir(parents=True)
    (base / "link.yml").symlink_to(outside / "compose.yml")
    with pytest.raises(ValueError, match="outside the allowed roots"):
        _confine(base, "link.yml", [tmp_path / "roots"])


def test_confine_follows_a_symlink_that_lands_inside_an_allowed_root(tmp_path: Path):
    app = tmp_path / "wsp" / "app"
    app.mkdir(parents=True)
    (app / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    slot = tmp_path / "project" / "slot"
    slot.mkdir(parents=True)
    (slot / "source").symlink_to(app)
    resolved = _confine(slot, "source/compose.yml", [tmp_path / "project", tmp_path / "wsp"])
    assert resolved == (app / "compose.yml").resolve()


def test_validate_compose_merge_refuses_a_file_outside_the_roots(tmp_path: Path, monkeypatch):
    base = tmp_path / "slot"
    base.mkdir()
    monkeypatch.setattr(app_evidence, "allowed_roots", lambda: [base])
    out = validate_compose_merge(base, "../elsewhere.yml")
    assert out["ok"] is False
    assert ".." in out["error"]


# ---------------------------------------------------------------- classification


def _http(status=None, error=None):
    return {"status_code": status, "error": error, "latency_ms": 1.0}


ROUTER = [{"key": "app-https", "match": "host_exact", "backends": ["http://bf-edge:8787"]}]
GOOD_TLS = {"handshake_ok": True, "hostname_in_cert": True}


def test_no_router_is_route_missing_even_when_the_edge_answers():
    kind, _ = classify_url_result([], GOOD_TLS, _http(404))
    assert kind == "route_missing"


def test_a_hostname_absent_from_the_certificate_beats_a_502():
    # The whole point of the ordering: the app is also down, but re-issuing the certificate
    # is the step that has to happen first either way.
    kind, detail = classify_url_result(
        ROUTER, {"handshake_ok": True, "hostname_in_cert": False}, _http(502)
    )
    assert kind == "tls_invalid"
    assert "SANs" in detail


def test_router_present_and_502_is_backend_unreachable_and_names_the_backend():
    kind, detail = classify_url_result(ROUTER, GOOD_TLS, _http(502))
    assert kind == "backend_unreachable"
    assert "http://bf-edge:8787" in detail


def test_a_transport_failure_is_backend_unreachable_not_a_silent_pass():
    kind, detail = classify_url_result(ROUTER, GOOD_TLS, _http(None, "ConnectionError: refused"))
    assert kind == "backend_unreachable"
    assert "refused" in detail


def test_500_is_unhealthy_rather_than_unreachable():
    assert classify_url_result(ROUTER, GOOD_TLS, _http(500))[0] == "unhealthy"


def test_2xx_and_4xx_are_ok_because_the_app_answered():
    assert classify_url_result(ROUTER, GOOD_TLS, _http(200))[0] == "ok"
    # core-router answers 404 for a hostname with no tenant — that is the app working.
    assert classify_url_result(ROUTER, GOOD_TLS, _http(404))[0] == "ok"


def test_a_failed_handshake_is_tls_invalid():
    kind, _ = classify_url_result(ROUTER, {"handshake_ok": False, "error": "timed out"}, _http(None))
    assert kind == "tls_invalid"


# ----------------------------------------------------------------- SAN matching


def test_exact_and_single_label_wildcard_san_matching():
    sans = ["*.utility-server.lh", "utility-server.lh"]
    assert _san_matches("utility-server.lh", sans)
    assert _san_matches("panel.utility-server.lh", sans)
    # A wildcard covers one label only, and never the bare domain by itself.
    assert not _san_matches("a.b.utility-server.lh", sans)
    assert not _san_matches("utility-server-edge.lh", sans)


def test_a_wildcard_directly_below_a_tld_matches_nothing():
    # `*.lh` is legal to write and refused by every TLS client — the reason
    # certs/generate-certs.sh issues one explicit SAN per hostname.
    assert not _san_matches("panel.utility-server.lh", ["*.lh"])
    assert not _san_matches("dashboard.lh", ["*.lh"])
    assert _san_matches("dashboard.lh", ["dashboard.lh"])
