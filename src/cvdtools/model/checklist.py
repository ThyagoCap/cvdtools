"""Checklist de verificação do modelo (plano, seção 2.6).

Fontes independentes, todas relidas do disco:

- coluna **Inputs Sheet**     <- planilha geral + configuração (relidas aqui, não reaproveita o builder)
- coluna **Orcaflex**         <- .dat do caso salvo pelo builder
- coluna **OrcaFlex Default** <- modelo base sem alterações

Expressões da aba ``Checklist`` da configuração:

Lado do input:   ``line.od`` | ``floaters.kps[0]`` | ``inverse(line.mbr)`` | ``diff(mcv.a, mcv.f)``
                 | ``sum(mcv.b, mcv.f)`` | ``first(br_b.length, br.length)`` | ``wet_weight(line.mass, line.od)``
                 | ``box_ixx(m, base, b, f)`` | ``box_iyy(m, g, base, b, f)`` | ``box_izz(m, g, base)``
Lado OrcaFlex:   ``{LINE_TYPE}.OD`` | ``{LINE}.Length[0]`` | ``Floater Link 1.EndAZ``
                 | ``wet_weight({LINE_TYPE})`` | ``submerged_mass({MCV})``
                 | ``angle_from_declination({LINE})`` | ``criterion(Axial Tension)``
                 | ``cog_x({MCV})`` (também ``cog_z``, ``cov_x``, ``cov_z``, ``inertia_x|y|z``)
                 | ``time_step({GENERAL})``

O status sugerido usa tolerância (o "=" exato do template antigo falha com
ponto flutuante). A coluna de aceite e o status final continuam manuais.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import logging

from .. import __version__
from ..log import log
from .._orcaflex import require_orcfxapi
from ..inputs import resolve_case
from ..io.config import load_config
from ..io.snapshot import file_info
from ..io.workbook import read_input_sheet
from ..schema import CaseInputs, ChecklistRow
from ..values import CurveRef, Missing, is_number
from .roles import resolve_name

SEAWATER_DENSITY = 1.025

OK, VERIFY, CHECK_DEFAULT, MISSING_INPUT, MANUAL = "OK", "VERIFY", "CHECK DEFAULT", "MISSING INPUT", "MANUAL"

HEADERS = ["Model Object", "Variable", "Units", "Inputs/TQF", "Inputs Sheet", "Orcaflex", "OrcaFlex Default",
           "Automatic Verification", "Verificado", "Status", "Comment", "Fonte do input", "Expressão OrcaFlex",
           "Tol. abs", "Tol. rel"]

_FUNC = re.compile(r"^\s*(\w+)\((.*)\)\s*$")
_ATTR = re.compile(r"^\s*(\{[A-Z0-9_]+\}|[^.\[\]]+?)\.(\w+)(?:\[(\d+)\])?\s*$")
_KEY = re.compile(r"^\s*([a-z_]+\.[a-z0-9_]+)(?:\[(\d+)\])?\s*$")
_SOURCE_LABEL = {"input_sheet": "Input Sheet", "override": "Override", "default": "Default",
                 "legacy_json": "JSON antigo", "missing": "-"}


@dataclass
class CheckResult:
    row: ChecklistRow
    input_value: Any
    input_source: str
    orcaflex_value: Any
    default_value: Any
    status: str
    comment: str = ""


# --------------------------------------------------------------------------- avaliação

def eval_input(expr: str, case: CaseInputs) -> tuple[Any, str]:
    """Devolve (valor, fonte)."""
    expr = expr.strip()
    if not expr:
        return Missing("sem input"), "-"
    if expr == "zero()":
        return 0.0, "biblioteca de curvas"
    m = _FUNC.match(expr)
    if m:
        fn, args = m.group(1), [a.strip() for a in m.group(2).split(",")]
        vals = [eval_input(a, case) for a in args]
        if fn == "first":   # primeiro valor definido (ex.: br_b.length; vazio = o da ponta A)
            for v, s in vals:
                if is_number(v):
                    return v, s
            return vals[0]
        nums = [v for v, _ in vals]
        src = ", ".join(sorted({s for _, s in vals}))
        bad = [v for v in nums if not is_number(v)]
        if bad:
            if fn == "winch_sections" and isinstance(bad[0], Missing):
                return Missing("premissa não definida"), src
            return bad[0], src
        if fn == "inverse":
            return 1.0 / nums[0], src
        if fn == "diff":
            return nums[0] - nums[1], src
        if fn == "sum":
            return float(sum(nums)), src
        if fn in ("box_ixx", "box_iyy", "box_izz"):
            from . import mcvprops

            return getattr(mcvprops, fn)(*nums), src
        if fn == "winch_sections":
            return (2.0 if nums[0] else 1.0), src
        if fn == "wet_weight":
            mass, od = nums
            return mass - math.pi * od ** 2 / 4 * SEAWATER_DENSITY, src
        raise ValueError(f"Função de input desconhecida: {fn}")
    m = _KEY.match(expr)
    if not m:
        raise ValueError(f"Expressão de input inválida: {expr!r}")
    key, idx = m.group(1), m.group(2)
    rv = case.values.get(key)
    value = case.get(key)
    if idx is not None:
        value = value[int(idx)] if isinstance(value, list) and int(idx) < len(value) else Missing("sem item")
    return value, _SOURCE_LABEL.get(rv.source, rv.source) if rv else "-"


def _attr(model, ref: str, roles: dict[str, str]) -> Any:
    m = _ATTR.match(ref)
    if not m:
        raise ValueError(f"Referência OrcaFlex inválida: {ref!r}")
    name, prop, idx = resolve_name(m.group(1), roles), m.group(2), m.group(3)
    value = getattr(model[name], prop)
    if idx is not None:
        value = value[int(idx)]
    return value


def eval_orcaflex(expr: str, model, case: CaseInputs) -> Any:
    expr = expr.strip()
    if not expr:
        return None
    m = _FUNC.match(expr)
    if m:
        fn, arg = m.group(1), m.group(2).strip()
        if fn == "curve_deviation":
            return _curve_deviation(model, case, arg)
        if fn == "criterion":
            for c in case.criteria:
                if c.name == arg:
                    return c.limit if is_number(c.limit) else None
            return None
        if model is None:
            return None
        obj = model[resolve_name(arg, case.roles)]
        if fn == "wet_weight":
            return obj.MassPerUnitLength - math.pi * obj.OD ** 2 / 4 * SEAWATER_DENSITY
        if fn == "submerged_mass":
            return obj.Mass - SEAWATER_DENSITY * obj.Volume
        if fn == "angle_from_declination":
            return 180.0 - obj.EndADeclination   # gooseneck: declinação = 180° − ângulo
        if fn == "section_count":
            return float(len(obj.Length))
        if fn == "line_length":
            return float(sum(obj.Length))
        if fn[:4] in ("cog_", "cov_") or fn.startswith("inertia_"):
            from .mcvprops import get_vector

            kind, axis = fn.rsplit("_", 1)
            vec = get_vector(obj, kind)
            if vec is None:
                raise ValueError(f"{kind} não encontrado no objeto (nome do dado)")
            return vec["xyz".index(axis)]
        if fn == "time_step":
            variable = str(getattr(obj, "ImplicitUseVariableTimeStep", "No")).lower() in ("yes", "true", "1")
            return obj.ImplicitVariableMaxTimeStep if variable else obj.ImplicitConstantTimeStep
        if fn == "modelled_ends":
            free = {"anchored", "fixed", "free"}
            a = str(obj.EndAConnection).lower() not in free
            b = str(obj.EndBConnection).lower() not in free
            return float(a + b)
        raise ValueError(f"Função OrcaFlex desconhecida: {fn}")
    if model is None:
        return None
    return _attr(model, expr, case.roles)


def _curve_deviation(model, case: CaseInputs, key: str) -> Optional[float]:
    """Maior diferença relativa entre a tabela gravada no modelo e a curva da biblioteca (0 = idênticas)."""
    curve = case.get(key)
    if model is None or not hasattr(curve, "si"):
        return None
    obj = model[curve.name]
    mx = [float(v) for v in obj.IndependentValue]
    my = [float(v) for v in obj.DependentValue]
    x, y = curve.si()
    if len(mx) != len(x):
        return float("inf")
    scale_x = max(abs(v) for v in x) or 1.0
    scale_y = max(abs(v) for v in y) or 1.0
    return max([abs(a - b) / scale_x for a, b in zip(mx, x)] + [abs(a - b) / scale_y for a, b in zip(my, y)])


def _close(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(a - b) <= abs_tol + rel_tol * max(abs(a), abs(b))


def classify(row: ChecklistRow, inp: Any, orc_val: Any, default: Any) -> tuple[str, str]:
    if isinstance(inp, Missing):
        return MISSING_INPUT, f"Input {inp.reason}"
    if orc_val is None:
        return MANUAL, "Sem valor no OrcaFlex para comparar"
    if isinstance(inp, CurveRef):
        return MANUAL, f"Input aponta para curva externa não cadastrada ({inp.text}); conferir a curva '{orc_val}' no modelo"
    if hasattr(inp, "si"):  # curva da biblioteca: o tipo de linha tem de apontar para ela pelo nome
        if str(orc_val).strip() == inp.name:
            return OK, f"Curva {inp.id} (load case: {inp.load_case or '-'}); tabela conferida na linha 'Curvas'"
        return VERIFY, f"Esperada a curva '{inp.name}', modelo usa '{orc_val}'"
    if isinstance(orc_val, float) and orc_val == float("inf"):
        return VERIFY, "Número de pontos da curva no modelo difere da biblioteca"
    if not (is_number(inp) and is_number(orc_val)):
        if str(inp).strip() == str(orc_val).strip():
            return OK, ""
        return MANUAL, "Valores não numéricos: conferir visualmente"
    if _close(float(inp), float(orc_val), row.abs_tol, row.rel_tol):
        return OK, ""
    if is_number(default) and _close(float(orc_val), float(default), row.abs_tol, row.rel_tol):
        return CHECK_DEFAULT, "OrcaFlex igual ao modelo base: a propriedade provavelmente não foi escrita"
    return VERIFY, f"Diferença {float(orc_val) - float(inp):+.6g}"


def _safe(fn, *args):
    try:
        return fn(*args), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _rows_for_case(cfg_rows: list[ChecklistRow], case: CaseInputs) -> list[ChecklistRow]:
    rows = []
    has_floaters = isinstance(case.get("floaters.kps"), list) and len(case.get("floaters.kps")) > 0
    for r in cfg_rows:
        if r.applies_to == "camelback" and not case.operation.camelback:
            continue
        if r.applies_to == "winch_extension" and not case.flag("model.winch_extension"):
            continue
        if r.applies_to == "floaters" and not has_floaters:
            continue
        if r.applies_to == "two_ends" and case.modelled_ends() != 2:
            continue
        rows.append(r)
    for key, curve in case.curves().items():
        rows.append(ChecklistRow("Curvas", f"{curve.name} ← {key}", "-", "zero()", f"curve_deviation({key})",
                                 1e-9, 0, "all"))
    if has_floaters:
        for i in range(len(case.get("floaters.kps"))):
            n = i + 1
            rows += [
                ChecklistRow(f"Floater {n}", "KP", "m", f"floaters.kps[{i}]", f"Floater Link {n}.EndAZ", 1e-4, 0, "floaters"),
                ChecklistRow(f"Floater {n}", "Sling Length", "m", f"floaters.sling_length[{i}]",
                             f"Floater Link {n}.UnstretchedLength", 1e-4, 0, "floaters"),
                ChecklistRow(f"Floater {n}", "Sling Stiffness", "kN", f"floaters.sling_stiffness[{i}]",
                             f"Floater Link {n}.Stiffness", 1e-3, 1e-6, "floaters"),
            ]
    return rows


def evaluate(case: CaseInputs, cfg_rows: list[ChecklistRow], case_model, base_model) -> list[CheckResult]:
    results = []
    for row in _rows_for_case(cfg_rows, case):
        (inp_src), err_i = _safe(eval_input, row.input_expr, case)
        inp, src = inp_src if inp_src else (Missing("erro"), "-")
        orc_val, err_o = _safe(eval_orcaflex, row.orcaflex_expr, case_model, case)
        default, _ = _safe(eval_orcaflex, row.orcaflex_expr, base_model, case)
        if row.orcaflex_expr.strip().startswith("criterion("):
            default = None
        status, comment = classify(row, inp, orc_val, default)
        notes = [c for c in (comment, err_i and f"input: {err_i}", err_o and f"OrcaFlex: {err_o}") if c]
        results.append(CheckResult(row, inp, src, orc_val, default, status, "; ".join(notes)))
    return results


# --------------------------------------------------------------------------- escrita

def _cell(v):
    if v is None:
        return None
    if isinstance(v, Missing):
        return v.reason
    if isinstance(v, CurveRef):
        return v.text
    if hasattr(v, "si"):
        return f"curva:{v.id} → {v.name}"
    if is_number(v):
        return float(v)
    return str(v)


def next_free_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 1
    while True:
        cand = path.with_name(f"{path.stem}_rev{n}{path.suffix}")
        if not cand.exists():
            return cand
        n += 1


def write_checklist(results: list[CheckResult], case: CaseInputs, out_path: Path, info: dict) -> Path:
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    out_path = next_free_path(Path(out_path))
    wb = Workbook()
    ws = wb.active
    ws.title = "CheckList"
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="305496")
        c.alignment = Alignment(wrap_text=True, vertical="center")

    dv = DataValidation(type="list", formula1='"TRUE,FALSE"', allow_blank=False)
    ws.add_data_validation(dv)

    group_start: dict[str, int] = {}
    prev_group = None
    for i, res in enumerate(results, start=2):
        r = res.row
        numeric = is_number(res.input_value) and is_number(res.orcaflex_value)
        ws.append([
            r.group if r.group != prev_group else None, r.variable, r.unit, None,
            _cell(res.input_value), _cell(res.orcaflex_value), _cell(res.default_value),
            None, False, f'=IF(I{i},"OK","NOK")', res.comment, res.input_source,
            r.orcaflex_expr, r.abs_tol, r.rel_tol,
        ])
        if numeric:
            ws[f"H{i}"] = (
                f'=IF(OR(E{i}="",F{i}=""),"{MISSING_INPUT}",'
                f'IF(ABS(E{i}-F{i})<=N{i}+O{i}*MAX(ABS(E{i}),ABS(F{i})),"{OK}",'
                f'IF(AND(ISNUMBER(G{i}),ABS(F{i}-G{i})<=N{i}+O{i}*MAX(ABS(F{i}),ABS(G{i}))),"{CHECK_DEFAULT}","{VERIFY}")))'
            )
        else:
            ws[f"H{i}"] = res.status
        dv.add(ws[f"I{i}"])
        if r.group != prev_group:
            group_start[r.group] = i
        prev_group = r.group

    last = len(results) + 1
    rows_by_group = Counter(res.row.group for res in results)
    for g, start in group_start.items():
        end = start + rows_by_group[g] - 1
        if end > start:
            ws.merge_cells(start_row=start, start_column=1, end_row=end, end_column=1)
        ws.cell(start, 1).alignment = Alignment(vertical="center", wrap_text=True)

    colors = {OK: "C6EFCE", VERIFY: "FFC7CE", CHECK_DEFAULT: "F8CBAD", MISSING_INPUT: "FFEB9C", MANUAL: "BDD7EE"}
    for status, color in colors.items():
        ws.conditional_formatting.add(f"H2:H{last}", CellIsRule(operator="equal", formula=[f'"{status}"'],
                                                                fill=PatternFill("solid", fgColor=color)))
    for status, color in (("OK", "C6EFCE"), ("NOK", "FFC7CE")):
        ws.conditional_formatting.add(f"J2:J{last}", CellIsRule(operator="equal", formula=[f'"{status}"'],
                                                                fill=PatternFill("solid", fgColor=color)))
    widths = [18, 26, 9, 12, 16, 16, 16, 20, 11, 9, 60, 14, 34, 9, 9]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(1, idx).column_letter].width = w
    ws.freeze_panes = "C2"

    info_ws = wb.create_sheet("Info")
    counts = Counter(r.status for r in results)
    lines = [
        ("ID", case.case_id), ("Operation Type", case.operation.operation_type),
        ("Operation name", case.operation_name), ("Gerado em", _dt.datetime.now().isoformat(timespec="seconds")),
        ("cvdtools", __version__),
        *[(f"Status sugerido: {k}", counts.get(k, 0)) for k in (OK, VERIFY, CHECK_DEFAULT, MISSING_INPUT, MANUAL)],
        *[(f"Premissa: {name}", f"{value}  [{source}]") for name, value, source in case.premises()],
    ]
    for label, data in info.items():
        if isinstance(data, dict):
            for k, v in data.items():
                lines.append((f"{label} - {k}", v))
        else:
            lines.append((label, data))
    for label, value in lines:
        info_ws.append([label, str(value) if value is not None else ""])
    info_ws.column_dimensions["A"].width = 32
    info_ws.column_dimensions["B"].width = 120

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


# --------------------------------------------------------------------------- orquestração

def build_checklist(workbook: Path, config: Path, case_id: str, case_model: Path, base_model: Path,
                    out_path: Path, project_code: str = "", curve_files: Optional[list] = None
                    ) -> tuple[Path, list[CheckResult]]:
    """Relê planilha, configuração, curvas e os dois modelos do disco e grava o checklist."""
    from ..curves import CurveLibrary

    orc = require_orcfxapi()
    log.debug("  Checklist: relendo planilha geral, configuração e curvas")
    cfg = load_config(config)
    library = CurveLibrary.load([Path(p) for p in curve_files], Path(curve_files[0]).parent) if curve_files else None
    case = resolve_case(read_input_sheet(workbook), cfg, case_id, project_code, library)
    log.debug("  Checklist: abrindo %s e %s", Path(case_model).name, Path(base_model).name)
    model = orc.Model(str(case_model))
    base = orc.Model(str(base_model))
    results = evaluate(case, cfg.checklist, model, base)
    for r in results:
        lvl = logging.DEBUG if r.status in (OK, MISSING_INPUT, MANUAL) else logging.WARNING
        log.log(lvl, "  %-13s %-36s %s", r.status, f"{r.row.group}/{r.row.variable}", r.comment)
    info = {
        "Planilha geral": file_info(workbook), "Configuração": file_info(config),
        "Modelo do caso": file_info(case_model), "Modelo base": file_info(base_model),
        **{f"Curvas ({Path(p).name})": file_info(Path(p)) for p in (curve_files or [])},
    }
    return write_checklist(results, case, out_path, info), results
