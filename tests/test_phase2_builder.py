"""Fase 2: builder e catenária (com OrcFxAPI falso)."""

import json
import math

import numpy as np
import pytest

from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.model.builder import build_model, catenary_estimate
from cvdtools.schema import MissingInputError, OperationSpec
from fake_orcfxapi import FakeModel, FakeObject, write_base_model


def legacy_solve_catenary(mcv, line_type, horizontal_t, line_total_length):
    """Cópia literal da conta do _01.GenerateModel.py (solveCatenary)."""
    xTop = mcv["G"]
    angleTop = mcv["Angle"]
    tTop = -np.tan(angleTop * np.pi / 180)
    tBottom = -np.tan(0 * np.pi / 180)
    subWeight = line_type["MassPerUnitLength"] - np.pi * line_type["OD"] * 2 / 4 * 1.025
    EA = line_type["EA"]
    l = np.abs((tTop - tBottom) / horizontal_t / subWeight)
    xBottom = xTop + l * (1 / (tBottom - tTop) * np.log((np.abs(tTop) + np.sqrt(1 + tTop ** 2)) /
                                                       (np.abs(tBottom) + np.sqrt(1 + tBottom ** 2))) + horizontal_t / EA)
    return xBottom + (line_total_length - l)


@pytest.fixture
def built(tmp_path, fake_orc, legacy_inputs):
    base = write_base_model(tmp_path / "base.dat")
    case = case_from_legacy_json(legacy_inputs)
    res = build_model(case, base, tmp_path / "caso.dat", catenary="legacy")
    return FakeModel(str(res.model_path)).objects, res, case


def test_builder_properties(built, legacy_inputs):
    o, res, case = built
    mcv, lt, br, ef = (legacy_inputs[k] for k in ("MCVDims", "LineTypeDims", "BendRestrictorDims", "EndFittingDims"))
    lda_change = 1448 - 1000
    assert o["Environment"]["WaterDepth"] == 1448
    assert o["Environment"]["SeabedNormalStiffness"] == 100
    assert o["Hub Height"]["Length"] == pytest.approx(mcv["A"] - mcv["F"])
    assert o["MCV"]["Mass"] == mcv["MassInAir"] and o["MCV"]["Volume"] == mcv["Volume"]
    assert o["MCV"]["VertexX"][-2:] == pytest.approx([mcv["G"] - mcv["C"], mcv["G"]])
    assert o["MCV"]["VertexZ"][-3:] == pytest.approx([mcv["F"] + mcv["B"], mcv["F"] + mcv["B"], mcv["F"]])
    assert o["Crane Wire"]["EndBZ"] == pytest.approx(mcv["F"] + mcv["B"])
    assert o["Line"]["EndAConnection"] == "MCV"
    assert o["Line"]["EndADeclination"] == 90 + mcv["Angle"]
    assert o["Line Type"]["OD"] == lt["OD"] and o["Line Type"]["EIx"] == lt["EIx"]
    assert o["Line Type"]["SeabedLateralFrictionCoefficient"] == 0.9
    assert o["Bend Restrictor Type"]["EIx"] == "Bend Restrictor Stiffness Curve"
    assert o["Bend Restrictor Type"]["xMinRadius"] == br["xMinRadius"]
    assert o["Bend Restrictor Stiffness Curve"]["IndependentValue"] == pytest.approx([0, 1 / 2.9, 1 + 1 / 2.9])
    inertia_br = math.pi / 4 * ((br["OD"] / 2) ** 4 - (br["ID"] / 2) ** 4)
    assert o["Bend Restrictor Stiffness Curve"]["DependentValue"][2] == pytest.approx(0.01 + 2.07e8 * inertia_br)
    assert o["EndFitting Type"]["EIx"] == pytest.approx(2.07e8 * math.pi * (ef["OD"] ** 4 - ef["ID"] ** 4) / 64)
    assert o["Link CB 1"]["EndAZ"] == 17.0 and o["Link CB 2"]["EndAZ"] == 22.0
    compl = math.ceil(ef["Length"] + br["Length"]) - (ef["Length"] + br["Length"])
    assert o["Line"]["Length"] == pytest.approx([ef["Length"], br["Length"], compl, 50.0, 300.0])
    assert o["Line"]["NumberOfSegments"] == [1, 20, 1, 100, 50]
    assert o["Line"]["Attachmentz"][0] == ef["Length"]
    assert o["Masterlink"]["InitialZ"] == pytest.approx(-10 - lda_change)
    assert o["Crane Wire"]["Length"][0] == pytest.approx(100 + lda_change)
    assert o["Winch Wire"]["Length"] == pytest.approx([832 + lda_change, 400])
    assert o["Winch Extension Wire Type"]["OD"] == 0.25, "premissa indefinida: extensão não é configurada"
    assert any("winch_extension não definida" in n for n in res.notes)


def test_legacy_catenary_matches_old_script(built, legacy_inputs):
    o, res, _ = built
    expected = legacy_solve_catenary(legacy_inputs["MCVDims"], legacy_inputs["LineTypeDims"], 0.5,
                                     sum(o["Line"]["Length"]))
    assert o["Line"]["EndBX"] == pytest.approx(expected, rel=1e-12)


def test_fixed_catenary_uses_real_submerged_weight(legacy_inputs):
    lt, mcv = legacy_inputs["LineTypeDims"], legacy_inputs["MCVDims"]
    fixed = catenary_estimate(mcv["G"], mcv["Angle"], lt["MassPerUnitLength"], lt["OD"], lt["EA"], 0.5, "fixed")
    legacy = catenary_estimate(mcv["G"], mcv["Angle"], lt["MassPerUnitLength"], lt["OD"], lt["EA"], 0.5, "legacy")
    expected_w = (lt["MassPerUnitLength"] - math.pi * lt["OD"] ** 2 / 4 * 1.025) * 9.80665
    assert fixed["sub_weight"] == pytest.approx(expected_w)
    a = 0.5 / expected_w
    tan = math.tan(math.radians(mcv["Angle"]))
    assert fixed["suspended_length"] == pytest.approx(a * tan)
    assert fixed["x_bottom"] == pytest.approx(mcv["G"] + a * math.asinh(tan) + a * tan * 0.5 / lt["EA"])
    assert legacy["sub_weight"] < 0, "o script antigo dá peso submerso negativo para esta linha"
    assert fixed["suspended_length"] != pytest.approx(legacy["suspended_length"])


def test_fixed_catenary_rejects_floating_line():
    with pytest.raises(ValueError, match="negativo"):
        catenary_estimate(1.0, 60, mass=0.01, od=0.5, ea=1e5, horizontal_tension=0.5, mode="fixed")


def test_builder_reports_all_missing_inputs(tmp_path, fake_orc, legacy_inputs):
    del legacy_inputs["CamelBackDims"]["Leg1KP"]
    legacy_inputs["LineTypeDims"]["OD"] = "N/I"
    case = case_from_legacy_json(legacy_inputs)
    with pytest.raises(MissingInputError) as exc:
        build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")
    assert "camelback.leg1_kp" in str(exc.value) and "line.od" in str(exc.value)
    assert not (tmp_path / "c.dat").exists()


def test_builder_refuses_unsupported_operation(tmp_path, fake_orc, legacy_inputs):
    case = case_from_legacy_json(legacy_inputs, operation=OperationSpec("Handshake"))
    with pytest.raises(NotImplementedError):
        build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")


def test_builder_uses_roles(tmp_path, fake_orc, legacy_inputs):
    base = write_base_model(tmp_path / "b.dat")
    data = json.loads(base.read_text())
    data["objects"]["MCV MOBO"] = data["objects"].pop("MCV")
    base.write_text(json.dumps(data))
    case = case_from_legacy_json(legacy_inputs)
    case.roles["MCV"] = "MCV MOBO"
    res = build_model(case, base, tmp_path / "c.dat")
    o = FakeModel(str(res.model_path)).objects
    assert o["MCV MOBO"]["Mass"] == legacy_inputs["MCVDims"]["MassInAir"]
    assert o["Line"]["EndAConnection"] == "MCV MOBO"


def test_floaters(tmp_path, fake_orc, legacy_inputs):
    legacy_inputs["FloaterDims"] = {"FloaterBuoyancy": [1.0, 2.0], "FloaterKPs": [30.0, 40.0],
                                    "FloaterSlingLength": [5.0, 6.0], "FloaterSlingStiffness": [100.0, 200.0]}
    res = build_model(case_from_legacy_json(legacy_inputs), write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")
    o = FakeModel(str(res.model_path)).objects
    assert o["Floater 2"]["Volume"] == pytest.approx(2.0 / 1.025)
    assert o["Floater 2"]["InitialZ"] == pytest.approx(-1448 + 6.0)
    assert o["Floater Link 1"]["EndAConnection"] == "Line" and o["Floater Link 1"]["EndAZ"] == 30.0
    assert "2 flutuador(es) criado(s)" in res.notes


# --------------------------------------------------------------------------- premissas

def _case(legacy_inputs, **values):
    from cvdtools.schema import ResolvedValue
    case = case_from_legacy_json(legacy_inputs)
    for k, v in values.items():
        case.values[k.replace("__", ".")] = ResolvedValue(v, "override")
    return case


EXT = {"winch_ext__length": 400.0, "winch_ext__od": 0.084, "winch_ext__mass": 0.00288, "winch_ext__ea": 5000.0}


def test_winch_extension_configured(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs, model__winch_extension=1.0, **EXT)
    res = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")
    o = FakeModel(str(res.model_path)).objects
    assert o["Winch Wire"]["Length"] == pytest.approx([832 + 448, 400])
    ext = o["Winch Extension Wire Type"]
    assert (ext["OD"], ext["MassPerUnitLength"], ext["EA"]) == (0.084, 0.00288, 5000.0)
    assert ext["EIx"] == 120.0, "EIx vazio mantém o do modelo base"


def test_winch_extension_length_change_keeps_total(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs, model__winch_extension=1.0, **{**EXT, "winch_ext__length": 300.0})
    o = FakeModel(str(build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path)).objects
    assert o["Winch Wire"]["Length"] == pytest.approx([932 + 448, 300])


def test_winch_extension_requires_inputs(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs, model__winch_extension=1.0)
    with pytest.raises(MissingInputError, match="winch_ext.od"):
        build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")


def test_no_winch_extension_merges_sections(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs, model__winch_extension=0.0)
    res = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")
    o = FakeModel(str(res.model_path)).objects
    assert o["Winch Wire"]["Length"] == pytest.approx([1232 + 448])
    assert any("removida" in n for n in res.notes)


def test_winch_extension_created_when_base_has_one_section(tmp_path, fake_orc, legacy_inputs):
    base = write_base_model(tmp_path / "b.dat")
    data = json.loads(base.read_text())
    data["objects"]["Winch Wire"].update(Length=[1232.0], LineType=["Winch Wire Type"], NumberOfSegments=[50])
    base.write_text(json.dumps(data))
    case = _case(legacy_inputs, model__winch_extension=1.0, **EXT)
    o = FakeModel(str(build_model(case, base, tmp_path / "c.dat").model_path)).objects
    assert o["Winch Wire"]["LineType"] == ["Winch Wire Type", "Winch Extension Wire Type"]
    assert o["Winch Wire"]["Length"] == pytest.approx([832 + 448, 400])


TWO_ENDS = {"model__modelled_ends": 2.0, "line__total_length": 159.0, "mcv_b__angle": 60.0, "mcv_b__a": 3.2274,
            "mcv_b__b": 0.9919, "mcv_b__c": 1.7168, "mcv_b__f": 1.4004, "mcv_b__g": 2.1414, "mcv_b__mass_air": 7.926,
            "mcv_b__volume": 1.0052, "mcv_b__base_size": 0.6}


def test_two_ends_needs_fixed_mode(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs, **TWO_ENDS)
    with pytest.raises(ValueError, match="modo 'fixed'"):
        build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat", catenary="legacy")


def test_two_ends_model(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.model.twoends import estimate_distance

    case = _case(legacy_inputs, **TWO_ENDS)
    base = write_base_model(tmp_path / "b.dat")
    m = FakeModel(str(base))
    m.objects["Hub Height"]["OriginX"] = 0.0
    m.objects["MCV"].update(InitialX=0.0, Connection="Anchored")
    m.objects["Line"]["EndAxBendingStiffness"] = 1e308
    m.SaveData(str(base))
    res = build_model(case, base, tmp_path / "c.dat")
    o = FakeModel(str(res.model_path)).objects
    line = o["Line"]
    ef, br = legacy_inputs["EndFittingDims"]["Length"], legacy_inputs["BendRestrictorDims"]["Length"]
    assert sum(line["Length"]) == pytest.approx(159.0)
    assert line["Length"][:2] == pytest.approx([ef, br]) and line["Length"][-2:] == pytest.approx([br, ef])
    assert line["NumberOfSegments"][:2] == [1, 20] and line["NumberOfSegments"][-2:] == [20, 1]
    # trechos dos BRs com o tipo da linha flexível; o BR é o stiffener de cada ponta
    assert line["LineType"][:3] == ["EndFitting Type", "Line Type", "Line Type"]
    assert line["LineType"][-2:] == ["Line Type", "EndFitting Type"]
    assert line["AttachmentType"] == ["Bend Restrictor", "Bend Restrictor B"]
    assert line["Attachmentz"] == pytest.approx([ef, ef]) and line["AttachmentzRelativeTo"] == ["End A", "End B"]
    assert line["AttachmentName"] == ["Bend Restrictor Element", "Bend Restrictor Element B"]
    st_b = o["Bend Restrictor B"]
    assert st_b["Length"] == pytest.approx(br) and st_b["LineType"] == "Bend Restrictor Type"
    assert (st_b["ConnectionArclength"], st_b["ConnectionArclengthRelativeTo"]) == (0.0, "End B")
    assert o["Bend Restrictor"]["ConnectionArclengthRelativeTo"] == "End A"
    assert line["StaticsStep1"] == "Quick" and line["ContentsDensity"] == 0.0
    middle = line["Length"][3:-2]
    assert all(lt == "Line Type" for lt in line["LineType"][3:-2])
    # trecho fino (0,5 m) junto aos BRs, central mais grosso (2 m)
    segs = [L / n for L, n in zip(middle, line["NumberOfSegments"][3:-2])]
    assert segs[0] <= 0.5 + 1e-9 and segs[-1] <= 0.5 + 1e-9 and max(segs) <= 2.0 + 1e-9
    # MCV B: cópia do MCV, espelhado, num constraint só com X livre, na distância estimada
    b = o["MCV B"]
    assert b["Connection"] == "Constraint B" and b["Mass"] == 7.926
    assert b["VertexX"][-2:] == pytest.approx([-(2.1414 - 1.7168), -2.1414])
    assert (b["InitialX"], b["InitialY"], b["InitialZ"]) == (0.0, 0.0, 0.0)
    c = o["Constraint B"]
    assert c["DOFFree"] == ["Yes", "No", "No", "No", "No", "No"]
    assert c["InFrameConnection"] == "Anchored" and c["InitialZ"] == pytest.approx(3.2274 - 1.4004)
    d = estimate_distance(case)["distance"]
    assert c["InitialX"] == pytest.approx(d) and res.catenary["end_distance"] == pytest.approx(d)
    pull = o["MCV B Pull"]   # tirante: deformação 1 na posição inicial -> tração = Stiffness = alvo
    assert (pull["EndAConnection"], pull["EndBConnection"]) == ("MCV B", "Anchored")
    assert pull["EndBX"] == pytest.approx(d + 200.0) and pull["EndBZ"] == pytest.approx(3.2274 - 1.4004)
    assert pull["UnstretchedLength"] == pytest.approx(100.0) and pull["Stiffness"] == pytest.approx(0.5)
    assert 140.0 < d < 175.0
    assert o["Hub Height B"]["OriginX"] == pytest.approx(d) and o["Hub Height B"]["Length"] == pytest.approx(3.2274 - 1.4004)
    # End B no gooseneck do MCV B, chegando subindo (convenção End A -> End B)
    assert line["EndBConnection"] == "MCV B"
    assert (line["EndBX"], line["EndBZ"]) == pytest.approx((-2.1414, 1.4004))
    assert line["EndBDeclination"] == 60.0 and line["EndBAzimuth"] == 0.0
    assert line["EndBxBendingStiffness"] == 1e308
    assert any("MCV B" in n and "cópia" in n for n in res.notes)
    # MCV B pelas cotas dele (sem D e E: valores alternativos da aba MCVs), espelhado em x
    assert b["Height"] == pytest.approx(0.9919 + 1.4004)
    assert (b["CentreOfMassX"], b["CentreOfMassZ"]) == pytest.approx((-0.3, 1.4004 - 0.3))
    assert (b["CentreOfVolumeX"], b["CentreOfVolumeZ"]) == pytest.approx((-0.3, 1.4004 - 0.3))
    lx, ly, lz = 2.1414 + 0.3, 0.6, 0.9919 + 1.4004
    assert (b["MomentOfInertiaX"], b["MomentOfInertiaY"], b["MomentOfInertiaZ"]) == pytest.approx(
        (7.926 * (ly ** 2 + lz ** 2) / 12, 7.926 * (lx ** 2 + lz ** 2) / 12, 7.926 * (lx ** 2 + ly ** 2) / 12))
    assert any("MCV B" in n and "cota D sem input" in n for n in res.notes)
    assert o["Hub Height B"]["ShapeType"] == "Drawing" and o["Hub Height"]["ShapeType"] == "Drawing"


def test_two_ends_mcv_b_with_d_and_e(tmp_path, fake_orc, legacy_inputs):
    """GLF27 1CVD PLET: CoG = (−(G − E); 0; F − D) com as cotas da Input Sheet."""
    case = _case(legacy_inputs, **TWO_ENDS, mcv_b__d=0.4553, mcv_b__e=1.9756)
    o = FakeModel(str(build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path)).objects
    b = o["MCV B"]
    assert (b["CentreOfMassX"], b["CentreOfMassY"], b["CentreOfMassZ"]) == pytest.approx(
        (-(2.1414 - 1.9756), 0.0, 1.4004 - 0.4553))


def test_two_ends_anchored_mode(tmp_path, fake_orc, legacy_inputs):
    """model.end_b_x_free = nao: MCV B ancorado, como até a 0.9.1."""
    from cvdtools.model.twoends import estimate_distance

    case = _case(legacy_inputs, **TWO_ENDS, model__end_b_x_free="nao")
    o = FakeModel(str(build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path)).objects
    b = o["MCV B"]
    assert b["Connection"] == "Anchored" and b["InitialZ"] == pytest.approx(3.2274 - 1.4004)
    assert b["InitialX"] == pytest.approx(estimate_distance(case)["distance"])
    assert "Constraint B" not in o and "MCV B Pull" not in o


def test_two_ends_x_free_mode_values(legacy_inputs):
    from cvdtools.model.twoends import x_free_mode

    assert x_free_mode(_case(legacy_inputs, **TWO_ENDS)) == "montagem"
    assert x_free_mode(_case(legacy_inputs, **TWO_ENDS, model__end_b_x_free="Não")) == "nao"
    assert x_free_mode(_case(legacy_inputs, **TWO_ENDS, model__end_b_x_free="sempre")) == "sempre"
    assert x_free_mode(_case(legacy_inputs, **TWO_ENDS, model__end_distance=150.0)) == "nao"
    with pytest.raises(MissingInputError, match="montagem, sempre ou nao"):
        x_free_mode(_case(legacy_inputs, **TWO_ENDS, model__end_b_x_free="talvez"))


def test_two_ends_given_distance(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs, **TWO_ENDS, model__end_distance=150.0)
    res = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")
    assert FakeModel(str(res.model_path)).objects["MCV B"]["InitialX"] == pytest.approx(150.0)
    assert res.catenary["end_distance_source"] == "model.end_distance"


def test_two_ends_too_short(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs, **{**TWO_ENDS, "line__total_length": 10.0})
    with pytest.raises(MissingInputError, match="curta demais"):
        build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")


class SpanPhysics(FakeModel):
    """Menor tração entre os BRs = 15 kN/m x (d - 160) (mínimo -2 kN, linha frouxa); a corcova somaria 50 kN.

    Com o MCV B num constraint com X livre, o equilíbrio é onde essa tração iguala a do tirante.
    """

    statics_runs = 0

    def CalculateStatics(self):
        type(self).statics_runs += 1
        o = self.objects
        x_a = o["MCV"].get("InitialX", 0.0)
        camelback = "Link CB 1" in o or "Winch Wire" in o
        extra = 50.0 if camelback else 0.0
        on_constraint = o["MCV B"].get("Connection") == "Constraint B"
        if on_constraint and o["Constraint B"]["DOFFree"][0] == "Yes":
            pull = o.get("MCV B Pull", {}).get("Stiffness", 0.0)
            d = 160.0 + (pull - extra) / 15.0
        else:
            d = (o["Constraint B"] if on_constraint else o["MCV B"])["InitialX"] - x_a
        t = max(-2.0, 15.0 * (d - 160.0)) + extra
        arcs = FakeObject(self, "Line").NodeArclengths
        self.results = {("Line", "Effective tension", f"arc={s:g}"): [t + 0.01 * abs(s - 80.0)] for s in arcs}
        self.results[("MCV B", "X", "None")] = [x_a + d]


def test_tune_end_distance(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.model.twoends import tune_end_distance

    fake_orc.Model = SpanPhysics
    case = _case(legacy_inputs, **TWO_ENDS)
    path = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path
    SpanPhysics.statics_runs = 0
    res = tune_end_distance(case, path)
    assert res["status"] == "OK", res
    # alvo 0,5 kN no ponto de menor tração (s = 80 m): 15 (d - 160) = 0,5 -> uma estática só (X livre)
    assert res["end_distance"] == pytest.approx(160.0 + 0.5 / 15.0, abs=0.02)
    # escada de damping: 1e3/1e4 e depois ÷10 até o piso (100/1000, 10/100) partindo das posições convergidas
    assert res["end_tension"] == pytest.approx(0.5, abs=0.01) and SpanPhysics.statics_runs == 3
    o = FakeModel(str(path)).objects
    assert o["Constraint B"]["InitialX"] == pytest.approx(res["end_distance"])
    assert o["Constraint B"]["DOFFree"][0] == "No", "modo montagem: X fixo no modelo real"
    assert "MCV B Pull" not in o, "tirante só na estática do ajuste"
    assert "Winch Wire" in o and "Link CB 1" in o, "modelo real mantém a corcova"
    assert res["removed_for_tuning"] == ["Link CB 1", "Link CB 2", "Winch Wire", "Masterlink"]
    # cópia da estática salva antes de rodar, para conferir no OrcaFlex
    work = FakeModel(str(path.parent / "00.Montagem" / f"{path.stem}_ajuste_hubs.dat")).objects
    assert "Link CB 1" not in work and work["Constraint B"]["DOFFree"][0] == "Yes" and "MCV B Pull" in work
    # 0.10.3: cópia do ajuste sem atrito com o fundo; o modelo real mantém o atrito da planilha
    assert work["Line Type"]["SeabedAxialFrictionCoefficient"] == 0.0
    assert work["Line Type"]["SeabedLateralFrictionCoefficient"] == 0.0
    assert o["Line Type"]["SeabedAxialFrictionCoefficient"] == case.num("environment.friction_axial")


def test_tune_end_distance_always_free(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.model.twoends import tune_end_distance

    fake_orc.Model = SpanPhysics
    case = _case(legacy_inputs, **TWO_ENDS, model__end_b_x_free="sempre")
    path = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path
    res = tune_end_distance(case, path)
    o = FakeModel(str(path)).objects
    assert o["Constraint B"]["DOFFree"][0] == "Yes" and "MCV B Pull" in o
    assert o["MCV B Pull"]["EndBX"] == pytest.approx(res["end_distance"] + 200.0)


def test_tune_end_distance_anchored_search(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.model.twoends import tune_end_distance

    fake_orc.Model = SpanPhysics
    case = _case(legacy_inputs, **TWO_ENDS, model__end_b_x_free="nao")
    path = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path
    res = tune_end_distance(case, path)
    assert res["status"] == "OK" and res["end_distance"] == pytest.approx(160.0 + 0.5 / 15.0, abs=0.02)
    assert FakeModel(str(path)).objects["MCV B"]["InitialX"] == pytest.approx(res["end_distance"])


# --------------------------------------------------------------------------- geometria corrigida

def test_geometric_catenary_is_consistent():
    from cvdtools.model.builder import catenary_geometric

    r = catenary_geometric(g=1.7, height=4.4, theta_deg=30.0, line_length=359.0)
    a, x = r["catenary_parameter"], r["x_span"]
    assert a * (math.cosh(x / a) - 1) == pytest.approx(4.4), "chega na altura do End A"
    assert math.sinh(x / a) == pytest.approx(math.tan(math.radians(30))), "com a inclinação do End A"
    assert a * math.sinh(x / a) == pytest.approx(r["suspended_length"])
    assert r["end_b_x"] == pytest.approx(1.7 + x + 359.0 - r["suspended_length"])
    with pytest.raises(ValueError, match="entre 0 e 90"):
        catenary_geometric(1.0, 4.0, 0.0, 300.0)


def test_default_mode_declination_and_end_b(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.model.builder import catenary_geometric

    case = case_from_legacy_json(legacy_inputs)
    base = write_base_model(tmp_path / "b.dat")
    new = FakeModel(str(build_model(case, base, tmp_path / "n.dat").model_path)).objects["Line"]
    old = FakeModel(str(build_model(case, base, tmp_path / "o.dat", catenary="legacy").model_path)).objects["Line"]
    mcv, lt = legacy_inputs["MCVDims"], legacy_inputs["LineTypeDims"]
    assert new["EndADeclination"] == 180 - mcv["Angle"]
    assert old["EndADeclination"] == 90 + mcv["Angle"]
    expected = catenary_geometric(mcv["G"], mcv["A"] - lt["OD"] / 2, 90 - mcv["Angle"], sum(new["Length"]))
    assert new["EndBX"] == pytest.approx(expected["end_b_x"])
    assert new["EndBX"] < sum(new["Length"]) + mcv["G"], "trecho suspenso consome parte da projeção horizontal"


class EndBPhysics(FakeModel):
    """Tração no End B = 20 kN/m x (EndBX - 358) para EndBX > 358; lingas tracionadas puxariam a linha."""

    def CalculateStatics(self):
        x = self.objects["Line"]["EndBX"]
        camelback_present = "Link CB 1" in self.objects or "Winch Wire" in self.objects
        assert self.objects["General"]["StaticsMaxIterations"] <= 2000, "iterações limitadas no ajuste"
        t = max(0.0, 20.0 * (x - 358.0)) + (50.0 if camelback_present else 0.0)
        self.results = {("Line", "Effective tension", "EndB"): [t]}


def test_tune_end_b(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.model.endb import tune_end_b

    fake_orc.Model = EndBPhysics
    case = case_from_legacy_json(legacy_inputs)
    path = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path
    res = tune_end_b(case, path)
    assert res["status"] == "OK"
    assert res["end_b_x"] == pytest.approx(358.0 + 0.5 / 20.0, abs=0.011)
    assert res["end_b_tension"] <= 0.5 + 1e-9
    o = FakeModel(str(path)).objects
    assert o["Line"]["EndBX"] == pytest.approx(res["end_b_x"])
    assert o["Link CB 1"]["UnstretchedLength"] == legacy_inputs["CamelBackDims"]["SlingLength"], "modelo real intacto"
    assert "Winch Wire" in o and "Masterlink" in o
    assert o["General"]["StaticsMaxIterations"] == 10000, "limite de iterações só na cópia de trabalho"
    assert res["removed_for_tuning"] == ["Link CB 1", "Link CB 2", "Winch Wire", "Masterlink"]


def test_default_view_covers_mcv_and_hub(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.io.legacy import case_from_legacy_json
    from cvdtools.model.builder import build_model
    from fake_orcfxapi import FakeModel, write_base_model

    case = case_from_legacy_json(legacy_inputs)
    base = write_base_model(tmp_path / "b.dat")
    m = FakeModel(str(base))
    m.objects["MCV"].update(Connection="Anchored", InitialX=0.0, InitialZ=3.0)
    m.objects["Environment"]["WaterDepth"] = 1448.0
    m.SaveData(str(base))
    res = build_model(case, base, tmp_path / "c.dat")
    g = FakeModel(str(res.model_path)).objects["General"]
    assert g["DefaultViewAzimuth"] == 270.0 and g["DefaultViewElevation"] == 0.0
    assert 15.0 <= g["DefaultViewSize"] <= 100.0
    depth = FakeModel(str(res.model_path)).objects["Environment"].get("WaterDepth", 0.0)
    assert -depth <= g["DefaultViewCentreZ"] <= -depth + g["DefaultViewSize"]   # nomes por componente (API)
