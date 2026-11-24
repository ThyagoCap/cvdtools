"""Planilha geral sintética completa, gerada a partir do JSON antigo do GLF-49."""

from openpyxl import Workbook

from cvdtools import params as P
from cvdtools.io.legacy import case_from_legacy_json

CASE_ID = "GLF49 2CVD MOBO New In"


def full_workbook(path, legacy_inputs, overrides=None, case_id=CASE_ID, op="2nd end CVD"):
    case = case_from_legacy_json(legacy_inputs)
    meta = {"Item": 1, "Well": "GLF-49", "Line Description": "Flow", "Inputs sheet": "INPUTS",
            "Operation Type": op, "Location": "MOBO New In", "ID": case_id, "Folder": "02.CVD2-MOBO In"}
    cols = list(meta) + [p.input_column for p in P.PARAMS if p.input_column]
    row = []
    for c in cols:
        if c in meta:
            row.append(meta[c])
            continue
        key = P.BY_COLUMN[c].key
        if overrides and key in overrides:
            row.append(overrides[key])
            continue
        v = case.get(key)
        row.append(v if isinstance(v, (int, float, str)) else "N/I")
    wb = Workbook()
    ws = wb.active
    ws.title = "Input Sheet"
    ws.append([None])
    ws.append(cols)
    ws.append(row)
    wb.save(path)
    return path
