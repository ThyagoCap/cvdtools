"""Preparo da corcova (masterlink, lingas e cabo do guincho) antes da estática final.

Causa raiz da instabilidade do modelo montado (GLF27 2CVD MOBO New Out, v0.5.4)
------------------------------------------------------------------------------
O builder muda a geometria da linha (declinação 180 - ângulo, End B X, KPs das
lingas 17/22 m em vez de 13,74/19,74 m do modelo base), mas o masterlink e o cabo
do guincho só eram deslocados pela variação de LDA. A estática do modelo completo
partia, portanto, de um masterlink que não tem relação com a linha: as lingas
começam esticadas (ou frouxas) por metros, com rigidez de 6000 kN, e a estática
de Newton diverge. No log: 10 000 iterações com erro em torno de 98e3 no nó 12 da
linha (restritor de curvatura, a ~6 m do MCV), enquanto o mesmo modelo sem a
corcova (ajuste do End B) converge em ~1 s.

O que este módulo faz (o papel do antigo _02 "ajuste do guincho", automatizado):
1. estática numa cópia SEM a corcova -> posição convergida da linha nos KPs das lingas
   (se ela não convergir, os pontos saem da catenária geométrica do builder);
2. masterlink (``model.masterlink_position``):

   - ``baixo`` (padrão desde a 0.10.0): ``model.masterlink_height`` = 2 m acima do fundo (0.10.3; 4,5 m na 0.10.2),
     com X na média dos dois pontos das lingas CB1/CB2 na linha + ``model.masterlink_x_offset``
     (1 m) e Y na média. As lingas ficam
     frouxas; a corcova sai da puxada pelo guincho;
   - ``apice`` (até a 0.9.3): acima dos dois pontos, com as lingas exatamente no
     comprimento (tração zero);
3. comprimento da seção principal do cabo do guincho calculado para o masterlink
   pendurar nessa posição, descontando o alongamento pelo peso próprio (seção de
   extensão pela curva axial, se houver) e somando uma folga
   ``model.camelback_slack`` (lingas levemente frouxas = antes da "puxada zero").

A estática final (``endb.finalize_statics``) roda depois em duas etapas: masterlink
fixo nessa posição (1ª estática) e, a partir das posições convergidas, masterlink livre,
pendurado no cabo do guincho perto dessa posição.
"""

from __future__ import annotations

import math
from pathlib import Path

from .._orcaflex import StaticsFailed, calculate_statics_robust, require_orcfxapi
from ..log import log
from ..schema import CaseInputs

G = 9.80665
RHO = 1.025  # t/m³


# --------------------------------------------------------------------------- geometria (pura, testável)

def masterlink_position(p1, p2, sling: float) -> tuple[tuple[float, float, float], str]:
    """Ponto acima de p1 e p2 (plano vertical que os contém) a ``sling`` de ambos.

    Devolve (posição, aviso). Se os pontos estão mais afastados que 2 × linga, o
    masterlink fica no ponto médio, 0,1 m acima, e o aviso explica.
    """
    (x1, y1, z1), (x2, y2, z2) = p1, p2
    mx, my, mz = (x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    d = math.sqrt(dx * dx + dy * dy + dz * dz)
    if d < 1e-9:
        return (mx, my, mz + sling), ""
    half = d / 2
    if half >= sling:
        return (mx, my, mz + 0.1), (f"pontos das lingas a {d:.2f} m um do outro, mais que 2 × linga ({2 * sling:.2f} m): "
                                    f"masterlink posto no ponto médio")
    h = math.sqrt(sling * sling - half * half)
    # perpendicular a (dx,dy,dz) no plano vertical, apontando para cima
    hx, hy = dx, dy
    hor = math.sqrt(hx * hx + hy * hy)
    if hor < 1e-9:   # pontos na mesma vertical (não deveria ocorrer)
        return (mx + h, my, mz), ""
    ux, uy = hx / hor, hy / hor
    # vetor no plano vertical: n = (-dz*ux, -dz*uy, hor) normalizado
    nx, ny, nz = -dz * ux, -dz * uy, hor
    nn = math.sqrt(nx * nx + ny * ny + nz * nz)
    return (mx + h * nx / nn, my + h * ny / nn, mz + h * nz / nn), ""


def low_masterlink_position(p1, p2, seabed_z: float, height: float, x_offset: float = 0.0) -> tuple[float, float, float]:
    """Masterlink a ``height`` acima do fundo, com X na média dos pontos das lingas + ``x_offset`` e Y na média."""
    return (p1[0] + p2[0]) / 2.0 + x_offset, (p1[1] + p2[1]) / 2.0, seabed_z + height


def masterlink_mode(case: CaseInputs) -> str:
    v = case.get("model.masterlink_position")
    text = str(v).strip().lower().replace("á", "a") if isinstance(v, str) and v.strip() else "baixo"
    if text not in ("baixo", "apice"):
        from ..schema import MissingInputError

        raise MissingInputError(f"model.masterlink_position = {v!r}: use baixo ou apice")
    return text


def estimate_line_points(case: CaseInputs, model, arcs) -> list[tuple[float, float, float]]:
    """Pontos da linha nos arc lengths pela catenária geométrica do builder (quando a estática falha).

    Catenária inextensível do End A (gooseneck do MCV) até o toque horizontal no solo e trecho
    reto no fundo depois dele; fundo plano na lâmina d'água.
    """
    from .builder import catenary_geometric, end_a_declination

    od = case.num("line.od")
    x_end_a = _num(model[case.role("MCV")], "InitialX") + case.num("mcv.g")
    z_bottom = -float(model.environment.WaterDepth) + od / 2.0
    total = sum(float(v) for v in model[case.role("LINE")].Length)
    try:
        cat = catenary_geometric(0.0, case.num("mcv.a") - od / 2.0,
                                 end_a_declination(case.num("mcv.angle"), "fixed") - 90.0, total)
    except ValueError:
        return [(x_end_a + float(s), 0.0, z_bottom) for s in arcs]
    a, s_susp, x_span = cat["catenary_parameter"], cat["suspended_length"], cat["x_span"]
    pts = []
    for s in arcs:
        s = float(s)
        if s < s_susp:
            sigma = s_susp - s
            pts.append((x_end_a + x_span - a * math.asinh(sigma / a), 0.0, z_bottom + math.hypot(a, sigma) - a))
        else:
            pts.append((x_end_a + x_span + (s - s_susp), 0.0, z_bottom))
    return pts


def submerged_weight_per_m(mass_per_m: float, od: float) -> float:
    """kN/m de uma linha cheia de ar (massa t/m, OD m)."""
    return (mass_per_m - RHO * math.pi / 4 * od * od) * G


def strain_function(ea=None, table=None):
    """Deformação em função da tração: EA constante (kN) ou tabela (deformação, tração)."""
    if table is not None:
        xs, ys = [float(v) for v in table[0]], [float(v) for v in table[1]]
        pts = sorted(zip(ys, xs))
        ts, es = [p[0] for p in pts], [p[1] for p in pts]

        def f(t: float) -> float:
            if t <= ts[0]:
                return es[0]
            for i in range(1, len(ts)):
                if t <= ts[i]:
                    w = (t - ts[i - 1]) / (ts[i] - ts[i - 1]) if ts[i] > ts[i - 1] else 0.0
                    return es[i - 1] + w * (es[i] - es[i - 1])
            slope = (es[-1] - es[-2]) / (ts[-1] - ts[-2]) if len(ts) > 1 and ts[-1] > ts[-2] else 0.0
            return es[-1] + slope * (t - ts[-1])
        return f
    ea = float(ea)
    return lambda t: t / ea if ea > 0 else 0.0


def hanging_lengths(sections, bottom_load: float, ds: float = 1.0) -> list[float]:
    """Comprimento esticado de cada seção de um cabo pendurado (de baixo para cima).

    ``sections``: lista [(comprimento sem esticar, peso submerso kN/m, f_deformação)]
    da seção de BAIXO para a de CIMA. ``bottom_load``: carga pendurada no fim (kN).
    """
    out, t = [], bottom_load
    for length, w, eps in sections:
        n = max(1, int(math.ceil(length / ds)))
        h = length / n
        stretched = 0.0
        for _ in range(n):
            t_mid = t + w * h / 2
            stretched += h * (1.0 + eps(max(t_mid, 0.0)))
            t += w * h
        out.append(stretched)
    return out


def solve_top_section(top_target: float, fixed_sections, top_w: float, top_eps, bottom_load: float) -> float:
    """Comprimento sem esticar da seção de CIMA para o cabo esticado medir ``top_target``.

    ``fixed_sections``: seções de baixo (já definidas), na ordem de ``hanging_lengths``.
    """
    below = hanging_lengths(fixed_sections, bottom_load)
    load = bottom_load + sum(L * w for L, w, _ in fixed_sections)
    need = top_target - sum(below)
    if need <= 0:
        raise ValueError(f"O cabo abaixo da seção principal já mede {sum(below):.2f} m esticado, mais que a "
                         f"distância disponível ({top_target:.2f} m)")
    length = need
    for _ in range(50):
        got = hanging_lengths([(length, top_w, top_eps)], load)[0]
        new = length * need / got
        if abs(new - length) < 1e-6:
            break
        length = new
    return length


# --------------------------------------------------------------------------- leitura do modelo

def _num(obj, name, default=0.0):
    try:
        return float(getattr(obj, name))
    except Exception:
        return default


def _line_type_strain(model, lt):
    ea = getattr(lt, "EA", 0.0)
    if isinstance(ea, str) and ea:
        vd = model[ea]
        return strain_function(table=(list(vd.IndependentValue), list(vd.DependentValue))), f"curva '{ea}'"
    return strain_function(ea=float(ea)), f"EA {float(ea):g} kN"


def winch_sections(model, wire):
    """Seções do cabo do guincho de BAIXO para CIMA: [(L, w, f_eps, nome do tipo)]."""
    out = []
    for i in range(int(wire.NumberOfSections)):
        lt = model[wire.LineType[i]]
        eps, desc = _line_type_strain(model, lt)
        w = submerged_weight_per_m(_num(lt, "MassPerUnitLength"), _num(lt, "OD"))
        out.append((float(wire.Length[i]), w, eps, f"{wire.LineType[i]} ({desc})"))
    return list(reversed(out))


def line_points(line, arcs, orc) -> list[tuple[float, float, float]]:
    pts = []
    for s in arcs:
        oe = orc.oeArcLength(float(s))
        pts.append(tuple(float(line.StaticResult(v, oe)) for v in ("X", "Y", "Z")))
    return pts


# --------------------------------------------------------------------------- preparo

def prepare_camelback(case: CaseInputs, model_path: Path) -> dict:
    """Coloca o masterlink e ajusta o cabo do guincho a partir da linha convergida sem a corcova."""
    orc = require_orcfxapi()
    model_path = Path(model_path)
    res: dict = {"status": "FALHOU"}

    real = orc.Model(str(model_path))
    wire = real[case.role("WINCH_WIRE")]
    ml = real[case.role("MASTERLINK")]
    conn = str(getattr(wire, "EndAConnection", "Fixed"))

    # 1) cópia de trabalho: sem as lingas (a linha fica como sem corcova) e com o masterlink fixo onde está.
    #    O cabo do guincho continua, então a posição global do End A sai da estática qualquer que seja a
    #    conexão (Fixed, ou um objeto como 'Moonpool').
    work = orc.Model(str(model_path))
    for role in ("CB_LINK_1", "CB_LINK_2"):
        try:
            work.DestroyObject(work[case.role(role)])
        except Exception:
            pass
    work[case.role("MASTERLINK")].Connection = "Fixed"
    try:
        max_it = int(case.num("model.end_b_statics_max_iterations"))
        if work.general.StaticsMaxIterations > max_it:
            work.general.StaticsMaxIterations = max_it
    except Exception:
        pass
    from .._orcaflex import save_work_copy

    save_work_copy(work, model_path, "_sem_corcova")
    mode = masterlink_mode(case)
    problems: list[str] = []
    statics_ok = True
    try:
        calculate_statics_robust(work, case, "linha sem corcova")   # cópia descartada
    except StaticsFailed as exc:
        statics_ok = False
        msg = f"estática da linha sem corcova não convergiu: {exc}"
        if mode != "baixo":   # o ápice precisa dos pontos convergidos da linha
            res["message"] = msg
            log.warning("  %s", msg)
            return res
        problems.append(msg)
        log.warning("  %s; pontos das lingas pela catenária geométrica", msg)
    top = None
    if statics_ok:
        try:
            oe = orc.oeEndA
            top = tuple(float(work[case.role("WINCH_WIRE")].StaticResult(v, oe)) for v in ("X", "Y", "Z"))
        except Exception as exc:
            if conn != "Fixed":
                problems.append(f"posição do End A do cabo do guincho (conectado a '{conn}') não lida: {exc}")
    if top is None and conn == "Fixed":
        top = (_num(wire, "EndAX"), _num(wire, "EndAY"), _num(wire, "EndAZ"))
    if top is not None:
        log.info("  End A do cabo do guincho (%s): (%.2f, %.2f, %.2f) m", conn, *top)

    l1, l2 = real[case.role("CB_LINK_1")], real[case.role("CB_LINK_2")]
    arcs = [float(l1.EndAZ), float(l2.EndAZ)]
    if statics_ok:
        p1, p2 = line_points(work[case.role("LINE")], arcs, orc)
    else:
        p1, p2 = estimate_line_points(case, real, arcs)
    sling = min(float(l1.UnstretchedLength), float(l2.UnstretchedLength))
    if mode == "baixo":
        height = case.num("model.masterlink_height")
        dx = case.num("model.masterlink_x_offset")
        pos = low_masterlink_position(p1, p2, -float(real.environment.WaterDepth), height, dx)
        log.info("  Pontos das lingas na linha: X = %.2f e %.2f m (%s); masterlink baixo: %.2f m acima do fundo, "
                 "X na média %+g m", p1[0], p2[0], "estática" if statics_ok else "catenária estimada", height, dx)
    else:
        pos, warn = masterlink_position(p1, p2, sling)
        if warn:
            log.warning("  %s", warn)

    # posição inicial atual -> quanto as lingas estariam esticadas (diagnóstico)
    old = (_num(ml, "InitialX"), _num(ml, "InitialY"), _num(ml, "InitialZ"))
    stretch_before = [math.dist(old, p) - float(lk.UnstretchedLength) for p, lk in ((p1, l1), (p2, l2))]
    log.info("  Lingas na posição antiga do masterlink: esticadas %.2f m e %.2f m (comprimento %.2f m)",
             *stretch_before, sling)

    # 2) cabo do guincho: masterlink pendurado em `pos` + folga
    slack = case.num("model.camelback_slack")
    secs = winch_sections(real, wire)          # de baixo para cima
    ml_load = (_num(ml, "Mass") - RHO * _num(ml, "Volume")) * G
    bottom = [(L, w, f) for L, w, f, _ in secs[:-1]]
    L_top_old, w_top, f_top, top_name = secs[-1]
    L_top, dist, stretched = L_top_old, None, None
    if top is not None:
        dist = math.dist(top, pos)
        try:
            L_top = solve_top_section(dist + slack, bottom, w_top, f_top, ml_load)
            stretched = hanging_lengths([*bottom, (L_top, w_top, f_top)], ml_load)
        except ValueError as exc:
            if mode != "baixo":
                res["message"] = str(exc)
                log.warning("  %s", exc)
                return res
            problems.append(str(exc))
            L_top = L_top_old
    else:
        problems.append("comprimento do cabo do guincho mantido (posição do guincho desconhecida)")

    n = int(wire.NumberOfSections)
    wire.Length[0] = L_top                     # seção 0 = seção de cima (End A no guincho)
    ml.InitialX, ml.InitialY, ml.InitialZ = pos
    real.SaveData(str(model_path))

    res.update(status="OK" if not problems else "PARCIAL", masterlink=pos, masterlink_before=old,
               masterlink_mode=mode, sling_points=[p1, p2], sling_arcs=arcs, sling_points_from_statics=statics_ok,
               sling_stretch_before=stretch_before, winch_top=top, winch_distance=dist, slack=slack,
               winch_main_length_before=L_top_old, winch_main_length=L_top, winch_sections=n,
               winch_stretched=stretched, masterlink_submerged_weight=ml_load)
    if problems:
        res["message"] = "; ".join(problems)
    log.info("  Masterlink: (%.2f, %.2f, %.2f) -> (%.2f, %.2f, %.2f) [%s]", *old, *pos,
             f"{case.num('model.masterlink_height'):g} m acima do fundo, X na média das lingas "
             f"{case.num('model.masterlink_x_offset'):+g} m" if mode == "baixo"
             else "lingas no comprimento, tração zero")
    if stretched is not None:
        log.info("  Cabo do guincho, seção principal %s: %.3f -> %.3f m (distância %.2f m + folga %.2f m; "
                 "alongamento pelo peso %.3f m)", top_name, L_top_old, L_top, dist, slack,
                 sum(stretched) - sum(L for L, *_ in bottom) - L_top)
    return res
