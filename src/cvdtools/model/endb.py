"""Ajuste do End B (âncora) da linha pela estática do OrcaFlex.

Uma catenária analítica com o ângulo do gooseneck imposto não representa a
rigidez à flexão da linha perto do MCV. Aqui o ``EndBX`` é ajustado até a
**tração efetiva no End B** (= tração horizontal da linha assentada, sem atrito
na estática) ficar igual a ``model.end_b_tension_target`` (padrão 0,5 kN, a
intenção do _01).

O ajuste roda numa **cópia** do modelo sem o sistema da corcova (lingas, cabo do
guincho e masterlink, como o "Nom" do _08): assim a corcova não levanta a linha
e não entra na estática o cabo do guincho frouxo, que converge mal. O número de
iterações da estática é limitado (``model.end_b_statics_max_iterations``) para
uma estática que não converge não travar a rodada. No modelo real só o
``EndBX`` final é gravado.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from .._orcaflex import (use_calculated_positions, StaticsFailed, calculate_statics, calculate_statics_robust,
                         require_orcfxapi, restore_statics_settings, robust_mode,
                         save_work_copy)
from ..log import log
from ..schema import CaseInputs
from ..statics.solver import NotBracketed, Search, TooManyEvaluations

CAMELBACK_ROLES = ("CB_LINK_1", "CB_LINK_2", "WINCH_WIRE", "MASTERLINK")  # nesta ordem (dependências)


def _strip_camelback(model, case: CaseInputs) -> list[str]:
    removed = []
    for role in CAMELBACK_ROLES:
        name = case.roles.get(role, "")
        if not name:
            continue
        try:
            obj = model[name]
        except Exception:
            continue
        model.DestroyObject(obj)
        removed.append(name)
    return removed


def tune_end_b(case: CaseInputs, model_path: Path) -> dict:
    orc = require_orcfxapi()
    model_path = Path(model_path)
    work = orc.Model(str(model_path))          # cópia de trabalho (não é salva)
    removed = _strip_camelback(work, case) if case.operation.camelback else []
    if removed:
        log.info("  Modelo de ajuste sem: %s", ", ".join(removed))
    max_it = int(case.num("model.end_b_statics_max_iterations"))
    try:
        if work.general.StaticsMaxIterations > max_it:
            log.info("  Iterações de estática limitadas a %d no ajuste (modelo base: %s)", max_it,
                     work.general.StaticsMaxIterations)
            work.general.StaticsMaxIterations = max_it
    except Exception:
        pass

    line = work[case.role("LINE")]
    static = orc.Period(orc.PeriodNum.StaticState)
    target = case.num("model.end_b_tension_target")
    g = case.num("mcv.g")
    length = float(line.CumulativeLength[-1])
    x0 = float(line.EndBX)

    def tension(x: float) -> float:
        line.EndBX = x
        log.debug("  Calculando estática com End B X = %.3f m ...", x)
        t = time.perf_counter()
        try:
            calculate_statics_robust(work, case, f"End B X = {x:.3f}")   # cópia descartada
        except Exception as exc:
            log.warning("  End B X = %.3f m: estática não convergiu (%.1fs): %s", x, time.perf_counter() - t,
                        str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__)
            return float("nan")
        value = float(line.TimeHistory("Effective tension", static, orc.oeEndB)[0])
        log.info("  End B X = %.3f m -> tração no End B %.3f kN (alvo %g) [%.1fs]", x, value, target,
                 time.perf_counter() - t)
        return value - target

    search = Search(tension, xtol=case.num("statics.pull_tolerance"), max_evals=int(case.num("statics.max_evaluations")))
    result = {"end_b_x_start": x0, "end_b_tension_target": target, "removed_for_tuning": removed}
    save_work_copy(work, model_path, "_ajuste_endB")
    log.info("  Primeira estática do ajuste (End B X = %.3f m) ...", x0)
    try:
        f0 = search.eval(x0)
        direction = -1 if (math.isnan(f0) or f0 > 0) else +1  # esticada demais (ou sem convergir): aproxima
        x = search.solve(x0, 0.5, g + 1.0, g + length, direction)
        f = search.eval(x)
        result.update(end_b_x=x, end_b_tension=f + target, evaluations=len(search.history), status="OK")
    except (NotBracketed, TooManyEvaluations) as exc:
        x = x0
        result.update(end_b_x=x0, status="FALHOU", message=str(exc), evaluations=len(search.history))

    real = orc.Model(str(model_path))   # modelo completo: só o End B X muda
    real[case.role("LINE")].EndBX = x
    real.SaveData(str(model_path))
    return result


def finalize_statics(case: CaseInputs, model_path: Path) -> dict:
    """Estática do modelo COMPLETO e gravação das posições calculadas como forma inicial.

    Com corcova (e ``model.prepare_camelback``), em duas etapas:
      A) masterlink FIXO na posição preparada (baixo: 2 m acima do fundo), com damping alto
         (``statics.robust_*``); posições gravadas como forma inicial (user specified);
      B) a partir das posições de A, masterlink com a conexão original (livre), ainda com
         damping alto; posições gravadas e só então o damping volta aos valores do modelo.
    Se A converge e B não, o modelo é salvo com as posições de A (status PARCIAL):
    ainda é um ponto de partida muito melhor que o do modelo base.

    ``UseCalculatedPositions(setLinesToUserSpecifiedStartingShape=True)`` grava as
    posições convergidas antes de salvar. Se nada converge o arquivo não é alterado.
    """
    orc = require_orcfxapi()
    model_path = Path(model_path)
    model = orc.Model(str(model_path))
    t0 = time.perf_counter()
    out: dict = {"stages": []}
    original_limit = None
    try:   # falha rápido em vez de 10 000 iterações; o limite original volta antes de salvar
        limit = int(case.num("statics.max_iterations"))
        if model.general.StaticsMaxIterations > limit:
            original_limit = model.general.StaticsMaxIterations
            model.general.StaticsMaxIterations = limit
    except Exception:
        pass

    def restore_limit():
        if original_limit is not None:
            model.general.StaticsMaxIterations = original_limit

    staged = bool(case.operation.camelback) and case.flag("model.prepare_camelback") is not False \
        and bool(case.roles.get("MASTERLINK"))
    ml = model[case.role("MASTERLINK")] if staged else None
    original = str(ml.Connection) if staged else ""

    robust: list = []   # valores originais de General trocados pela escada de damping
    # Com corcova, cada etapa usa a escada de damping (statics.robust_*: alto, ÷10 depois de convergir; ×10 e
    # Jacobiano 1e-3 se não convergir). Damping e Jacobiano voltam aos do modelo depois de gravar as posições.
    hold = staged and robust_mode(case) != "nunca"
    if hold:
        log.info("  Escada de damping nas duas etapas (damping %g a %g, %g iterações; ÷%g depois de convergir)",
                 case.num("statics.robust_min_damping"), case.num("statics.robust_max_damping"),
                 case.num("statics.robust_max_iterations"), case.num("statics.robust_factor"))

    def restore_robust():
        while robust:
            restore_statics_settings(model, robust.pop())

    def run(label: str) -> str | None:
        t = time.perf_counter()
        try:
            orig = calculate_statics_robust(model, case, label, "direto" if hold else None)
            if orig:
                robust.append(orig)
        except StaticsFailed as exc:
            log.warning("  Estática (%s) NÃO convergiu (%.1fs): %s", label, time.perf_counter() - t, exc)
            out["stages"].append({"stage": label, "status": "FALHOU", "message": str(exc),
                                  "seconds": time.perf_counter() - t})
            return str(exc)
        log.info("  Estática (%s) convergiu [%.1fs]", label, time.perf_counter() - t)
        out["stages"].append({"stage": label, "status": "OK", "seconds": time.perf_counter() - t})
        return None

    stage_a_ok = False
    if staged:
        ml.Connection = "Fixed"
        err = run("masterlink fixo")
        if err is not None:
            out.update(status="FALHOU", message=f"etapa masterlink fixo: {err}", seconds=time.perf_counter() - t0)
            return out
        use_calculated_positions(model)   # posições da 1ª etapa como forma inicial (user specified)
        restore_robust()                   # a 2ª etapa recomeça a escada no damping alto
        log.info("  Posições da etapa 'masterlink fixo' gravadas; masterlink liberado ('%s')", original)
        ml.Connection = original
        stage_a_ok = True

    err = run("modelo completo")
    if err is not None:
        if stage_a_ok:
            restore_robust()
            restore_limit()
            model.SaveData(str(model_path))
            log.warning("  Modelo salvo com as posições da etapa 'masterlink fixo' (masterlink de volta a '%s')",
                        original)
            out.update(status="PARCIAL", message=f"masterlink livre: {err}", seconds=time.perf_counter() - t0)
        else:
            out.update(status="FALHOU", message=err, seconds=time.perf_counter() - t0)
        return out
    use_calculated_positions(model)
    from .view import set_default_view

    set_default_view(model, case)   # MCV pode ter mudado de lugar na estática
    restore_robust()   # posições gravadas: damping e Jacobiano de volta aos valores do modelo
    if hold:
        log.info("  Posições da etapa 'modelo completo' gravadas; damping e Jacobiano de volta aos valores do modelo")
    restore_limit()
    model.SaveData(str(model_path))
    log.info("  Posições calculadas gravadas como forma inicial em %s", model_path.name)
    out.update(status="OK", seconds=time.perf_counter() - t0)
    return out
