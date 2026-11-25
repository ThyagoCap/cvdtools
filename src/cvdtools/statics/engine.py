"""Avaliação estática de um modelo em função da puxada do guincho.

Convenções:

- **Puxada** ``p`` (m): encurtamento da 1ª seção do cabo do guincho em relação ao
  comprimento de referência ``L_ref``: ``Length[0] = L_ref - p``.
- **Referência** ``L_ref``: comprimento em que as lingas da corcova começam a
  tracionar (tração nas lingas = base com cabo frouxo + ``statics.pull_tension_rise``), com o
  MCV no estado fixo. Substitui o _02.
- **Estado do MCV**: "fixed" (conectado ao hub) ou "free" (livre, pendurado no
  guindaste). Cada estado é uma lista ``Propriedade=Valor`` aplicada ao objeto do
  MCV (``statics.mcv_fixed_state`` / ``statics.mcv_free_state``).
"""

from __future__ import annotations

import csv
import math
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .._orcaflex import (DampingLadder, StaticsFailed, robust_mode, use_calculated_positions, calculate_statics,
                         calculate_statics_robust, require_orcfxapi,
                         restore_statics_settings)
from ..schema import CaseInputs, MissingInputError
from ..log import log
from ..values import is_number
from .solver import NotBracketed, Search

FIXED, FREE = "fixed", "free"


@dataclass
class Evaluation:
    purpose: str
    state: str
    pull: float
    winch_length: float
    converged: bool
    angle: float = float("nan")
    link_tension: float = float("nan")
    uf_max: float = float("nan")
    governing: str = ""
    ufs: dict[str, float] = field(default_factory=dict)
    error: str = ""
    crane_length: float = float("nan")
    mcv_z: float = float("nan")
    mcv_x: float = float("nan")


def parse_state(text: str) -> list[tuple[str, Any]]:
    """'Connection=Free; DegreesOfFreedomInStatics=All' -> [(prop, valor), ...]."""
    pairs = []
    for part in str(text or "").split(";"):
        if not part.strip():
            continue
        if "=" not in part:
            raise MissingInputError(f"Estado do MCV inválido: {part!r} (use Propriedade=Valor; ...)")
        k, v = (s.strip() for s in part.split("=", 1))
        try:
            v = float(v.replace(",", "."))
        except ValueError:
            pass
        pairs.append((k, v))
    return pairs


_CONTROL_RE = re.compile(r"^\s*\{?([A-Za-z0-9_ ]+?)\}?\.([A-Za-z0-9_]+)\s*(?:\[\s*(-?\d+)\s*\])?\s*$")


def parse_control(text: str, roles: dict[str, str]) -> tuple[str, str, Optional[int]]:
    """'{WINCH_WIRE}.Length[0]' -> ('Winch Wire', 'Length', 0). Aceita nome de objeto no lugar do papel."""
    m = _CONTROL_RE.match(str(text or ""))
    if not m:
        raise MissingInputError(f"statics.length_control inválido: {text!r} (use '{{PAPEL}}.Propriedade[índice]')")
    who, prop, idx = m.group(1).strip(), m.group(2), m.group(3)
    return roles.get(who, who), prop, (int(idx) if idx is not None else None)


class StaticsEngine:
    """Avaliação estática em função da puxada.

    Com corcova: controle = 1ª seção do cabo do guincho; puxada zero = lingas
    começam a tracionar. Sem corcova (fase 5, premissa do usuário: "mesmas
    posições, manipulando o guincho para reduzir o comprimento suspenso"):
    controle = ``statics.length_control``; puxada zero = modelo montado.
    """

    def __init__(self, case: CaseInputs, model_path: Path):
        self.orc = require_orcfxapi()
        self.case = case
        self.model_path = Path(model_path)
        self.model = self.orc.Model(str(self.model_path))
        # 0.10.4: o damping que convergiu numa avaliação é o ponto de partida da seguinte (só sobe se falhar)
        self.ladder = DampingLadder(case) if robust_mode(case) != "nunca" else None
        # 0.10.5: com corcova, cada estática começa com o masterlink fixo (a 1ª é a mais difícil)
        self.ml_name = case.roles.get("MASTERLINK", "") if case.operation.camelback else ""
        if self.ml_name and case.flag("statics.masterlink_fixed_first") is False:
            self.ml_name = ""
        # 0.13.1: uma escada só. A estática com o masterlink livre parte do nível em que a fixa convergiu (antes
        # cada uma tinha a sua, e a livre voltava a 1e3/1e4 logo depois de a fixa ter descido a 10/100)
        self.ladder_fixed = self.ladder
        self._ml_settled = False   # 0.11.2: masterlink fixo só até a 1ª estática com ele livre convergir
        self._converged_length = None   # 0.11.3: ponto de partida dos passos de cabo (lido depois do wire)
        self.static = self.orc.Period(self.orc.PeriodNum.StaticState)
        self.log: list[Evaluation] = []
        self.state = ""
        self.L_ref: Optional[float] = None
        self.criteria = [c for c in case.criteria if c.static and is_number(c.limit)]
        self.skipped = [c.name for c in case.criteria if c.static and not is_number(c.limit)]
        log.info("Abrindo %s", self.model_path.name)
        self.camelback = bool(case.operation.camelback)
        if self.camelback:
            self.control = (case.role("WINCH_WIRE"), "Length", 0)
        else:
            self.control = self._length_control()
        self.wire = self.model[self.control[0]]
        name, prop, idx = self.control
        if idx is not None and idx < 0:              # Length[-1] -> última seção
            try:
                n = int(self.wire.NumberOfSections)
            except Exception:
                n = len(getattr(self.wire, prop))
            self.control = (name, prop, n + idx)
        log.info("Controle da puxada: %s.%s[%s]", *self.control)
        self.L_built = float(self._get_control())
        self._converged_length = self.L_built
        self._states = {FIXED: parse_state(self._text("statics.mcv_fixed_state")),
                        FREE: parse_state(self._text("statics.mcv_free_state"))}
        self._check_objects()
        # Forma de partida por estado (MCV livre/fixo): a última solução convergida daquele estado.
        # Trocar de estado partindo da forma do outro (ex.: 05 fixo depois de 04 livre com puxada grande)
        # fazia a estática divergir (GLF27, 18/11/2026).
        import tempfile

        # Cabo do guindaste: na horizontal ele é ajustado para o MCV ficar na altura do hub (comentário do
        # usuário, 18/11/2026); as posições seguintes partem desse comprimento.
        self.crane_name = case.roles.get("CRANE_WIRE", "")
        try:
            self.crane_built = float(self.model[self.crane_name].Length[0])
        except Exception:
            self.crane_name, self.crane_built = "", float("nan")
        self.crane_length: Optional[float] = None    # None = como no arquivo
        self.hub_z: Optional[float] = None           # Z do MCV conectado ao hub (estado fixo)
        self.horizontal: Optional[dict] = None       # {"pull", "crane", "angle"} da posição horizontal
        self.angle_sign: Optional[int] = None        # sinal de d(ângulo)/d(puxada) perto da horizontal
        self.x_shift = 0.0                           # translação em X aplicada para alinhar MCV e hub
        self.without_crane = False                   # 05.Max_Fixed_Overpull: cabo do guindaste removido
        self._horizontal_shape: Optional[Path] = None  # forma convergida da horizontal (ponto de partida)
        self._reload_base: Optional[Path] = None     # modelo de partida para estados sem forma guardada
        self._shape_dir = Path(tempfile.mkdtemp(prefix="cvd_shapes_"))
        self._shapes: dict[str, Path] = {}
        self._limit_iterations()

    def _limit_iterations(self) -> None:
        try:
            limit = int(self.case.num("statics.max_iterations"))
            if self.model.general.StaticsMaxIterations > limit:
                self.model.general.StaticsMaxIterations = limit
        except Exception:
            pass

    def _reload(self, source: Path) -> None:
        self.model = self.orc.Model(str(source))
        # formas salvas pelo motor já convergiram com o masterlink livre; só o modelo montado não
        self._ml_settled = Path(source).resolve() != Path(self.model_path).resolve()
        self.wire = self.model[self.control[0]]
        self._converged_length = self._read_length()   # formas salvas: o comprimento em que convergiram
        self.state = ""          # força set_state a reaplicar o estado do MCV
        self._limit_iterations()

    def _check_objects(self) -> None:
        """Falha logo no início se critérios ou papéis apontam para objetos inexistentes."""
        problems = []
        for role in (("MCV", "WINCH_WIRE", "CB_LINK_1", "CB_LINK_2") if self.camelback else ("MCV",)):
            try:
                self.model[self.case.role(role)]
            except Exception:
                problems.append(f"papel {role} -> '{self.case.roles.get(role)}' não existe no modelo")
        for c in self.criteria:
            try:
                self.model[c.obj]
            except Exception:
                problems.append(f"critério '{c.name}' -> objeto '{c.obj}' não existe no modelo")
        if problems:
            raise MissingInputError("Estática não pode começar:\n  - " + "\n  - ".join(problems))

    def _text(self, key: str) -> str:
        v = self.case.get(key)
        if not isinstance(v, str) or not v.strip():
            raise MissingInputError(f"{key} não definido")
        return v

    # ------------------------------------------------------------------ comandos no modelo
    def set_state(self, state: str) -> None:
        if state == self.state:
            return
        mcv = self.model[self.case.role("MCV")]
        for prop, value in self._states[state]:
            setattr(mcv, prop, value)
        self.state = state

    def _length_control(self) -> tuple[str, str, Optional[int]]:
        """Sem corcova. 'auto' (premissa do usuário, 18/11/2026):
        - linha com End B na embarcação -> o próprio comprimento da linha (última seção, lado da embarcação);
        - linha curta, suspensa pelo guincho (com o MCV da outra extremidade) -> 1ª seção do cabo do guincho.
        """
        text = self._text("statics.length_control")
        if text.strip().lower() != "auto":
            return parse_control(text, self.case.roles)
        line = self.model[self.case.role("LINE")]
        vessel = self.case.roles.get("VESSEL", "")
        end_b = str(getattr(line, "EndBConnection", ""))
        if vessel and end_b == vessel:
            log.info("Linha conectada à embarcação ('%s'): o comprimento da linha controla a puxada", end_b)
            return self.case.role("LINE"), "Length", -1
        winch = self.case.roles.get("WINCH_WIRE", "")
        try:
            self.model[winch]
        except Exception:
            raise MissingInputError(
                f"statics.length_control = auto: End B da linha em '{end_b}' (não é a embarcação '{vessel}') e não "
                f"há cabo do guincho '{winch}' no modelo. Defina statics.length_control.") from None
        log.info("Linha suspensa pelo guincho: o cabo '%s' controla a puxada", winch)
        return winch, "Length", 0

    def _get_control(self) -> float:
        _, prop, idx = self.control
        v = getattr(self.wire, prop)
        return float(v[idx] if idx is not None else v)

    def set_length(self, length: float) -> None:
        if length <= 0:
            raise MissingInputError(f"{self.control[0]}.{self.control[1]} ficaria {length:g} m")
        _, prop, idx = self.control
        if idx is None:
            setattr(self.wire, prop, length)
        else:
            getattr(self.wire, prop)[idx] = length

    def pull_to_length(self, pull: float) -> float:
        if self.L_ref is None:
            raise RuntimeError("Referência de puxada ainda não calculada")
        return self.L_ref - pull

    # ------------------------------------------------------------------ leitura de resultados
    def _th(self, obj_name: str, var: str, extra=None) -> float:
        obj = self.model[obj_name]
        return float(obj.TimeHistory(var, self.static, extra)[0])

    def _extra(self, value):
        orc = self.orc
        if value == "End A":
            return orc.oeEndA
        if value == "End B":
            return orc.oeEndB
        if value == "Touchdown":
            return orc.oeTouchdown
        return None

    def link_tension(self) -> float:
        if not self.camelback:
            return float("nan")
        return max(self._th(self.case.role(r), "Tension") for r in ("CB_LINK_1", "CB_LINK_2"))

    def angle(self) -> float:
        return self._th(self.case.role("MCV"), self._text("statics.angle_variable"))

    def utilization(self) -> tuple[float, str, dict[str, float]]:
        """Mesmo cálculo do checkCriteria do _03 (valor estático / limite)."""
        ufs = {}
        for c in self.criteria:
            obj = self.model[c.obj]
            if c.type == "Time History":
                value = obj.TimeHistory(c.variable, self.static, self._extra(c.extra))[0]
            else:  # Range Graph Max
                arc = self.orc.arSpecifiedArclengths(*c.extra) if isinstance(c.extra, list) else None
                value = max(obj.RangeGraph(c.variable, self.static, None, arc).Mean)
            ufs[c.name] = float(value) / float(c.limit)
        if not ufs:
            return float("nan"), "", ufs
        name = max(ufs, key=ufs.get)
        return ufs[name], name, ufs

    # ------------------------------------------------------------------ avaliação
    def evaluate(self, purpose: str, state: str, length: float) -> Evaluation:
        if self.state and state != self.state:
            source = self._shapes.get(state, self._reload_base or self.model_path)
            log.debug("  Troca de estado %s -> %s: partindo de %s", self.state, state,
                      "última forma convergida desse estado" if state in self._shapes else "modelo montado")
            self._reload(source)
        self._apply_crane_presence()
        self.set_state(state)
        self.set_length(length)
        if self.crane_name and self.crane_length is not None and not self.without_crane:
            self.model[self.crane_name].Length[0] = self.crane_length
        pull = (self.L_ref - length) if self.L_ref is not None else float("nan")
        ev = Evaluation(purpose, state, pull, length, converged=False)
        if self.crane_name:
            ev.crane_length = self.crane_length if self.crane_length is not None else self.crane_built
        t = time.perf_counter()
        robust = None
        try:
            self._step_towards(purpose, length)
            self.set_length(length)
            robust = self._statics(f"{purpose}, cabo {length:.3f} m")
        except Exception as exc:  # não convergência
            ev.error = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
            self.log.append(ev)
            log.warning("  [%s] %s, cabo %.3f m (puxada %s): estática NÃO convergiu (%.1fs): %s", purpose, state,
                        length, "-" if math.isnan(pull) else f"{pull:.3f}", time.perf_counter() - t, ev.error)
            return ev
        ev.converged = True
        self._converged_length = length
        if self.ml_name and not self._ml_settled:
            self._ml_settled = True
            log.info("  Masterlink resolvido (estática com ele livre convergiu): as próximas estáticas mudam o cabo "
                     "com o masterlink livre")
        try:
            ev.link_tension = self.link_tension()
            ev.angle = self.angle()
            ev.uf_max, ev.governing, ev.ufs = self.utilization()
        except Exception as exc:
            ev.error = f"leitura de resultados: {exc}"
        try:
            ev.mcv_z = self._th(self.case.role("MCV"), "Z")
            ev.mcv_x = self._th(self.case.role("MCV"), "X")
            if state == FIXED:
                self.hub_z = ev.mcv_z
        except Exception:
            pass
        try:  # parte da solução anterior na próxima avaliação (como os scripts antigos)
            use_calculated_positions(self.model)
            restore_statics_settings(self.model, robust)   # valores originais antes de salvar a forma
            robust = None
            shape = self._shape_dir / f"forma_{state}.dat"
            self.model.SaveData(str(shape))
            self._shapes[state] = shape
        except Exception as exc:
            log.debug("  UseCalculatedPositions falhou: %s", exc)
        restore_statics_settings(self.model, robust)
        self.log.append(ev)
        log.info("  [%s] %s, cabo %.3f m (puxada %s): ângulo %.2f°, lingas %.2f kN, UF %.3f %s [%.1fs]",
                 purpose, state, length, "-" if math.isnan(pull) else f"{pull:.3f}", ev.angle, ev.link_tension,
                 ev.uf_max, f"({ev.governing})" if ev.governing else "", time.perf_counter() - t)
        if ev.error:
            log.warning("  [%s] %s", purpose, ev.error)
        return ev

    def _read_length(self):
        try:
            return float(self._get_control())
        except Exception:
            return None

    def _step_towards(self, purpose: str, target: float) -> None:
        """0.11.3: muda o cabo no máximo ``statics.max_length_step`` (0,5 m) por estática.

        Entre o último comprimento convergido e ``target``, roda estáticas intermediárias em passos
        iguais, cada uma partindo da anterior (UseCalculatedPositions). Uma que não converge faz a
        avaliação inteira falhar.
        """
        cap = self._max_step()
        start = self._converged_length
        if not cap or cap <= 0 or start is None or abs(target - start) <= cap + 1e-9:
            return
        n = int(math.ceil(abs(target - start) / cap - 1e-9))
        log.info("  Cabo de %.3f para %.3f m em %d passos de até %g m", start, target, n, cap)
        for i in range(1, n):
            step = start + (target - start) * i / n
            self.set_length(step)
            try:
                robust = self._statics(f"{purpose}, passo {i}/{n}, cabo {step:.3f} m")
            except StaticsFailed as exc:
                raise StaticsFailed(f"passo intermediário {i}/{n} (cabo {step:.3f} m) não convergiu: "
                                    f"{exc.orcaflex_message}", exc.last_progress) from exc
            use_calculated_positions(self.model)
            restore_statics_settings(self.model, robust)
            self._converged_length = step

    def _statics(self, label: str):
        """Estática da avaliação. Com corcova, enquanto o masterlink não estiver resolvido (nenhuma estática com
        ele livre convergiu desde o modelo montado): masterlink fixo na posição atual -> posições gravadas ->
        masterlink com a conexão original -> estática. Depois, só a estática com ele livre (0.11.2: mudar o
        cabo com o masterlink fixo não faz sentido). Devolve os valores originais de General."""
        ml = None
        if self.ml_name:
            try:
                ml = self.model[self.ml_name]
                original = str(ml.Connection)
            except Exception as exc:
                log.debug("  Masterlink '%s' indisponível (%s): estática direta", self.ml_name, exc)
                ml = None
        if ml is None or original == "Fixed" or self._ml_settled:
            return calculate_statics_robust(self.model, self.case, label, ladder=self.ladder)
        ml.Connection = "Fixed"
        try:
            robust = calculate_statics_robust(self.model, self.case, f"{label}, masterlink fixo",
                                              ladder=self.ladder_fixed)
        except Exception as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            ml.Connection = original
            log.warning("  Estática (%s) com o masterlink fixo não convergiu (%s); tentando com ele livre",
                        label, str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__)
        else:
            use_calculated_positions(self.model)          # parte da forma com o masterlink fixo
            restore_statics_settings(self.model, robust)
            ml.Connection = original
            log.debug("  Posições com o masterlink fixo gravadas; masterlink de volta a '%s'", original)
        return calculate_statics_robust(self.model, self.case, f"{label}, masterlink livre", ladder=self.ladder)

    def _search(self, purpose: str, state: str, fn, length_of, ftol=None) -> Search:
        def f(x: float) -> float:
            ev = self.evaluate(purpose, state, length_of(x))
            return fn(ev) if ev.converged else float("nan")

        # 0.11.6: passo máximo de cabo dentro da própria busca (cada passo avaliado; ver Search.eval)
        cap = self._max_step()
        l0, l1 = length_of(0.0), length_of(1.0)

        def current():
            if self._converged_length is None or l1 == l0:
                return None
            return (self._converged_length - l0) / (l1 - l0)

        return Search(f, xtol=self.case.num("statics.pull_tolerance"),
                      max_evals=int(self.case.num("statics.max_evaluations")), ftol=ftol,
                      max_step=cap or None, current=current if cap else None)

    def _max_step(self) -> float:
        v = self.case.get("statics.max_length_step")
        return float(v) if v not in (None, "") else 0.5

    # ------------------------------------------------------------------ referência (substitui o _02)
    def find_reference(self) -> float:
        """Comprimento do cabo em que as lingas começam a tracionar (MCV fixo).

        Sem corcova: a referência é o modelo montado (puxada zero = como montado).
        """
        if not self.camelback:
            self.L_ref = self.L_built
            log.info("Referência (sem corcova): %s.%s = %.3f m do modelo montado", self.control[0], self.control[1],
                     self.L_ref)
            return self.L_ref
        log.info("Buscando a puxada zero (lingas começam a tracionar, MCV fixo)")
        rise = self.case.num("statics.pull_tension_rise")
        step = self.case.num("statics.pull_step")
        max_pull = self.case.num("statics.pull_max")
        # Linha de base: com o cabo frouxo o masterlink fica pendurado nas lingas, que levam o peso dele
        # (e do cabo que se apoia nele). A tração nunca chega a zero; a puxada zero é quando a tração
        # SOBE acima dessa base, ou seja, quando o guincho começa a levantar a linha.
        slack = self.case.num("statics.reference_slack")
        base_ev = self.evaluate("referência (base, cabo frouxo)", FIXED, self.L_built + slack)
        if base_ev.error or math.isnan(base_ev.link_tension):
            raise NotBracketed(f"Estática com o cabo frouxo ({self.L_built + slack:.3f} m) falhou: {base_ev.error}")
        base = base_ev.link_tension
        log.info("  Tração nas lingas com o cabo frouxo (+%.2f m): %.3f kN = base (peso do masterlink e do cabo "
                 "apoiado). Puxada zero: base + %.2f kN", slack, base, rise)
        self.reference_base = base
        # variável: encurtamento d em relação ao comprimento do modelo montado
        s = self._search("referência", FIXED, lambda ev: ev.link_tension - base - rise, lambda d: self.L_built - d)
        f0 = s.eval(0.0)
        direction = -1 if (not math.isnan(f0) and f0 > 0) else +1   # já levantando a linha: solta cabo
        d = s.solve(0.0, step, -max_pull, max_pull, direction)
        self.L_ref = self.L_built - d
        log.info("Referência: cabo %.3f m (modelo montado %.3f m; lingas começam a tracionar)", self.L_ref, self.L_built)
        for ev in self.log:  # puxada das avaliações já feitas
            if ev.purpose == "referência":
                ev.pull = self.L_ref - ev.winch_length
        return self.L_ref

    # ------------------------------------------------------------------ alvos
    def solve_pull(self, purpose: str, state: str, target: str, value: float, start: float = 0.0,
                   direction: int = +1, allow_payout: bool = False) -> float:
        """Puxada em que ``target`` atinge ``value``, procurando a partir de ``start`` no sentido ``direction``
        (+1 = mais puxada = cabo do guincho mais curto).

        Alvos: 'angle' (ângulo do MCV), 'uf' (UF máximo) e 'uf_or_angle' (para no que vier primeiro: UF =
        ``value`` ou variação de ângulo, no sentido do pull-in, = ``statics.free_overpull_angle_factor`` ×
        ``statics.cb_angle`` a partir da horizontal).
        """
        if self.L_ref is None:
            self.find_reference()
        if target == "angle":
            fn = lambda ev: ev.angle - value  # noqa: E731
        elif target in ("uf", "uf_or_angle"):
            if not self.criteria and target == "uf":
                raise MissingInputError("Nenhum critério estático com limite numérico: não há como achar o overpull")
            if target == "uf":
                fn = lambda ev: ev.uf_max - value  # noqa: E731
            else:
                limit_angle = self.case.num("statics.free_overpull_angle_factor") * self.case.num("statics.cb_angle")
                a0 = self.horizontal["angle"] if self.horizontal else 0.0
                sgn = self.angle_sign or 1

                def fn(ev):
                    terms = [sgn * (ev.angle - a0) / limit_angle - 1.0]
                    if self.criteria:
                        terms.append(ev.uf_max / value - 1.0)
                    return max(terms)
        else:
            raise ValueError(f"Alvo desconhecido: {target}")
        # 0.11.5: alvo de ângulo para em |Δângulo| <= statics.angle_tolerance (0,005°), não só na tolerância da puxada
        ftol = None
        if target == "angle":
            v = self.case.get("statics.angle_tolerance")
            ftol = float(v) if v not in (None, "") else 0.005
        s = self._search(purpose, state, fn, self.pull_to_length, ftol=ftol)
        lo, hi = 0.0, self.case.num("statics.pull_max")
        if target in ("uf", "uf_or_angle"):
            f0 = s.eval(start)
            if not math.isnan(f0) and f0 > 0:
                if not allow_payout:
                    raise NotBracketed(f"Critério já atingido na puxada de partida {start:.3f} m "
                                       f"(UF já passa do limite {value:g} ou ângulo além do limite); não há overpull "
                                       f"admissível acima dela")
                log.info("  UF %.3f > %g na puxada de partida %.3f m: soltando o guincho até UF = %g",
                         f0 + value, value, start, value)
                direction, lo = -1, -hi
                try:
                    return s.solve(start, self.case.num("statics.pull_step"), lo, hi, direction)
                except NotBracketed as exc:
                    raise NotBracketed(f"UF não cai abaixo de {value:g} soltando o guincho a partir de {start:.3f} m "
                                       f"({exc})") from exc
        return s.solve(start, self.case.num("statics.pull_step"), lo, hi, direction)

    def solve_horizontal(self, purpose: str, start: float = 0.0) -> float:
        """MCV livre, horizontal e na altura do hub.

        Laço alternado: (1) cabo do guincho para ângulo 0 com o cabo do guindaste fixo; (2) cabo do guindaste
        corrigido pela diferença de altura do MCV em relação ao hub. Repete até as duas tolerâncias.
        Também mede o sinal de d(ângulo)/d(puxada), usado para distinguir pull-in de pay-out.
        """
        if self.L_ref is None:
            self.find_reference()
        tol_z = self.case.num("statics.height_tolerance")
        pull = start
        if self.crane_name and self.hub_z is None:   # altura do hub = Z do MCV conectado (estado fixo)
            self.at(f"{purpose} (altura do hub)", FIXED, start)
        if self.crane_name and self.hub_z is None:
            log.warning("  Altura do hub desconhecida (nenhuma estática com o MCV fixo): horizontal sem ajuste do "
                        "cabo do guindaste")
        crane = self.crane_length if self.crane_length is not None else self.crane_built
        for k in range(int(self.case.num("statics.max_height_iterations"))):
            if self.crane_name:
                self.crane_length = crane
            n0 = len(self.log)
            ev_start = self.at(purpose, FREE, pull)
            direction = +1
            if ev_start.converged and self.angle_sign is not None:
                direction = +1 if (0.0 - ev_start.angle) * self.angle_sign > 0 else -1
            pull = self.solve_pull(purpose, FREE, "angle", 0.0, pull, direction)
            self._update_angle_sign(self.log[n0:])
            ev = self.at(purpose, FREE, pull)
            if not ev.converged:
                raise NotBracketed(f"Estática não converge na horizontal (puxada {pull:.3f} m)")
            if not self.crane_name or self.hub_z is None or math.isnan(ev.mcv_z):
                break
            dz = ev.mcv_z - self.hub_z
            log.info("  Horizontal, iteração %d: cabo do guindaste %.3f m, puxada %.3f m, MCV %.3f m %s do hub",
                     k + 1, crane, pull, abs(dz), "acima" if dz > 0 else "abaixo")
            if abs(dz) <= tol_z:
                break
            cap = self._max_step()   # 0.11.6: cabo do guindaste também muda no máximo max_length_step
            step_dz = max(-cap, min(cap, dz)) if cap else dz
            if step_dz != dz:
                log.info("  Correção do cabo do guindaste limitada a %+.3f m (diferença %.3f m)", step_dz, dz)
            crane += step_dz     # MCV alto -> solta cabo do guindaste
        else:
            raise NotBracketed(f"MCV não chegou à altura do hub em {k + 1} iterações (diferença {dz:.3f} m)")
        # alinha o MCV com o hub em X transladando o modelo (comentário do usuário, 18/11/2026)
        ev = self._align_with_hub(purpose, pull, ev)
        self.horizontal = {"pull": pull, "crane": self.crane_length, "angle": ev.angle, "x_shift": self.x_shift}
        # sinal do ângulo com a puxada (pull-in = mais puxada)
        step = self.case.num("statics.pull_step")
        ev2 = self.at(f"{purpose} (sentido do ângulo)", FREE, pull + step)
        if ev2.converged and not math.isnan(ev2.angle) and ev2.angle != ev.angle:
            self.angle_sign = 1 if ev2.angle > ev.angle else -1
        else:
            self.angle_sign = self.angle_sign or 1
            log.warning("  Não consegui medir o sentido do ângulo com a puxada; assumindo %+d", self.angle_sign)
        log.info("  Ângulo do MCV %s com a puxada (pull-in = ângulo %s)",
                 "aumenta" if self.angle_sign > 0 else "diminui", "positivo" if self.angle_sign > 0 else "negativo")
        # volta ao estado exato da horizontal (forma de partida das próximas posições)
        self.at(purpose, FREE, pull)
        shape = self._shape_dir / "horizontal.dat"
        self.model.SaveData(str(shape))
        self._horizontal_shape = shape
        return pull

    def _align_with_hub(self, purpose: str, pull: float, ev: Evaluation) -> Evaluation:
        from ..model.view import hub_x, shift_model_x

        # lido durante a avaliação: depois do UseCalculatedPositions o modelo volta a Reset e não há resultados
        x = ev.mcv_x
        if math.isnan(x):
            log.warning("  X do MCV não disponível na avaliação: modelo não alinhado com o hub")
            return ev
        target = hub_x(self.model, self.case)
        v = self.case.get("statics.align_tolerance")
        tol = float(v) if v not in (None, "") else 0.001
        ev2 = ev
        for k in range(3):   # 0.11.5: repete até o MCV ficar a <= statics.align_tolerance do hub
            x = ev2.mcv_x
            if math.isnan(x):
                break
            dx = target - x
            if abs(dx) <= tol:
                break
            ev2 = self._shift_once(purpose, pull, dx, x, k)
        if not math.isnan(ev2.mcv_x):
            log.info("  Depois da translação: MCV em X = %.4f m (hub em %.4f m; tolerância %g m)", ev2.mcv_x,
                     target, tol)
        if ev2 is ev:
            return ev
        # formas de partida antigas ficaram na posição anterior: a base passa a ser o modelo alinhado
        base = self._shape_dir / "horizontal_alinhado.dat"
        self.model.SaveData(str(base))
        self._reload_base = base
        self._shapes = {FREE: self._shapes.get(FREE, base)}
        return ev2

    def _shift_once(self, purpose: str, pull: float, dx: float, x: float, k: int) -> Evaluation:
        from ..model.view import shift_model_x

        method = shift_model_x(self.model, self.case, dx, self.orc)
        self.x_shift += dx
        log.info("  Modelo transladado %.4f m em X (MCV em X = %.4f m -> hub) por %s%s", dx, x, method,
                 f" (correção {k})" if k else "")
        ev2 = self.at(f"{purpose} (alinhado com o hub)", FREE, pull)
        if not ev2.converged:
            # as formas iniciais gravadas ("User specified") podem não ter acompanhado a translação:
            # volta ao método de estática das linhas do modelo montado e recalcula
            restored = self._restore_line_statics_method()
            log.warning("  Estática não convergiu depois da translação; refazendo com o método original das linhas "
                        "(%s)", ", ".join(restored) or "-")
            ev2 = self.at(f"{purpose} (alinhado com o hub, forma recalculada)", FREE, pull)
        if not ev2.converged:
            raise NotBracketed("Estática não converge depois de transladar o modelo para alinhar o MCV com o hub")
        return ev2

    def _has_object(self, name: str) -> bool:
        try:
            self.model[name]
            return True
        except Exception:
            return False

    def _apply_crane_presence(self) -> None:
        """Remove o cabo do guindaste (05) ou o traz de volta (recarregando) nas outras posições."""
        if not self.crane_name:
            return
        present = self._has_object(self.crane_name)
        if self.without_crane and present:
            self.model.DestroyObject(self.model[self.crane_name])
            log.info("  Cabo do guindaste '%s' removido do modelo", self.crane_name)
        elif not self.without_crane and not present:
            state = self.state
            self._reload(self._horizontal_shape or self._reload_base or self.model_path)
            log.debug("  Modelo recarregado com o cabo do guindaste (estado anterior %s)", state)

    def start_from_horizontal(self, state: str) -> None:
        """Próxima avaliação parte da forma convergida da horizontal (MCV livre, alinhado, guindaste ajustado)."""
        if self._horizontal_shape is None:
            return
        self._reload(self._horizontal_shape)
        self._shapes = {FREE: self._horizontal_shape}
        log.debug("  Partindo da forma da horizontal (%s)", state)

    def _restore_line_statics_method(self) -> list[str]:
        """StaticsStep1 das linhas como no modelo montado (antes de UseCalculatedPositions)."""
        out = []
        try:
            original = self.orc.Model(str(self.model_path))
        except Exception:
            return out
        for role in ("LINE", "CRANE_WIRE", "WINCH_WIRE"):
            name = self.case.roles.get(role, "")
            try:
                step = original[name].StaticsStep1
                self.model[name].StaticsStep1 = step
                out.append(f"{name}: {step}")
            except Exception:
                continue
        return out

    def _update_angle_sign(self, evs: list) -> None:
        """Sinal de d(ângulo)/d(puxada) pelas duas avaliações livres convergidas mais próximas da raiz."""
        pts = sorted({(e.pull, e.angle) for e in evs if e.converged and e.state == FREE
                      and not math.isnan(e.angle) and not math.isnan(e.pull)}, key=lambda t: abs(t[1]))
        for (p1, a1) in pts[:1]:
            for (p2, a2) in pts[1:]:
                if abs(p2 - p1) > 1e-9 and a2 != a1:
                    self.angle_sign = 1 if (a2 - a1) / (p2 - p1) > 0 else -1
                    return

    def sweep(self, state: str, pulls: list[float]) -> list[Evaluation]:
        if self.L_ref is None:
            self.find_reference()
        return [self.evaluate("varredura", state, self.pull_to_length(p)) for p in pulls]

    # ------------------------------------------------------------------ saída
    def at(self, purpose: str, state: str, pull: float) -> Evaluation:
        """Recalcula a estática exatamente na puxada final (estado salvo depois em .sim)."""
        return self.evaluate(purpose, state, self.pull_to_length(pull))

    def save_simulation(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Cada avaliação termina com UseCalculatedPositions, que altera os dados e devolve o modelo ao
        # estado Reset; o .sim só pode ser salvo com a estática calculada. Recalcula a partir das posições
        # gravadas (converge em poucas iterações) e salva.
        state = str(getattr(self.model, "state", "InStaticState"))
        if "InStaticState" not in state:
            calculate_statics(self.model, f"salvar {path.stem}")
        self.model.SaveSimulation(str(path))
        return path

    def write_log(self, path: Path) -> Path:
        path = Path(path)
        names = sorted({k for ev in self.log for k in ev.ufs})
        fields = [k for k in asdict(self.log[0]) if k != "ufs"] + [f"UF {n}" for n in names] if self.log else []
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for ev in self.log:
                row = {k: v for k, v in asdict(ev).items() if k != "ufs"}
                row.update({f"UF {n}": ev.ufs.get(n, "") for n in names})
                w.writerow(row)
        return path
