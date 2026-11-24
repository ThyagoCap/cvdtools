"""``missing_data.json``: inputs obrigatórios que faltam, para o usuário preencher.

Quando o ``build`` (02_montar_modelo.py) encontra inputs obrigatórios faltando, grava na pasta do caso um
``missing_data.json`` com um item por input e ``"valor": null``. O usuário preenche os valores e roda de
novo; a próxima rodada lê o arquivo e grava os valores no ``cvd_config.xlsx`` (com ``.bak``):

- parâmetros de projeto (sem coluna na Input Sheet: lâmina d'água, atritos, EIx dos cabos, corcova...)
  vão para a aba **Defaults**;
- o ID do MCV da outra extremidade (``model.end_b_case_id``) e os valores que deveriam vir da Input Sheet
  (planilha geral, que é só leitura) vão para a aba **Overrides** deste caso.

Nada é inventado: item sem valor continua faltando e o arquivo é gerado de novo. Depois de aplicado, o
arquivo vira ``missing_data.aplicado_<data>.json`` (registro do que foi gravado).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Optional

from . import params as P
from .values import CurveRef, Missing, is_number

FILE_NAME = "missing_data.json"
DEFAULTS, OVERRIDES = "Defaults", "Overrides"
END_B_KEY = "model.end_b_case_id"

_HELP = ("Preencha 'valor' de cada item (número, texto entre aspas ou lista 'a;b;c') e rode de novo o "
         "02_montar_modelo.py. Itens com destino 'Defaults' valem para o projeto todo (aba Defaults do "
         "cvd_config.xlsx); com destino 'Overrides', só para este caso. O cvd_config.xlsx precisa estar fechado "
         "no Excel. Item deixado em null continua faltando.")


class MissingDataError(ValueError):
    """``missing_data.json`` ilegível ou de outro caso (o arquivo do usuário não é sobrescrito)."""


def destination(key: str) -> str:
    p = P.get(key)
    if key == END_B_KEY or p.input_column or key.startswith(("mcv_b.", "ef_b.", "br_b.")):
        return OVERRIDES
    return DEFAULTS


def _text(v: Any) -> str:
    if isinstance(v, Missing):
        return str(v)   # "<motivo>"
    if isinstance(v, CurveRef):
        return f"curva: {v.text}"
    return str(v)


def _end_b_problem(case, sheet) -> Optional[str]:
    """Motivo para pedir o ID da outra extremidade (None se ele já é válido)."""
    other = case.get(END_B_KEY)
    if not isinstance(other, str) or not other.strip():
        return "não definido"
    if other.strip() == case.case_id:
        return "a outra extremidade não pode ser o próprio caso"
    try:
        sheet.find(other.strip())
    except (KeyError, ValueError) as exc:
        return str(exc).strip('"')
    return None


def pending_items(case, sheet, missing: dict[str, Any]) -> list[dict]:
    """Itens do ``missing_data.json`` para os inputs que faltam no caso."""
    items: list[dict] = []
    keys = list(missing)
    if case.modelled_ends() == 2:
        problem = _end_b_problem(case, sheet)
        if problem is not None:
            # sem um ID válido as cotas do MCV B não têm de onde vir: pede só o ID
            keys = [k for k in keys if not k.startswith(("mcv_b.", "ef_b.", "br_b."))]
            from .validation import other_end_candidates

            try:
                options = other_end_candidates(sheet, case.case_id, case.operation.counterpart)
            except (KeyError, ValueError):
                options = []
            hint = str(case.meta.get("other_end_id") or "").strip()   # coluna 'Other End ID' da planilha geral
            if hint and hint != "-" and hint != case.case_id:
                options = [hint] + [o for o in options if o != hint]
            item = {"parametro": END_B_KEY, "descricao": P.get(END_B_KEY).description, "unidade": "-",
                    "destino": OVERRIDES, "valor_atual": _text(case.get(END_B_KEY)),
                    "observacao": "ID (coluna ID da Input Sheet) da linha com o MCV da outra extremidade. Com ele, "
                                  "as cotas e a massa do MCV B vêm dessa linha da planilha geral.",
                    "valor": None}
            if problem != "não definido":
                item["erro"] = problem
            if options:
                item["sugestoes"] = options
            items.append(item)
    curve_errors = case.meta.get("curve_errors", {})
    for key in keys:
        p = P.get(key)
        item = {"parametro": key, "descricao": p.description, "unidade": p.unit, "destino": destination(key),
                "valor_atual": _text(missing[key])}
        notes = []
        if p.input_column:
            notes.append(f"Vem da planilha geral (coluna '{p.input_column}'). Corrija lá, ou informe aqui para "
                         f"valer só neste caso (Overrides).")
        elif key.startswith(("mcv_b.", "ef_b.", "br_b.")):
            notes.append(f"Vem da linha '{case.get(END_B_KEY)}' da planilha geral (outra extremidade), que está "
                         f"vazia. Corrija lá, ou informe aqui para valer só neste caso (Overrides).")
        if p.kind == P.CURVE_OR_NUMBER:
            notes.append("Aceita um número, 'curva:ID' de uma curva do cvd_curves.xlsx ou o nome de uma curva que "
                         "já existe no modelo base.")
        if key in curve_errors:
            notes.append(f"Curva não encontrada: {curve_errors[key]}")
        if p.kind == P.LIST:
            notes.append("Lista separada por ';'.")
        if notes:
            item["observacao"] = " ".join(notes)
        item["valor"] = None
        items.append(item)
    return items


def write_missing_data(path: Path, case, items: list[dict], config_path: Optional[Path] = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"LEIA-ME": _HELP, "caso": case.case_id,
           "gerado_em": _dt.datetime.now().isoformat(timespec="seconds"),
           "config": str(config_path) if config_path else "", "itens": items}
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_missing_data(path: Path, case_id: str) -> list[dict]:
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise MissingDataError(f"{path.name} não é um JSON válido (linha {exc.lineno}, coluna {exc.colno}: "
                               f"{exc.msg}). Corrija o arquivo e rode de novo.") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("itens"), list):
        raise MissingDataError(f"{path.name}: formato inesperado (falta a lista 'itens').")
    if str(doc.get("caso", "")).strip() != case_id:
        raise MissingDataError(f"{path.name} é do caso {doc.get('caso')!r}, não de {case_id!r}. Apague ou mova o "
                               f"arquivo.")
    return [i for i in doc["itens"] if isinstance(i, dict) and i.get("parametro")]


def _filled(v: Any) -> bool:
    return v is not None and not (isinstance(v, str) and not v.strip())


def check_value(key: str, raw: Any, case_id: str, sheet) -> tuple[Any, Optional[str]]:
    """Valor para gravar na planilha de configuração, ou (None, erro)."""
    from .io.config import _param_value

    try:
        p = P.get(key)
    except KeyError:
        return None, f"parâmetro desconhecido: {key!r}"
    if isinstance(raw, dict) or (isinstance(raw, bool) and p.kind != P.BOOL):
        return None, f"valor {raw!r} inválido"
    if isinstance(raw, list):
        if p.kind != P.LIST:
            return None, f"valor {raw!r} inválido (não é lista)"
        raw = ";".join(str(x) for x in raw)
    if key == END_B_KEY:
        other = str(raw).strip()
        if other == case_id:
            return None, "a outra extremidade não pode ser o próprio caso"
        try:
            sheet.find(other)
        except (KeyError, ValueError) as exc:
            return None, str(exc).strip('"')
        return other, None
    v = _param_value(key, raw)
    if p.kind == P.LIST:
        if not isinstance(v, list):
            return None, f"lista inválida: {raw!r} (use 'a;b;c')"
        return ";".join(f"{x:g}" for x in v), None
    if p.kind == P.BOOL:
        if not is_number(v):
            return None, f"use S ou N (veio {raw!r})"
        return ("S" if v else "N"), None
    if p.kind == P.TEXT:
        return str(raw).strip(), None
    if is_number(v):
        return float(v), None
    if p.kind == P.CURVE_OR_NUMBER and isinstance(raw, str) and raw.strip():
        return raw.strip(), None
    return None, f"não é um número: {raw!r}"


def apply_missing_data(path: Path, case_id: str, sheet, config_path: Path) -> tuple[list[str], dict[str, tuple]]:
    """Grava no ``cvd_config.xlsx`` os itens preenchidos.

    Devolve (mudanças gravadas, {chave: (valor do usuário, erro)} dos itens preenchidos com valor inválido).
    Nada é gravado se a planilha não puder ser salva (arquivo aberto no Excel).
    """
    items = read_missing_data(path, case_id)
    defaults: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    invalid: dict[str, tuple] = {}
    for item in items:
        key, raw = str(item["parametro"]).strip(), item.get("valor")
        if not _filled(raw):
            continue
        value, error = check_value(key, raw, case_id, sheet)
        if error:
            invalid[key] = (raw, error)
            continue
        if destination(key) == DEFAULTS:
            defaults[key] = value
        else:
            overrides[key] = value
    from .io.config import set_config_values

    stamp = _dt.date.today().isoformat()
    changes = set_config_values(config_path, defaults, {case_id: overrides} if overrides else None,
                                comment=f"{FILE_NAME} ({stamp})")
    return changes, invalid


def archive(path: Path) -> Path:
    from .io.config import free_path

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = free_path(path.with_name(f"{path.stem}.aplicado_{stamp}{path.suffix}"))
    path.replace(target)
    return target


def summary(items: list[dict]) -> str:
    lines = []
    for i in items:
        line = f"  - {i['parametro']}: {i['valor_atual']}  ({i['descricao']}) -> {i['destino']}"
        if i.get("erro"):
            line += f"  [ERRO: {i['erro']}]"
        lines.append(line)
    return "\n".join(lines)
