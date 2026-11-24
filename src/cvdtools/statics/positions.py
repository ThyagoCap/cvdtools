"""Posições estáticas relevantes (substitui _03, _04, _05 e _08).

Casos com corcova (1st end DCVD / 2nd end CVD):

==========================  ======  ==========================================================
Posição                     MCV     Regra
==========================  ======  ==========================================================
01.Horizontal               livre   ângulo 0 e MCV na altura do hub (ajusta guincho e guindaste)
02.CB_PullIn                livre   a partir da horizontal, RECOLHENDO o guincho, até |Δângulo| = cb_angle
03.CB_PayOut                livre   a partir da horizontal, SOLTANDO o guincho, até |Δângulo| = cb_angle
04.Max_Free_Overpull        livre   maior puxada com UF ≤ uf_limit, parando também em
                                    Δângulo = free_overpull_angle_factor × cb_angle (padrão 3 × 3°)
05.Max_Fixed_Overpull       fixo    maior puxada com UF ≤ uf_limit
==========================  ======  ==========================================================

Pull-in = cabo do guincho mais curto que na horizontal (comentário do usuário, 18/11/2026); o sinal do
ângulo correspondente é medido no modelo, não suposto. As posições 02 a 05 usam o comprimento do cabo
do guindaste encontrado na horizontal.

Cada posição é recalculada exatamente na puxada final e salva como .sim. Os
resultados vão para ``positions.csv``; todas as avaliações (inclusive as que não
convergiram) vão para ``statics_log.csv``.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..schema import CaseInputs, MissingInputError
from ..log import log
from .engine import FIXED, FREE, StaticsEngine
from .solver import NotBracketed, TooManyEvaluations


@dataclass
class PositionSpec:
    name: str
    state: str
    target: str          # "horizontal" | "angle_rel" | "uf_or_angle" | "uf"
    value: float
    start_after: tuple = ()   # posições cujas puxadas servem de ponto de partida
    description: str = ""
    direction: int = +1       # +1 = recolhendo o guincho (mais puxada); -1 = soltando


def _positions(case: CaseInputs, prefix: str, what: str) -> list[PositionSpec]:
    a = case.num("statics.cb_angle")
    uf = case.num("statics.uf_limit")
    k = case.num("statics.free_overpull_angle_factor")
    return [
        PositionSpec("01.Horizontal", FREE, "horizontal", 0.0, (), "MCV livre, horizontal e na altura do hub"),
        PositionSpec(f"02.{prefix}PullIn", FREE, "angle_rel", a, ("01.Horizontal",),
                     f"pull-in: recolhendo o guincho a partir da horizontal até {a:g}°", +1),
        PositionSpec(f"03.{prefix}PayOut", FREE, "angle_rel", a, ("01.Horizontal",),
                     f"pay-out: soltando o guincho a partir da horizontal até {a:g}°", -1),
        PositionSpec("04.Max_Free_Overpull", FREE, "uf_or_angle", uf, ("01.Horizontal",),
                     f"maior {what} com o MCV livre: UF = {uf:g} ou {k * a:g}° a partir da horizontal", +1),
        PositionSpec("05.Max_Fixed_Overpull", FIXED, "uf", uf, ("01.Horizontal",),
                     f"maior {what} com o MCV no hub e SEM o cabo do guindaste que atende os critérios (se o UF já "
                     f"passa de {uf:g} na horizontal, solta o guincho até UF = {uf:g})", +1),
    ]


def camelback_positions(case: CaseInputs) -> list[PositionSpec]:
    return _positions(case, "CB_", "puxada")


def length_positions(case: CaseInputs) -> list[PositionSpec]:
    """Sem corcova (fase 5): as mesmas posições, controlando o comprimento suspenso."""
    return _positions(case, "", "redução de comprimento suspenso")


def positions_for(case: CaseInputs) -> list[PositionSpec]:
    return camelback_positions(case) if case.operation.camelback else length_positions(case)


@dataclass
class PositionResult:
    name: str
    description: str
    state: str
    target: str
    value: float
    pull: float = float("nan")
    winch_length: float = float("nan")
    angle: float = float("nan")
    uf_max: float = float("nan")
    governing: str = ""
    link_tension: float = float("nan")
    crane_length: float = float("nan")
    mcv_z: float = float("nan")
    x_shift: float = 0.0
    sim: str = ""
    status: str = "OK"
    message: str = ""


def run_positions(case: CaseInputs, model_path: Path, out_dir: Path, only: Optional[list[str]] = None,
                  save_sims: bool = True) -> tuple[list[PositionResult], StaticsEngine]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    eng = StaticsEngine(case, model_path)
    eng.find_reference()
    specs = [s for s in positions_for(case) if not only or s.name in only or s.name.split(".", 1)[-1] in only]
    results: dict[str, PositionResult] = {}

    for spec in specs:
        res = PositionResult(spec.name, spec.description, spec.state, spec.target, spec.value)
        starts = [results[n].pull for n in spec.start_after if n in results and not math.isnan(results[n].pull)]
        start = max(starts) if starts else 0.0
        log.info("Posição %s: %s (partindo de puxada %.3f m)", spec.name, spec.description, start)
        try:
            if spec.target == "horizontal":
                pull = eng.solve_horizontal(spec.name, start)
            else:
                # todas as posições partem da horizontal (puxada, cabo do guindaste, translação e forma)
                if eng.horizontal is None:
                    log.info("  (calculando a horizontal antes, ponto de partida de %s)", spec.name)
                    eng.solve_horizontal("01.Horizontal (referência)")
                start = eng.horizontal["pull"]
                eng.start_from_horizontal(spec.state)
                eng.without_crane = spec.name.startswith("05.")
                if spec.target == "angle_rel":
                    target = eng.horizontal["angle"] + spec.direction * eng.angle_sign * spec.value
                    log.info("  Alvo: ângulo %.2f° (%s o guincho)", target,
                             "recolhendo" if spec.direction > 0 else "soltando")
                    pull = eng.solve_pull(spec.name, spec.state, "angle", target, start, spec.direction)
                else:
                    pull = eng.solve_pull(spec.name, spec.state, spec.target, spec.value, start, spec.direction,
                                          allow_payout=spec.target == "uf")
                    if spec.target == "uf" and pull < start - 1e-9:
                        res.message = f"UF passava de {spec.value:g} na horizontal: guincho solto {start - pull:.3f} m"
            ev = eng.at(spec.name, spec.state, pull)
            if not ev.converged:
                raise NotBracketed(f"estática não converge na puxada final {pull:.3f} m")
            res.pull, res.winch_length = pull, ev.winch_length
            res.angle, res.uf_max, res.governing, res.link_tension = ev.angle, ev.uf_max, ev.governing, ev.link_tension
            res.crane_length, res.mcv_z, res.x_shift = ev.crane_length, ev.mcv_z, eng.x_shift
            if spec.target == "uf_or_angle" and eng.horizontal is not None:
                limit_angle = case.num("statics.free_overpull_angle_factor") * case.num("statics.cb_angle")
                d_angle = abs(ev.angle - eng.horizontal["angle"])
                if ev.uf_max < spec.value - 1e-3 and d_angle >= limit_angle - 0.05:
                    res.governing = f"ângulo ({d_angle:.2f}° da horizontal)"
            if spec.target in ("uf", "uf_or_angle") and ev.uf_max > spec.value + 1e-6:
                res.status, res.message = "AVISO", f"UF final {ev.uf_max:.4f} acima do limite"
            if save_sims:
                try:
                    # .dat primeiro (posições calculadas como forma inicial = ponto de partida das dinâmicas)
                    from ..model.view import set_default_view

                    set_default_view(eng.model, case)
                    eng.model.SaveData(str(out_dir / f"{spec.name}.dat"))
                    res.sim = eng.save_simulation(out_dir / f"{spec.name}.sim").name
                except Exception as exc:  # falha ao salvar não perde as outras posições
                    res.status = "AVISO"
                    res.message = f"posição calculada, mas não consegui salvar os arquivos: {exc}".strip()
                    log.warning("  %s", res.message)
        except (NotBracketed, TooManyEvaluations, MissingInputError) as exc:
            res.status, res.message = "FALHOU", str(exc)
        finally:
            eng.without_crane = False
        if res.status == "FALHOU":
            log.error("Posição %s FALHOU: %s", spec.name, res.message)
        else:
            log.info("Posição %s: puxada %.3f m, ângulo %.2f°, UF %.3f (%s) %s", spec.name, res.pull, res.angle,
                     res.uf_max, res.governing or "-", res.message)
        results[spec.name] = res

    write_positions(list(results.values()), eng, out_dir / "positions.csv")
    eng.write_log(out_dir / "statics_log.csv")
    return list(results.values()), eng


def write_positions(results: list[PositionResult], eng: StaticsEngine, path: Path) -> Path:
    from dataclasses import asdict

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["# Caso", eng.case.case_id])
        w.writerow(["# Modelo", str(eng.model_path)])
        w.writerow(["# Comprimento do cabo no modelo montado (m)", f"{eng.L_built:.4f}"])
        w.writerow(["# Referência L_ref (lingas começam a tracionar) (m)", f"{eng.L_ref:.4f}"])
        w.writerow(["# Cabo do guindaste: modelo montado / horizontal (m)", f"{eng.crane_built:.4f}",
                    f"{eng.horizontal['crane']:.4f}" if eng.horizontal and eng.horizontal.get("crane") else "-"])
        w.writerow(["# Z do MCV no hub (m)", f"{eng.hub_z:.4f}" if eng.hub_z is not None else "-"])
        w.writerow(["# Critérios sem limite numérico (fora do UF)", "; ".join(eng.skipped) or "-"])
        fields = list(asdict(results[0]).keys()) if results else []
        w.writerow(fields)
        for r in results:
            w.writerow([getattr(r, k) for k in fields])
    return path
