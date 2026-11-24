"""Conversão entre o ``__INPUTS.json`` antigo e o caso resolvido.

- ``case_from_legacy_json``: monta um :class:`CaseInputs` a partir do JSON antigo
  (usado na regressão: o builder novo precisa reproduzir o modelo da pipeline antiga).
- ``case_to_legacy_json``: gera um JSON no formato antigo a partir de um caso
  resolvido da planilha, para que os scripts antigos (_03 em diante) continuem
  rodando durante a transição.
- ``diff_case_vs_legacy``: lista o que mudou entre a planilha e um JSON antigo.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import params as P
from ..schema import CaseInputs, Criterion, OperationSpec, PostItem, ResolvedValue
from ..values import CurveRef, Missing, is_number, parse_cell, parse_list, to_jsonable
from .config import DEFAULT_ROLES

LEGACY_UNITS = {
    "Length": "m", "Diameter": "m", "Radius": "m", "SeabedStiffness": "kN/m/m²", "Angle": "deg",
    "Mass": "t", "Volume": "m³", "MassPerLength": "t/m", "AxialStiffness": "kN",
    "FlexionalStiffness": "kn.m² or Curve Name", "TorsionalStiffness": "kn.m²",
}


def case_from_legacy_json(
    inputs: dict,
    operation: Optional[OperationSpec] = None,
    roles: Optional[dict[str, str]] = None,
) -> CaseInputs:
    operation = operation or OperationSpec("2nd end CVD", "camelback", camelback=True,
                                           base_model="00.Base/base_input.dat", supported=True)
    roles = dict(roles or {r: n for r, n, _ in DEFAULT_ROLES})
    case = CaseInputs(case_id=inputs.get("OperationName", "legacy"), operation=operation,
                      roles=roles, operation_name=inputs.get("OperationName", "legacy"))
    for p in P.PARAMS:
        raw: Any = None
        found = False
        if p.legacy and p.legacy[1] in inputs.get(p.legacy[0], {}):
            raw = inputs[p.legacy[0]][p.legacy[1]]
            found = True
        elif p.default is not None:
            raw = p.default
        if not found and raw is None:
            continue
        if p.kind == P.LIST:
            value = parse_list(raw)
        elif p.kind == P.BOOL:
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                value = 1.0 if float(raw) else 0.0     # default 1.0 era lido como "não"
            else:
                value = 1.0 if str(raw).strip().lower() in ("true", "1", "1.0", "s", "sim", "yes") else 0.0
        elif p.kind == P.TEXT:
            value = str(raw)
        else:
            value = parse_cell(raw)
            if p.kind == P.CURVE_OR_NUMBER and isinstance(value, CurveRef):
                value = value.text  # no JSON antigo, texto é o nome da curva no modelo
        case.values[p.key] = ResolvedValue(value, "legacy_json" if found else "default")

    for name, c in inputs.get("acceptanceCriteria", {}).items():
        case.criteria.append(Criterion(name, c["Type"], c["OrcaFlex Parent"], c["OrcaFlex Variable Name"],
                                       c["OrcaFlex Extra"], float(c["Limit"]), "legacy_json"))
    for label, it in inputs.get("postProcess", {}).items():
        case.postproc.append(PostItem(label, it["OrcaFlex Method"], it["OrcaFlex Parent"],
                                      it["OrcaFlex Variable Name"], it["OrcaFlex Extra"],
                                      it.get("Simulation Period", "")))
    case.meta["legacy_simulation_data"] = inputs.get("SimulationData", {})
    return case


def case_to_legacy_json(case: CaseInputs) -> dict:
    """JSON no formato do __INPUTS.json antigo (valores ausentes ficam como texto <N/I>)."""
    out: dict[str, Any] = {"OperationName": case.operation_name, "Units": dict(LEGACY_UNITS)}
    sim = dict(case.meta.get("legacy_simulation_data", {}))
    for p in P.PARAMS:
        if not p.legacy:
            continue
        section, fld = p.legacy
        v = case.get(p.key)
        if section == "SimulationData" and p.kind == P.BOOL:
            v = "True" if is_number(v) and v else "False"
        elif isinstance(v, Missing):
            v = f"<{v.reason}>"
        elif not is_number(v) and not isinstance(v, list):
            v = str(v)
        (sim if section == "SimulationData" else out.setdefault(section, {}))[fld] = v
    out["SimulationData"] = sim
    out["acceptanceCriteria"] = {c.name: c.to_legacy() for c in case.criteria if c.static}
    out["postProcess"] = {
        i.label: {"OrcaFlex Method": i.method, "OrcaFlex Parent": i.obj, "OrcaFlex Variable Name": i.variable,
                  "OrcaFlex Extra": i.extra, "Simulation Period": i.period}
        for i in case.postproc
    }
    return out


def diff_case_vs_legacy(case: CaseInputs, legacy_inputs: dict, rel_tol: float = 1e-6) -> list[dict]:
    """Parâmetros cujo valor resolvido difere do JSON antigo."""
    rows = []
    for p in P.PARAMS:
        if not p.legacy or p.legacy[1] not in legacy_inputs.get(p.legacy[0], {}):
            continue
        old = legacy_inputs[p.legacy[0]][p.legacy[1]]
        new = case.get(p.key)
        same = False
        if p.kind == P.BOOL:
            old_b = str(old).strip().lower() in ("true", "1", "s", "sim", "yes")
            same = is_number(new) and bool(new) == old_b
        elif is_number(new) and isinstance(old, (int, float)):
            same = abs(new - old) <= rel_tol * max(abs(new), abs(old), 1e-12)
        elif isinstance(new, list) and isinstance(old, list):
            same = [float(x) for x in old] == new
        else:
            same = str(old) == str(new)
        if not same:
            rv = case.values.get(p.key)
            rows.append({"parametro": p.key, "descricao": p.description, "unidade": p.unit,
                         "json_antigo": old, "planilha": to_jsonable(new),
                         "fonte": rv.source if rv else "-"})
    return rows
