"""Snapshot do caso resolvido (``case.json``) com rastreabilidade das fontes."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Optional

from .. import __version__
from ..regression.capture import sha256
from ..schema import CaseInputs


def file_info(path: Optional[Path]) -> Optional[dict]:
    if path is None:
        return None
    path = Path(path)
    if not path.is_file():
        return {"path": str(path), "exists": False}
    st = path.stat()
    return {"path": str(path.resolve()), "sha256": sha256(path),
            "modified": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


def write_snapshot(case: CaseInputs, out_dir: Path, workbook: Optional[Path] = None,
                   config: Optional[Path] = None, extra: Optional[dict] = None,
                   filename: str = "case.json") -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "cvdtools_version": __version__,
        "sources": {"workbook": file_info(workbook), "config": file_info(config)},
        **case.to_dict(),
    }
    if extra:
        data["extra"] = extra
    path = out_dir / filename
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path
