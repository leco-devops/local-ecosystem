"""Run lifecycle hooks from merged localhost profile."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from leco_app.schema import LifecycleStep, load_merged_manifest

HookPhase = Literal["prepare", "build", "preStart"]


def _steps_for_phase(merged, phase: HookPhase) -> list[LifecycleStep]:
    lc = merged.localhost.lifecycle
    if phase == "prepare":
        return list(lc.prepare)
    if phase == "build":
        return list(lc.build)
    return list(lc.pre_start)


def run_hooks_phase(
    manifest_path: Path,
    phase: HookPhase,
    *,
    echo: Callable[[str], None] = print,
) -> int:
    """Execute all steps for the phase; cwd relative to app root. Returns 0 if all steps exit 0."""
    mp = manifest_path.resolve()
    merged = load_merged_manifest(mp)
    app_root = merged.manifest.resolved_root(mp)
    steps = _steps_for_phase(merged, phase)
    if not steps:
        echo(f"No {phase} hooks defined in localhost profile.")
        return 0
    for i, step in enumerate(steps, 1):
        cwd = (app_root / step.cwd).resolve() if step.cwd else app_root
        if not cwd.is_dir():
            echo(f"Hook {i}/{len(steps)}: cwd not a directory: {cwd}")
            return 1
        echo(f"→ [{phase}] ({i}/{len(steps)}) {step.command}")
        try:
            if step.shell:
                r = subprocess.run(
                    step.command,
                    shell=True,
                    cwd=cwd,
                    timeout=step.timeout_sec,
                    check=False,
                )
            else:
                r = subprocess.run(
                    shlex.split(step.command),
                    cwd=cwd,
                    timeout=step.timeout_sec,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            echo(f"Hook {i} timed out after {step.timeout_sec}s")
            return 1
        except OSError as e:
            echo(f"Hook {i} failed to start: {e}")
            return 1
        if r.returncode != 0:
            echo(f"Hook {i} exited with code {r.returncode}")
            return r.returncode
    return 0
