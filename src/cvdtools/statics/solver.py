"""Busca de raiz em uma variável, robusta a avaliações que falham (estática que não converge).

``f(x)`` pode devolver ``nan`` quando a estática não converge. A busca:

1. **Enquadramento**: parte de ``x0`` e anda com passo ``step`` (dobrando) na direção
   pedida até encontrar troca de sinal, sem passar de ``[lo, hi]``.
2. **Refino**: falsa posição (Illinois) com salvaguarda de bisseção até o intervalo
   ficar menor que ``xtol``. Um ponto ``nan`` dentro do intervalo é contornado
   avaliando entre ele e o último ponto bom.

Tudo o que é avaliado fica em ``Search.history`` (vai para o statics_log.csv).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional


class NotBracketed(RuntimeError):
    pass


class TooManyEvaluations(RuntimeError):
    pass


@dataclass
class Search:
    f: Callable[[float], float]
    xtol: float = 0.01
    max_evals: int = 40
    history: list[tuple[float, float]] = field(default_factory=list)

    def eval(self, x: float) -> float:
        if len(self.history) >= self.max_evals:
            raise TooManyEvaluations(f"Limite de {self.max_evals} avaliações atingido")
        for hx, hy in self.history:  # reaproveita avaliações repetidas
            if abs(hx - x) < 1e-12:
                return hy
        y = self.f(x)
        y = float("nan") if y is None else float(y)
        self.history.append((x, y))
        return y

    # ------------------------------------------------------------------ enquadramento
    def bracket(self, x0: float, step: float, lo: float, hi: float, direction: int = +1
                ) -> tuple[float, float, float, float]:
        """Devolve (a, fa, b, fb) com fa e fb de sinais opostos."""
        a = min(max(x0, lo), hi)
        fa = self.eval(a)
        tries = 0
        while math.isnan(fa):  # ponto inicial não converge: anda até achar um bom
            tries += 1
            a = min(max(a + direction * step, lo), hi)
            if tries > 6 or a in (lo, hi):
                raise NotBracketed(f"Estática não converge perto de x0={x0:g}")
            fa = self.eval(a)
        if fa == 0:
            return a, fa, a, fa
        h = step
        while True:
            b = a + direction * h
            clipped = False
            if b > hi:
                b, clipped = hi, True
            if b < lo:
                b, clipped = lo, True
            fb = self.eval(b)
            if math.isnan(fb):
                if clipped or h < 4 * self.xtol:
                    raise NotBracketed(f"Estática não converge entre {a:g} e {b:g}")
                h /= 2  # recua e tenta mais perto
                continue
            if fb == 0 or (fa < 0) != (fb < 0):
                return (a, fa, b, fb) if a < b else (b, fb, a, fa)
            if clipped:
                raise NotBracketed(f"Sem troca de sinal até o limite {b:g} (f={fb:g})")
            a, fa = b, fb
            h *= 2

    # ------------------------------------------------------------------ refino
    def refine(self, a: float, fa: float, b: float, fb: float) -> float:
        if fa == 0:
            return a
        if fb == 0:
            return b
        side = 0
        while abs(b - a) > self.xtol:
            # falsa posição (Illinois), protegida por bisseção
            x = (a * fb - b * fa) / (fb - fa) if fb != fa else (a + b) / 2
            if not (min(a, b) + 0.1 * abs(b - a) < x < max(a, b) - 0.1 * abs(b - a)):
                x = (a + b) / 2
            fx = self.eval(x)
            if math.isnan(fx):
                fx = self._around_nan(a, x, b)
                if fx is None:
                    raise NotBracketed(f"Estática não converge dentro de [{a:g}, {b:g}]")
                x, fx = fx
            if fx == 0:
                return x
            if (fx < 0) == (fa < 0):
                a, fa = x, fx
                if side == -1:
                    fb /= 2
                side = -1
            else:
                b, fb = x, fx
                if side == +1:
                    fa /= 2
                side = +1
        # devolve o extremo do lado "aceitável" (f <= 0) quando possível
        return a if fa <= 0 else b

    def _around_nan(self, a: float, x: float, b: float) -> Optional[tuple[float, float]]:
        for t in (0.5, 0.25, 0.75):
            for c in (a + t * (x - a), x + t * (b - x)):
                fc = self.eval(c)
                if not math.isnan(fc):
                    return c, fc
        return None

    def solve(self, x0: float, step: float, lo: float, hi: float, direction: int = +1) -> float:
        return self.refine(*self.bracket(x0, step, lo, hi, direction))
