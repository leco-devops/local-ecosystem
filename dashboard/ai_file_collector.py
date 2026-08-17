"""
Smart file collector for AI-assisted onboarding.

Reads an app directory and selects the most informative files within a
token budget.  Larger budgets (cloud providers) include more files and
longer excerpts; smaller budgets (local Ollama) focus on identity and
config files only.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
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
            "wrangler.toml", "wrangler.json", "wrangler.jsonc",
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
        # Any script whose command starts with a known runtime, rather than a six-word
        # allowlist of script *names*: a monorepo names scripts for its domain ("control",
        # "mesh", "edge"), and those were invisible. The model judges relevance better than
        # a fixed list does.
        runtimes = ("node", "bun", "deno", "tsx", "ts-node", "python", "python3", "nodemon")
        for _key, cmd in scripts.items():
            parts = cmd.split()
            if not parts:
                continue
            head = parts[0].rsplit("/", 1)[-1]
            if head not in runtimes and not any(p.rsplit("/", 1)[-1] in runtimes for p in parts[:2]):
                continue
            for p in parts:
                # .mjs is the modern default and "dev.mjs".endswith(".js") is False, so every
                # entry script in an ESM project used to be skipped.
                if p.endswith((".js", ".ts", ".mjs", ".cjs", ".mts", ".cts", ".py")):
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


# ---------------------------------------------------------------------------
# Discovery below the root
# ---------------------------------------------------------------------------

_INFRA_GLOBS = (
    "docker-compose*.y*ml", "compose*.y*ml",
    "wrangler.toml", "wrangler.json", "wrangler.jsonc",
)

# Directories that never hold the answer and are expensive to walk.
_DISCOVER_PRUNE = {
    ".git", "node_modules", "dist", "build", "coverage", ".next", ".nuxt",
    "vendor", "__pycache__", ".venv", "venv", ".wrangler", ".turbo", "target",
}


def _discover_infra_files(root: Path, *, max_depth: int = 3, limit: int = 24) -> list[str]:
    """Relative paths to compose and wrangler configs anywhere near the root.

    The collector previously only ever looked at ``root / name`` plus a hardcoded ``conf/``,
    so a monorepo keeping its compose at ``infra/docker/docker-compose.yml`` and its Workers
    under ``workers/*/wrangler.jsonc`` presented as having neither. The model then reported
    ``compose_files: []`` and ``has_wrangler: false`` for a repository that is nothing but
    Workers, and a working compose was ignored in favour of a generated one.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        if len(rel_dir.parts) >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in _DISCOVER_PRUNE and not d.startswith(".")]
        for pattern in _INFRA_GLOBS:
            for name in fnmatch.filter(filenames, pattern):
                rel = str((rel_dir / name)) if str(rel_dir) != "." else name
                if rel not in found:
                    found.append(rel)
                if len(found) >= limit:
                    return found
    return found


def _paths_from_script_commands(root: Path) -> list[str]:
    """Config paths named inside package.json scripts.

    ``"dev:docker": "docker compose -f infra/docker/docker-compose.yml up --build"`` names the
    real compose file. The collector already read package.json and did not follow it.
    """
    pkg = root / "package.json"
    if not pkg.is_file():
        return []
    try:
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for cmd in scripts.values():
        if not isinstance(cmd, str):
            continue
        for tok in cmd.split():
            tok = tok.strip("\"'")
            if tok.endswith((".yml", ".yaml", ".toml", ".json", ".jsonc")) and "/" in tok:
                if (root / tok).is_file() and tok not in out:
                    out.append(tok)
    return out


def _local_imports_of(root: Path, rel_paths: list[str], *, limit: int = 8) -> list[str]:
    """One hop of relative imports out of already-selected entry scripts.

    The ports for every worker in one real project live in ``infra/dev/topology.mjs``, which
    nothing references by name in package.json — it is imported by the dev entry script. With
    it invisible the model said, in its own trace, "we'll set 3000 arbitrarily". One hop is
    enough to reach data like that without walking the whole tree.
    """
    out: list[str] = []
    for rel in rel_paths:
        fp = root / rel
        if not fp.is_file() or fp.suffix not in (".mjs", ".js", ".ts", ".cjs", ".mts"):
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"""(?:from|import)\s+['"](\.[^'"]+)['"]""", text):
            target = (fp.parent / m.group(1)).resolve()
            candidates = [target] if target.suffix else [
                target.with_suffix(ext) for ext in (".mjs", ".js", ".ts")
            ]
            for cand in candidates:
                try:
                    rel_c = str(cand.relative_to(root))
                except ValueError:
                    continue
                if cand.is_file() and rel_c not in out and rel_c not in rel_paths:
                    out.append(rel_c)
                    break
            if len(out) >= limit:
                return out
    return out


def collect_app_context(app_root: str | Path, token_budget: int = 12_000) -> CollectedContext:
    """Collect source files from app_root within a token budget.

    Higher budgets (cloud AI) include more files and longer excerpts.
    Lower budgets (local Ollama) focus on tier 1-2 files only.
    """
    root = Path(app_root).resolve()
    if not root.is_dir():
        return CollectedContext(app_root=str(root), budget=token_budget)

    ctx = CollectedContext(app_root=str(root), budget=token_budget)
    _INFRA_READ_FULL: set[str] = set()
    tokens_used = 0
    collected_names: set[str] = set()
    # Dedup on filesystem identity (st_dev, st_ino), not the candidate string and not
    # resolve() — APFS and NTFS are case-insensitive, so "README.md" and "readme.md"
    # are one file with two spellings, and resolve() returns whichever spelling it was
    # handed. Both passed is_file() and both were sent, once costing 41% of the whole
    # token budget on a single duplicated file.
    collected_paths: set[str] = set()

    # Detect extra entry scripts from package.json
    extra_scripts = _detect_scripts_from_package_json(root)
    # Everything below the root that the fixed candidate list cannot see.
    extra_scripts += [p for p in _paths_from_script_commands(root) if p not in extra_scripts]
    _discovered_infra = _discover_infra_files(root)
    _INFRA_READ_FULL.update(_discovered_infra)
    extra_scripts += [p for p in _discovered_infra if p not in extra_scripts]
    _imports = _local_imports_of(root, list(extra_scripts))
    _INFRA_READ_FULL.update(_imports)
    extra_scripts += [p for p in _imports if p not in extra_scripts]
    # Files discovered below the root are read further than a general source file. They are
    # small, and they are *data* — a compose file or a port table truncated halfway answers
    # the question wrongly rather than partially. Truncating topology.mjs at 100 lines left
    # 3 of 10 worker ports visible, which is how a port gets invented.

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
            try:
                st = fp.stat()
                real = f"{st.st_dev}:{st.st_ino}"
            except OSError:
                real = str(fp.resolve()) if fp.exists() else str(fp)
            if real in collected_paths:
                continue

            # Discovered infra/config data gets a wider window than ordinary source.
            lines_for_file = max_lines
            if name in _INFRA_READ_FULL or name.endswith((".yml", ".yaml", ".toml", ".jsonc")):
                lines_for_file = max(max_lines, 400)
            content, orig_lines, truncated = _read_file_truncated(fp, lines_for_file)
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
            collected_paths.add(real)
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
