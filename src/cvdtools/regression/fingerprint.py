"""Impressão digital de modelos OrcaFlex.

O modelo é convertido para o formato texto do OrcaFlex (.yml) e lido como
dicionário. Assim dois modelos podem ser comparados propriedade a propriedade,
com tolerância numérica, sem depender de quais propriedades o código mexe.

Arquivos .yml podem ser lidos sem OrcaFlex; .dat e .sim exigem OrcFxAPI.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from .._orcaflex import require_orcfxapi

YAML_SUFFIXES = {".yml", ".yaml"}


def load_yaml_text(text: str) -> dict:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("O arquivo não parece um modelo OrcaFlex em texto (raiz não é um dicionário).")
    return data


def model_to_yaml_text(model_or_path: Any) -> str:
    """Devolve o texto .yml do modelo (aceita caminho ou objeto OrcFxAPI.Model)."""
    if isinstance(model_or_path, (str, Path)):
        path = Path(model_or_path)
        if path.suffix.lower() in YAML_SUFFIXES:
            return path.read_text(encoding="utf-8-sig")
        orc = require_orcfxapi()
        model = orc.Model(str(path))
    else:
        model = model_or_path

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "model.yml"
        model.SaveData(str(out))
        return out.read_text(encoding="utf-8-sig")


def model_to_dict(model_or_path: Any) -> dict:
    return load_yaml_text(model_to_yaml_text(model_or_path))


def save_model_yaml(model_or_path: Any, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(model_to_yaml_text(model_or_path), encoding="utf-8")
    return out_path
