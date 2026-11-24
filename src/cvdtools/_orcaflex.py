"""Importação tardia do OrcFxAPI.

O pacote pode ser importado sem OrcaFlex instalado. Só as funções que realmente
abrem modelos chamam ``require_orcfxapi()``.
"""

from __future__ import annotations


def has_orcfxapi() -> bool:
    try:
        import OrcFxAPI  # noqa: F401
    except Exception:
        return False
    return True


def require_orcfxapi():
    try:
        import OrcFxAPI
    except Exception as exc:  # ImportError ou falha ao carregar a DLL
        raise RuntimeError(
            "OrcFxAPI não está disponível. Instale com 'pip install OrcFxAPI' "
            "numa máquina com OrcaFlex e licença."
        ) from exc
    return OrcFxAPI


def orcaflex_version() -> str | None:
    if not has_orcfxapi():
        return None
    orc = require_orcfxapi()
    try:
        return str(orc.DLLVersion())
    except Exception:
        return None


# --------------------------------------------------------------------------- estática interrompível

PROGRESS_LOG_INTERVAL = 1.0  # s


class StaticsFailed(RuntimeError):
    """Estática não convergiu. ``last_progress`` é a última mensagem do OrcaFlex."""

    def __init__(self, message: str, last_progress: str = "", messages: int = 0):
        self.orcaflex_message = message.strip().splitlines()[-1] if message.strip() else "falhou"
        self.last_progress = last_progress
        self.messages = messages
        text = self.orcaflex_message
        if last_progress:
            text += f" | último progresso: {last_progress}"
        super().__init__(text)


class StaticsStalled(StaticsFailed):
    """Estática cancelada por falta de progresso (``stall_seconds``): tratada como não convergida."""


def calculate_statics(model, label: str = "", stall_seconds: float = 0.0) -> None:
    """``model.CalculateStatics()`` que pode ser interrompido com Ctrl+C.

    Enquanto a DLL do OrcaFlex calcula, o Python não processa o Ctrl+C. Aqui o
    Ctrl+C só liga uma marca, e o tratador de progresso da estática (chamado pelo
    OrcaFlex durante o cálculo) pede o cancelamento. O progresso vai para o log
    detalhado (-v). Ao final, se houve Ctrl+C, levanta KeyboardInterrupt.

    ``stall_seconds`` > 0: cancela (``StaticsStalled``) quando o erro da estática não cai à metade
    em ``stall_seconds`` segundos (0.10.2: a etapa sem corcova parou em 1,7e-3 e andou ~1 it/s).
    """
    import signal
    import threading

    from .log import log

    state = {"stop": False, "last": "", "stalled": False, "phase": None, "ref": None, "t_ref": 0.0}

    def on_sigint(signum, frame):
        if not state["stop"]:
            log.warning("Ctrl+C recebido: cancelando a estática%s ...", f" ({label})" if label else "")
        state["stop"] = True

    import time as _time

    state["t"] = 0.0
    state["n"] = 0

    def progress(*args):
        text = str(args[-1]).strip() if args else ""
        if text and text != state["last"]:
            state["last"] = text
            state["n"] += 1
            now = _time.perf_counter()
            # no máximo ~1 linha por segundo no log (antes: uma por iteração, logs de MB)
            if now - state["t"] >= PROGRESS_LOG_INTERVAL:
                state["t"] = now
                log.debug("    estática%s: %s", f" ({label})" if label else "", text)
            if stall_seconds and stall_seconds > 0 and not state["stop"]:
                phase, err = parse_statics_progress(text)
                if err is not None:
                    if phase != state["phase"] or state["ref"] is None or err <= 0.5 * state["ref"]:
                        state.update(phase=phase, ref=err, t_ref=now)
                    elif now - state["t_ref"] >= stall_seconds:
                        log.warning("  Estática%s sem progresso há %.0f s (erro %.3g, era %.3g): cancelando",
                                    f" ({label})" if label else "", now - state["t_ref"], err, state["ref"])
                        state["stop"] = state["stalled"] = True
        return state["stop"]  # True = cancelar

    main_thread = threading.current_thread() is threading.main_thread()
    old = signal.signal(signal.SIGINT, on_sigint) if main_thread else None
    handler_set = False
    try:
        try:
            model.staticsProgressHandler = progress
            handler_set = True
        except Exception:
            pass
        try:
            model.CalculateStatics()
        except Exception as exc:
            if state["stalled"]:
                raise StaticsStalled(f"sem progresso em {stall_seconds:g} s", state["last"], state["n"]) from exc
            if state["stop"]:
                raise KeyboardInterrupt from exc
            # o "último progresso" diz ONDE a estática travou (objeto, nó, grau de liberdade)
            raise StaticsFailed(str(exc), state["last"], state["n"]) from exc
    finally:
        if handler_set:
            try:
                model.staticsProgressHandler = None
            except Exception:
                pass
        if main_thread:
            signal.signal(signal.SIGINT, old)
    if state["stalled"]:   # cancelada, mas o OrcaFlex não levantou exceção
        raise StaticsStalled(f"sem progresso em {stall_seconds:g} s", state["last"], state["n"])
    if state["stop"]:
        raise KeyboardInterrupt


def parse_statics_progress(text: str):
    """``("Whole system statics", 0.00167)`` de "Whole system statics: 16862 (latest error = 0,00167 at ...)".

    Aceita vírgula decimal e os formatos do OrcaFlex "10,15e3" e "115e-6". Sem erro na linha: (fase, None).
    """
    import re

    phase = text.split(":", 1)[0].strip() if ":" in text else text.strip()
    m = re.search(r"error\s*=\s*([-+]?[0-9]+(?:[.,][0-9]*)?(?:[eE][-+]?[0-9]+)?)", text)
    if not m:
        return phase, None
    try:
        return phase, abs(float(m.group(1).replace(",", ".")))
    except ValueError:
        return phase, None


def use_calculated_positions(model) -> None:
    """``Model.UseCalculatedPositions`` gravando a forma das linhas como "User specified".

    O OrcFxAPI nomeia o argumento ``setLinesToUserSpecifiedStartingShape`` (com "s"
    minúsculo); a v0.6.1 usava "S" maiúsculo e quebrava com TypeError.
    """
    try:
        model.UseCalculatedPositions(setLinesToUserSpecifiedStartingShape=True)
    except TypeError:
        model.UseCalculatedPositions(True)


WORK_DIR = "00.Montagem"


def save_work_copy(model, model_path, suffix: str):
    """Grava a cópia de trabalho ANTES da estática, para conferir no OrcaFlex mesmo se não convergir.

    Vai para ``<pasta do caso>/00.Montagem/<nome><suffix>.dat``. Falha ao gravar só gera aviso.
    """
    from pathlib import Path

    from .log import log

    model_path = Path(model_path)
    out = model_path.parent / WORK_DIR / f"{model_path.stem}{suffix}.dat"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        model.SaveData(str(out))
        log.info("  Modelo da estática salvo antes de rodar: %s", out)
        return out
    except Exception as exc:   # gravar a cópia é ajuda à verificação, não pode parar a montagem
        log.warning("  Não foi possível salvar %s: %s", out.name, exc)
        return None


# --------------------------------------------------------------------------- estática "robusta"

ROBUST_SETTINGS = (("StaticsMaxIterations", "statics.robust_max_iterations"),
                   ("StaticsMinDamping", "statics.robust_min_damping"),
                   ("StaticsMaxDamping", "statics.robust_max_damping"))
# Dados de General que a escada de damping pode mudar: todos voltam aos valores do modelo no final
LADDER_NAMES = ("StaticsMaxIterations", "StaticsMinDamping", "StaticsMaxDamping", "JacobianPerturbationFactor")


def robust_mode(case) -> str:
    """``direto`` (já roda com os valores robustos), ``repetir`` (só se falhar) ou ``nunca``.

    ``statics.robust``: ``auto`` (padrão) = direto com 2 extremidades, repetir com 1.
    """
    try:
        v = case.get("statics.robust")
    except KeyError:
        v = None
    text = str(v).strip().lower().replace("ã", "a") if isinstance(v, str) else ""
    if text in ("", "auto"):
        try:
            return "direto" if case.modelled_ends() == 2 else "repetir"
        except Exception:
            return "repetir"
    aliases = {"direto": "direto", "sempre": "direto", "repetir": "repetir", "se falhar": "repetir",
               "nunca": "nunca", "nao": "nunca"}
    if text not in aliases:
        from .schema import MissingInputError

        raise MissingInputError(f"statics.robust = {v!r}: use auto, sempre, repetir ou nunca")
    return aliases[text]


def _case_num(case, key, default):
    try:
        v = case.num(key)
        return default if v is None else v
    except Exception:
        return default


def _set_general(model, values: dict, original: dict) -> None:
    """Grava ``values`` em General guardando em ``original`` o valor do modelo (só na 1ª troca)."""
    from .log import log

    general = model.general
    for name, value in values.items():
        try:
            current = getattr(general, name)
            if name == "StaticsMaxIterations" or isinstance(current, int):
                value = int(round(value))   # o OrcFxAPI recusa float em dado inteiro (0.10.1: ficava em 2000)
            original.setdefault(name, current)
            setattr(general, name, value)
        except Exception as exc:
            log.debug("  %s não alterado: %s", name, exc)


def apply_robust_settings(model, case) -> dict:
    """Grava os valores robustos em General e devolve os originais (para ``restore_statics_settings``)."""
    original: dict = {}
    _set_general(model, {name: case.num(key) for name, key in ROBUST_SETTINGS}, original)
    return original


def restore_statics_settings(model, original) -> None:
    """Volta os valores de General gravados por ``apply_robust_settings``/``calculate_statics_robust``.

    Mudar dados devolve o modelo ao estado Reset: chame depois de ler os resultados (e do
    ``UseCalculatedPositions``) e antes de salvar.
    """
    if not original:
        return
    for name, value in original.items():
        try:
            setattr(model.general, name, value)
        except Exception:
            pass


DAMPING_BOUNDS = (("statics.robust_min_damping_low", 10.0), ("statics.robust_min_damping_high", 1e4),
                  ("statics.robust_max_damping_low", 100.0), ("statics.robust_max_damping_high", 1e4))


def clamp_damping(case, dmin: float, dmax: float) -> tuple[float, float]:
    """Damping mínimo em [10, 1e4] e máximo em [1e2, 1e4] (``statics.robust_*_damping_low/high``); máx >= mín."""
    lo_min, hi_min, lo_max, hi_max = (_case_num(case, key, default) for key, default in DAMPING_BOUNDS)
    dmin = min(max(dmin, lo_min), hi_min)
    dmax = min(max(dmax, lo_max), hi_max)
    return dmin, max(dmax, dmin)


def _describe(model) -> str:
    g = model.general
    parts = []
    for name, short in (("StaticsMaxIterations", "iterações"), ("StaticsMinDamping", "damping mín"),
                        ("StaticsMaxDamping", "damping máx"), ("JacobianPerturbationFactor", "Jacobiano")):
        try:
            parts.append(f"{short} {getattr(g, name):g}")
        except Exception:
            pass
    return ", ".join(parts)


class DampingLadder:
    """Nível de damping lembrado entre estáticas seguidas (0.10.4).

    Cada estática começa no nível mais baixo que já convergiu (``start``); depois de convergir, o damping
    desce ÷ ``statics.robust_factor`` até o piso (``clamp_damping``), partindo das posições convergidas; só
    sobe (× fator e Jacobiano ``statics.robust_jacobian``) quando a estática não converge. Um nível em que
    uma estática "do zero" falhou não volta a ser ponto de partida, e uma descida que falhou não é tentada de
    novo com o mesmo ``ladder`` (evita repetir a falha a cada avaliação).
    """

    def __init__(self, case):
        self.case = case
        self.factor = _case_num(case, "statics.robust_factor", 10.0) or 10.0
        self.jacobian_up = _case_num(case, "statics.robust_jacobian", 1e-3)
        self.iterations = case.num("statics.robust_max_iterations")
        self.start = clamp_damping(case, case.num("statics.robust_min_damping"),
                                   case.num("statics.robust_max_damping"))
        self.jacobian = None          # None = o do modelo; 1e-3 depois da 1ª subida (fica até o fim)
        self.failed_fresh: tuple | None = None   # maior nível em que uma estática do zero falhou
        self.failed_down: tuple | None = None    # maior nível em que uma descida (das posições convergidas) falhou

    def down(self, level):
        return clamp_damping(self.case, level[0] / self.factor, level[1] / self.factor)

    def up(self, level):
        return clamp_damping(self.case, level[0] * self.factor, level[1] * self.factor)

    def remember_fresh_failure(self, level) -> None:
        if self.failed_fresh is None or level[0] > self.failed_fresh[0]:
            self.failed_fresh = level

    def remember_converged(self, level) -> None:
        """Próxima estática parte daqui, mas acima de qualquer nível que já falhou do zero."""
        if self.failed_fresh is not None and level[0] <= self.failed_fresh[0]:
            level = self.up(self.failed_fresh)
        self.start = level


def calculate_statics_robust(model, case, label: str = "", mode: str | None = None,
                             ladder: DampingLadder | None = None):
    """Estática com a "escada de damping" para os casos de convergência difícil.

    1. Começa em ``ladder.start``: na 1ª estática, ``statics.robust_min_damping``/``robust_max_damping``
       (1e3/1e4), com ``statics.robust_max_iterations`` (50000) iterações; nas seguintes (mesmo ``ladder``,
       p.ex. as avaliações da puxada), no nível mais baixo que já convergiu.
    2. Não convergiu (ou ficou ``statics.stall_seconds`` sem progresso): damping × ``statics.robust_factor``
       (10) e Jacobian perturbation factor ``statics.robust_jacobian`` (1e-3), até o teto.
    3. Convergiu: grava as posições (UseCalculatedPositions) e desce o damping ÷ 10 (mesmas iterações) até
       o piso. Se uma descida não convergir, volta ao último nível que convergiu (parte das posições dele).

    Os dampings ficam sempre nos limites ``clamp_damping`` (mín. 10 a 1e4, máx. 1e2 a 1e4).

    ``direto`` começa na escada; ``repetir`` tenta antes com os valores do modelo (``mode`` força um deles).
    Devolve os valores originais de General (ou None se nada mudou): quem chamou lê os resultados e depois
    chama ``restore_statics_settings`` — a versão final fica com damping e Jacobiano do modelo. Se falhar,
    os valores já voltam antes da exceção.
    """
    from .log import log

    mode = mode or robust_mode(case)
    if mode != "direto":
        try:
            calculate_statics(model, label)
            return None
        except StaticsFailed as exc:
            if mode == "nunca":
                raise
            log.warning("  Estática (%s) não convergiu (%s); repetindo com a escada de damping",
                        label, exc.orcaflex_message)

    ladder = ladder or DampingLadder(case)
    stall = _case_num(case, "statics.stall_seconds", 120.0)
    name = f"{label}, robusta" if label else "robusta"
    factor = ladder.factor

    original: dict = {}

    def apply(level):
        values = {"StaticsMaxIterations": ladder.iterations, "StaticsMinDamping": level[0],
                  "StaticsMaxDamping": level[1]}
        if ladder.jacobian is not None:
            values["JacobianPerturbationFactor"] = ladder.jacobian
        _set_general(model, values, original)

    level = ladder.start
    apply(level)
    try:
        while True:   # subida: só quando não converge
            log.info("  Estática (%s) com %s", label, _describe(model))
            try:
                calculate_statics(model, name, stall)
                break
            except StaticsFailed as exc:
                ladder.remember_fresh_failure(level)
                up = ladder.up(level)
                jac = ladder.jacobian
                if jac is None:
                    try:
                        jac = model.general.JacobianPerturbationFactor
                    except Exception:
                        jac = None
                if up == level and jac is not None and abs(jac - ladder.jacobian_up) <= 1e-12:
                    raise   # já no teto de damping, com o Jacobiano da subida
                level = up
                ladder.jacobian = ladder.jacobian_up
                log.warning("  Estática (%s) não convergiu (%s); damping × %g (limitado: %g a %g) e Jacobiano %g",
                            label, exc.orcaflex_message, factor, level[0], level[1], ladder.jacobian)
                apply(level)
        while True:   # descida: até o piso, partindo das posições convergidas
            lower = ladder.down(level)
            if lower == level:
                break   # piso
            if ladder.failed_down is not None and lower[0] <= ladder.failed_down[0]:
                break   # essa descida já falhou nesta sequência de estáticas: não perde tempo de novo
            use_calculated_positions(model)
            apply(lower)
            log.info("  Estática (%s) convergiu; descendo o damping ÷ %g: %s", label, factor, _describe(model))
            try:
                calculate_statics(model, f"{name}, damping {lower[0]:g} a {lower[1]:g}", stall)
                level = lower
            except StaticsFailed as exc:
                log.warning("  Estática (%s) com damping %g a %g não convergiu (%s); de volta a %g a %g",
                            label, lower[0], lower[1], exc.orcaflex_message, level[0], level[1])
                if ladder.failed_down is None or lower[0] > ladder.failed_down[0]:
                    ladder.failed_down = lower
                apply(level)
                calculate_statics(model, name, stall)   # parte das posições do nível que convergiu
                break
        ladder.remember_converged(level)
    except BaseException:
        restore_statics_settings(model, original)
        raise
    return original
