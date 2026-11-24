"""Triagem semi-dinâmica (substitui ``_16`` e o caderno ``_16.1``).

Cada arquivo impõe ao guincho um pay-out harmônico de amplitude A e período Tp
durante 3/4 de ciclo (variable data ``PAYOUT_RATE``, velocidade
A·2π/Tp·cos(2πt/Tp)), sem onda. O pós-processamento (``cvd post`` com as colunas
de critério) dá o UF máximo de cada (Tp, A). Daqui sai a **amplitude crítica**
A*(Tp): a amplitude de movimento vertical do ponto de içamento que leva o UF a
``statics.uf_limit``.

Amplitude crítica: interpolação linear no primeiro cruzamento do UF com o limite
(robusta e sem extrapolar). O ajuste polinomial de 2º grau do caderno é
registrado como comparação (R² e A* do ajuste), não como resultado.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .._orcaflex import require_orcfxapi
from ..log import log
from ..schema import CaseInputs

NAME_RE = re.compile(r"semiDynamic\s*(\d+(?:\.\d+)?)Tp\s*(\d+(?:\.\d+)?)Amp")


def semidynamic_name(operation_name: str, tp: float, amp: float) -> str:
    """Mesmo padrão do _16 (o _16.1 depende dele)."""
    return operation_name + "_dyn_test" + "semiDynamic%00.2fTp%00.2fAmp" % (tp, amp)


def parse_name(name: str) -> Optional[tuple[float, float]]:
    m = NAME_RE.search(str(name))
    return (float(m.group(1)), float(m.group(2))) if m else None


def payout_history(tp: float, amp: float, dt: float) -> tuple[list[float], list[float]]:
    """Tempo e velocidade de pay-out (mesma lei do _16: 3/4 de ciclo a partir de t = 0)."""
    n = int(math.ceil((2 * tp + 0.01) / dt - 1e-9))   # = len(np.arange(0, 2Tp + 0.01, dt))
    times = [-10.0, -dt] + [round(i * dt, 10) for i in range(n)]
    vel = [amp * 2 * math.pi / tp * math.cos(2 * math.pi / tp * t) if 0 <= t <= 0.75 * tp else 0.0 for t in times]
    return times, vel


def generate(case: CaseInputs, base_model: Path, out_dir: Path, tps=None, amps=None,
             overwrite: bool = False) -> list[Path]:
    orc = require_orcfxapi()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tps = list(tps or case.lst("screening.tps"))
    amps = list(amps or case.lst("screening.amplitudes"))
    dt = case.num("screening.time_step")
    payout = case.roles.get("PAYOUT_RATE") or "PayoutRate"
    files = []
    for tp in tps:
        for amp in amps:
            target = out_dir / f"{semidynamic_name(case.operation_name, tp, amp)}.dat"
            if target.exists() and not overwrite:
                raise FileExistsError(f"{target} já existe (use --overwrite)")
            model = orc.Model(str(base_model))
            g = model.general
            g.ImplicitUseVariableTimeStep = "No"
            g.ImplicitConstantTimeStep = dt
            g.TargetLogSampleInterval = case.num("screening.log_interval")
            env = model.environment
            env.WaveDirection = 0
            env.RefCurrentDirection = 0
            env.WaveHeight = 0
            g.StageDuration[0] = 10
            g.StageDuration[1] = 2 * tp
            t, v = payout_history(tp, amp, dt)
            model[payout].IndependentValue = t
            model[payout].DependentValue = v
            model.SaveData(str(target))
            files.append(target)
    log.info("%d arquivo(s) semi-dinâmicos em %s", len(files), out_dir)
    return files


@dataclass
class CriticalAmplitude:
    tp: float
    amplitude: Optional[float]     # None = UF não cruza o limite nas amplitudes testadas
    status: str                    # OK | ACIMA (todas reprovam) | ABAIXO (nenhuma reprova)
    governing: str = ""
    poly2_amplitude: Optional[float] = None
    poly2_r2: Optional[float] = None
    points: int = 0


def _poly2(xs, ys, target):
    import numpy as np

    if len(xs) < 3:
        return None, None
    c = np.polyfit(xs, ys, 2)
    pred = np.polyval(c, xs)
    ss_res = float(np.sum((np.asarray(ys) - pred) ** 2))
    ss_tot = float(np.sum((np.asarray(ys) - np.mean(ys)) ** 2))
    r2 = 1.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot
    roots = np.roots([c[0], c[1], c[2] - target])
    real = sorted(float(r.real) for r in roots if abs(r.imag) < 1e-9 and r.real > 0)
    return (real[0] if real else None), r2


def critical_amplitudes(status_df, uf_limit: float) -> list[CriticalAmplitude]:
    """``status_df``: saída de ``post.window.evaluate`` com colunas 'Tp (s)', 'Amplitude (m)'."""
    out = []
    df = status_df.dropna(subset=["Tp (s)", "Amplitude (m)"])
    for tp, g in df.groupby("Tp (s)"):
        g = g.sort_values("Amplitude (m)")
        xs = [float(x) for x in g["Amplitude (m)"]]
        ys = [float(y) if y == y else math.inf for y in g["UF máx"]]
        gov = list(g["Governa"])
        p_amp, p_r2 = _poly2([x for x, y in zip(xs, ys) if math.isfinite(y)],
                             [y for y in ys if math.isfinite(y)], uf_limit)
        if ys[0] > uf_limit:
            out.append(CriticalAmplitude(float(tp), None, "ACIMA", gov[0], p_amp, p_r2, len(xs)))
            continue
        crit = None
        for i in range(1, len(xs)):
            if ys[i] > uf_limit:
                y0, y1 = ys[i - 1], ys[i]
                w = (uf_limit - y0) / (y1 - y0) if math.isfinite(y1) and y1 > y0 else 0.0
                crit = CriticalAmplitude(float(tp), xs[i - 1] + w * (xs[i] - xs[i - 1]), "OK", gov[i], p_amp, p_r2,
                                         len(xs))
                break
        out.append(crit or CriticalAmplitude(float(tp), None, "ABAIXO", gov[-1], p_amp, p_r2, len(xs)))
    return out


def evaluate(df, case: CaseInputs, uf_limit: Optional[float] = None):
    """UF/Status (mesma regra da janela) + Tp e amplitude lidos do nome do arquivo."""
    from ..post import window as W

    st = W.evaluate(df, case, uf_limit)
    parsed = [parse_name(n) for n in st["Simulation Name"]]
    st["Tp (s)"] = [p[0] if p else None for p in parsed]
    st["Amplitude (m)"] = [p[1] if p else None for p in parsed]
    return st


def write_critical(path: Path, crits: list[CriticalAmplitude]) -> Path:
    import pandas as pd

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([{"Tp (s)": c.tp, "Amplitude crítica (m)": c.amplitude, "Status": c.status,
                        "Governa": c.governing, "A* ajuste 2º grau (m)": c.poly2_amplitude,
                        "R² ajuste": c.poly2_r2, "Pontos": c.points} for c in crits])
    df.to_csv(path, index=False, sep=";", encoding="utf-8-sig")
    return path


def read_critical(path: Path) -> list[CriticalAmplitude]:
    import pandas as pd

    df = pd.read_csv(path, sep=";", encoding="utf-8-sig")
    out = []
    for _, r in df.iterrows():
        a = r["Amplitude crítica (m)"]
        out.append(CriticalAmplitude(float(r["Tp (s)"]), None if a != a else float(a), str(r["Status"]),
                                     str(r.get("Governa", "") or "")))
    return out
