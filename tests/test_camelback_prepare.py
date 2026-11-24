"""Preparo da corcova, estática final em duas etapas e diagnóstico (v0.6.0)."""

import math

import pytest

from cvdtools._orcaflex import StaticsFailed, calculate_statics
from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.model.builder import build_model
from cvdtools.model.camelback import (hanging_lengths, masterlink_position, solve_top_section, strain_function,
                                      submerged_weight_per_m)
from fake_orcfxapi import FakeModel, write_base_model


# ------------------------------------------------------------------ geometria pura

def test_masterlink_symmetric():
    pos, warn = masterlink_position((0, 0, 0), (4, 0, 0), 2.5)
    assert warn == ""
    assert pos == pytest.approx((2, 0, 1.5))


def test_masterlink_inclined_points_keep_sling_length():
    p1, p2 = (10.0, 0.0, -100.0), (14.0, 0.5, -99.0)
    pos, _ = masterlink_position(p1, p2, 7.5)
    assert math.dist(pos, p1) == pytest.approx(7.5)
    assert math.dist(pos, p2) == pytest.approx(7.5)
    assert pos[2] > max(p1[2], p2[2])


def test_masterlink_points_too_far():
    pos, warn = masterlink_position((0, 0, 0), (20, 0, 0), 7.5)
    assert "ponto médio" in warn and pos[0] == pytest.approx(10)


def test_hanging_constant_ea_matches_closed_form():
    L, w, ea, W0 = 1000.0, 0.3, 1.0e5, 5.0
    got = hanging_lengths([(L, w, strain_function(ea=ea))], W0, ds=1.0)[0]
    assert got == pytest.approx(L + (W0 * L + w * L * L / 2) / ea, rel=1e-6)


def test_solve_top_section_hits_target():
    ext = (400.0, 0.1, strain_function(table=([0, 0.001, 0.01], [0, 20, 500])))
    w_top, f_top = 0.35, strain_function(ea=172520.0)
    target = 1470.0
    L = solve_top_section(target, [ext], w_top, f_top, 5.0)
    total = sum(hanging_lengths([ext, (L, w_top, f_top)], 5.0))
    assert total == pytest.approx(target, abs=1e-3)
    assert L < target - 400


def test_strain_table_extrapolates():
    f = strain_function(table=([0, 0.01, 0.02], [0, 100, 300]))
    assert f(50) == pytest.approx(0.005)
    assert f(500) == pytest.approx(0.03)


def test_submerged_weight():
    assert submerged_weight_per_m(0.13149, 0.27784) == pytest.approx((0.13149 - 1.025 * math.pi / 4 * 0.27784 ** 2)
                                                                     * 9.80665)


# ------------------------------------------------------------------ modelo falso com "física"

class CamelPhysics(FakeModel):
    """Linha reta no fundo; a estática completa só converge com lingas perto do comprimento."""

    lda = None

    def CalculateStatics(self):
        o = self.objects
        lda = o["Environment"]["WaterDepth"]
        for s in (17.0, 22.0, 13.0, 15.0, 1.0):
            self.results[("Line", "X", f"arc={s:g}")] = [s + 1.0]
            self.results[("Line", "Y", f"arc={s:g}")] = [0.0]
            self.results[("Line", "Z", f"arc={s:g}")] = [-lda + 0.14]
        w = o.get("Winch Wire")
        if w is not None:   # End A do cabo do guincho em coordenadas globais (fixo no navio)
            for v in ("X", "Y", "Z"):
                self.results[("Winch Wire", v, "EndA")] = [w.get(f"EndA{v}", 0.0)]
        if "Masterlink" not in o or "Link CB 1" not in o:
            return
        ml = o["Masterlink"]
        if ml.get("Connection") == "Fixed" and CamelPhysics.fail_fixed:
            raise RuntimeError("Whole system statics: Not converged.")
        if ml.get("Connection") != "Fixed" and CamelPhysics.fail_free:
            raise RuntimeError("Whole system statics: Not converged.")
        pos = (ml["InitialX"], ml["InitialY"], ml["InitialZ"])
        for k in ("Link CB 1", "Link CB 2"):   # tirante: frouxo é aceito; esticado demais não converge
            s = o[k]["EndAZ"]
            d = math.dist(pos, (s + 1.0, 0.0, -lda + 0.14))
            if d - o[k]["UnstretchedLength"] > 0.5:
                raise RuntimeError("Whole system statics: Not converged.")

    def UseCalculatedPositions(self, setLinesToUserSpecifiedStartingShape=False):
        self.objects["Line"]["UsesCalculatedPositions"] = self.objects.get("Masterlink", {}).get("Connection")


CamelPhysics.fail_fixed = False
CamelPhysics.fail_free = False


@pytest.fixture
def built(tmp_path, fake_orc, legacy_inputs):
    fake_orc.Model = CamelPhysics
    case = case_from_legacy_json(legacy_inputs)
    path = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path
    return case, path


def test_prepare_low_masterlink_by_default(built):
    """0.10.0: masterlink 4,5 m acima do fundo, X na média dos pontos das lingas + 1 m; lingas frouxas."""
    from cvdtools.model.camelback import prepare_camelback

    case, path = built
    res = prepare_camelback(case, path)
    assert res["status"] == "OK" and res["masterlink_mode"] == "baixo" and res["sling_points_from_statics"]
    o = FakeModel(str(path)).objects
    lda = o["Environment"]["WaterDepth"]
    x1, x2 = (o[k]["EndAZ"] + 1.0 for k in ("Link CB 1", "Link CB 2"))
    ml = o["Masterlink"]
    assert (ml["InitialX"], ml["InitialY"], ml["InitialZ"]) == pytest.approx(((x1 + x2) / 2 + 1.0, 0.0, -lda + 2.0))
    assert o["Winch Wire"]["Length"][0] == pytest.approx(res["winch_main_length"])
    assert sum(res["winch_stretched"]) == pytest.approx(res["winch_distance"] + res["slack"], abs=1e-3)


def test_prepare_low_masterlink_without_converged_line(built):
    """Sem a estática da linha, os pontos das lingas vêm da catenária geométrica."""
    from cvdtools.model.camelback import estimate_line_points, prepare_camelback

    case, path = built

    class Fails(CamelPhysics):
        def CalculateStatics(self):
            raise RuntimeError("Whole system statics: Not converged.")

    import cvdtools._orcaflex as ofx
    orc = ofx.require_orcfxapi()
    old = orc.Model
    orc.Model = Fails
    try:
        res = prepare_camelback(case, path)
    finally:
        orc.Model = old
    assert res["status"] == "PARCIAL" and "não convergiu" in res["message"]
    assert not res["sling_points_from_statics"]
    est = estimate_line_points(case, FakeModel(str(path)), res["sling_arcs"])
    o = FakeModel(str(path)).objects
    assert o["Masterlink"]["InitialX"] == pytest.approx((est[0][0] + est[1][0]) / 2 + 1.0)
    assert o["Masterlink"]["InitialZ"] == pytest.approx(-o["Environment"]["WaterDepth"] + 2.0)


def test_estimate_line_points_follow_the_catenary(built):
    from cvdtools.model.builder import catenary_geometric
    from cvdtools.model.camelback import estimate_line_points

    case, path = built
    m = FakeModel(str(path))
    od = case.num("line.od")
    cat = catenary_geometric(0.0, case.num("mcv.a") - od / 2, 90.0 - case.num("mcv.angle"), 400.0)
    (x0, _, z0), (x1, _, z1) = estimate_line_points(case, m, [0.0, cat["suspended_length"] + 10.0])
    bottom = -m.objects["Environment"]["WaterDepth"] + od / 2
    assert (x0, z0) == pytest.approx((case.num("mcv.g"), bottom + case.num("mcv.a") - od / 2))
    assert (x1, z1) == pytest.approx((case.num("mcv.g") + cat["x_span"] + 10.0, bottom))


def test_prepare_places_masterlink_and_sets_winch(built):
    from cvdtools.model.camelback import prepare_camelback
    from cvdtools.schema import ResolvedValue

    case, path = built
    case.values["model.masterlink_position"] = ResolvedValue("apice", "override")
    res = prepare_camelback(case, path)
    assert res["status"] == "OK"
    o = FakeModel(str(path)).objects
    lda = o["Environment"]["WaterDepth"]
    pos = (o["Masterlink"]["InitialX"], o["Masterlink"]["InitialY"], o["Masterlink"]["InitialZ"])
    for k in ("Link CB 1", "Link CB 2"):
        p = (o[k]["EndAZ"] + 1.0, 0.0, -lda + 0.14)
        assert math.dist(pos, p) == pytest.approx(o[k]["UnstretchedLength"], abs=1e-6)
    assert res["winch_main_length"] != pytest.approx(res["winch_main_length_before"])
    assert o["Winch Wire"]["Length"][0] == pytest.approx(res["winch_main_length"])
    # cabo esticado = distância + folga
    assert sum(res["winch_stretched"]) == pytest.approx(res["winch_distance"] + res["slack"], abs=1e-3)


def test_finalize_two_stages_ok(built):
    from cvdtools.model.camelback import prepare_camelback
    from cvdtools.model.endb import finalize_statics

    case, path = built
    CamelPhysics.fail_fixed = CamelPhysics.fail_free = False
    # sem preparo, a estática completa falha (masterlink longe das lingas)
    assert finalize_statics(case, path)["status"] == "FALHOU"
    prepare_camelback(case, path)
    res = finalize_statics(case, path)
    assert res["status"] == "OK", res
    assert [s["stage"] for s in res["stages"]] == ["masterlink fixo", "modelo completo"]
    o = FakeModel(str(path)).objects
    assert o["Masterlink"]["Connection"] == "Free"
    assert o["Line"]["UsesCalculatedPositions"] == "Free"


def test_finalize_partial_keeps_stage_a(built):
    from cvdtools.model.camelback import prepare_camelback
    from cvdtools.model.endb import finalize_statics

    case, path = built
    prepare_camelback(case, path)
    CamelPhysics.fail_free = True
    try:
        res = finalize_statics(case, path)
    finally:
        CamelPhysics.fail_free = False
    assert res["status"] == "PARCIAL"
    o = FakeModel(str(path)).objects
    assert o["Masterlink"]["Connection"] == "Free"            # conexão original restaurada
    assert o["Line"]["UsesCalculatedPositions"] == "Fixed"     # posições da etapa A


def test_diagnose_points_to_camelback(built, tmp_path):
    from cvdtools.model.diagnose import diagnose

    case, path = built
    res = diagnose(case, path, tmp_path / "diag", only=["como_gerado", "sem_corcova", "masterlink_fixo"])
    st = {r.name: r.status for r in res["results"]}
    assert st["como_gerado"] == "FALHOU" and st["sem_corcova"] == "OK"
    assert any("Lingas fora do comprimento" in v for v in res["verdict"])
    assert res["csv"].exists()
    assert res["precheck"]["links"][0]["stretch"] > 0.25


def test_statics_failed_carries_last_progress():
    class M:
        staticsProgressHandler = None

        def CalculateStatics(self):
            self.staticsProgressHandler(self, "Whole system statics: 9999 (latest error = 98e3 at Line, node 12, X)")
            raise RuntimeError("Static calculation failed (Whole system statics: Not converged.)")

    with pytest.raises(StaticsFailed) as ei:
        calculate_statics(M(), "x")
    assert "node 12" in str(ei.value) and "Not converged" in ei.value.orcaflex_message


def test_prepare_with_winch_on_moonpool(built, monkeypatch):
    """End A do cabo do guincho num objeto ('Moonpool'): posição global vem da estática (GLF27, 18/11)."""
    from cvdtools.model.camelback import prepare_camelback

    case, path = built
    m = FakeModel(str(path))
    m.objects["Winch Wire"].update(EndAConnection="Moonpool", EndAX=0.0, EndAY=0.0, EndAZ=-5.0)  # relativo ao pai
    m.SaveData(str(path))

    class Moon(CamelPhysics):
        def CalculateStatics(self):
            super().CalculateStatics()
            for v, val in (("X", 17.0), ("Y", 0.0), ("Z", 20.0)):   # posição global do ponto no moonpool
                self.results[("Winch Wire", v, "EndA")] = [val]

    from cvdtools import _orcaflex
    orc = _orcaflex.require_orcfxapi()
    monkeypatch.setattr(orc, "Model", Moon)
    res = prepare_camelback(case, path)
    assert res["status"] == "OK"
    assert res["winch_top"] == pytest.approx((17.0, 0.0, 20.0))


def test_finalize_holds_high_damping_until_last_positions(built):
    """Escada de damping em cada etapa (fixo -> livre): 1e3/1e4, posições, ÷10 até o piso; no fim, os do modelo."""
    from cvdtools.model.camelback import prepare_camelback
    from cvdtools.model.endb import finalize_statics

    case, path = built
    CamelPhysics.fail_fixed = CamelPhysics.fail_free = False
    prepare_camelback(case, path)
    seen = []

    class Recorder(CamelPhysics):
        def CalculateStatics(self):
            g = self.objects["General"]
            seen.append((self.objects["Masterlink"]["Connection"], g["StaticsMinDamping"], g["StaticsMaxDamping"]))
            super().CalculateStatics()

        def UseCalculatedPositions(self, setLinesToUserSpecifiedStartingShape=False):
            seen.append(("positions", setLinesToUserSpecifiedStartingShape, self.objects["General"]["StaticsMinDamping"]))

    import cvdtools._orcaflex as ofx
    orc = ofx.require_orcfxapi()
    old = orc.Model
    orc.Model = Recorder
    try:
        res = finalize_statics(case, path)
    finally:
        orc.Model = old
    assert res["status"] == "OK", res
    # 0.10.4: depois de convergir, o damping desce ÷10 até o piso (10/100), partindo das posições convergidas
    assert seen == [("Fixed", 1000.0, 10000.0), ("positions", True, 1000.0), ("Fixed", 100.0, 1000.0),
                    ("positions", True, 100.0), ("Fixed", 10.0, 100.0), ("positions", True, 10.0),
                    ("Free", 1000.0, 10000.0), ("positions", True, 1000.0), ("Free", 100.0, 1000.0),
                    ("positions", True, 100.0), ("Free", 10.0, 100.0), ("positions", True, 10.0)]
    g = FakeModel(str(path)).objects["General"]
    assert (g["StaticsMinDamping"], g["StaticsMaxDamping"], g["StaticsMaxIterations"]) == (200, 1000, 10000)
    assert g["JacobianPerturbationFactor"] == case.num("model.jacobian_perturbation_factor")
