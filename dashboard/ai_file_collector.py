"""
Smart file collector for AI-assisted onboarding.

Reads an app directory and selects the most informative files within a
token budget.  Larger budgets (cloud providers) include more files and
longer excerpts; smaller budgets (local Ollama) focus on identity and
config files only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CollectedFile:
    """One collected file with metadata."""
    name: str            # relative path from app_root
    content: str         # (possibly truncated) text content
    lines: int           # original line count
    truncated: bool      # whether we had to cut it
    tokens_est: int      # rough token estimate


@dataclass
class CollectedContext:
    """Result of the file collection phase."""
    files: list[CollectedFile] = field(default_factory=list)
    total_tokens: int = 0
    budget: int = 0
    app_root: str = ""
    skipped: list[str] = field(default_factory=list)


# Rough token estimate: ~4 chars per token for code
def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# Directories to never descend into
SKIP_DIRS = frozenset({
    "node_modules", ".git", ".svn", "__pycache__", ".next", ".nuxt",
    "dist", "build", "vendor", "bower_components", ".cache", ".turbo",
    "coverage", ".nyc_output", ".tox", ".venv", "venv", "env",
})

# Extensions we can read
TEXT_EXTENSIONS = frozenset({
    ".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".py", ".rb", ".php", ".java", ".go", ".rs",
    ".sh", ".bash", ".zsh",
    ".conf", ".vcl", ".xml",
    ".md", ".txt", ".env", ".example", ".sample",
    ".dockerfile", ".makefile", ".procfile",
    "", # extensionless files like Dockerfile, Makefile, Procfile
})

# Priority tiers: (glob_patterns, max_lines, description)
# Lower tier number = higher priority = collected first
PRIORITY_TIERS: list[dict[str, Any]] = [
    # Tier 1: Identity files — always include
    {
        "tier": 1,
        "names": [
            "package.json",
            "config.js", "config.ts", "config/index.js", "config/index.ts",
            "config.mjs", "config.cjs",
            ".env.example", ".env.sample", ".env.template",
            "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
            "Dockerfile",
        ],
        "max_lines": 200,
        "description": "identity & config files",
    },
    # Tier 2: Behaviour files — include if budget allows
    {
        "tier": 2,
        "names": [
            "server.js", "app.js", "index.js", "main.js", "src/index.js", "src/app.js",
            "server.ts", "app.ts", "index.ts", "main.ts", "src/index.ts", "src/app.ts",
            "worker.js", "cron.js", "requestQueue.js", "queue.js", "consumer.js",
            "worker.ts", "cron.ts", "requestQueue.ts",
        ],
        "max_lines": 100,
        "description": "entry points & processes",
    },
    # Tier 3: Infrastructure configs — include if budget allows
    {
        "tier": 3,
        "names": [
            "nginx.conf", "conf/nginx/nginx.conf", "conf/nginx/default.conf",
            "default.vcl", "conf/varnish/default.vcl",
            "redis.conf", "conf/redis/redis.conf",
            "docker-compose.override.yml",
            "docker-compose.leco-hosting.yml",
            "leco-docker-preload.js",
            "leco.yaml", "leco.app.yaml",
            "pm2.config.js", "pm2.json", "ecosystem.config.js",
            "Procfile", "Makefile",
        ],
        "max_lines": 120,
        "description": "infra configs & process managers",
    },
    # Tier 4: Context files — only with generous budget
    {
        "tier": 4,
        "names": [
            "README.md", "readme.md",
            "wrangler.toml", "wrangler.json",
            "tsconfig.json", "next.config.js", "next.config.mjs",
            "vite.config.js", "vite.config.ts",
            "pyproject.toml", "requirements.txt",
            "go.mod", "Cargo.toml", "Gemfile",
        ],
        "max_lines": 80,
        "description": "context & build config",
    },
]

# Auto-detect additional entry scripts by scanning package.json scripts
def _detect_scripts_from_package_json(app_root: Path) -> list[str]:
    """Extract script target files from package.json scripts section."""
    pj = app_root / "package.json"
    if not pj.is_file():
        return []
    try:
        import json
        data = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
        scripts = data.get("scripts", {})
        extra = []
        for key, cmd in scripts.items():
            if key in ("start", "dev", "worker", "cron", "queue", "serve"):
                # Extract the JS file from "node server.js" or "node src/worker.js"
                parts = cmd.split()
                for p in parts:
                    if p.endswith(".js") or p.endswith(".ts"):
                        extra.append(p)
        return extra
    except Exception:
        return []


def _read_file_truncated(path: Path, max_lines: int) -> tuple[str, int, bool]:
    """Read a file, truncate to max_lines. Returns (content, original_lines, was_truncated)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return "", 0, False
    lines = raw.splitlines(keepends=True)
    original = len(lines)
    if original <= max_lines:
        return raw, original, False
    truncated = "".join(lines[:max_lines])
    truncated += f"\n# [truncated at {max_lines} of {original} lines]\n"
    return truncated, original, True


def collect_app_context(app_root: str | Path, token_budget: int = 12_000) -> CollectedContext:
    """Collect source files from app_root within a token budget.

    Higher budgets (cloud AI) include more files and longer excerpts.
    Lower budgets (local Ollama) focus on tier 1-2 files only.
    """
    root = Path(app_root).resolve()
    if not root.is_dir():
        return CollectedContext(app_root=str(root), budget=token_budget)

    ctx = CollectedContext(app_root=str(root), budget=token_budget)
    tokens_used = 0
    collected_names: set[str] = set()

    # Detect extra entry scripts from package.json
    extra_scripts = _detect_scripts_from_package_json(root)

    for tier_def in PRIORITY_TIERS:
        tier = tier_def["tier"]
        max_lines = tier_def["max_lines"]
        names = list(tier_def["names"])

        # Add detected scripts to tier 2
        if tier == 2:
            for s in extra_scripts:
                if s not in names:
                    names.append(s)

        # Scale max_lines based on budget generosity
        if token_budget >= 40_000:
            max_lines = int(max_lines * 1.5)
        elif token_budget <= 10_000:
            max_lines = int(max_lines * 0.7)

        for name in names:
            if tokens_used >= token_budget:
                ctx.skipped.append(f"[budget exhausted at tier {tier}]")
                break

            fp = root / name
            if not fp.is_file():
                continue
            if name in collected_names:
                continue

            content, orig_lines, truncated = _read_file_truncated(fp, max_lines)
            if not content.strip():
                continue

            est = _estimate_tokens(content)
            if tokens_used + est > token_budget and tokens_used > 0:
                ctx.skipped.append(name)
                continue

            cf = CollectedFile(
                name=name,
                content=content,
                lines=orig_lines,
                truncated=truncated,
                tokens_est=est,
            )
            ctx.files.append(cf)
            collected_names.add(name)
            tokens_used += est

        if tokens_used >= token_budget:
            break

    # Also scan for *.conf and *.vcl in conf/ subdirectory (tier 3)
    conf_dir = root / "conf"
    if conf_dir.is_dir() and tokens_used < token_budget:
        for sub in sorted(conf_dir.iterdir()):
            if not sub.is_dir():
                continue
            for f in sorted(sub.iterdir()):
                if f.suffix not in (".conf", ".vcl", ".ini", ".cfg"):
                    continue
                rel = str(f.relative_to(root))
                if rel in collected_names:
                    continue
                content, orig_lines, truncated = _read_file_truncated(f, 120)
                if not content.strip():
                    continue
                est = _estimate_tokens(content)
                if tokens_used + est > token_budget:
                    ctx.skipped.append(rel)
                    continue
                ctx.files.append(CollectedFile(
                    name=rel,
                    content=content,
                    lines=orig_lines,
                    truncated=truncated,
                    tokens_est=est,
                ))
                collected_names.add(rel)
                tokens_used += est

    ctx.total_tokens = tokens_used
    return ctx
