"""Leitura da aba ``Input Sheet`` da planilha geral (somente leitura).

A planilha geral tem links externos para o SharePoint; ela é aberta com
``data_only=True`` (valores em cache) e nunca é salva por este módulo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import params as P
from ..values import Missing, Value, parse_cell

INPUT_SHEET = "Input Sheet"
FORWARD_FILL = ("well", "line_description", "inputs_sheet")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text)).strip() if text is not None else ""


@dataclass
class InputRow:
    excel_row: int
    meta: dict[str, Any]
    values: dict[str, Value]          # chave canônica -> valor
    unknown: dict[str, Any] = field(default_factory=dict)  # colunas sem parâmetro mapeado

    @property
    def id(self) -> str:
        return self.meta.get("id") or ""

    @property
    def operation_type(self) -> str:
        return self.meta.get("operation_type") or ""


@dataclass
class InputSheet:
    path: Path
    header_row: int
    columns: list[str]
    rows: list[InputRow]

    def ids(self) -> list[str]:
        return [r.id for r in self.rows]

    def find(self, case_id: str) -> InputRow:
        matches = [r for r in self.rows if r.id == case_id]
        if not matches:
            raise KeyError(f"ID {case_id!r} não encontrado na aba '{INPUT_SHEET}' de {self.path.name}")
        if len(matches) > 1:
            rows = ", ".join(str(r.excel_row) for r in matches)
            raise ValueError(f"ID {case_id!r} aparece {len(matches)} vezes (linhas {rows}). Corrija a planilha geral.")
        return matches[0]

    def unmapped_columns(self) -> list[str]:
        known = set(P.BY_COLUMN) | set(P.META_COLUMNS)
        return [c for c in self.columns if c and c not in known]


def read_input_sheet(path: Path | str, sheet: str = INPUT_SHEET) -> InputSheet:
    from openpyxl import load_workbook

    path = Path(path)
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise KeyError(f"Aba '{sheet}' não existe em {path.name}. Abas: {wb.sheetnames}")
        ws = wb[sheet]
        all_rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    header_idx = None
    for i, row in enumerate(all_rows[:10]):
        names = {_norm(c) for c in row}
        if "ID" in names and "Operation Type" in names:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Cabeçalho com 'ID' e 'Operation Type' não encontrado nas 10 primeiras linhas de '{sheet}'.")

    columns = [_norm(c) for c in all_rows[header_idx]]
    rows: list[InputRow] = []
    last_meta: dict[str, Any] = {}
    for offset, raw_row in enumerate(all_rows[header_idx + 1:], start=header_idx + 2):
        cells = dict(zip(columns, raw_row))
        case_id = _norm(cells.get("ID"))
        if not case_id:
            continue
        meta: dict[str, Any] = {}
        values: dict[str, Value] = {}
        unknown: dict[str, Any] = {}
        for col, raw in cells.items():
            if not col:
                continue
            if col in P.META_COLUMNS:
                meta[P.META_COLUMNS[col]] = _norm(raw) if raw is not None else None
            if col in P.BY_COLUMN:
                values[P.BY_COLUMN[col].key] = parse_cell(raw)
            elif col not in P.META_COLUMNS:
                unknown[col] = raw
        same_line = not meta.get("line_description") and bool(last_meta.get("line_description"))
        for key in FORWARD_FILL:  # células mescladas: herdam da linha anterior
            if not meta.get(key) and last_meta.get(key):
                meta[key] = last_meta[key]
        # Comprimento da linha também vem mesclado por linha (mesma descrição)
        if same_line and isinstance(values.get("line.total_length"), Missing) and rows:
            values["line.total_length"] = rows[-1].values.get("line.total_length", Missing("vazio"))
        last_meta = meta
        rows.append(InputRow(offset, meta, values, unknown))

    return InputSheet(path, header_idx + 1, columns, rows)


def describe(value: Value) -> str:
    if isinstance(value, Missing):
        return value.reason
    return str(value)
