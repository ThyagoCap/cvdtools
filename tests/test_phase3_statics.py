"""Fase 3: solver de puxada, referência, posições e varredura (OrcFxAPI falso com física sintética)."""

import csv
import math

import pytest

from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.model.builder import build_model
from cvdtools.schema import ResolvedValue
from cvdtools.statics.engine import StaticsEngine, parse_state
from cvdtools.statics.positions import run_positions
from cvdtools.statics.solver import NotBracketed, Search
from fake_orcfxapi import FakeModel, write_base_model

# --------------------------------------------------------------------------- solver


def test_solver_monotone():
    s = Search(lambda x: x ** 3 - 8, xtol=1e-4)
    assert s.solve(0, 0.5, -10, 10) == pytest.approx(2, abs=1e-4)
    assert len(s.history) < 30


def test_solver_decreasing_and_downward():
    s = Search(lambda x: 5 - x, xtol=1e-3)
    assert s.solve(10, 0.5, 0, 20, direction=-1) == pytest.approx(5, abs=1e-3)


def test_solver_skips_nonconvergence():
    f = lambda x: float("nan") if 1.5 < x < 2.0 else x - 4.0  # noqa: E731
    s = Search(f, xtol=1e-3)
    assert s.solve(0, 0.6, 0, 20) == pytest.approx(4, abs=1e-3)
    assert any(math.isnan(y) for _, y in s.history), "deveria ter passado pela região que não converge"


def test_solver_returns_admissible_side():
    s = Search(lambda x: x - 1.0, xtol=0.05)
    x = s.solve(0, 0.3, 0, 5)
    assert x <= 1.0 and x == pytest.approx(1.0, abs=0.05), "devolve o lado com f <= 0 (UF dentro do limite)"


def test_solver_not_bracketed():
    with pytest.raises(NotBracketed):
        Search(lambda x: x + 1).solve(0, 0.5, 0, 10)


def test_parse_state():
    assert parse_state("Connection=Free; DegreesOfFreedomInStatics=All") == [
        ("Connection", "Free"), ("DegreesOfFreedomInStatics", "All")]
    assert parse_state("InitialZ=3,5") == [("InitialZ", 3.5)]


# --------------------------------------------------------------------------- modelo com física sintética

SLACK = 2.0   # encurtamento (m) a partir do modelo montado até as lingas tracionarem


class PhysicsModel(FakeModel):
    """Puxada p = encurtamento - SLACK.
    Livre: ângulo = -5 + p ; UF = 0.2 + 0.05 p (UF=1 em p=16) ; não converge em 10.0 < p < 10.3
    Fixo:  UF = 0.3 + 0.1 p (UF=1 em p=7)
    """

    built = None
    crane_built = None
    HUB_Z = -100.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        if PhysicsModel.built is None and "Winch Wire" in self.objects:   # comprimento do modelo montado
            PhysicsModel.built = self.objects["Winch Wire"]["Length"][0]
            PhysicsModel.crane_built = self.objects["Crane Wire"]["Length"][0]

    # estados do OrcFxAPI: UseCalculatedPositions devolve o modelo a Reset; .sim só com estática calculada
    state = "Reset"

    def UseCalculatedPositions(self, setLinesToUserSpecifiedStartingShape=False):
        self.state = "Reset"
        self.results = {}       # como no OrcaFlex: em Reset não há resultados (v0.6.7 lia o X do MCV aqui)

    def SaveSimulation(self, path):
        if self.state != "InStaticState":
            raise RuntimeError("Invalid model state (C_SaveSimulation cannot be called when the model is in Reset "
                               "state)")
        self.SaveData(path)

    def CalculateStatics(self):
        self.state = "InStaticState"
        wire = self.objects["Winch Wire"]
        if PhysicsModel.built is None:
            PhysicsModel.built = wire["Length"][0]
        d = PhysicsModel.built - wire["Length"][0]
        p = d - SLACK
        free = self.objects["MCV"].get("Connection") == "Free"
        if free and 10.0 < p < 10.3:
            raise RuntimeError("Statics failed to converge")
        uf = (0.2 + 0.05 * p) if free else (0.3 + 0.1 * max(p, -1.0))   # fixo: UF mínimo 0,2
        # livre: MCV 2 m acima do hub com o guindaste do modelo montado; 1 m de cabo = 1 m de altura
        crane_built = PhysicsModel.crane_built if PhysicsModel.crane_built is not None else \
            self.objects["Crane Wire"]["Length"][0]
        z = self.HUB_Z + 2.0 - (self.objects["Crane Wire"]["Length"][0] - crane_built) if free else self.HUB_Z
        angle = -5.0 + p - 0.3 * (z - self.HUB_Z) if free else 0.0
        self.results = {
            ("Link CB 1", "Tension", "None"): [50.0 * max(0.0, p)],
            ("Link CB 2", "Tension", "None"): [40.0 * max(0.0, p)],
            ("MCV", "Rotation 2", "None"): [angle],
            ("MCV", "Z", "None"): [z],
            # livre: o MCV fica 3 m à frente da posição dos dados (o alinhamento com o hub deve corrigir)
            ("MCV", "X", "None"): [self.objects["MCV"].get("InitialX", 0.0) + (3.0 if free else 0.0)],
            ("Line", "End Force", "EndA"): [uf * 95.0],
            ("Line", "End Moment", "EndA"): [10.0],
            ("Line", "Shear force", "EndA"): [5.0],
            ("Line", "Curvature", "range"): [0.01, 0.02],
            ("Bend Restrictor Element", "Bend moment", "range"): [1.0],
        }


@pytest.fixture
def built_case(tmp_path, fake_orc, legacy_inputs):
    PhysicsModel.built = None
    PhysicsModel.crane_built = None
    fake_orc.Model = PhysicsModel
    case = case_from_legacy_json(legacy_inputs)
    path = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "caso.dat").model_path
    PhysicsModel.built = None
    return case, path


def test_missing_criterion_object_fails_early(built_case):
    from cvdtools.schema import MissingInputError

    case, path = built_case
    case.criteria[0].obj = "Objeto Que Não Existe"
    with pytest.raises(MissingInputError, match="Objeto Que Não Existe"):
        StaticsEngine(case, path)


def test_reference(built_case):
    case, path = built_case
    eng = StaticsEngine(case, path)
    L_ref = eng.find_reference()
    assert eng.L_built - L_ref == pytest.approx(SLACK, abs=0.011)
    assert eng.state == "fixed"


def test_positions(built_case, tmp_path):
    case, path = built_case
    results, eng = run_positions(case, path, tmp_path / "pos")
    by = {r.name: r for r in results}
    # horizontal: guindaste solto 2 m (MCV no hub) e ângulo 0
    assert by["01.Horizontal"].pull == pytest.approx(5.0, abs=0.011)
    assert by["01.Horizontal"].mcv_z == pytest.approx(PhysicsModel.HUB_Z, abs=0.011)
    assert by["01.Horizontal"].crane_length == pytest.approx(PhysicsModel.crane_built + 2.0, abs=0.011)
    assert eng.angle_sign == +1
    # alinhamento com o hub (X = 0): modelo transladado -3 m, inclusive nas posições seguintes
    assert eng.x_shift == pytest.approx(-3.0)
    o = FakeModel(str(tmp_path / "pos" / by["01.Horizontal"].sim)).objects
    assert o["MCV"]["InitialX"] == pytest.approx(-3.0)
    assert o["General"]["DefaultViewAzimuth"] == 270.0
    o5 = FakeModel(str(tmp_path / "pos" / by["05.Max_Fixed_Overpull"].sim)).objects
    assert o5["MCV"]["InitialX"] == pytest.approx(-3.0)
    # pull-in = guincho mais curto (mais puxada) que a horizontal; pay-out = mais longo
    assert by["02.CB_PullIn"].pull == pytest.approx(8.0, abs=0.011)
    assert by["02.CB_PullIn"].winch_length < by["01.Horizontal"].winch_length
    assert by["03.CB_PayOut"].pull == pytest.approx(2.0, abs=0.011)
    assert by["03.CB_PayOut"].winch_length > by["01.Horizontal"].winch_length
    assert by["02.CB_PullIn"].crane_length == pytest.approx(by["01.Horizontal"].crane_length)
    # free overpull: UF chegaria a 1 só em p = 16; o ângulo chega a 3 × 3° em p = 14
    assert by["04.Max_Free_Overpull"].pull == pytest.approx(14.0, abs=0.011)
    assert by["04.Max_Free_Overpull"].governing.startswith("ângulo")
    assert by["05.Max_Fixed_Overpull"].pull == pytest.approx(7.0, abs=0.011)
    o5 = FakeModel(str(tmp_path / "pos" / by["05.Max_Fixed_Overpull"].sim)).objects
    assert "Crane Wire" not in o5                                   # 05 sem o cabo do guindaste
    o4 = FakeModel(str(tmp_path / "pos" / by["04.Max_Free_Overpull"].sim)).objects
    assert "Crane Wire" in o4
    # todas as posições partem da horizontal
    starts = {}
    for e in eng.log:
        starts.setdefault(e.purpose, e.pull)
    for name in ("02.CB_PullIn", "03.CB_PayOut", "04.Max_Free_Overpull", "05.Max_Fixed_Overpull"):
        assert starts[name] == pytest.approx(by["01.Horizontal"].pull, abs=1e-6), name
    assert by["05.Max_Fixed_Overpull"].governing == "Axial Tension"
    assert all(r.status == "OK" for r in results)
    for r in results:
        assert (tmp_path / "pos" / r.sim).is_file()
        o = FakeModel(str(tmp_path / "pos" / r.sim)).objects
        assert o["MCV"]["Connection"] == ("Free" if r.state == "free" else "Anchored")
        assert o["Winch Wire"]["Length"][0] == pytest.approx(r.winch_length)
    rows = list(csv.reader(open(tmp_path / "pos" / "positions.csv", encoding="utf-8-sig")))
    assert rows[3][0].startswith("# Referência")
    log = list(csv.DictReader(open(tmp_path / "pos" / "statics_log.csv", encoding="utf-8-sig")))
    assert {r["purpose"] for r in log} >= {"referência", "01.Horizontal", "05.Max_Fixed_Overpull"}
    assert "UF Axial Tension" in log[0]
    assert len(eng.log) < 200


def test_fixed_overpull_pays_out_when_uf_above_limit(built_case, tmp_path):
    """UF já > limite na horizontal (p = 5 -> UF 0,8 > 0,5): solta o guincho até UF = 0,5 (p = 2)."""
    case, path = built_case
    case.values["statics.uf_limit"] = ResolvedValue(0.5, "override")
    results, eng = run_positions(case, path, tmp_path / "pos", only=["Max_Fixed_Overpull"])
    assert [r.name for r in results] == ["05.Max_Fixed_Overpull"]
    r = results[0]
    assert r.status == "OK" and r.pull == pytest.approx(2.0, abs=0.011) and r.uf_max <= 0.5 + 1e-9
    assert "solto" in r.message
    assert "Crane Wire" not in FakeModel(str(tmp_path / "pos" / r.sim)).objects


def test_fixed_overpull_fails_when_payout_does_not_help(built_case, tmp_path):
    case, path = built_case
    case.values["statics.uf_limit"] = ResolvedValue(0.1, "override")   # UF fixo nunca fica abaixo de 0,2
    results, _ = run_positions(case, path, tmp_path / "pos", only=["Max_Fixed_Overpull"], save_sims=False)
    assert results[0].status == "FALHOU" and "não cai abaixo" in results[0].message


def test_sweep_logs_nonconvergence(built_case):
    case, path = built_case
    eng = StaticsEngine(case, path)
    evs = eng.sweep("free", [9.9, 10.1, 10.5])
    assert [e.converged for e in evs] == [True, False, True]
    assert "converge" in evs[1].error


def test_no_camelback_uses_length_control(built_case, legacy_inputs, tmp_path):
    """Fase 5: sem corcova, referência = modelo montado e as mesmas posições (nomes sem 'CB')."""
    from cvdtools.schema import OperationSpec
    from cvdtools.statics.engine import parse_control
    from cvdtools.statics.positions import run_positions

    case = case_from_legacy_json(legacy_inputs, operation=OperationSpec("1st end CVD", "no_camelback",
                                                                        camelback=False, supported=True))
    assert parse_control("{WINCH_WIRE}.Length[0]", case.roles) == ("Winch Wire", "Length", 0)
    assert parse_control("Crane Wire.Length[-1]", case.roles) == ("Crane Wire", "Length", -1)
    PhysicsModel.built = None
    eng = StaticsEngine(case, built_case[1])
    assert eng.find_reference() == pytest.approx(eng.L_built)
    assert math.isnan(eng.link_tension())
    PhysicsModel.built = None
    res, eng = run_positions(case, built_case[1], tmp_path / "pos", save_sims=False)
    names = [r.name for r in res]
    assert names == ["01.Horizontal", "02.PullIn", "03.PayOut", "04.Max_Free_Overpull", "05.Max_Fixed_Overpull"]
    by = {r.name: r for r in res}
    # física do teste: livre, ângulo = -5 + (p - SLACK) com p medido do modelo montado
    assert by["01.Horizontal"].pull == pytest.approx(5 + SLACK, abs=0.02)


def test_auto_length_control(built_case, legacy_inputs):
    """'auto': End B na embarcação -> última seção da linha; senão, cabo do guincho."""
    from cvdtools.schema import OperationSpec

    case = case_from_legacy_json(legacy_inputs, operation=OperationSpec("1st end CVD", "no_camelback",
                                                                        camelback=False, supported=True))
    assert case.get("statics.length_control") == "auto"
    PhysicsModel.built = None
    eng = StaticsEngine(case, built_case[1])
    assert eng.control == ("Winch Wire", "Length", 0)          # End B 'Anchored' -> guincho

    m = FakeModel(str(built_case[1]))
    m.objects["Line"]["EndBConnection"] = case.role("VESSEL")
    m.SaveData(str(built_case[1]))
    eng = StaticsEngine(case, built_case[1])
    n = len(m.objects["Line"]["Length"])
    assert eng.control == ("Line", "Length", n - 1)
    assert eng.L_built == pytest.approx(m.objects["Line"]["Length"][-1])


class HangingMasterlink(PhysicsModel):
    """Lingas sempre com o peso do masterlink (5,3 kN) quando o cabo está frouxo (GLF27, 18/11/2026)."""

    def CalculateStatics(self):
        super().CalculateStatics()
        for k in (("Link CB 1", "Tension", "None"), ("Link CB 2", "Tension", "None")):
            self.results[k] = [self.results[k][0] + 5.3]


def test_reference_with_masterlink_weight_on_slings(built_case, fake_orc):
    fake_orc.Model = HangingMasterlink
    HangingMasterlink.built = None
    eng = StaticsEngine(built_case[0], built_case[1])
    L = eng.find_reference()
    assert eng.reference_base == pytest.approx(5.3)
    # base + 0,5 kN com 50 kN/m de puxada -> 0,01 m depois de SLACK
    assert eng.L_built - L == pytest.approx(SLACK + 0.01, abs=0.011)


def test_state_switch_restarts_from_shape_of_same_state(built_case):
    """05 (MCV fixo) depois de 04 (livre) partia da forma livre e divergia (GLF27, 18/11/2026)."""
    from cvdtools.statics.engine import FIXED, FREE

    PhysicsModel.built = None
    eng = StaticsEngine(built_case[0], built_case[1])
    eng.L_ref = eng.L_built
    eng.evaluate("a", FIXED, eng.L_built - 1)
    first = eng.model
    eng.evaluate("b", FREE, eng.L_built - 3)
    assert eng.model is not first and str(eng.model.path) == str(built_case[1])   # sem forma livre: modelo montado
    eng.evaluate("c", FIXED, eng.L_built - 2)
    assert str(eng.model.path).endswith("forma_fixed.dat")                         # última forma fixa
    assert eng.model.objects["MCV"]["Connection"] == "Anchored"
    eng.evaluate("d", FREE, eng.L_built - 4)
    assert str(eng.model.path).endswith("forma_free.dat")


def test_each_statics_starts_with_fixed_masterlink(built_case, fake_orc):
    """0.10.5: com corcova, masterlink fixo -> posições gravadas -> masterlink com a conexão original."""
    seen = []

    class Recorder(PhysicsModel):
        def CalculateStatics(self):
            seen.append(("statics", self.objects["Masterlink"]["Connection"]))
            super().CalculateStatics()

        def UseCalculatedPositions(self, setLinesToUserSpecifiedStartingShape=False):
            seen.append(("positions", self.objects["Masterlink"]["Connection"]))
            super().UseCalculatedPositions(setLinesToUserSpecifiedStartingShape)

    case, path = built_case
    fake_orc.Model = Recorder
    original = FakeModel(str(path)).objects["Masterlink"]["Connection"]
    assert original != "Fixed"
    eng = StaticsEngine(case, path)
    ev = eng.evaluate("teste", "fixed", eng.L_built)
    assert ev.converged
    assert seen[:3] == [("statics", "Fixed"), ("positions", "Fixed"), ("statics", original)]
    assert eng.model["Masterlink"].Connection == original

    seen.clear()
    case.values["statics.masterlink_fixed_first"] = ResolvedValue(0.0, "override")
    eng = StaticsEngine(case, path)
    eng.evaluate("teste", "fixed", eng.L_built)
    assert seen[0] == ("statics", original) and ("statics", "Fixed") not in seen
