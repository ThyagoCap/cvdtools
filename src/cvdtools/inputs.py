"""Resolução de um caso: Input Sheet + configuração -> :class:`CaseInputs`."""

from __future__ import annotations

import re
from typing import Optional

from . import params as P
from .io.config import ProjectConfig
from .io.workbook import InputRow, InputSheet
from .model.roles import resolve_name
from .schema import CaseInputs, Criterion, CriterionSpec, PostItem, ResolvedValue
from .values import CurveRef, Missing, Value, is_number


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", text.strip()).strip("_")


def resolve_value(key: str, row: Optional[InputRow], cfg: ProjectConfig, case_id: str) -> ResolvedValue:
    raw_input = row.values.get(key) if row is not None else None
    override = cfg.overrides.get(case_id, {}).get(key)
    if override is not None and not isinstance(override, Missing):
        return ResolvedValue(override, "override", raw_input)
    if raw_input is not None and not isinstance(raw_input, Missing):
        return ResolvedValue(raw_input, "input_sheet", raw_input)
    default = cfg.defaults.get(key)
    if default is None and P.get(key).default is not None:
        default = P.get(key).default
    if default is not None and not isinstance(default, Missing):
        return ResolvedValue(default, "default", raw_input)
    return ResolvedValue(raw_input if raw_input is not None else Missing("não definido"), "missing", raw_input)


MCV_B_FROM_ROW = ("angle", "a", "b", "c", "d", "e", "f", "g", "mass_air", "mass_sub", "volume")
END_B_FROM_ROW = {"ef_b.length": "ef.length", "br_b.length": "br.length"}   # vazio = mesmo da ponta A


def resolve_end_b(sheet: InputSheet, cfg: ProjectConfig, case: CaseInputs) -> None:
    """Duas extremidades: o MCV da extremidade B vem da linha ``model.end_b_case_id`` da Input Sheet.

    Overrides ``mcv_b.*`` do próprio caso continuam valendo por cima.
    """
    other = case.get("model.end_b_case_id")
    if not isinstance(other, str) or not other.strip():
        return
    try:
        row_b = sheet.find(other.strip())
    except (KeyError, ValueError) as exc:
        for k in MCV_B_FROM_ROW:
            if case.values[f"mcv_b.{k}"].source != "override":
                case.values[f"mcv_b.{k}"] = ResolvedValue(Missing(f"ID da outra extremidade inválido: {exc}"), "missing")
        return
    for k in MCV_B_FROM_ROW:
        if case.values[f"mcv_b.{k}"].source == "override":
            continue
        v = row_b.values.get(f"mcv.{k}", Missing("vazio"))
        case.values[f"mcv_b.{k}"] = ResolvedValue(v, f"input_sheet:{row_b.id}" if not isinstance(v, Missing) else "missing", v)
    for dst, src in END_B_FROM_ROW.items():
        if dst not in case.values or case.values[dst].source == "override":
            continue
        v = row_b.values.get(src, Missing("vazio"))
        if not isinstance(v, Missing):
            case.values[dst] = ResolvedValue(v, f"input_sheet:{row_b.id}", v)


def resolve_criteria(specs: list[CriterionSpec], case: CaseInputs) -> list[Criterion]:
    out = []
    for s in specs:
        if s.limit_source:
            v: Value = case.get(s.limit_source)
            origin = f"{s.limit_source} ({case.values[s.limit_source].source})" if s.limit_source in case.values else s.limit_source
            if is_number(v) and s.transform == "inverse":
                v = 1.0 / v
                origin = f"1/{origin}"
        elif s.limit is not None:
            v, origin = float(s.limit), "config"
        else:
            v, origin = Missing("sem limite"), "config"
        out.append(Criterion(s.name, s.type, resolve_name(s.obj, case.roles), s.variable, s.extra,
                             v, origin, s.static, s.dynamic))
    return out


def resolve_postproc(items: list[PostItem], roles: dict[str, str]) -> list[PostItem]:
    return [PostItem(i.label, i.method, resolve_name(i.obj, roles), i.variable, i.extra, i.period,
                     i.template, i.unit) for i in items]


def resolve_case(sheet: InputSheet, cfg: ProjectConfig, case_id: str, project_code: str = "",
                 curves=None) -> CaseInputs:
    row = sheet.find(case_id)
    op = cfg.operation(row.operation_type)
    name = slug(case_id)
    case = CaseInputs(case_id=case_id, operation=op, meta=dict(row.meta), roles=dict(cfg.roles),
                      operation_name=f"{project_code}.{name}" if project_code else name)
    case.meta["excel_row"] = row.excel_row
    for p in P.PARAMS:
        case.values[p.key] = resolve_value(p.key, row, cfg, case_id)
    if case.modelled_ends() == 2:
        resolve_end_b(sheet, cfg, case)
    resolve_curves(case, curves)
    case.criteria = resolve_criteria(cfg.criteria.get(op.template, []), case)
    case.postproc = resolve_postproc(cfg.postproc.get(op.template, []), case.roles)
    return case


def is_usable(key: str, v: Value) -> bool:
    kind = P.get(key).kind
    if kind == P.LIST:
        return isinstance(v, list)
    if kind == P.CURVE_OR_NUMBER:
        return is_number(v) or (isinstance(v, str) and bool(v)) or hasattr(v, "si")
    if isinstance(v, CurveRef):
        return False
    return is_number(v)


def resolve_curves(case: CaseInputs, library) -> None:
    """Troca referências a curvas externas por objetos Curve da biblioteca.

    Falhas ficam em ``case.meta['curve_errors']`` (a validação transforma em ERRO).
    """
    from .curves import PARAM_KIND, CurveLibrary

    errors = case.meta.setdefault("curve_errors", {})
    for p in P.PARAMS:
        if p.kind != P.CURVE_OR_NUMBER:
            continue
        rv = case.values.get(p.key)
        if rv is None:
            continue
        v = rv.value
        ref = v.text if isinstance(v, CurveRef) else (v if isinstance(v, str) and CurveLibrary.looks_like_ref(v) else None)
        if ref is None:
            continue
        if library is None:
            errors[p.key] = f"'{ref}' é uma curva externa, mas o projeto não tem biblioteca de curvas (cvd_curves.xlsx)"
            continue
        try:
            curve = library.lookup(ref)
        except KeyError as exc:
            errors[p.key] = str(exc).strip('"')
            continue
        expected = PARAM_KIND.get(p.key)
        if expected and curve.kind != expected:
            errors[p.key] = f"Curva {curve.id} é do tipo '{curve.kind}', mas {p.key} exige '{expected}'"
            continue
        case.values[p.key] = ResolvedValue(curve, rv.source, rv.raw_input)
