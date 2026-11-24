"""0.10.0: propriedades do MCV pelas cotas, conteúdo, estática da linha, hubs, General (comparação com o Modelo 2)."""

import pytest

from cvdtools.model.builder import build_model
from cvdtools.model.mcvprops import get_vector, mcv_properties, set_vector
from fake_orcfxapi import FakeModel, write_base_model
from test_phase2_builder import _case


def test_box_inertia_and_cog_reproduce_model_2():
    """GLF27 2CVD MOBO New Out: Modelo 2 tem CoG (0,409; 0; 1,006), altura 1,83 m e inércia 0,62/1,22/0,72 t·m²."""
    p = mcv_properties({"b": 0.869, "f": 0.961, "g": 1.6884, "mass_air": 2.002, "base": 0.6, "d": -0.045,
                        "e": 1.279})
    assert p["cog"] == pytest.approx((0.4094, 0.0, 1.006)) and p["cov"] == p["cog"]
    assert p["height"] == pytest.approx(1.83)
    assert [round(v, 2) for v in p["inertia"]] == [0.62, 1.22, 0.72]
    assert p["notes"] == []


def test_missing_d_e_use_sheet_alternates():
    p = mcv_properties({"b": 1.0, "f": 1.0, "g": 2.0, "mass_air": 1.0, "base": 0.6, "d": None, "e": None})
    assert p["cog"] == pytest.approx((0.3, 0.0, 0.7)) and len(p["notes"]) == 2
    assert mcv_properties({"b": 1.0, "f": 1.0, "g": 2.0, "mass_air": 1.0, "base": 0.6, "d": 0.1, "e": 1.5},
                          mirror=True)["cog"] == pytest.approx((-0.5, 0.0, 0.9))


class StrictBuoy:
    """Como o OrcFxAPI: só aceita os nomes de dados que existem (DataNameValid)."""

    VALID = {"MomentsOfInertiaX", "MomentsOfInertiaY", "MomentsOfInertiaZ", "CentreOfMassX", "CentreOfMassY",
             "CentreOfMassZ"}

    def __init__(self):
        object.__setattr__(self, "data", {})

    def DataNameValid(self, name):
        return name in self.VALID

    def __setattr__(self, name, value):
        if name not in self.VALID:
            raise AttributeError(name)
        self.data[name] = value

    def __getattr__(self, name):
        if name in self.VALID:
            return self.data.get(name, 0.0)
        raise AttributeError(name)


def test_vector_names_checked_with_data_name_valid():
    obj = StrictBuoy()
    assert set_vector(obj, "inertia", (1.0, 2.0, 3.0)) == "MomentsOfInertia"
    assert get_vector(obj, "inertia") == (1.0, 2.0, 3.0)
    assert set_vector(obj, "cov", (0.1, 0.0, 0.2)) is None    # nenhum candidato aceito: o build avisa


def test_one_end_build_writes_new_properties(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs)
    res = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")
    o = FakeModel(str(res.model_path)).objects
    mcv, line, g = o["MCV"], o["Line"], o["General"]
    a, b, f, gg = (legacy_inputs["MCVDims"][k] for k in ("A", "B", "F", "G"))
    assert mcv["Height"] == pytest.approx(b + f)
    assert mcv["CentreOfMassX"] == mcv["CentreOfVolumeX"] and mcv["CentreOfMassZ"] == mcv["CentreOfVolumeZ"]
    assert mcv["MomentOfInertiaY"] == pytest.approx(mcv["Mass"] * ((gg + 0.3) ** 2 + (b + f) ** 2) / 12)
    assert o["Hub Height"]["ShapeType"] == "Drawing"
    assert line["ContentsDensity"] == 0.0 and line["StaticsStep1"] == "Quick"
    assert g["JacobianPerturbationFactor"] == 0.0
    if case.num("simulation.variable_time_step"):   # o JSON antigo usa passo variável
        assert (g["ImplicitUseVariableTimeStep"], g["ImplicitVariableMaxTimeStep"]) == ("Yes", 0.05)
    case = _case(legacy_inputs, simulation__variable_time_step=0.0, simulation__time_step=0.05)
    g = FakeModel(str(build_model(case, write_base_model(tmp_path / "b2.dat"), tmp_path / "c2.dat")
                      .model_path)).objects["General"]
    assert (g["ImplicitUseVariableTimeStep"], g["ImplicitConstantTimeStep"]) == ("No", 0.05)
    assert any(n.startswith("MCV: CoG = CoV") for n in res.notes)


def test_legacy_mode_keeps_the_old_model(tmp_path, fake_orc, legacy_inputs):
    """O modo legacy reproduz o _01 (regressão contra o baseline): nada disso muda."""
    case = _case(legacy_inputs)
    o = FakeModel(str(build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat",
                                  catenary="legacy").model_path)).objects
    assert o["MCV"]["CentreOfMassX"] == 0.203 and o["MCV"]["MomentOfInertiaX"] == 100.0
    assert o["Hub Height"]["ShapeType"] == "Elastic solid"
    assert o["Line"]["ContentsDensity"] == 1.025 and o["Line"]["StaticsStep1"] == "Catenary"
    assert o["General"]["JacobianPerturbationFactor"] == 1e-5


def test_parameters_can_restore_old_choices(tmp_path, fake_orc, legacy_inputs):
    case = _case(legacy_inputs, model__line_statics_step1="Catenary", line__contents_density=1.025,
                 model__hub_shape_type="Elastic solid", model__jacobian_perturbation_factor=1e-5)
    o = FakeModel(str(build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path)).objects
    assert (o["Line"]["StaticsStep1"], o["Line"]["ContentsDensity"]) == ("Catenary", 1.025)
    assert o["Hub Height"]["ShapeType"] == "Elastic solid" and o["General"]["JacobianPerturbationFactor"] == 1e-5
