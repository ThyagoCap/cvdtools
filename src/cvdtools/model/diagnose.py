"""Diagnóstico de estática que não converge: roda variantes do modelo e compara.

Cada variante abre uma cópia do modelo (nada é salvo), aplica UMA mudança e
calcula a estática com limite de iterações. A combinação de resultados aponta a
causa. Variantes:

- ``como_gerado``        modelo como está no arquivo
- ``sem_corcova``        sem lingas, cabo do guincho e masterlink
- ``masterlink_fixo``    masterlink com conexão Fixed na posição do arquivo
- ``extensao_EA_linear`` curva axial da extensão do guincho trocada por EA secante (10% da tração máx. da curva)
- ``linha_EI_linear``    curva de flexão da linha trocada pela rigidez do 1º trecho (sem histerese)
- ``restritor_EI_linear`` idem para o restritor de curvatura

Além disso, com a linha convergida sem a corcova, calcula quanto cada linga
estaria esticada na posição do masterlink do arquivo (sinal da causa principal).
"""

from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from .._orcaflex import StaticsFailed, calculate_statics, require_orcfxapi
from ..log import log
from ..schema import CaseInputs


@dataclass
class VariantResult:
    name: str
    description: str
    status: str = "-"          # OK | FALHOU | N/A
    seconds: float = 0.0
    message: str = ""
    extra: dict = field(default_factory=dict)


def _secant_ea(model, curve_name: str, fraction: float = 0.1) -> float:
    vd = model[curve_name]
    xs, ys = [float(v) for v in vd.IndependentValue], [float(v) for v in vd.DependentValue]
    target = fraction * max(ys)
    for i in range(1, len(ys)):
        if ys[i] >= target:
            w = (target - ys[i - 1]) / (ys[i] - ys[i - 1]) if ys[i] > ys[i - 1] else 0.0
            eps = xs[i - 1] + w * (xs[i] - xs[i - 1])
            return target / eps if eps > 0 else float("nan")
    return ys[-1] / xs[-1]


def _first_slope(model, curve_name: str) -> float:
    vd = model[curve_name]
    xs, ys = [float(v) for v in vd.IndependentValue], [float(v) for v in vd.DependentValue]
    for i in range(1, len(xs)):
        if xs[i] > xs[0]:
            return (ys[i] - ys[0]) / (xs[i] - xs[0])
    raise ValueError(f"curva '{curve_name}' sem pontos suficientes")


def _linearize(model, role_type: str, case: CaseInputs, prop: str, how) -> str:
    lt = model[case.role(role_type)]
    cur = getattr(lt, prop)
    if not (isinstance(cur, str) and cur):
        return f"{case.role(role_type)}.{prop} já é constante ({cur}): variante igual ao modelo"
    value = how(model, cur)
    setattr(lt, prop, value)
    return f"{case.role(role_type)}.{prop}: '{cur}' -> {value:.6g}"


def variants(case: CaseInputs):
    from .endb import _strip_camelback

    def as_is(model):
        return ""

    def strip(model):
        return "removidos: " + ", ".join(_strip_camelback(model, case))

    def ml_fixed(model):
        ml = model[case.role("MASTERLINK")]
        old = ml.Connection
        ml.Connection = "Fixed"
        return f"Masterlink.Connection: {old} -> Fixed"

    def ext_linear(model):
        return _linearize(model, "WINCH_EXT_TYPE", case, "EA", _secant_ea)

    def line_linear(model):
        return _linearize(model, "LINE_TYPE", case, "EIx", _first_slope)

    def br_linear(model):
        return _linearize(model, "BR_TYPE", case, "EIx", _first_slope)

    out = [("como_gerado", "modelo como está no arquivo", as_is)]
    if case.operation.camelback:
        out += [("sem_corcova", "sem lingas, cabo do guincho e masterlink", strip),
                ("masterlink_fixo", "masterlink fixo na posição do arquivo", ml_fixed)]
    if case.roles.get("WINCH_EXT_TYPE"):
        out.append(("extensao_EA_linear", "extensão do guincho com EA constante (secante a 10% da tração máx.)",
                    ext_linear))
    out += [("linha_EI_linear", "linha com EI constante (1º trecho da curva, sem histerese)", line_linear),
            ("restritor_EI_linear", "restritor com EI constante (1º trecho da curva)", br_linear)]
    return out


def sling_precheck(case: CaseInputs, model_path: Path, orc) -> dict:
    """Linha sem corcova -> esticamento das lingas na posição atual do masterlink."""
    from .camelback import line_points
    from .endb import _strip_camelback

    model = orc.Model(str(model_path))
    ml = model[case.role("MASTERLINK")]
    links = [model[case.role("CB_LINK_1")], model[case.role("CB_LINK_2")]]
    mpos = tuple(float(getattr(ml, a)) for a in ("InitialX", "InitialY", "InitialZ"))
    arcs = [float(lk.EndAZ) for lk in links]
    lens = [float(lk.UnstretchedLength) for lk in links]
    stiff = [float(lk.Stiffness) for lk in links]
    _strip_camelback(model, case)
    calculate_statics(model, "pré-verificação das lingas")
    pts = line_points(model[case.role("LINE")], arcs, orc)
    out = {"masterlink": mpos, "points": pts, "links": []}
    for i, (p, L, k) in enumerate(zip(pts, lens, stiff), start=1):
        d = math.dist(mpos, p)
        stretch = d - L
        out["links"].append({"link": i, "arc": arcs[i - 1], "distance": d, "length": L, "stretch": stretch,
                             "strain": stretch / L, "stiffness": k})
    return out


def diagnose(case: CaseInputs, model_path: Path, out_dir: Path, only: list[str] | None = None,
             max_iterations: int | None = None) -> dict:
    orc = require_orcfxapi()
    model_path, out_dir = Path(model_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    max_it = int(max_iterations or case.num("model.end_b_statics_max_iterations"))
    results: list[VariantResult] = []
    log.info("Diagnóstico de %s (estática limitada a %d iterações por variante)", model_path.name, max_it)

    precheck = None
    if case.operation.camelback:
        try:
            precheck = sling_precheck(case, model_path, orc)
            for lk in precheck["links"]:
                log.info("  Linga %d (KP %.2f m): distância ao masterlink %.2f m, comprimento %.2f m -> %s %.2f m "
                         "(%.0f%%, rigidez %g)", lk["link"], lk["arc"], lk["distance"], lk["length"],
                         "esticada" if lk["stretch"] > 0 else "frouxa", abs(lk["stretch"]), 100 * lk["strain"],
                         lk["stiffness"])
        except Exception as exc:
            log.warning("  Pré-verificação das lingas falhou: %s", exc)

    for name, desc, apply in variants(case):
        if only and name not in only:
            continue
        r = VariantResult(name, desc)
        model = orc.Model(str(model_path))
        try:
            if model.general.StaticsMaxIterations > max_it:
                model.general.StaticsMaxIterations = max_it
        except Exception:
            pass
        try:
            r.extra["change"] = apply(model)
        except Exception as exc:
            r.status, r.message = "N/A", f"variante não aplicável: {exc}"
            results.append(r)
            log.info("  %-20s N/A  %s", name, r.message)
            continue
        t = time.perf_counter()
        try:
            calculate_statics(model, name)
            r.status = "OK"
        except StaticsFailed as exc:
            r.status, r.message = "FALHOU", str(exc)
        r.seconds = time.perf_counter() - t
        log.info("  %-20s %-6s %6.1fs  %s %s", name, r.status, r.seconds, r.extra.get("change", ""), r.message)
        results.append(r)

    verdict = interpret(results, precheck)
    for line in verdict:
        log.info("  >> %s", line)
    path = out_dir / "diagnose.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Variante", "Descrição", "Mudança", "Status", "Tempo (s)", "Mensagem"])
        for r in results:
            w.writerow([r.name, r.description, r.extra.get("change", ""), r.status, f"{r.seconds:.1f}", r.message])
        if precheck:
            w.writerow([])
            w.writerow(["Linga", "KP (m)", "Distância ao masterlink (m)", "Comprimento (m)", "Esticamento (m)",
                        "Deformação (%)", "Rigidez"])
            for lk in precheck["links"]:
                w.writerow([lk["link"], f"{lk['arc']:.2f}", f"{lk['distance']:.3f}", f"{lk['length']:.3f}",
                            f"{lk['stretch']:.3f}", f"{100 * lk['strain']:.1f}", lk["stiffness"]])
        w.writerow([])
        for line in verdict:
            w.writerow(["Conclusão", line])
    return {"results": results, "precheck": precheck, "verdict": verdict, "csv": path}


def interpret(results: list[VariantResult], precheck: dict | None) -> list[str]:
    st = {r.name: r.status for r in results}
    out = []
    if st.get("como_gerado") == "OK":
        out.append("Estática OK: o modelo como está converge. Se a simulação ainda falhar, o problema é na dinâmica "
                   "(passo de tempo, build-up, onda) e não na estática.")
        return out
    if precheck and any(abs(lk["stretch"]) > 0.25 for lk in precheck["links"]):
        out.append("Lingas fora do comprimento na posição atual do masterlink: a estática parte longe do equilíbrio. "
                   "Use o preparo da corcova (model.prepare_camelback = S, padrão a partir da v0.6.0).")
    if st.get("sem_corcova") == "OK" and st.get("masterlink_fixo") == "OK":
        out.append("Sem corcova e com masterlink fixo converge: causa no equilíbrio do masterlink/cabo do guincho "
                   "(posição inicial ou comprimento do cabo).")
    elif st.get("sem_corcova") == "OK":
        out.append("Só converge sem a corcova: causa no sistema lingas/masterlink/cabo do guincho.")
    elif st.get("sem_corcova") == "FALHOU":
        out.append("Nem sem a corcova converge: causa na linha/MCV (curvas, End B, segmentação).")
    for name, what in (("extensao_EA_linear", "curva axial da extensão do guincho"),
                       ("linha_EI_linear", "curva de flexão/histerese da linha"),
                       ("restritor_EI_linear", "curva do restritor de curvatura")):
        if st.get(name) == "OK":
            out.append(f"Converge com {name}: a {what} contribui para a não convergência (conferir unidades/pontos).")
    if not out:
        out.append("Nenhuma variante isolada converge: combinar mudanças ou revisar o modelo à mão.")
    return out
