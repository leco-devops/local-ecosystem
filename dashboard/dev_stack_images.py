"""Vetted container images and preflight checks for dev stacks."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import yaml

from dev_stack_compose import STACKS_ROOT, _slugify

# Bitnami removed public Magento images (2025); legacy archive preserves paths/env.
MAGENTO_APP_IMAGE = "docker.io/bitnamilegacy/magento-archived:2"
# Magento 2 (Bitnami) supports MariaDB 10.2–10.4; 11.x breaks the install script.
MAGENTO_DB_IMAGE = "docker.io/bitnamilegacy/mariadb:10.6"

# Exact or substring replacements applied to existing compose files on start/create.
IMAGE_REWRITES: tuple[tuple[str, str], ...] = (
    ("docker.io/bitnami/magento:2", MAGENTO_APP_IMAGE),
    ("bitnami/magento:2", "bitnamilegacy/magento-archived:2"),
    ("docker.io/bitnami/mariadb:latest", MAGENTO_DB_IMAGE),
    ("docker.io/bitnamilegacy/mariadb:11.4", MAGENTO_DB_IMAGE),
    ("bitnamilegacy/mariadb:11.4", "bitnamilegacy/mariadb:10.6"),
)

# Images that will never pull; fail fast with a clear message (no network call).
KNOWN_UNAVAILABLE: frozenset[str] = frozenset(
    {
        "docker.io/bitnami/magento:2",
        "bitnami/magento:2",
        "docker.io/bitnami/magento:latest",
    }
)


def extract_images_from_compose(compose: dict[str, Any]) -> list[str]:
    services = compose.get("services")
    if not isinstance(services, dict):
        return []
    images: list[str] = []
    for spec in services.values():
        if isinstance(spec, dict):
            img = str(spec.get("image") or "").strip()
            if img:
                images.append(img)
    return images


def normalize_compose_images(compose: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return compose with deprecated image refs rewritten."""
    logs: list[str] = []
    services = compose.get("services")
    if not isinstance(services, dict):
        return compose, logs
    for svc_name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        img = str(spec.get("image") or "").strip()
        if not img:
            continue
        new_img = img
        for old, new in IMAGE_REWRITES:
            if old in new_img:
                new_img = new_img.replace(old, new)
        if new_img != img:
            spec["image"] = new_img
            logs.append(f"Service {svc_name}: image {img} → {new_img}")
    return compose, logs


def _service_has_host_bind(spec: dict[str, Any], host_path_fragment: str) -> bool:
    volumes = spec.get("volumes")
    if not isinstance(volumes, list):
        return False
    for vol in volumes:
        if isinstance(vol, str) and vol.startswith("./") and host_path_fragment in vol:
            return True
    return False


def _nginx_compose_config_needs_fix(compose: dict[str, Any]) -> bool:
    """Detect nginx edge config missing Compose $$ escapes (breaks proxy_set_header)."""
    configs = compose.get("configs")
    if not isinstance(configs, dict):
        return True
    nginx = configs.get("nginx_edge_conf")
    if not isinstance(nginx, dict):
        return True
    content = str(nginx.get("content") or "")
    if not content.strip():
        return True
    return "$host" in content and "$$host" not in content


def upgrade_magento_full_edge_compose(compose: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Varnish/Nginx via Compose configs (no host bind mounts); refresh nginx $$ escapes."""
    services = compose.get("services")
    if not isinstance(services, dict):
        return compose, []
    varnish = services.get("varnish")
    edge = services.get("edge")
    if not isinstance(varnish, dict) or not isinstance(edge, dict):
        return compose, []
    needs_varnish = _service_has_host_bind(varnish, "varnish/default.vcl")
    needs_nginx = _service_has_host_bind(edge, "nginx/default.conf")
    needs_configs = not compose.get("configs") or _nginx_compose_config_needs_fix(compose)
    if not needs_varnish and not needs_nginx and not needs_configs:
        return compose, []

    from dev_stack_templates import magento_full_edge_configs

    updated = dict(compose)
    updated["configs"] = magento_full_edge_configs()
    services = dict(services)
    v = dict(varnish)
    v.pop("volumes", None)
    v["configs"] = [{"source": "varnish_vcl", "target": "/etc/varnish/default.vcl"}]
    services["varnish"] = v
    e = dict(edge)
    e.pop("volumes", None)
    e["configs"] = [{"source": "nginx_edge_conf", "target": "/etc/nginx/conf.d/default.conf"}]
    services["edge"] = e
    updated["services"] = services
    logs: list[str] = []
    if needs_varnish or needs_nginx:
        logs.append(
            "Varnish/Nginx: switched host bind mounts to Compose configs (no Docker file-sharing required)"
        )
    if needs_configs:
        logs.append("Nginx edge: fixed Compose config ($$ escapes for proxy_set_header variables)")
    return updated, logs


# Backward-compatible alias for tests
upgrade_magento_full_bind_mounts = upgrade_magento_full_edge_compose


def normalize_stack_compose_file(stack_id: str) -> list[str]:
    """Rewrite deprecated images in an on-disk stack compose file."""
    sid = _slugify(stack_id)
    path = STACKS_ROOT / sid / "docker-compose.yml"
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    updated, logs = normalize_compose_images(raw)
    updated, mount_logs = upgrade_magento_full_edge_compose(updated)
    logs = [*logs, *mount_logs]
    if logs:
        path.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    return logs


def _normalize_image_ref(image: str) -> str:
    img = image.strip()
    for old, new in IMAGE_REWRITES:
        if old in img:
            img = img.replace(old, new)
    return img


def check_image_available(image: str, *, timeout: int = 25) -> tuple[bool, str]:
    """Return (ok, detail). Uses local cache first, then registry manifest inspect."""
    img = _normalize_image_ref(image)
    if img in KNOWN_UNAVAILABLE or "bitnami/magento" in img:
        return False, "deprecated Bitnami Magento image — destroy and recreate the stack from Platform tab"
    try:
        local = subprocess.run(
            ["docker", "image", "inspect", img],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if local.returncode == 0:
            return True, "cached locally"
        proc = subprocess.run(
            ["docker", "manifest", "inspect", img],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return True, "registry check skipped (timeout; will pull on compose up)"
    except FileNotFoundError:
        return False, "docker CLI not found"
    except OSError as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, "available on registry"
    err = (proc.stderr or proc.stdout or "").strip()
    if "manifest unknown" in err.lower() or "not found" in err.lower():
        return False, "image not found on registry"
    if "denied" in err.lower():
        return False, "pull access denied or image removed from registry"
    return False, err[:200] if err else "manifest inspect failed"


def verify_compose_images(compose: dict[str, Any], *, skip_registry: bool = False) -> list[str]:
    """Collect human-readable errors for compose image references."""
    images: list[str] = []
    seen: set[str] = set()
    for image in extract_images_from_compose(compose):
        norm = _normalize_image_ref(image)
        if norm in seen:
            continue
        seen.add(norm)
        images.append(image)

    errors: list[str] = []
    for image in images:
        norm = _normalize_image_ref(image)
        if norm in KNOWN_UNAVAILABLE or "bitnami/magento" in norm:
            errors.append(f"{image}: deprecated — use Platform → Destroy, then create the stack again")

    if skip_registry:
        return errors

    to_check = [
        img
        for img in images
        if _normalize_image_ref(img) not in KNOWN_UNAVAILABLE and "bitnami/magento" not in _normalize_image_ref(img)
    ]
    if not to_check:
        return errors

    with ThreadPoolExecutor(max_workers=min(6, len(to_check))) as pool:
        futures = {pool.submit(check_image_available, img): img for img in to_check}
        for fut in as_completed(futures):
            image = futures[fut]
            ok, detail = fut.result()
            if not ok:
                errors.append(f"{image}: {detail}")
    return errors


def verify_stack_compose_file(stack_id: str, *, skip_registry: bool = False) -> list[str]:
    path = STACKS_ROOT / _slugify(stack_id) / "docker-compose.yml"
    if not path.is_file():
        return [f"Missing compose file for {stack_id}"]
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"Invalid compose file: {exc}"]
    if not isinstance(raw, dict):
        return ["Invalid compose file: root must be a mapping"]
    return verify_compose_images(raw, skip_registry=skip_registry)
