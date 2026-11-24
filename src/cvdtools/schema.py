"""Estruturas de dados da pipeline: caso resolvido, itens de pós-processamento,
critérios, linhas do checklist e especificação das operações."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from . import params as P
from .values import CurveRef, Missing, Value, is_number, to_jsonable


class MissingInputError(ValueError):
    pass


# --------------------------------------------------------------------------- operações

@dataclass
class OperationSpec:
    operation_type: str
    template: str = ""            # template de pós-processamento/critérios
    camelback: bool = False       # operação com corcova (guincho + lingas)
    base_model: str = ""          # caminho do modelo base (relativo ao arquivo de projeto)
    supported: bool = False       # o builder já sabe montar esse tipo?
    base_model_two_ends: str = "" # modelo base quando as duas extremidades são modeladas
    counterpart: str = ""         # operação do "outro lado" (sugestão para a outra extremidade)

    def requirement_groups(self) -> set[str]:
        groups = {P.BUILD}
        if self.camelback:
            groups.add(P.CAMELBACK)
        return groups


# --------------------------------------------------------------------------- pós-processamento

STATIC_PERIODS = {"", "static", "estático", "estatico", "static result", None}
WHOLE_PERIODS = {"whole simulation", "dinâmico", "dinamico", "dynamic"}


@dataclass
class PostItem:
    """Uma coluna da tabela de resultados (mesma semântica do bloco postProcess do JSON)."""

    label: str
    method: str            # Get data | Static result | Min | Max | Range graph min | Range graph max
    obj: str               # nome do objeto ou papel "{LINE}"
    variable: str
    extra: Any = ""        # "End A" | "End B" | "Touchdown" | [a, b] | ""
    period: str = ""       # "" (estático) | "Whole simulation"
    template: str = ""
    unit: str = ""

    METHODS = ("Get data", "Static result", "Min", "Max", "Range graph min", "Range graph max",
               "Max abs", "Range graph max abs")

    def __post_init__(self):
        self.method = _canon_method(self.method)
        self.period = _canon_period(self.period)
        self.extra = _canon_extra(self.extra)

    @property
    def is_static(self) -> bool:
        return self.period == ""


@dataclass
class CriterionSpec:
    """Critério de aceitação como está na configuração (antes de resolver o limite)."""

    name: str
    type: str              # "Time History" | "Range Graph Max"
    obj: str
    variable: str
    extra: Any = ""
    limit: Optional[float] = None
    limit_source: str = ""   # chave de parâmetro, ex.: "mcv.axial_tension_limit"
    transform: str = ""      # "" | "inverse" (ex.: curvatura = 1/MBR)
    static: bool = True
    dynamic: bool = True
    template: str = ""

    def __post_init__(self):
        self.extra = _canon_extra(self.extra)


@dataclass
class Criterion:
    """Critério com limite resolvido para um caso."""

    name: str
    type: str
    obj: str
    variable: str
    extra: Any
    limit: Value
    limit_origin: str
    static: bool = True
    dynamic: bool = True

    def to_legacy(self) -> dict:
        return {
            "Type": self.type,
            "OrcaFlex Parent": self.obj,
            "OrcaFlex Variable Name": self.variable,
            "OrcaFlex Extra": self.extra,
            "Limit": self.limit if is_number(self.limit) else to_jsonable(self.limit),
        }


# --------------------------------------------------------------------------- checklist

@dataclass
class ChecklistRow:
    group: str           # coluna "Model Object"
    variable: str
    unit: str
    input_expr: str      # chave de parâmetro ou função (ver model/checklist.py)
    orcaflex_expr: str   # "{ROLE}.Prop", "{ROLE}.Prop[0]" ou função
    abs_tol: float = 1e-6
    rel_tol: float = 1e-4
    applies_to: str = "all"   # "all" | "camelback" | "floaters"


# --------------------------------------------------------------------------- caso

@dataclass
class ResolvedValue:
    value: Value
    source: str            # "override", "input_sheet", "default", "legacy_json"
    raw_input: Value = None  # o que a Input Sheet dizia (mesmo quando outra fonte venceu)


@dataclass
class CaseInputs:
    case_id: str
    operation: OperationSpec
    meta: dict[str, Any] = field(default_factory=dict)
    values: dict[str, ResolvedValue] = field(default_factory=dict)
    criteria: list[Criterion] = field(default_factory=list)
    postproc: list[PostItem] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)
    operation_name: str = ""

    # ---- acesso
    def get(self, key: str) -> Value:
        P.get(key)  # valida a chave
        rv = self.values.get(key)
        return rv.value if rv is not None else Missing("não definido")

    def num(self, key: str) -> float:
        v = self.get(key)
        if is_number(v):
            return float(v)
        raise MissingInputError(f"{key} ({P.get(key).description}) não é numérico no caso {self.case_id}: {v}")

    def num_or_name(self, key: str):
        """Número, nome de curva já existente no modelo, ou curva da biblioteca (objeto Curve)."""
        v = self.get(key)
        if is_number(v):
            return float(v)
        if hasattr(v, "si"):  # curva da biblioteca
            return v
        if isinstance(v, str) and v:
            return v
        if isinstance(v, CurveRef):
            raise MissingInputError(
                f"{key} aponta para uma curva externa ({v.text}) que não foi encontrada na biblioteca de curvas. "
                f"Cadastre-a no cvd_curves.xlsx (ou use 'curva:ID'), ou defina em Overrides um número ou o nome "
                f"de uma curva já existente no modelo base."
            )
        raise MissingInputError(f"{key} não definido no caso {self.case_id}: {v}")

    def lst(self, key: str) -> list[float]:
        v = self.get(key)
        if isinstance(v, list):
            return [float(x) for x in v]
        raise MissingInputError(f"{key} deveria ser uma lista no caso {self.case_id}: {v}")

    def role(self, name: str) -> str:
        try:
            return self.roles[name]
        except KeyError:
            raise KeyError(f"Papel {name!r} não definido na aba 'Papéis'") from None

    def curves(self) -> dict:
        """Parâmetros cujo valor é uma curva da biblioteca: {chave: Curve}."""
        return {k: rv.value for k, rv in self.values.items() if hasattr(rv.value, "si")}

    # ---- premissas
    def flag(self, key: str) -> Optional[bool]:
        """Premissa S/N: True, False ou None (não definida)."""
        v = self.get(key)
        return bool(v) if is_number(v) else None

    def modelled_ends(self) -> int:
        v = self.get("model.modelled_ends")
        return int(round(v)) if is_number(v) else 1

    def requirement_groups(self) -> set[str]:
        groups = self.operation.requirement_groups()
        if self.flag("model.winch_extension"):
            groups.add(P.WINCH_EXT)
        if self.modelled_ends() == 2:
            groups.add(P.TWO_ENDS)
        return groups

    def premises(self) -> list[tuple[str, str, str]]:
        """(premissa, valor, fonte) para relatórios."""
        def src(k):
            rv = self.values.get(k)
            return rv.source if rv else "-"
        ext = self.flag("model.winch_extension")
        ext_txt = "NÃO DEFINIDA (mantém o modelo base)" if ext is None else ("sim" if ext else "não")
        ends = self.modelled_ends()
        rows = [("Cabo do guincho em duas seções", ext_txt, src("model.winch_extension")),
                ("Extremidades modeladas com MCV", str(ends), src("model.modelled_ends"))]
        if ends == 2:
            rows.append(("Outra extremidade (ID)", str(self.get("model.end_b_case_id")), src("model.end_b_case_id")))
        return rows

    def missing(self, keys: list[str]) -> dict[str, Value]:
        out = {}
        for k in keys:
            v = self.get(k)
            kind = P.get(k).kind
            ok = (is_number(v) or (kind == P.CURVE_OR_NUMBER and ((isinstance(v, str) and v) or hasattr(v, "si")))
                  or (kind == P.LIST and isinstance(v, list)) or (kind == P.BOOL and is_number(v))
                  or (kind == P.TEXT and isinstance(v, str) and v))
            if not ok:
                out[k] = v
        return out

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "operation_name": self.operation_name,
            "operation": asdict(self.operation),
            "meta": self.meta,
            "values": {
                k: {"value": to_jsonable(rv.value), "source": rv.source, "input_sheet": to_jsonable(rv.raw_input)}
                for k, rv in sorted(self.values.items())
            },
            "criteria": [
                {**{k: to_jsonable(v) for k, v in asdict(c).items()}} for c in self.criteria
            ],
            "postproc": [asdict(i) for i in self.postproc],
            "roles": self.roles,
        }


# --------------------------------------------------------------------------- normalização

_METHOD_ALIASES = {m.lower(): m for m in PostItem.METHODS}


def _canon_method(m: str) -> str:
    key = str(m).strip().lower()
    if key not in _METHOD_ALIASES:
        raise ValueError(f"Método de pós-processamento desconhecido: {m!r}. Use um de {PostItem.METHODS}")
    return _METHOD_ALIASES[key]


def _canon_period(p: Any) -> str:
    key = "" if p is None else str(p).strip().lower()
    if key in STATIC_PERIODS:
        return ""
    if key in WHOLE_PERIODS:
        return "Whole simulation"
    raise ValueError(f"Período desconhecido: {p!r}. Use vazio/Estático ou 'Whole simulation'/Dinâmico")


def _canon_extra(e: Any) -> Any:
    if e is None:
        return ""
    if isinstance(e, (list, tuple)):
        if len(e) != 2:
            raise ValueError(f"Trecho de arc length deve ter 2 valores: {e!r}")
        return [float(e[0]), float(e[1])]
    text = str(e).strip()
    low = text.lower()
    if low in ("", "-", "none"):
        return ""
    if low in ("end a", "enda"):
        return "End A"
    if low in ("end b", "endb"):
        return "End B"
    if low in ("touchdown", "tdp"):
        return "Touchdown"
    for sep in (";", " a ", "–", "-"):
        if sep in text.strip("[]"):
            parts = [x.strip() for x in text.strip("[]").replace(",", ".").split(sep)]
            parts = [x for x in parts if x]
            if len(parts) == 2:
                try:
                    return [float(parts[0]), float(parts[1])]
                except ValueError:
                    pass
    if text.startswith("[") and text.endswith("]"):
        parts = [x.strip() for x in text[1:-1].split(",")]
        if len(parts) == 2:
            return [float(parts[0]), float(parts[1])]
    raise ValueError(f"OrcaFlex Extra não reconhecido: {e!r} (use End A, End B, Touchdown, 'a;b' ou vazio)")
