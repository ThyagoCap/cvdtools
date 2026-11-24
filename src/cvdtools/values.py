"""Valores especiais que aparecem nas planilhas de input.

Na planilha geral uma célula pode conter, além de números:

- ``N/I``, ``-``, ``?`` ou vazio  -> valor não informado (:class:`Missing`)
- ``Script``                      -> valor vem do script do fabricante (:class:`Missing`, motivo "Script")
- ``Histerese - WSI ... [15]``    -> referência a uma curva em outro arquivo (:class:`CurveRef`)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Union

MISSING_TOKENS = {"n/i": "N/I", "-": "-", "?": "?", "": "vazio", "script": "Script", "no data": "No data"}


@dataclass(frozen=True)
class Missing:
    reason: str = "vazio"

    def __bool__(self) -> bool:  # permite "if valor:" tratar Missing como ausente
        return False

    def __str__(self) -> str:
        return f"<{self.reason}>"


@dataclass(frozen=True)
class CurveRef:
    """Texto que aponta para uma curva (histerese, rigidez não linear etc.)."""

    text: str

    def __str__(self) -> str:
        return f"curva: {self.text}"


Value = Union[float, str, list, Missing, CurveRef]

_CURVE_HINT = re.compile(r"(histerese|hysteresis|stiffness|curve|curva|\.xlsx|\[\d+\])", re.IGNORECASE)
_NUM = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")


def parse_cell(raw: Any) -> Value:
    """Converte o conteúdo bruto de uma célula num valor da pipeline."""
    if raw is None:
        return Missing("vazio")
    if isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    key = text.lower()
    if key in MISSING_TOKENS:
        return Missing(MISSING_TOKENS[key])
    if _NUM.match(text):
        return float(text.replace(",", "."))
    if _CURVE_HINT.search(text):
        return CurveRef(text)
    return text


def parse_list(raw: Any) -> list[float] | Missing:
    """Lista separada por ';' (ex.: KPs de flutuadores). Vazio -> lista vazia."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return []
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    if isinstance(raw, (int, float)):
        return [float(raw)]
    text = str(raw).strip()
    if text.lower() in MISSING_TOKENS:
        return Missing(MISSING_TOKENS[text.lower()])
    return [float(p.replace(",", ".")) for p in text.split(";") if p.strip()]


def is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def to_jsonable(v: Any) -> Any:
    if hasattr(v, "to_json"):
        return v.to_json()
    if isinstance(v, Missing):
        return {"missing": v.reason}
    if isinstance(v, CurveRef):
        return {"curve": v.text}
    return v
