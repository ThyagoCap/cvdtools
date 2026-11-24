"""Comparação de estruturas aninhadas com tolerância numérica.

Usada para conferir se o código novo reproduz o baseline da pipeline antiga:
modelos OrcaFlex (via YAML), resultados estáticos e tabelas de pós-processamento.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Tolerance:
    """Tolerância combinada: passa se |a-b| <= abs_tol + rel_tol*max(|a|,|b|)."""

    rel_tol: float = 1e-9
    abs_tol: float = 1e-9

    def close(self, a: float, b: float) -> bool:
        if math.isnan(a) and math.isnan(b):
            return True
        if math.isinf(a) or math.isinf(b):
            return a == b
        return abs(a - b) <= self.abs_tol + self.rel_tol * max(abs(a), abs(b))


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str  # "value", "type", "missing", "extra", "length"
    expected: Any = None
    actual: Any = None

    def __str__(self) -> str:
        if self.kind == "missing":
            return f"{self.path}: ausente no resultado (esperado {self.expected!r})"
        if self.kind == "extra":
            return f"{self.path}: sobrando no resultado ({self.actual!r})"
        if self.kind == "length":
            return f"{self.path}: tamanho {self.actual} != esperado {self.expected}"
        return f"{self.path}: {self.actual!r} != esperado {self.expected!r}"


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def compare(
    expected: Any,
    actual: Any,
    tol: Tolerance = Tolerance(),
    path: str = "",
    ignore: Iterable[str] = (),
) -> list[Difference]:
    """Compara ``actual`` com ``expected`` e devolve a lista de diferenças.

    ``ignore``: trechos de caminho a ignorar (ex.: "General.RunMode").
    Números são comparados com ``tol``; int e float são intercambiáveis.
    """
    ignore = tuple(ignore)
    diffs: list[Difference] = []
    _compare(expected, actual, tol, path or "$", ignore, diffs)
    return diffs


def _compare(exp, act, tol, path, ignore, diffs):
    if any(token in path for token in ignore):
        return

    if _is_number(exp) and _is_number(act):
        if not tol.close(float(exp), float(act)):
            diffs.append(Difference(path, "value", exp, act))
        return

    if isinstance(exp, dict) and isinstance(act, dict):
        for key in exp:
            sub = f"{path}.{key}"
            if key not in act:
                if not any(token in sub for token in ignore):
                    diffs.append(Difference(sub, "missing", expected=exp[key]))
                continue
            _compare(exp[key], act[key], tol, sub, ignore, diffs)
        for key in act:
            if key not in exp:
                sub = f"{path}.{key}"
                if not any(token in sub for token in ignore):
                    diffs.append(Difference(sub, "extra", actual=act[key]))
        return

    if isinstance(exp, (list, tuple)) and isinstance(act, (list, tuple)):
        if _named(exp) and _named(act):
            # Listas de objetos do OrcaFlex (- Name: ...): compara por nome, não por posição
            exp_by = {e["Name"]: e for e in exp}
            act_by = {a["Name"]: a for a in act}
            for name, e in exp_by.items():
                sub = f"{path}[{name}]"
                if name not in act_by:
                    if not any(token in sub for token in ignore):
                        diffs.append(Difference(sub, "missing", expected="(objeto)"))
                    continue
                _compare(e, act_by[name], tol, sub, ignore, diffs)
            for name in act_by.keys() - exp_by.keys():
                sub = f"{path}[{name}]"
                if not any(token in sub for token in ignore):
                    diffs.append(Difference(sub, "extra", actual="(objeto)"))
            return
        if len(exp) != len(act):
            diffs.append(Difference(path, "length", len(exp), len(act)))
        for i, (e, a) in enumerate(zip(exp, act)):
            _compare(e, a, tol, f"{path}[{i}]", ignore, diffs)
        return

    if type(exp) is not type(act) and not (_is_number(exp) and _is_number(act)):
        diffs.append(Difference(path, "type", exp, act))
        return

    if exp != act:
        diffs.append(Difference(path, "value", exp, act))


def _named(seq) -> bool:
    return bool(seq) and all(isinstance(x, dict) and "Name" in x for x in seq) and \
        len({x["Name"] for x in seq}) == len(seq)


def format_report(diffs: list[Difference], limit: int = 50) -> str:
    if not diffs:
        return "Sem diferenças."
    lines = [f"{len(diffs)} diferença(s):"]
    lines += [f"  - {d}" for d in diffs[:limit]]
    if len(diffs) > limit:
        lines.append(f"  ... e mais {len(diffs) - limit}")
    return "\n".join(lines)
