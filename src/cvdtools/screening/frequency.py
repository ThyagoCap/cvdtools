"""Janela prevista pela dinâmica no domínio da frequência do OrcaFlex (o antigo ``_17``, generalizado).

Para cada (Tp, direção) o modelo dinâmico base roda em **frequency domain** com o
mesmo JONSWAP das irregulares (gamma de ``jonswap_gamma``, direção relativa à proa,
corrente na mesma direção). Cada critério dinâmico vira

    valor = |estático| + MPM(tempestade de ``storm_hours`` h)

e UF = valor / limite. O MPM é o do OrcaFlex (``FrequencyDomainMPM``), que é o
equivalente estatístico do "maior evento em 3 h" usado nas irregulares.

Busca do Hs limite: a parte dinâmica é quase proporcional a Hs (só a linearização do
arrasto muda), então UF(Hs) é extrapolado linearmente a partir de um Hs de
referência e refinado por secante (2 a 4 análises por par Tp × direção).

Variáveis no domínio da frequência: grandezas que são módulos (End force, End
moment, Shear force, Curvature) podem não estar disponíveis. Se a variável do
critério falha, tenta-se ``FD_COMPONENTS`` (componentes combinadas pela raiz da
soma dos quadrados, conservador). A variável usada vai para a planilha.

Limitações (por isso é triagem, com validação contra as irregulares em
``screening.compare``): linearização em torno da estática, sem snap/folga de cabos,
sem o travamento do BR e com o contato com o solo linearizado.

Rigidez flexional histerética: o OrcaFlex (11.6) **não** faz a análise no domínio da
frequência com curva de EIx histerética. Cada análise falha com "Operation not available
(This model uses a feature (hysteretic bend stiffness, line type '...') that is not
available for frequency domain analysis.)" (GLF27, 19/11/2026; a help não traz esse
recurso na lista de restrições). Por isso a triagem verifica o modelo **antes** de
qualquer análise e para com uma mensagem clara. Para rodar, ``bend="secant"`` troca a
curva dos tipos da linha por uma rigidez linear equivalente EI = M(κ)/κ numa curvatura
de referência κ informada (``--secant-curvature``). Não há κ universalmente
conservador: κ pequeno dá a rigidez de "stick" (momento maior, curvatura menor), κ
grande a de "slip" (o contrário), e o amortecimento da histerese fica de fora. Ponto de
partida sugerido: κ = 1/MBR; calibre 2 ou 3 valores com ``--compare`` contra as
irregulares. Com histerese, o resultado é **ponto de partida** da busca adaptativa
(``cvd window --seed``), não a janela.
"""

from __future__ import annotations

import concurrent.futures
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .._orcaflex import require_orcfxapi
from ..log import log
from ..schema import CaseInputs

FD_COMPONENTS = {
    "end force": ["Effective tension"],
    "end moment": ["x bend moment", "y bend moment"],
    "bend moment": ["x bend moment", "y bend moment"],
    "shear force": ["x shear force", "y shear force"],
    "curvature": ["x curvature", "y curvature"],
}


@dataclass
class CriterionFD:
    name: str
    obj: str
    variable: str
    extra: object
    kind: str          # "Time History" | "Range Graph Max"
    limit: float


@dataclass
class FDResult:
    direction: float
    tp: float
    hs: float
    uf: Optional[float]
    governing: str = ""
    values: dict = field(default_factory=dict)     # critério -> (estático, MPM, valor, UF, variável usada)
    error: str = ""


def criteria_for_fd(case: CaseInputs) -> list[CriterionFD]:
    out = []
    for c in case.criteria:
        if not c.dynamic:
            continue
        try:
            limit = float(c.limit)
        except (TypeError, ValueError):
            continue
        out.append(CriterionFD(c.name, c.obj, c.variable, c.extra, c.type, limit))
    return out


HYSTERESIS_ERROR = (
    "O OrcaFlex não faz análise no domínio da frequência com rigidez flexional histerética ({items}). "
    "Use --bend-stiffness secant --secant-curvature κ (1/m): a curva vira EI = M(κ)/κ.{hint} Calibre 2 ou 3 "
    "valores de κ com --compare contra as irregulares. A janela continua vindo das irregulares (busca adaptativa: "
    "'cvd window --seed').")


class HystereticFDError(ValueError):
    """Modelo com rigidez flexional histerética e análise FD pedida sem linearização."""


def line_type_names(model, line_name: str) -> list[str]:
    try:
        lt = model[line_name].LineType
    except Exception:
        return []
    names = [lt] if isinstance(lt, str) else list(lt or [])
    return sorted({str(n) for n in names if n})


def bend_curves(model, line_name: str) -> list[tuple[str, str, bool]]:
    """(tipo de linha, curva de EIx, histerética) dos tipos de linha usados pela linha."""
    out = []
    for name in line_type_names(model, line_name):
        try:
            ei = model[name].EIx
        except Exception:
            continue
        if isinstance(ei, str) and ei.strip() and ei.strip() != "~":
            try:
                hyst = str(model[ei].Hysteretic).strip().lower() == "yes"
            except Exception:
                hyst = False
            out.append((name, ei, hyst))
    return out


def secant_ei(curvature_values, moment_values, curvature: float) -> float:
    """EI secante = M(κ)/κ na curva momento × curvatura (interpolação linear; extrapola pelo último trecho)."""
    if curvature <= 0:
        raise ValueError("curvatura de referência deve ser positiva")
    pts = sorted(zip((float(x) for x in curvature_values), (float(y) for y in moment_values)))
    if len(pts) < 2:
        raise ValueError("curva com menos de 2 pontos")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= curvature <= x2 and x2 > x1:
            return (y1 + (curvature - x1) * (y2 - y1) / (x2 - x1)) / curvature
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    return (y2 + (curvature - x2) * (y2 - y1) / (x2 - x1)) / curvature


def hysteretic_items(model, line_name: str) -> list[str]:
    return [f"tipo '{lt}', curva '{curve}'" for lt, curve, h in bend_curves(model, line_name) if h]


def check_bend_stiffness(base_dyn, line_name: str, bend: str, secant_curvature: Optional[float],
                         mbr: Optional[float] = None) -> list[str]:
    """Verificação antes das análises: falha cedo com histerese em ``bend="model"``.

    Devolve as curvas histeréticas encontradas (vazio = nenhuma).
    """
    if bend == "secant" and (not secant_curvature or secant_curvature <= 0):
        raise ValueError("--bend-stiffness secant precisa de --secant-curvature > 0 (1/m)")
    if not line_name:
        return []
    orc = require_orcfxapi()
    model = orc.Model(str(base_dyn))
    hyst = hysteretic_items(model, line_name)
    if hyst and bend != "secant":
        hint = f" Ponto de partida: κ = 1/MBR = {1.0 / mbr:.4g} 1/m (MBR {mbr:g} m)." if mbr else ""
        raise HystereticFDError(HYSTERESIS_ERROR.format(items="; ".join(hyst), hint=hint))
    return hyst


def linearise_bend_stiffness(model, line_name: str, curvature: float) -> list[str]:
    """Troca a curva de EIx dos tipos da linha pela rigidez secante na curvatura dada."""
    notes = []
    for lt, curve, hyst in bend_curves(model, line_name):
        obj = model[curve]
        ei = secant_ei(list(obj.IndependentValue), list(obj.DependentValue), curvature)
        model[lt].EIx = ei
        notes.append(f"{lt}: curva '{curve}'{' (histerética)' if hyst else ''} trocada por EI secante "
                     f"{ei:.1f} kN.m² (κ = {curvature:g} 1/m)")
    if not notes:
        notes.append(f"--bend-stiffness secant: a linha '{line_name}' não usa curva de EIx; nada mudou")
    return notes


def _object_extra(orc, extra):
    if extra == "End A":
        return orc.oeEndA
    if extra == "End B":
        return orc.oeEndB
    if extra == "Touchdown":
        return orc.oeTouchdown
    if isinstance(extra, (int, float)) and not isinstance(extra, bool):
        return orc.oeArcLength(float(extra))
    return None


def _arclength_range(orc, extra):
    if isinstance(extra, (list, tuple)) and len(extra) == 2:
        return orc.arSpecifiedArclengths(float(extra[0]), float(extra[1]))
    return None


def _th_value(obj, orc, variable: str, oe, storm_hours: float) -> tuple[float, float]:
    r = obj.FrequencyDomainResults(variable, oe)
    mpm = obj.FrequencyDomainMPM(variable, storm_hours, oe)
    return float(r.StaticValue), float(mpm)


def _rg_value(obj, orc, variable: str, rng, storm_hours: float) -> tuple[float, float]:
    rg = obj.RangeGraph(variable, None, None, rng, storm_hours)
    best = None
    for s, m in zip(rg.StaticValue, rg.MPM):
        v = abs(float(s)) + float(m)
        if best is None or v > best[0]:
            best = (v, float(s), float(m))
    if best is None:
        raise ValueError(f"Range graph vazio para {variable}")
    return best[1], best[2]


def criterion_value(model, orc, c: CriterionFD, storm_hours: float) -> tuple[float, float, float, str]:
    """(estático, MPM, valor = |estático| + MPM, variável usada)."""
    obj = model[c.obj]

    def one(variable):
        if c.kind == "Time History":
            return _th_value(obj, orc, variable, _object_extra(orc, c.extra), storm_hours)
        return _rg_value(obj, orc, variable, _arclength_range(orc, c.extra), storm_hours)

    try:
        s, m = one(c.variable)
        return s, m, abs(s) + m, c.variable
    except Exception as first:
        comps = FD_COMPONENTS.get(c.variable.strip().lower())
        if not comps:
            raise first
        parts = [one(v) for v in comps]
        value = math.sqrt(sum((abs(s) + m) ** 2 for s, m in parts))
        static = math.sqrt(sum(s ** 2 for s, _ in parts))
        mpm = math.sqrt(sum(m ** 2 for _, m in parts))
        return static, mpm, value, " ⊕ ".join(comps)


def _set_sea(model, vessel: str, direction: float, tp: float, hs: float) -> None:
    from ..dynamics.seastates import jonswap_gamma

    try:
        model.Reset()          # dados só mudam com o modelo em Reset
    except Exception:
        pass
    heading = model[vessel].InitialHeading
    env = model.environment
    env.WaveType = "JONSWAP"
    env.WaveDirection = direction + heading
    try:
        env.RefCurrentDirection = direction + heading
    except Exception:
        pass
    env.WaveGamma = jonswap_gamma(tp)
    env.WaveTp = tp
    env.WaveHs = hs


def evaluate_fd(model, orc, crits: list[CriterionFD], vessel: str, direction: float, tp: float, hs: float,
                storm_hours: float) -> FDResult:
    _set_sea(model, vessel, direction, tp, hs)
    model.RunSimulation()
    res = FDResult(direction, tp, hs, None)
    worst = (-1.0, "")
    failed = []
    for c in crits:
        try:
            s, m, v, used = criterion_value(model, orc, c, storm_hours)
        except Exception as exc:     # critério sem resultado em FD: anotado, não derruba os outros
            msg = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
            res.values[c.name] = (None, None, None, None, f"sem FD: {msg}")
            failed.append(c.name)
            continue
        uf = v / c.limit
        res.values[c.name] = (s, m, v, uf, used)
        if uf > worst[0]:
            worst = (uf, c.name)
    if worst[0] >= 0:
        res.uf, res.governing = worst
    if failed:
        res.error = "critérios sem resultado em FD (fora do UF): " + ", ".join(failed)
    return res


def _limit_from(points: list[FDResult], uf_limit: float) -> Optional[float]:
    """Hs com UF = limite por interpolação/extrapolação linear dos dois pontos mais próximos do limite."""
    pts = sorted((p.hs, p.uf) for p in points if p.uf is not None)
    if not pts:
        return None
    if len(pts) == 1:
        hs, uf = pts[0]
        return hs * uf_limit / uf if uf > 0 else None   # parte estática desprezada no 1º passo
    best = sorted(pts, key=lambda p: abs(p[1] - uf_limit))[:2]
    (h1, u1), (h2, u2) = sorted(best)
    if abs(u2 - u1) < 1e-12:
        return None
    return h1 + (uf_limit - u1) * (h2 - h1) / (u2 - u1)


def fd_pair(base_dyn: str, crits: list[dict], vessel: str, direction: float, tp: float, grid_hs: list[float],
            hs_ref: float, hs_max: float, storm_hours: float, uf_limit: float, tol: float = 0.02,
            max_runs: int = 6, line: str = "", bend: str = "model", secant_curvature: Optional[float] = None) -> dict:
    """Um par (Tp, direção): UF nos Hs da grade e Hs limite. Função de módulo (roda em outro processo).

    ``bend``: ``"model"`` (rigidez do modelo; com curva histerética o par não roda) ou ``"secant"`` (EI secante em
    ``secant_curvature`` nos tipos da linha ``line``).
    """
    orc = require_orcfxapi()
    crits = [CriterionFD(**c) for c in crits]
    try:
        model = orc.Model(base_dyn, threadCount=1)
    except TypeError:
        model = orc.Model(base_dyn)
    try:
        model.general.DynamicsSolutionMethod = "Frequency domain"
    except Exception as exc:
        return {"direction": direction, "tp": tp, "points": [], "hs_limit": None,
                "error": f"modelo não aceita frequency domain: {exc}"}
    notes: list[str] = []
    if bend == "secant":
        if not secant_curvature or secant_curvature <= 0:
            return {"direction": direction, "tp": tp, "points": [], "hs_limit": None, "notes": notes,
                    "error": "--bend-stiffness secant precisa de --secant-curvature > 0 (1/m)"}
        notes += linearise_bend_stiffness(model, line, float(secant_curvature))
    elif line:
        hyst = hysteretic_items(model, line)
        if hyst:
            return {"direction": direction, "tp": tp, "points": [], "hs_limit": None, "notes": notes,
                    "error": HYSTERESIS_ERROR.format(items=", ".join(hyst), hint="")}
    points: list[FDResult] = []
    error = ""

    def run(hs):
        try:
            p = evaluate_fd(model, orc, crits, vessel, direction, tp, hs, storm_hours)
        except Exception as exc:     # uma análise que falha não derruba o par
            p = FDResult(direction, tp, hs, None, error=str(exc).strip().splitlines()[-1] if str(exc).strip()
                         else type(exc).__name__)
        points.append(p)
        return p

    for hs in sorted(set(grid_hs) | {hs_ref}):
        run(hs)
    limit = None
    for _ in range(max_runs):
        guess = _limit_from(points, uf_limit)
        if guess is None:
            break
        guess = min(max(guess, 0.05), hs_max)
        if any(abs(p.hs - guess) < tol for p in points):
            limit = guess
            break
        p = run(guess)
        if p.uf is None:
            error = p.error or "análise sem UF"
            break
        if abs(p.uf - uf_limit) < 0.005:
            limit = guess
            break
    else:
        limit = _limit_from(points, uf_limit)
    if limit is not None and limit >= hs_max - 1e-9:
        limit = hs_max
    errs = [p.error for p in points if p.error]
    return {"direction": direction, "tp": tp, "hs_limit": limit,
            "points": [p.__dict__ for p in sorted(points, key=lambda q: q.hs)],
            "error": error or (errs[0] if errs else ""), "notes": notes}


def run_fd_screening(case: CaseInputs, base_dyn: Path, grid, hs_ref: float, storm_hours: float = 3.0,
                     workers: int = 1, uf_limit: Optional[float] = None, hs_max: Optional[float] = None,
                     bend: str = "model", secant_curvature: Optional[float] = None) -> list[dict]:
    """``grid``: [(direção relativa, Tp, [Hs da grade])] (a aba Mares). Um par por (direção, Tp)."""
    from dataclasses import asdict

    crits = [asdict(c) for c in criteria_for_fd(case)]
    if not crits:
        raise ValueError("Nenhum critério dinâmico com limite numérico (aba Critérios)")
    uf_limit = float(uf_limit if uf_limit is not None else case.num("statics.uf_limit"))
    hs_max = float(hs_max if hs_max is not None else case.num("simulation.hs_max"))
    vessel = case.role("VESSEL")
    pairs: dict[tuple[float, float], set] = {}
    for d, tp, hss in grid:
        pairs.setdefault((float(d), float(tp)), set()).update(float(h) for h in hss)
    try:
        line = case.role("LINE")
    except KeyError:
        line = ""
    try:
        mbr = case.num("line.mbr")
    except Exception:
        mbr = None
    check_bend_stiffness(base_dyn, line, bend, secant_curvature, mbr)       # antes de qualquer análise
    common = dict(crits=crits, vessel=vessel, hs_ref=hs_ref, hs_max=hs_max, storm_hours=storm_hours,
                  uf_limit=uf_limit, line=line, bend=bend, secant_curvature=secant_curvature)
    out = []
    if workers <= 1:
        for (d, tp), hss in sorted(pairs.items()):
            out.append(fd_pair(str(base_dyn), direction=d, tp=tp, grid_hs=sorted(hss), **common))
            log.info("  FD dir %g°, Tp %g s: Hs limite %s", d, tp, _fmt(out[-1]["hs_limit"]))
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fd_pair, str(base_dyn), direction=d, tp=tp, grid_hs=sorted(hss), **common): (d, tp)
                    for (d, tp), hss in sorted(pairs.items())}
            for fut in concurrent.futures.as_completed(futs):
                d, tp = futs[fut]
                try:
                    out.append(fut.result())
                except Exception as exc:
                    out.append({"direction": d, "tp": tp, "hs_limit": None, "points": [], "error": str(exc)})
                log.info("  FD dir %g°, Tp %g s: Hs limite %s", d, tp, _fmt(out[-1]["hs_limit"]))
    notes = fd_notes(out)
    for n in notes:
        log.warning("%s", n)
    return sorted(out, key=lambda r: (r["direction"], r["tp"]))


def fd_notes(results: list[dict]) -> list[str]:
    """Notas únicas das análises (histerese, linearização)."""
    seen = []
    for r in results:
        for n in r.get("notes", []) or []:
            if n not in seen:
                seen.append(n)
    return seen


def _fmt(v) -> str:
    return "-" if v is None else f"{v:.2f} m"


def write_fd(path: Path, results: list[dict], hs_step: float, title: str, operation: str) -> Path:
    """Planilha no formato da triagem (abas Janela prevista e Previsão) + aba 'UF FD' com todos os pontos."""
    import pandas as pd

    from .predict import Predicted

    pred = []
    for r in results:
        raw = r["hs_limit"]
        hs = None if raw is None else round(math.floor(raw / hs_step + 1e-9) * hs_step, 6)
        pred.append(Predicted(r["direction"], r["tp"], hs, raw, r.get("error", "")))
    rows = []
    for r in results:
        for p in r["points"]:
            row = {"Direção (°)": r["direction"], "Tp (s)": r["tp"], "Hs (m)": p["hs"], "UF FD": p["uf"],
                   "Governa": p["governing"], "Erro": p.get("error", "")}
            for name, (s, m, v, uf, used) in p["values"].items():
                row[f"{name}: estático"] = s
                row[f"{name}: MPM"] = m
                row[f"{name}: UF"] = uf
                row[f"{name}: variável"] = used
            rows.append(row)
    _write_prediction_book(path, pred, title, operation,
                           "Janela PREVISTA por frequency domain (triagem, não substitui as irregulares)")
    with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        pd.DataFrame(rows or [{}]).to_excel(w, sheet_name="UF FD", index=False)
        notes = fd_notes(results)
        if notes:
            pd.DataFrame({"Nota": notes}).to_excel(w, sheet_name="Notas FD", index=False)
    return path


def _write_prediction_book(path: Path, pred, title: str, operation: str, banner: str) -> None:
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"Direção (°)": p.direction, "Tp (s)": p.tp, "Hs prevista (m)": p.hs, "Hs limite (contínuo, m)": p.raw,
             "Obs.": p.note} for p in pred]
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="Previsão", index=False)
    wb = load_workbook(path)
    ws = wb.create_sheet("Janela prevista", 0)
    bold = Font(bold=True)
    ws.cell(1, 2, banner)
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
