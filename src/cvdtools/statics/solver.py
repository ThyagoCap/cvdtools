"""Busca de raiz em uma variável, robusta a avaliações que falham (estática que não converge).

``f(x)`` pode devolver ``nan`` quando a estática não converge. A busca:

1. **Enquadramento**: parte de ``x0`` e anda com passo ``step`` (dobrando) na direção
   pedida até encontrar troca de sinal, sem passar de ``[lo, hi]``.
2. **Refino**: falsa posição (Illinois) com salvaguarda de bisseção até o intervalo
   ficar menor que ``xtol``. Um ponto ``nan`` dentro do intervalo é contornado
   avaliando entre ele e o último ponto bom.

Tudo o que é avaliado fica em ``Search.history`` (vai para o statics_log.csv).

Passo máximo (0.11.6, ``max_step``): o passo do enquadramento não passa de ``max_step`` (não dobra além
dele) e nenhuma avaliação fica a mais de ``max_step`` do último ponto convergido (``current()``): os
pontos intermediários são avaliados de verdade, e uma troca de sinal entre dois deles já vira o
intervalo de refino (``EarlyBracket``), sem andar o resto do caminho.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional


class NotBracketed(RuntimeError):
    pass


class TooManyEvaluations(RuntimeError):
    pass


class EarlyBracket(Exception):
    """Troca de sinal encontrada nos passos intermediários de uma avaliação: (a, fa, b, fb)."""

    def __init__(self, a: float, fa: float, b: float, fb: float):
        super().__init__(f"troca de sinal entre {a:g} e {b:g}")
        self.bracket = (a, fa, b, fb) if a < b else (b, fb, a, fa)


@dataclass
class Search:
    f: Callable[[float], float]
    xtol: float = 0.01
    max_evals: int = 40
    ftol: Optional[float] = None      # 0.11.5: para também quando |f| <= ftol (ângulo); xtol vira só um piso
    xtol_min: float = 1e-5
    max_step: Optional[float] = None                     # 0.11.6: maior passo entre avaliações
    current: Optional[Callable[[], Optional[float]]] = None   # x do último ponto convergido (fora da busca)
    history: list[tuple[float, float]] = field(default_factory=list)
    _solving: bool = False

    def _lookup(self, x: float):
        for hx, hy in self.history:  # reaproveita avaliações repetidas
            if abs(hx - x) < 1e-12:
                return hy
        return None

    def _eval1(self, x: float) -> float:
        if len(self.history) >= self.max_evals:
            raise TooManyEvaluations(f"Limite de {self.max_evals} avaliações atingido")
        known = self._lookup(x)
        if known is not None:
            return known
        y = self.f(x)
        y = float("nan") if y is None else float(y)
        self.history.append((x, y))
        return y

    def eval(self, x: float) -> float:
        known = self._lookup(x)
        if known is not None:
            return known
        cur = self.current() if (self.current and self.max_step) else None
        if cur is None or abs(x - cur) <= self.max_step + 1e-9:
            return self._eval1(x)
        # passos de no máximo max_step até x, cada um avaliado; troca de sinal no caminho -> refina ali
        n = int(math.ceil(abs(x - cur) / self.max_step - 1e-9))
        prev_x, prev_y = cur, self._lookup(cur)
        for i in range(1, n + 1):
            xi = x if i == n else cur + (x - cur) * i / n
            yi = self._eval1(xi)
            if (self._solving and i < n and prev_y is not None and not math.isnan(prev_y) and not math.isnan(yi)
                    and (yi == 0 or (prev_y < 0) != (yi < 0))):
                raise EarlyBracket(prev_x, prev_y, xi, yi)
            if not math.isnan(yi):
                prev_x, prev_y = xi, yi
        return yi

    # ------------------------------------------------------------------ enquadramento
    def bracket(self, x0: float, step: float, lo: float, hi: float, direction: int = +1
                ) -> tuple[float, float, float, float]:
        """Devolve (a, fa, b, fb) com fa e fb de sinais opostos."""
        if self.max_step:
            step = min(step, self.max_step)
        a = min(max(x0, lo), hi)
        fa = self.eval(a)
        tries = 0
        while math.isnan(fa):  # ponto inicial não converge: anda até achar um bom
            tries += 1
            a = min(max(a + direction * step, lo), hi)
            if tries > 6 or a in (lo, hi):
                raise NotBracketed(f"Estática não converge perto de x0={x0:g}")
            fa = self.eval(a)
        if fa == 0 or (self.ftol and abs(fa) <= self.ftol):
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
            h = min(h * 2, self.max_step) if self.max_step else h * 2

    # ------------------------------------------------------------------ refino
    def _done(self, a: float, fa: float, b: float, fb: float) -> bool:
        if self.ftol:   # com tolerância em f: até |f| <= ftol (ou o intervalo ficar menor que xtol_min)
            return min(abs(fa), abs(fb)) <= self.ftol or abs(b - a) <= self.xtol_min
        return abs(b - a) <= self.xtol

    def refine(self, a: float, fa: float, b: float, fb: float) -> float:
        if fa == 0 or (self.ftol and abs(fa) <= self.ftol and (abs(fa) <= abs(fb))):
            return a
        if fb == 0 or (self.ftol and abs(fb) <= self.ftol):
            return b
        side = 0
        while not self._done(a, fa, b, fb):
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
            if fx == 0 or (self.ftol and abs(fx) <= self.ftol):
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
        if self.ftol:   # alvo de igualdade (ângulo): o extremo mais perto
            return a if abs(fa) <= abs(fb) else b
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
        self._solving = True
        try:
            try:
                br = self.bracket(x0, step, lo, hi, direction)
            except EarlyBracket as e:
                br = e.bracket
            while True:
                try:
                    return self.refine(*br)
                except EarlyBracket as e:
                    br = e.bracket
        finally:
            self._solving = False
