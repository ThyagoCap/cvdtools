"""Busca adaptativa do Hs limite com as simulações irregulares (domínio do tempo).

Por que existe: com a rigidez flexional histerética, a janela de referência só sai do
domínio do tempo. O OrcaFlex não roda o domínio da frequência com rigidez flexional
histerética ("feature ... not available for frequency domain analysis"); com a rigidez
secante equivalente (``screen fd --bend-stiffness secant``) a análise roda, mas sem a
energia dissipada nos laços de histerese. A semi-dinâmica não tem onda direta. As duas
servem como **ponto de partida**, não como resultado. Esta busca usa as próprias irregulares para chegar ao
limite com poucas simulações por (Tp, direção).

Regra, por par (Tp, direção):
1. Intervalo atual: ``hs_ok`` = maior Hs aprovado abaixo do menor reprovado,
   ``hs_bad`` = menor Hs reprovado (instável/incompleta conta como reprovada).
2. Fechado quando ``hs_bad - hs_ok`` <= um passo de Hs, quando Hs máx foi aprovada ou
   quando Hs mín já reprova.
3. Estimativa do limite (Hs com UF = limite):
   - 2+ pontos com UF: interpolação linear entre o maior aprovado e o menor reprovado
     (ou extrapolação pelos dois pontos mais próximos do limite);
   - 1 ponto: UF proporcional a Hs (a parte dinâmica domina);
   - sem UF: previsão (``--seed``: ``screen fd``/``screen predict``), senão o Tp
     vizinho da mesma direção, senão o meio da faixa; com só reprovações instáveis,
     bisseção.
4. Próximos Hs: os ``per_pair`` pontos da grade (``hs_min + k·passo``) estritamente
   dentro do intervalo atual e mais próximos de ``estimativa + passo/2``. Com
   ``per_pair = 2`` saem o Hs logo abaixo e o logo acima da estimativa: se a
   estimativa acertar, o par fecha numa rodada.

Hipótese: UF cresce com Hs no par (monotonia). Aprovação acima de uma reprovação é
anotada (como no ``window_limits``) e o intervalo usa o menor Hs reprovado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

EPS = 1e-9
CLOSED, OPEN, ABOVE_MAX, BELOW_MIN, EMPTY = "fechado", "aberto", "≥ Hs máx", "< Hs mín", "sem Hs na grade"


@dataclass
class PairSearch:
    tp: float
    direction: float
    hs_ok: Optional[float] = None
    hs_bad: Optional[float] = None
    estimate: Optional[float] = None
    source: str = ""
    status: str = OPEN
    runs: int = 0
    next: list[float] = field(default_factory=list)
    note: str = ""


def _key(tp, d) -> tuple[float, float]:
    return round(float(tp), 6), round(float(d), 6)


def _points(g, uf_limit: float) -> list[tuple[float, float, bool]]:
    """(Hs, UF, aprovado). Reprovação sem UF (instável, incompleta, sem valor) vira UF infinito."""
    from .window import APPROVED

    out = []
    for _, r in g.iterrows():
        hs = float(r["Hs (m)"])
        ok = r["Status"] == APPROVED
        try:
            uf = float(r.get("UF máx"))
        except (TypeError, ValueError):
            uf = math.nan
        if math.isnan(uf):
            uf = math.inf if not ok else math.nan
        out.append((hs, uf, ok))
    return out


def estimate_limit(points: list[tuple[float, float, bool]], uf_limit: float) -> tuple[Optional[float], str]:
    """Hs com UF = limite a partir dos pontos com UF finito."""
    fin = sorted((h, u) for h, u, _ in points if math.isfinite(u) and u > 0)
    if not fin:
        return None, ""
    below = [p for p in fin if p[1] <= uf_limit]
    above = [p for p in fin if p[1] > uf_limit]
    pair = None
    if below and above:
        a = max(below)
        b = min(above)
        if b[0] > a[0]:
            pair = (a, b)
    if pair is None and len({h for h, _ in fin}) >= 2:
        near = sorted(fin, key=lambda p: (abs(p[1] - uf_limit), p[0]))
        a = near[0]
        b = next((p for p in near[1:] if abs(p[0] - a[0]) > EPS), None)
        if b is not None:
            pair = tuple(sorted((a, b)))
    if pair is not None:
        (h1, u1), (h2, u2) = pair
        if u2 - u1 > 1e-12:
            return h1 + (uf_limit - u1) * (h2 - h1) / (u2 - u1), "UF (interpolação)"
    h, u = min(fin, key=lambda p: abs(p[1] - uf_limit))
    return h * uf_limit / u, "UF (proporcional a Hs)"


def _grid(hs_min: float, hs_max: float, step: float) -> list[float]:
    n = int(math.floor((hs_max - hs_min) / step + 1e-6))
    return [round(hs_min + k * step, 6) for k in range(n + 1)]


def _bracket(points) -> tuple[Optional[float], Optional[float], str]:
    ok = sorted(h for h, _, a in points if a)
    bad = sorted(h for h, _, a in points if not a)
    hs_bad = bad[0] if bad else None
    below = [h for h in ok if hs_bad is None or h < hs_bad - EPS]
    note = ""
    if hs_bad is not None and any(h > hs_bad + EPS for h in ok):
        note = f"não monotônico: aprovado acima de Hs {hs_bad:g} reprovado"
    return (below[-1] if below else None), hs_bad, note


def plan(status_df, hs_min: float, hs_max: float, hs_step: float, uf_limit: float,
         seeds: Optional[dict] = None, pairs=None, per_pair: int = 2) -> list[PairSearch]:
    """Estado da busca e próximos Hs de cada par (Tp, direção).

    ``seeds``: {(Tp, direção): Hs previsto contínuo}; ``pairs``: pares extras a incluir
    (ex.: todos os da aba Mares) mesmo sem simulação.
    """
    seeds = {_key(*k): v for k, v in (seeds or {}).items()}
    grid = _grid(hs_min, hs_max, hs_step)
    groups = {}
    if status_df is not None and len(status_df):
        df = status_df.dropna(subset=["Tp (s)", "Hs (m)", "Dir (°)"])
        for (tp, d), g in df.groupby(["Tp (s)", "Dir (°)"]):
            groups[_key(tp, d)] = g
    keys = set(groups) | set(seeds) | {_key(*p) for p in (pairs or [])}

    out: dict[tuple[float, float], PairSearch] = {}
    tested: dict[tuple[float, float], set] = {}
    for k in sorted(keys):
        tp, d = k
        s = PairSearch(tp, d)
        pts = _points(groups[k], uf_limit) if k in groups else []
        tested[k] = {round(h, 6) for h, _, _ in pts}
        s.runs = len(pts)
        s.hs_ok, s.hs_bad, s.note = _bracket(pts)
        s.estimate, s.source = estimate_limit(pts, uf_limit)
        if s.estimate is None and pts:
            lo = s.hs_ok if s.hs_ok is not None else hs_min
            hi = s.hs_bad if s.hs_bad is not None else hs_max
            s.estimate, s.source = (lo + hi) / 2.0, "bisseção (sem UF)"
        if s.estimate is None and seeds.get(k) is not None and math.isfinite(seeds[k]):
            s.estimate, s.source = float(seeds[k]), "previsão"
        out[k] = s

    # pares sem nenhuma informação: Tp vizinho da mesma direção, senão o meio da faixa
    known = {k: s.estimate for k, s in out.items() if s.estimate is not None and s.source != "vizinho"}
    for k, s in out.items():
        if s.estimate is not None:
            continue
        same = sorted((abs(t - k[0]), t, e) for (t, d), e in known.items() if abs(d - k[1]) < EPS)
        if same:
            s.estimate, s.source = same[0][2], f"vizinho (Tp {same[0][1]:g} s)"
        else:
            s.estimate, s.source = (hs_min + hs_max) / 2.0, "meio da faixa"

    for k, s in out.items():
        if s.estimate is not None:
            s.estimate = min(max(s.estimate, 0.0), hs_max * 10)
        if s.hs_bad is not None and s.hs_bad <= hs_min + EPS:
            s.status = BELOW_MIN
            continue
        if s.hs_ok is not None and s.hs_ok >= hs_max - EPS:
            s.status = ABOVE_MAX
            continue
        if s.hs_ok is not None and s.hs_bad is not None and s.hs_bad - s.hs_ok <= hs_step + EPS:
            s.status = CLOSED
            continue
        lo = s.hs_ok if s.hs_ok is not None else -math.inf
        hi = s.hs_bad if s.hs_bad is not None else math.inf
        cand = [h for h in grid if lo + EPS < h < hi - EPS and h not in tested[k]]
        if not cand:
            s.status = EMPTY
            continue
        centre = s.estimate + hs_step / 2.0
        cand.sort(key=lambda h: (abs(h - centre), h))
        s.next = sorted(cand[:max(1, int(per_pair))])
        s.status = OPEN
    return [out[k] for k in sorted(out)]


def suggestions(searches: list[PairSearch]) -> list[tuple[float, float, float]]:
    """(Tp, Hs, direção), no formato do ``window.suggest``."""
    return [(s.tp, h, s.direction) for s in searches for h in s.next]


def search_table(searches: list[PairSearch]):
    import pandas as pd

    return pd.DataFrame([{
        "Tp (s)": s.tp, "Dir (°)": s.direction, "Situação": s.status, "Simulações": s.runs,
        "Maior Hs aprovado (m)": s.hs_ok, "Menor Hs reprovado (m)": s.hs_bad,
        "Hs limite estimado (m)": None if s.estimate is None else round(s.estimate, 3), "Estimativa por": s.source,
        "Próximos Hs (m)": "; ".join(f"{h:g}" for h in s.next), "Obs.": s.note} for s in searches]
        or [{"Tp (s)": None}])
