"""Validação da planilha geral e dos casos resolvidos.

Nada é escrito na planilha geral: o resultado é uma lista de :class:`Issue`
que pode ser impressa ou salva num relatório separado (CSV/XLSX).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from . import params as P
from .inputs import is_usable, resolve_case
from .io.config import ProjectConfig
from .io.workbook import InputSheet
from .values import CurveRef, Missing, is_number

ERROR, WARNING, INFO = "ERRO", "AVISO", "INFO"

_ID_OP = re.compile(r"(?<![A-Za-z])([12])(DCVD|CVD)\b", re.IGNORECASE)
_FOLDER_OP = re.compile(r"(DCVD|CVD)([12])", re.IGNORECASE)
_SIDE = re.compile(r"\b(In|Out)\b", re.IGNORECASE)


@dataclass
class Issue:
    level: str
    case_id: str
    excel_row: int
    field: str
    message: str


def _op_from_tokens(n: str, kind: str) -> str:
    return f"{'1st' if n == '1' else '2nd'} end {kind.upper()}"


def validate_sheet(sheet: InputSheet, cfg: ProjectConfig, curves=None) -> list[Issue]:
    issues: list[Issue] = []
    for ci in (curves.issues if curves is not None else []):
        issues.append(Issue(ci.level, "", 0, f"curva:{ci.curve_id}", ci.message))
    counts = Counter(sheet.ids())
    for col in sheet.unmapped_columns():
        issues.append(Issue(INFO, "", sheet.header_row, col, "Coluna da Input Sheet sem parâmetro mapeado (ignorada)"))

    for row in sheet.rows:
        cid, r, op = row.id, row.excel_row, row.operation_type
        if counts[cid] > 1:
            issues.append(Issue(ERROR, cid, r, "ID", f"ID duplicado ({counts[cid]}x); o caso não pode ser resolvido"))

        m = _ID_OP.search(cid)
        if m and _op_from_tokens(*m.groups()).lower() != op.lower():
            issues.append(Issue(ERROR, cid, r, "Operation Type",
                                f"ID indica '{_op_from_tokens(*m.groups())}' mas Operation Type é '{op}'"))
        folder = row.meta.get("folder") or ""
        fm = _FOLDER_OP.search(folder)
        if fm and _op_from_tokens(fm.group(2), fm.group(1)).lower() != op.lower():
            issues.append(Issue(WARNING, cid, r, "Folder",
                                f"Pasta '{folder}' indica '{_op_from_tokens(fm.group(2), fm.group(1))}' mas Operation Type é '{op}'"))
        sides = {
            name: (s.group(1).capitalize() if (s := _SIDE.search(text or "")) else None)
            for name, text in (("ID", cid), ("Location", row.meta.get("location")), ("Folder", folder))
        }
        found = {k: v for k, v in sides.items() if v}
        if len(set(found.values())) > 1:
            issues.append(Issue(WARNING, cid, r, "In/Out",
                                "Lado inconsistente: " + ", ".join(f"{k}={v}" for k, v in found.items())))

        if op not in cfg.operations:
            issues.append(Issue(WARNING, cid, r, "Operation Type", f"Tipo '{op}' não está na aba 'Operações'"))
        if counts[cid] > 1:
            continue
        issues.extend(validate_case(sheet, cfg, cid, curves))
    return issues


def validate_case(sheet: InputSheet, cfg: ProjectConfig, case_id: str, curves=None) -> list[Issue]:
    issues: list[Issue] = []
    case = resolve_case(sheet, cfg, case_id, curves=curves)
    r = case.meta.get("excel_row", 0)
    op = case.operation
    if not op.supported:
        issues.append(Issue(INFO, case_id, r, "Operation Type",
                            f"'{op.operation_type}' ainda não é montado pelo builder (fase 5)"))
    required = set(P.required_keys(case.requirement_groups())) if op.supported else set()

    # ---- premissas de modelagem (precisam ser decididas explicitamente)
    if op.supported and case.flag("model.winch_extension") is None:
        issues.append(Issue(WARNING, case_id, r, "model.winch_extension",
                            "Premissa não definida: o cabo do guincho tem extensão (2 seções)? Defina S/N em Defaults "
                            "(projeto) ou Overrides (caso). Sem isso o builder mantém as seções do modelo base e "
                            "NÃO configura o tipo da extensão."))
    ends_v = case.get("model.modelled_ends")
    if not (is_number(ends_v) and ends_v in (1, 2)):
        issues.append(Issue(ERROR, case_id, r, "model.modelled_ends", f"Deve ser 1 ou 2 (está {ends_v})"))
    length = case.get("line.total_length")
    limit = case.get("model.two_ends_max_length")
    ends_src = case.values["model.modelled_ends"].source
    if case.modelled_ends() == 1 and is_number(length) and is_number(limit) and length < limit \
            and ends_src != "override":
        hint = _suggestion_text(sheet, case)
        issues.append(Issue(WARNING, case_id, r, "model.modelled_ends",
                            f"Linha de {length:g} m (< {limit:g} m) modelada com 1 extremidade. Confirme: para modelar "
                            f"as duas, Override model.modelled_ends = 2 + model.end_b_case_id (model.end_distance é "
                            f"opcional: vazio = ajustada pela estática); "
                            f"para manter 1, Override model.modelled_ends = 1 neste caso.{hint}"))
    if case.modelled_ends() == 2:
        other = case.get("model.end_b_case_id")
        if not isinstance(other, str) or not other.strip():
            issues.append(Issue(ERROR, case_id, r, "model.end_b_case_id",
                                "Duas extremidades: informe o ID da linha da Input Sheet com o MCV da outra ponta."
                                + _suggestion_text(sheet, case)))
        elif other.strip() == case_id:
            issues.append(Issue(ERROR, case_id, r, "model.end_b_case_id", "A outra extremidade não pode ser o próprio caso"))
        if op.supported and not op.base_model_two_ends:
            issues.append(Issue(INFO, case_id, r, "Operações",
                                f"2 extremidades com o modelo base comum de '{op.operation_type}': MCV B e hub B são "
                                f"criados como cópias do MCV e do hub (papéis MCV_B e HUB_B)"))
        if not is_number(length):
            issues.append(Issue(ERROR, case_id, r, "line.total_length",
                                "Duas extremidades: o comprimento total da linha é obrigatório"))
        else:
            try:
                from .model.twoends import line_sections
                line_sections(case)
            except Exception as exc:
                issues.append(Issue(ERROR, case_id, r, "line.total_length", str(exc)))

    curve_errors = case.meta.get("curve_errors", {})
    for key, rv in case.values.items():
        p = P.get(key)
        v = rv.value
        if key in curve_errors:
            level = ERROR if key in required else WARNING
            issues.append(Issue(level, case_id, r, key, f"Curva externa: {curve_errors[key]}"))
            continue
        if key in required and not is_usable(key, v):
            if isinstance(v, CurveRef):
                msg = f"Aponta para curva externa ({v.text}); defina em Overrides o valor ou o nome da curva no modelo"
            else:
                msg = f"Obrigatório para '{op.operation_type}' e está {v}"
            issues.append(Issue(ERROR, case_id, r, key, msg))
        elif p.kind == P.NUMBER and not isinstance(v, Missing) and not is_number(v):
            issues.append(Issue(ERROR, case_id, r, key, f"Esperado número, encontrado {v!s}"))
        if rv.source == "default" and isinstance(rv.raw_input, Missing) and p.input_column:
            issues.append(Issue(WARNING, case_id, r, key,
                                f"Input Sheet diz {rv.raw_input.reason}; usado o valor padrão {v}"))

    for c in case.criteria:
        if not is_number(c.limit):
            issues.append(Issue(WARNING, case_id, r, f"criterio:{c.name}", f"Sem limite numérico ({c.limit_origin}: {c.limit})"))

    lists = [case.get(k) for k in ("floaters.buoyancy", "floaters.kps", "floaters.sling_length", "floaters.sling_stiffness")]
    if all(isinstance(x, list) for x in lists) and len({len(x) for x in lists}) > 1:
        issues.append(Issue(ERROR, case_id, r, "floaters.*", f"Listas de flutuadores com tamanhos diferentes: {[len(x) for x in lists]}"))
    return issues


def other_end_candidates(sheet: InputSheet, case_id: str, counterpart: str = "") -> list[str]:
    """IDs da mesma linha (mesmo poço e descrição) em outra locação: candidatos à outra extremidade.

    Com ``counterpart`` (operação do outro lado, ex.: 1st end DCVD -> 2nd end DCVD), os casos
    dessa operação vêm primeiro. A escolha final é sempre do usuário.
    """
    me = sheet.find(case_id)
    desc, loc, well = me.meta.get("line_description"), me.meta.get("location"), me.meta.get("well")
    found = [r for r in sheet.rows
             if r.id != case_id and desc and r.meta.get("well") == well
             and r.meta.get("line_description") == desc and r.meta.get("location") != loc]
    found.sort(key=lambda r: 0 if counterpart and r.operation_type.lower() == counterpart.lower() else 1)
    return [r.id for r in found]


def _suggestion_text(sheet: InputSheet, case) -> str:
    cands = other_end_candidates(sheet, case.case_id, case.operation.counterpart)
    if not cands:
        return ""
    cp = case.operation.counterpart
    best = [c for c in cands if cp and sheet.find(c).operation_type.lower() == cp.lower()]
    if best:
        rest = [c for c in cands if c not in best]
        txt = f" Sugestão (operação do outro lado: {cp}): {', '.join(best)}."
        return txt + (f" Outras da mesma linha: {', '.join(rest)}." if rest else "")
    return f" Candidatas (mesma linha): {', '.join(cands)}."


def write_report(issues: list[Issue], path: Path) -> Path:
    import pandas as pd

    path = Path(path)
    df = pd.DataFrame([asdict(i) for i in issues], columns=["level", "case_id", "excel_row", "field", "message"])
    df.columns = ["Nível", "ID", "Linha Excel", "Campo", "Mensagem"]
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        df.to_excel(path, index=False, sheet_name="Validação")
    return path


def summarize(issues: list[Issue]) -> str:
    c = Counter(i.level for i in issues)
    cases = len({i.case_id for i in issues if i.level == ERROR and i.case_id})
    return f"{c[ERROR]} erro(s) em {cases} caso(s), {c[WARNING]} aviso(s), {c[INFO]} informação(ões)"
