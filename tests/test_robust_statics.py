"""Estática robusta: 50000 iterações e amortecimento 1e3–1e4 nos casos difíceis, depois os valores originais."""

import pytest

from cvdtools._orcaflex import (StaticsFailed, calculate_statics_robust, parse_statics_progress,
                                restore_statics_settings, robust_mode)
from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.schema import MissingInputError, ResolvedValue
from fake_orcfxapi import BASE_OBJECTS, FakeModel


class HardModel(FakeModel):
    """Só converge com amortecimento mínimo >= 1000; guarda os valores usados em cada estática."""

    def __init__(self, needs=1000, upper=None):
        super().__init__()
        self.objects = {"General": dict(BASE_OBJECTS["General"])}
        self.seen = []
        self.positions = 0
        self.needs, self.upper = needs, upper

    def CalculateStatics(self):
        g = self.objects["General"]
        assert isinstance(g["StaticsMaxIterations"], int), "o OrcFxAPI só aceita inteiro em StaticsMaxIterations"
        self.seen.append((g["StaticsMaxIterations"], g["StaticsMinDamping"], g["StaticsMaxDamping"]))
        if g["StaticsMinDamping"] < self.needs or (self.upper and g["StaticsMinDamping"] > self.upper):
            raise RuntimeError("Static calculation failed (Whole system statics: Not converged.)")

    def UseCalculatedPositions(self, setLinesToUserSpecifiedStartingShape=False):
        self.positions += 1


def _case(legacy_inputs, **values):
    case = case_from_legacy_json(legacy_inputs)
    for k, v in values.items():
        case.values[k.replace("__", ".")] = ResolvedValue(v, "override")
    return case


def test_retry_with_robust_values_then_restore(legacy_inputs):
    case = _case(legacy_inputs)                      # 1 extremidade: auto = repetir só se falhar
    assert robust_mode(case) == "repetir"
    m = HardModel()
    original = calculate_statics_robust(m, case, "teste")
    # modelo -> 1e3/1e4 converge -> ÷10 não converge -> de volta a 1e3/1e4
    assert m.seen == [(10000, 200, 1000), (50000, 1000.0, 10000.0), (50000, 100.0, 1000.0),
                      (50000, 1000.0, 10000.0)]
    restore_statics_settings(m, original)
    g = m.objects["General"]
    assert (g["StaticsMaxIterations"], g["StaticsMinDamping"], g["StaticsMaxDamping"]) == (10000, 200, 1000)


def test_two_ends_go_straight_to_robust(legacy_inputs):
    case = _case(legacy_inputs, model__modelled_ends=2.0)
    assert robust_mode(case) == "direto"
    m = HardModel()
    calculate_statics_robust(m, case, "teste")
    assert m.seen[0] == (50000, 1000.0, 10000.0)


def test_failure_restores_and_never_mode(legacy_inputs):
    m = HardModel(needs=1e9)
    case = _case(legacy_inputs, statics__robust="sempre")
    with pytest.raises(StaticsFailed):
        calculate_statics_robust(m, case, "teste")
    assert m.objects["General"]["StaticsMinDamping"] == 200, "valores voltam mesmo se a robusta falhar"
    with pytest.raises(StaticsFailed):
        calculate_statics_robust(HardModel(), _case(legacy_inputs, statics__robust="nunca"), "teste")
    with pytest.raises(MissingInputError):
        robust_mode(_case(legacy_inputs, statics__robust="talvez"))


def test_ladder_steps_down_after_converging(legacy_inputs):
    """Converge em 1e3/1e4 -> grava posições -> ÷10 até o piso (10/100), mesmas iterações -> volta aos originais."""
    m = HardModel(needs=10)
    original = calculate_statics_robust(m, _case(legacy_inputs, statics__robust="sempre"), "teste")
    assert m.seen == [(50000, 1000.0, 10000.0), (50000, 100.0, 1000.0), (50000, 10.0, 100.0)]
    assert m.positions == 2
    restore_statics_settings(m, original)
    g = m.objects["General"]
    assert (g["StaticsMinDamping"], g["StaticsMaxDamping"], g["JacobianPerturbationFactor"]) == (200, 1000, 1e-5)


def test_ladder_escalates_with_jacobian_then_restores(legacy_inputs):
    """Não converge em 1e3/1e4 -> 1e4/1e4 (limite) com Jacobiano 1e-3 -> ÷10 -> no fim, damping e Jacobiano do modelo."""
    m = HardModel(needs=10000)
    jac = []
    orig_calc = m.CalculateStatics

    def calc():
        jac.append(m.objects["General"]["JacobianPerturbationFactor"])
        orig_calc()

    m.CalculateStatics = calc
    original = calculate_statics_robust(m, _case(legacy_inputs, statics__robust="sempre"), "teste")
    # máximo limitado a 1e4: o degrau acima é 1e4/1e4; o ÷10 vai a 1e3/1e3 e não converge -> volta a 1e4/1e4
    assert m.seen == [(50000, 1000.0, 10000.0), (50000, 10000.0, 10000.0), (50000, 1000.0, 1000.0),
                      (50000, 10000.0, 10000.0)]
    assert jac == [1e-5, 1e-3, 1e-3, 1e-3]
    restore_statics_settings(m, original)
    g = m.objects["General"]
    assert (g["StaticsMaxIterations"], g["StaticsMinDamping"], g["StaticsMaxDamping"],
            g["JacobianPerturbationFactor"]) == (10000, 200, 1000, 1e-5)


def test_ladder_gives_up_after_escalations(legacy_inputs):
    m = HardModel(needs=1e9)
    with pytest.raises(StaticsFailed):
        calculate_statics_robust(m, _case(legacy_inputs, statics__robust="sempre"), "teste")
    assert len(m.seen) == 2
    assert m.objects["General"]["JacobianPerturbationFactor"] == 1e-5


def test_parse_statics_progress():
    assert parse_statics_progress("Whole system statics: 16862 (latest error = 0,00167 at Winch Wire, node 92, Z)") \
        == ("Whole system statics", 0.00167)
    assert parse_statics_progress("Whole system statics: 1014 (latest error = 10,15e3 at Winch Wire)")[1] == 10150.0
    assert parse_statics_progress("Whole system statics: 14000 (latest error = 100e-6 at Constraint B)")[1] \
        == pytest.approx(1e-4)
    assert parse_statics_progress("Dean wave initialisation: 1") == ("Dean wave initialisation", None)


def test_stall_cancels_statics(monkeypatch):
    """Erro parado: a estática é cancelada pelo tratador de progresso e conta como não convergida."""
    from cvdtools import _orcaflex
    from cvdtools._orcaflex import StaticsStalled, calculate_statics

    clock = iter(range(0, 10000, 30))
    monkeypatch.setattr("time.perf_counter", lambda: float(next(clock)))

    class Stuck(FakeModel):
        def CalculateStatics(self):
            for i in range(20):
                if self.staticsProgressHandler(self, f"Whole system statics: {i} (latest error = 0,0017 at X)"):
                    raise RuntimeError("Static calculation cancelled")

    m = Stuck()
    m.__dict__["staticsProgressHandler"] = None
    with pytest.raises(StaticsStalled):
        calculate_statics(m, "teste", stall_seconds=120)


def test_damping_bounds(legacy_inputs):
    """Mínimo sempre em [10, 1e4], máximo em [1e2, 1e4]; sem degrau possível, não repete a mesma estática."""
    from cvdtools._orcaflex import clamp_damping

    case = _case(legacy_inputs)
    assert clamp_damping(case, 1.0, 10.0) == (10.0, 100.0)
    assert clamp_damping(case, 1e5, 1e6) == (1e4, 1e4)
    assert clamp_damping(case, 100.0, 1000.0) == (100.0, 1000.0)
    # descida: 10/100 já é o piso -> não roda o passo ÷10
    m = HardModel(needs=0)
    calculate_statics_robust(m, _case(legacy_inputs, statics__robust="sempre", statics__robust_min_damping=10.0,
                                      statics__robust_max_damping=100.0), "teste")
    assert m.seen == [(50000, 10.0, 100.0)] and m.positions == 0
    # subida: já em 1e4/1e4 com Jacobiano 1e-3 -> desiste sem repetir
    m = HardModel(needs=1e9)
    m.objects["General"]["JacobianPerturbationFactor"] = 1e-3
    with pytest.raises(StaticsFailed):
        calculate_statics_robust(m, _case(legacy_inputs, statics__robust="sempre", statics__robust_min_damping=1e4,
                                          statics__robust_max_damping=1e4), "teste")
    assert len(m.seen) == 1


def test_ladder_remembers_level_between_evaluations(legacy_inputs):
    """0.10.4 (puxadas): a 2ª avaliação começa no piso que convergiu, sem subir; só sobe se não convergir."""
    from cvdtools._orcaflex import DampingLadder

    case = _case(legacy_inputs, statics__robust="sempre")
    ladder = DampingLadder(case)
    m = HardModel(needs=10)
    for _ in range(3):
        restore_statics_settings(m, calculate_statics_robust(m, case, "puxada", ladder=ladder))
    assert m.seen == [(50000, 1000.0, 10000.0), (50000, 100.0, 1000.0), (50000, 10.0, 100.0),
                      (50000, 10.0, 100.0), (50000, 10.0, 100.0)]
    assert ladder.start == (10.0, 100.0)
    # passa a exigir 100: falha no piso, sobe ×10 e converge; a próxima já parte de 100/1000
    m.needs = 100
    m.seen.clear()
    restore_statics_settings(m, calculate_statics_robust(m, case, "puxada", ladder=ladder))
    restore_statics_settings(m, calculate_statics_robust(m, case, "puxada", ladder=ladder))
    # (a descida a 10/100 falhou uma vez: não é tentada de novo)
    assert m.seen == [(50000, 10.0, 100.0), (50000, 100.0, 1000.0), (50000, 10.0, 100.0), (50000, 100.0, 1000.0),
                      (50000, 100.0, 1000.0)]
