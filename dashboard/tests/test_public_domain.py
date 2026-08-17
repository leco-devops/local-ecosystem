"""Onboarding on a real domain, and the guarantee that a local install is untouched.

Before this, ``leco_detect`` never consulted ``config/leco-platform.yaml``: every generated
hostname, Traefik rule and public URL was a hardcoded ``<slug>.lh``. Registering an app on a
server whose ``base_domain`` is ``mydomain.com`` produced ``myapp.lh``, which resolves nowhere.

Two properties are load-bearing here and each has its own test:

* **Cloud mode follows the platform config.** ``deployment_mode: cloud`` + ``base_domain`` moves
  every generated name, including the multi-label mesh hostnames.
* **Local mode is byte-for-byte what it always was.** The default config (``local`` / ``lh``) and
  every degraded case — no platform config, unreadable config, nonsense ``base_domain`` — must
  produce ``.lh``, because falling back to a broken domain would break every workstation.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

import leco_detect  # noqa: E402

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _fake_project_root(tmp_path: Path, platform_yaml: str | None) -> Path:
    """A minimal repo layout that ``dashboard/platform_config.py`` can be pointed at."""
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "ecosystem-stack" / "lib").mkdir(parents=True, exist_ok=True)
    (root / "ecosystem-stack" / "config").mkdir(parents=True, exist_ok=True)
    (root / "traefik").mkdir(parents=True, exist_ok=True)
    # A copy, not a symlink: platform_config derives PROJECT_ROOT from its own resolved
    # __file__, and a symlink would resolve straight back to the real repo.
    shutil.copy(ROOT / "ecosystem-stack" / "lib" / "platform_config.py", root / "ecosystem-stack" / "lib")
    for name in ("install-profiles.yaml", "component-catalog.yaml"):
        src = ROOT / "ecosystem-stack" / "config" / name
        if src.is_file():
            shutil.copy(src, root / "ecosystem-stack" / "config" / name)
    shutil.copy(ROOT / "traefik" / "traefik-static-acme.yaml", root / "traefik")
    if platform_yaml is not None:
        (root / "config" / "leco-platform.yaml").write_text(platform_yaml, encoding="utf-8")
    return root


@contextmanager
def platform(tmp_path: Path, platform_yaml: str | None):
    """
    Install a ``platform_config`` bound to a throwaway project root.

    ``leco_detect.routing_domain`` imports ``platform_config`` lazily, inside the call, so
    swapping ``sys.modules`` is enough and nothing has to be reloaded. The real
    ``config/leco-platform.yaml`` is never read or written by these tests.
    """
    root = _fake_project_root(tmp_path, platform_yaml)
    spec = importlib.util.spec_from_file_location(
        "leco_test_platform_config", ROOT / "dashboard" / "platform_config.py"
    )
    assert spec and spec.loader
    saved_env = None
    import os

    saved_env = os.environ.get("DASHBOARD_PROJECT_ROOT")
    os.environ["DASHBOARD_PROJECT_ROOT"] = str(root)
    saved_mod = sys.modules.get("platform_config")
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["platform_config"] = mod
        yield mod
    finally:
        if saved_mod is not None:
            sys.modules["platform_config"] = saved_mod
        else:
            sys.modules.pop("platform_config", None)
        if saved_env is None:
            os.environ.pop("DASHBOARD_PROJECT_ROOT", None)
        else:
            os.environ["DASHBOARD_PROJECT_ROOT"] = saved_env


LOCAL_YAML = "deployment_mode: local\nbase_domain: lh\ntls:\n  mode: mkcert\n"
CLOUD_YAML = (
    "deployment_mode: cloud\nbase_domain: leco.example.com\ntls:\n  mode: acme\n"
    "  acme_email: ops@example.com\n"
)


# --------------------------------------------------------------- local invariance


@pytest.mark.parametrize(
    "cfg",
    [
        pytest.param(LOCAL_YAML, id="default-local"),
        pytest.param(None, id="no-platform-config"),
        pytest.param("", id="empty-file"),
        pytest.param("deployment_mode: cloud\nbase_domain: lh\n", id="cloud-but-domain-still-lh"),
        pytest.param("deployment_mode: local\nbase_domain: mydomain.com\n", id="domain-without-cloud-mode"),
        pytest.param("deployment_mode: cloud\nbase_domain: 'not a domain'\n", id="invalid-domain"),
        pytest.param("deployment_mode: cloud\nbase_domain: '-bad.example.com'\n", id="illegal-leading-dash"),
        pytest.param("deployment_mode: cloud\nbase_domain: ''\n", id="blank-domain"),
        pytest.param(": : not yaml : [\n", id="unparseable-yaml"),
    ],
)
def test_local_and_degraded_configs_all_stay_on_lh(tmp_path, cfg):
    """Anything short of an explicit, valid cloud domain must keep producing ``.lh``."""
    with platform(tmp_path, cfg):
        assert leco_detect.routing_domain() == "lh"
        assert leco_detect.main_urls_from_app_id("myapp") == {
            "https": "https://myapp.lh",
            "http": "http://myapp.lh",
        }
        assert leco_detect.app_hostname("myapp") == "myapp.lh"
        assert leco_detect.app_hostname("myapp", "api") == "api.myapp.lh"


def test_no_platform_config_module_at_all_still_resolves(tmp_path, monkeypatch):
    """
    ``dashboard/Dockerfile`` does not COPY ``platform_config.py`` into the image.

    The container normally runs from the bind-mounted ``/project/dashboard``, where the module
    exists; the image's ``/app`` fallback has no ``platform_config`` at all. In that case
    onboarding must read ``leco-platform.yaml`` directly rather than silently answering ``.lh`` —
    on a cloud server that looks exactly like success while producing unroutable hostnames.
    """
    monkeypatch.setitem(sys.modules, "platform_config", None)

    root_local = _fake_project_root(tmp_path / "loc", LOCAL_YAML)
    monkeypatch.setenv("DASHBOARD_PROJECT_ROOT", str(root_local))
    assert leco_detect.routing_domain() == "lh"
    assert leco_detect.main_url_from_app_id("myapp") == "https://myapp.lh"

    root_cloud = _fake_project_root(tmp_path / "cld", CLOUD_YAML)
    monkeypatch.setenv("DASHBOARD_PROJECT_ROOT", str(root_cloud))
    assert leco_detect.routing_domain() == "leco.example.com"
    assert leco_detect.main_url_from_app_id("myapp") == "https://myapp.leco.example.com"

    # No platform config file at all on the fallback path either.
    monkeypatch.setenv("DASHBOARD_PROJECT_ROOT", str(_fake_project_root(tmp_path / "bare", None)))
    assert leco_detect.routing_domain() == "lh"


# --------------------------------------------------------------- cloud behaviour


def test_cloud_domain_drives_urls_and_hostnames(tmp_path):
    with platform(tmp_path, CLOUD_YAML):
        assert leco_detect.routing_domain() == "leco.example.com"
        assert leco_detect.main_urls_from_app_id("myapp") == {
            "https": "https://myapp.leco.example.com",
            "http": "http://myapp.leco.example.com",
        }
        assert leco_detect.app_hostname("myapp", "api") == "api.myapp.leco.example.com"


def test_generated_manifest_uses_the_public_domain(tmp_path):
    app = tmp_path / "src"
    app.mkdir()
    (app / "docker-compose.yml").write_text(
        "services:\n"
        "  frontend:\n    image: nginx\n    ports: ['3000:3000']\n"
        "  backend:\n    image: python\n    ports: ['8001:8001']\n",
        encoding="utf-8",
    )
    with platform(tmp_path, CLOUD_YAML):
        _manifest, localhost = leco_detect.build_default_manifest_and_localhost(app, "myapp")
    urls = [u["publicUrl"] for u in localhost["urls"]]
    assert urls == [
        "https://myapp.leco.example.com",
        "http://myapp.leco.example.com",
        "https://myapp.leco.example.com/api",
        "http://myapp.leco.example.com/api",
    ]
    entries = localhost["infrastructure"]["routing"]["entries"]
    assert entries[0]["hostname"] == "myapp.leco.example.com"
    assert not any(".lh" in u for u in urls)


def test_mesh_hostnames_are_valid_multi_label_dns_names(tmp_path):
    """``<runtime>.<slug>.<domain>`` must stay a legal name, label by label."""
    app = tmp_path / "mesh"
    app.mkdir()
    (app / "docker-compose.yml").write_text(
        "name: mesh\nservices:\n  edge:\n    image: node\n"
        "    ports: ['8787:8787', '8788:8788', '8789:8789']\n",
        encoding="utf-8",
    )
    (app / "leco.yaml").write_text(
        "schemaVersion: 2\ninfrastructure:\n"
        "  dockerCompose:\n    composeFile: docker-compose.yml\n    projectName: mesh\n"
        "  runtimes:\n"
        "    - id: Mod_Render\n      port: 8788\n"
        "    - id: api\n      port: 8789\n",
        encoding="utf-8",
    )
    localhost = yaml.safe_load((app / "leco.yaml").read_text(encoding="utf-8"))
    manifest = {"lecoAppVersion": "3", "name": "mesh", "root": "."}
    with platform(tmp_path, CLOUD_YAML):
        entries = leco_detect._infer_mesh_routing_entries(localhost, app, manifest, "mesh")

    hosts = [e["hostname"] for e in entries]
    assert hosts[0] == "mesh.leco.example.com"
    assert "mod-render.mesh.leco.example.com" in hosts
    assert "api.mesh.leco.example.com" in hosts
    for host in hosts:
        assert len(host) <= 253
        for label in host.split("."):
            assert _HOST_LABEL.match(label), f"{label!r} in {host!r} is not a DNS label"


def test_oversized_label_is_truncated_not_emitted_raw(tmp_path):
    with platform(tmp_path, CLOUD_YAML):
        host = leco_detect.app_hostname("myapp", "x" * 200)
    first = host.split(".", 1)[0]
    assert len(first) == 63
    assert _HOST_LABEL.match(first)


def test_hostname_over_253_chars_is_rejected(tmp_path):
    long_domain = ".".join(["abcdefghij"] * 22)  # 241 chars, still legal labels
    with platform(tmp_path, f"deployment_mode: cloud\nbase_domain: {long_domain}\n"):
        with pytest.raises(ValueError, match="253"):
            leco_detect.app_hostname("a" * 30)


def test_app_hostname_rejects_a_non_label_slug(tmp_path):
    with platform(tmp_path, LOCAL_YAML):
        for bad in ("", "not a slug", "-lead", "trail-", "has.dot", "UPPER"):
            with pytest.raises(ValueError):
                leco_detect.app_hostname(bad)


def test_samples_offered_by_the_wizard_use_the_live_domain(tmp_path):
    with platform(tmp_path, CLOUD_YAML):
        samples = leco_detect.register_yaml_samples()
    joined = "\n".join(str(s.get("localhost_yaml") or "") for s in samples)
    assert "wp.leco.example.com" in joined
    assert "my-monorepo.leco.example.com" in joined
    assert ".lh" not in joined

    with platform(tmp_path, LOCAL_YAML):
        local_samples = leco_detect.register_yaml_samples()
    local_joined = "\n".join(str(s.get("localhost_yaml") or "") for s in local_samples)
    assert "https://wp.lh" in local_joined


def test_overlay_env_still_matches_lh_hosts_after_the_move(tmp_path):
    """An app registered on a workstation keeps its overlay env once the box goes cloud."""
    with platform(tmp_path, CLOUD_YAML):
        assert leco_detect._primary_public_hostname_from_routing([{"hostname": "old.lh"}]) == "old.lh"
        assert (
            leco_detect._primary_public_hostname_from_routing([{"hostname": "new.leco.example.com"}])
            == "new.leco.example.com"
        )
        assert leco_detect._primary_public_hostname_from_routing([{"hostname": "elsewhere.net"}]) is None


# --------------------------------------------------------------- migration is explicit


def _registered_app(tmp_path: Path) -> Path:
    app = tmp_path / "registered"
    app.mkdir()
    (app / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n    ports: ['8080:80']\n", encoding="utf-8"
    )
    (app / "leco.app.yaml").write_text(
        'lecoAppVersion: "3"\nname: myapp\nroot: "."\nlocalHostProfile: leco.yaml\n', encoding="utf-8"
    )
    (app / "leco.yaml").write_text(
        "schemaVersion: 2\n"
        "infrastructure:\n"
        "  dockerCompose:\n    composeFile: docker-compose.yml\n    projectName: myapp\n"
        "  routing:\n    entries:\n      - hostname: myapp.lh\n        backendHost: myapp-web-1\n"
        "        backendPort: 80\n"
        "urls:\n  - role: frontend\n    label: App\n    publicUrl: https://myapp.lh\n",
        encoding="utf-8",
    )
    return app


def test_detect_does_not_rewrite_an_existing_manifest(tmp_path):
    """Switching the platform to cloud must not silently rebrand an app already on disk."""
    app = _registered_app(tmp_path)
    before = (app / "leco.yaml").read_text(encoding="utf-8")
    with platform(tmp_path, CLOUD_YAML):
        leco_detect.scan_app_directory(app)
        leco_detect.read_existing_registration_yaml(app)
    assert (app / "leco.yaml").read_text(encoding="utf-8") == before
    assert "myapp.lh" in before


def test_migration_helper_reports_before_it_writes(tmp_path):
    app = _registered_app(tmp_path)
    with platform(tmp_path, CLOUD_YAML):
        plan = leco_detect.migrate_manifest_to_domain(app / "leco.app.yaml", apply=False)
        assert plan["ok"] is True
        assert plan["applied"] is False
        moves = {(c["kind"], c["from"], c["to"]) for c in plan["changes"]}
        assert ("hostname", "myapp.lh", "myapp.leco.example.com") in moves
        assert ("url", "https://myapp.lh", "https://myapp.leco.example.com") in moves
        # Dry run really is dry.
        assert "myapp.lh" in (app / "leco.yaml").read_text(encoding="utf-8")

        applied = leco_detect.migrate_manifest_to_domain(app / "leco.app.yaml", apply=True)
        assert applied["applied"] is True

    after = yaml.safe_load((app / "leco.yaml").read_text(encoding="utf-8"))
    assert after["infrastructure"]["routing"]["entries"][0]["hostname"] == "myapp.leco.example.com"
    assert after["urls"][0]["publicUrl"] == "https://myapp.leco.example.com"


def test_migration_is_a_no_op_on_a_local_install(tmp_path):
    app = _registered_app(tmp_path)
    before = (app / "leco.yaml").read_text(encoding="utf-8")
    with platform(tmp_path, LOCAL_YAML):
        plan = leco_detect.migrate_manifest_to_domain(app / "leco.app.yaml", apply=True)
    assert plan["changes"] == []
    assert (app / "leco.yaml").read_text(encoding="utf-8") == before


# --------------------------------------------------------------- Traefik + TLS wiring


def _render_module():
    spec = importlib.util.spec_from_file_location(
        "leco_test_render_traefik", ROOT / "scripts" / "render-platform-traefik.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stack_routes_are_untouched_on_lh():
    mod = _render_module()
    source = (ROOT / "traefik" / "dynamic.yml").read_text(encoding="utf-8")
    assert mod.render("lh", "mkcert") == source


def test_stack_routes_move_to_the_domain_and_name_the_resolver():
    mod = _render_module()
    rendered = mod.render("leco.example.com", "acme")
    doc = yaml.safe_load(rendered)
    routers = doc["http"]["routers"]
    assert routers, "expected routers in traefik/dynamic.yml"
    assert not re.search(r"`[^`]*\.lh`", rendered), "a .lh rule survived the rewrite"

    resolver = mod.acme_resolver_name()
    static = yaml.safe_load((ROOT / "traefik" / "traefik-static-acme.yaml").read_text(encoding="utf-8"))
    assert resolver in (static.get("certificatesResolvers") or {}), "resolver name drifted from the static config"

    tls_routers = [name for name, r in routers.items() if r.get("tls")]
    assert tls_routers
    for name in tls_routers:
        assert routers[name]["tls"].get("certResolver") == resolver, name

    # The mkcert bundle is never generated on a cloud install; pointing Traefik at it only
    # produces a certificate-not-found error on every reload.
    assert "certificates" not in (doc.get("tls") or {})


def test_acme_static_config_is_coherent():
    static = yaml.safe_load((ROOT / "traefik" / "traefik-static-acme.yaml").read_text(encoding="utf-8"))
    resolvers = static["certificatesResolvers"]
    assert len(resolvers) == 1
    acme = next(iter(resolvers.values()))["acme"]
    # HTTP-01 must be answered on an entrypoint that is actually listening on :80.
    challenge_ep = acme["httpChallenge"]["entryPoint"]
    assert static["entryPoints"][challenge_ep]["address"] == ":80"
    # A store outside a mounted path is destroyed on `restart` -> rate-limit exhaustion.
    assert acme["storage"].startswith("/acme/")
    # An unauthenticated API on a public VM leaks the whole routing table.
    assert static["api"].get("insecure") is False


def _fresh_dev_stack_routes():
    """``dev_stack_routes`` binds ``public_hostname`` at import time, so reload per platform."""
    sys.modules.pop("dev_stack_routes", None)
    import dev_stack_routes

    return dev_stack_routes


def test_dev_stack_routes_follow_the_platform_domain(tmp_path):
    with platform(tmp_path, CLOUD_YAML) as pc:
        mod = _fresh_dev_stack_routes()
        assert mod.stack_hostname("t-django") == "t-django.leco.example.com"
        assert pc.router_tls_config() == {"certResolver": pc.acme_resolver_name()}
    with platform(tmp_path, LOCAL_YAML) as pc:
        mod = _fresh_dev_stack_routes()
        assert mod.stack_hostname("t-django") == "t-django.lh"
        assert pc.router_tls_config() is True
    sys.modules.pop("dev_stack_routes", None)


# --------------------------------------------------------------- mkcert refuses on a real domain


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.parametrize("mode", ["acme", "cloudflare", "static"])
def test_generate_certs_refuses_non_mkcert_modes(tmp_path, mode):
    root = _fake_project_root(tmp_path, f"deployment_mode: cloud\nbase_domain: leco.example.com\ntls:\n  mode: {mode}\n")
    (root / "certs").mkdir(parents=True, exist_ok=True)
    script = root / "certs" / "generate-certs.sh"
    shutil.copy(ROOT / "certs" / "generate-certs.sh", script)
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "Nothing was generated" in proc.stderr
    assert not (root / "certs" / "wildcard.lh.pem").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_generate_certs_refuses_mkcert_on_a_real_domain(tmp_path):
    root = _fake_project_root(
        tmp_path, "deployment_mode: cloud\nbase_domain: leco.example.com\ntls:\n  mode: mkcert\n"
    )
    (root / "certs").mkdir(parents=True, exist_ok=True)
    script = root / "certs" / "generate-certs.sh"
    shutil.copy(ROOT / "certs" / "generate-certs.sh", script)
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "not 'lh'" in proc.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_generate_certs_still_lists_lh_hosts_locally():
    """The local path is untouched: --list must still enumerate the .lh certificate SANs."""
    proc = subprocess.run(
        ["bash", str(ROOT / "certs" / "generate-certs.sh"), "--list"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "dashboard.lh" in proc.stdout
    assert "traefik.lh" in proc.stdout


# --------------------------------------------------------------- cloud installer preflight


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.parametrize(
    "args, expected",
    [
        ([], "--domain <fqdn> is required"),
        (["--domain", "leco.mydomain.com"], "--profile <name> is required"),
        (["--domain", "myapp.lh", "--profile", "minimal"], "local-only name"),
        (["--domain", "NotLower.COM", "--profile", "minimal"], "not a valid lowercase DNS name"),
        (["--domain", "leco.mydomain.com", "--profile", "nope"], "not defined in"),
        (["--domain", "leco.mydomain.com", "--profile", "minimal", "--tls", "mkcert"], "trusted only on the machine"),
        (["--domain", "leco.mydomain.com", "--profile", "minimal", "--tls", "junk"], "not one of"),
    ],
)
def test_cloud_install_preflight_refuses_bad_input(args, expected):
    proc = subprocess.run(
        ["bash", str(ROOT / "ecosystem-stack" / "cloud-install.sh"), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert expected in proc.stderr, proc.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_cloud_install_requires_an_explicit_security_acknowledgement():
    """A valid invocation still stops: an unauthenticated public control plane is a decision."""
    proc = subprocess.run(
        [
            "bash",
            str(ROOT / "ecosystem-stack" / "cloud-install.sh"),
            "--domain", "leco.mydomain.com",
            "--profile", "minimal",
            "--tls", "cloudflare",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "DASHBOARD_CONTROL_TOKEN" in proc.stderr
    assert "LECO_CLOUD_ACK=1" in proc.stderr
