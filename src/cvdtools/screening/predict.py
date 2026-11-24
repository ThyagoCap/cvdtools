"""Janela prevista = amplitude crítica (semi-dinâmico) ÷ resposta da embarcação.

Hs_prevista(direção, Tp) = A*(Tp) / R(direção, Tp)

- A*(Tp): amplitude de movimento vertical do ponto de içamento que leva o UF ao
  limite (``semidynamic.critical_amplitudes``);
- R(direção, Tp): heave máximo do ponto de içamento por metro de Hs
  (``vessel.response_per_hs``).

Hipóteses (simplificações conscientes, para triagem; a janela final continua
vindo das simulações irregulares):
1. a resposta da linha depende só do movimento vertical do ponto de içamento
   (sem efeito da onda direta na linha/MCV e sem movimentos horizontais);
2. a carga cresce com a amplitude como no pay-out harmônico de 3/4 de ciclo;
3. RAOs lineares (resposta proporcional a Hs).

O resultado arredondado para baixo no passo de Hs vira a grade inicial das
simulações irregulares (Hs prevista e um passo acima/abaixo), no lugar de uma
grade completa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .semidynamic import CriticalAmplitude


@dataclass
class Predicted:
    direction: float
    tp: float
    hs: Optional[float]      # None = sem previsão
    raw: Optional[float]
    note: str = ""


def _interp_critical(crits: dict[float, CriticalAmplitude], tp: float) -> tuple[Optional[float], str]:
    if tp in crits:
        c = crits[tp]
        if c.amplitude is not None:
            return c.amplitude, ""
        return (0.0, "UF acima do limite já na menor amplitude") if c.status == "ACIMA" else \
            (None, "UF não atinge o limite nas amplitudes testadas")
    tps = sorted(t for t, c in crits.items() if c.amplitude is not None)
    lo = [t for t in tps if t < tp]
    hi = [t for t in tps if t > tp]
    if not lo or not hi:
        return None, f"Tp {tp:g} s fora da faixa da triagem"
    a, b = lo[-1], hi[0]
    w = (tp - a) / (b - a)
    return crits[a].amplitude + w * (crits[b].amplitude - crits[a].amplitude), "A* interpolada entre Tps"


def predict(crits: list[CriticalAmplitude], response: dict[tuple[float, float], float], hs_step: float,
            hs_max: float) -> list[Predicted]:
    by_tp = {c.tp: c for c in crits}
    out = []
    for (d, tp), r in sorted(response.items()):
        amp, note = _interp_critical(by_tp, tp)
        if amp is None:
            out.append(Predicted(d, tp, None, None, note))
            continue
        if r <= 0:
            out.append(Predicted(d, tp, hs_max, math.inf, "resposta nula: limitado a Hs máx."))
            continue
        raw = amp / r
        hs = min(hs_max, math.floor(raw / hs_step + 1e-9) * hs_step)
        out.append(Predicted(d, tp, round(hs, 6), raw, note))
    return out


def seed_grid(pred: list[Predicted], hs_step: float, hs_min: float, hs_max: float, around: int = 1):
    """Grade (direção, Tp, [Hs]) para as irregulares: Hs prevista ± ``around`` passos."""
    grid = []
    for p in pred:
        if p.hs is None:
            continue
        hs = sorted({round(p.hs + k * hs_step, 6) for k in range(-around, around + 1)
                     if hs_min - 1e-9 <= p.hs + k * hs_step <= hs_max + 1e-9})
        if hs:
            grid.append((p.direction, p.tp, hs))
    return grid


def write_prediction(path, pred: list[Predicted], crits: list[CriticalAmplitude], title: str, operation: str):
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    rows = [{"Direção (°)": p.direction, "Tp (s)": p.tp, "Hs prevista (m)": p.hs, "A*/R (m)": p.raw, "Obs.": p.note}
            for p in pred]
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="Previsão", index=False)
        pd.DataFrame([{"Tp (s)": c.tp, "A* (m)": c.amplitude, "Status": c.status, "Governa": c.governing}
                      for c in crits]).to_excel(w, sheet_name="Amplitude crítica", index=False)
    wb = load_workbook(path)
    ws = wb.create_sheet("Janela prevista", 0)
    bold = Font(bold=True)
    ws.cell(1, 2, "Janela PREVISTA (triagem: semi-dinâmico ÷ resposta da embarcação) — não substitui as irregulares")
    ws.cell(2, 2, title).font = bold
    ws.cell(3, 4, operation).font = bold
    ws.cell(4, 4, "Direction [°]").font = bold
    ws.cell(5, 4, "Tp [s]").font = bold
    dirs = sorted({p.direction for p in pred})
    tps = sorted({p.tp for p in pred})
    for j, d in enumerate(dirs):
        ws.cell(4, 6 + j, d).font = bold
    for i, tp in enumerate(tps):
        ws.cell(5 + i, 5, tp).font = bold
    pos = {(p.direction, p.tp): p for p in pred}
    for i, tp in enumerate(tps):
        for j, d in enumerate(dirs):
            p = pos.get((d, tp))
            if p is not None and p.hs is not None:
                ws.cell(5 + i, 6 + j, p.hs)
    wb.save(path)
    return path
