"""Extract uploaded zip into hosting/app-available/<slug>/ (zip-slip safe); remove zip after success."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any

from werkzeug.datastructures import FileStorage

from leco_detect import require_registration_app_id

MAX_ZIP_BYTES = 200 * 1024 * 1024
ZIP_TMP_NAME = ".leco-upload.zip.tmp"


def _extract_zip_safe(zf: zipfile.ZipFile, dest: Path) -> int:
    """Extract members under dest; skip directories. Returns file count."""
    dest_resolved = dest.resolve()
    dest_resolved.mkdir(parents=True, exist_ok=True)
    n = 0
    for info in zf.infolist():
        name = info.filename
        if not name or name.endswith("/"):
            continue
        p = Path(name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe zip entry: {name!r}")
        out = (dest_resolved / name).resolve()
        try:
            out.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValueError(f"zip slip: {name!r}") from exc
        out.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        n += 1
    return n


def host_zip_upload(eco_root: Path, app_id_raw: str, file_storage: FileStorage | None) -> dict[str, Any]:
    aid = require_registration_app_id(app_id_raw or "")
    if file_storage is None or not file_storage.filename:
        raise ValueError("zip file required")

    dest = eco_root / "hosting" / "app-available" / aid
    dest.mkdir(parents=True, exist_ok=True)
    tmp_zip = dest / ZIP_TMP_NAME

    try:
        file_storage.save(tmp_zip)
        sz = tmp_zip.stat().st_size
        if sz > MAX_ZIP_BYTES:
            raise ValueError(f"zip exceeds limit ({MAX_ZIP_BYTES} bytes)")
        if sz == 0:
            raise ValueError("empty zip")

        with zipfile.ZipFile(tmp_zip, "r") as zf:
            n = _extract_zip_safe(zf, dest)
    finally:
        tmp_zip.unlink(missing_ok=True)

    return {
        "ok": True,
        "slug": aid,
        "extracted_to": str(dest.resolve()),
        "files_extracted": n,
    }
