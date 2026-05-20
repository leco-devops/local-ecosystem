"""Heuristic app archetype from repository layout (for localhost profile defaults)."""

from __future__ import annotations

import json
from pathlib import Path

from leco_app.schema import LocalhostArchetype


def detect_archetype(root: Path) -> LocalhostArchetype:
    r = root.resolve()
    if (r / "wp-config.php").is_file() or (r / "wp-config-sample.php").is_file():
        return "wordpress"
    if (r / "bin" / "magento").is_file() or (r / "app" / "etc" / "env.php").is_file():
        return "magento2"
    for name in ("next.config.js", "next.config.mjs", "next.config.ts"):
        if (r / name).is_file():
            return "nextjs"
    comp = r / "composer.json"
    if comp.is_file():
        try:
            data = json.loads(comp.read_text(encoding="utf-8"))
            req = data.get("require") or {}
            if isinstance(req, dict) and any("laravel/framework" in str(k) for k in req):
                return "laravel"
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return "php-fpm"
    pkg = r / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            if any("react" in str(v).lower() or "react" in k.lower() for k, v in deps.items()):
                return "node"
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return "node"
    if (r / "pom.xml").is_file() or (r / "build.gradle").is_file() or (r / "build.gradle.kts").is_file():
        return "java"
    if list(r.glob("*.csproj")):
        return "dotnet"
    if (r / "index.html").is_file() and not pkg.is_file():
        return "static"
    return "generic"
