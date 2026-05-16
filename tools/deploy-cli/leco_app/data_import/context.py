"""Import execution context shared by importers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


LogFn = Callable[[str], None]


@dataclass
class ImportContext:
    slug: str
    manifest_path: Path
    data_dir: Path
    compose_root: Path
    compose_tail: list[str]
    services: dict[str, dict[str, Any]]
    compose_ps: list[dict[str, Any]]
    local_cf: dict[str, Any]
    reimport: bool = False
    dry_run: bool = False
    _log: LogFn | None = field(default=None, repr=False)

    def log(self, text: str) -> None:
        msg = text if text.endswith("\n") else text + "\n"
        if self._log:
            self._log(msg)

    def container_for_service(self, service_name: str) -> str:
        for row in self.compose_ps:
            if str(row.get("service") or "") == service_name:
                c = str(row.get("container") or "").strip()
                if c:
                    return c
        spec = self.services.get(service_name) or {}
        return str(spec.get("container_name") or f"{self.slug}-{service_name}")
