"""Fase 2: checklist de verificação (com OrcFxAPI falso)."""

import json

import pytest
from openpyxl import load_workbook

from cvdtools.io.config import default_config, write_config
from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.model.builder import build_model
from cvdtools.model.checklist import (CHECK_DEFAULT, MISSING_INPUT, OK, VERIFY, evaluate, next_free_path,
                                      write_checklist)
from cvdtools.pipeline import build_case
from cvdtools.project import write_project_file, load_project
from fake_orcfxapi import FakeModel, write_base_model
from helpers import CASE_ID, full_workbook


@pytest.fixture
def setup(tmp_path, fake_orc, legacy_inputs):
    base = write_base_model(tmp_path / "base.dat")
    case = case_from_legacy_json(legacy_inputs)
    res = build_model(case, base, tmp_path / "caso.dat")
    cfg = default_config(legacy_inputs)
    return case, cfg, res.model_path, base


def _by(results):
    return {(r.row.group, r.row.variable): r for r in results}


def test_all_written_properties_are_ok(setup):
    case, cfg, model_path, base = setup
    res = _by(evaluate(case, cfg.checklist, FakeModel(str(model_path)), FakeModel(str(base))))
    must_be_ok = [("Line", "Outer Diameter"), ("Line", "Weight in SW"), ("Line", "Bending Stiffness"),
                  ("MCV", "Gooseneck angle"), ("MCV", "A - F (calculado)"),
                  ("Bend Restrictor", "Full Length"), ("Bend Restrictor", "Locking Curvature"),
                  ("End Fitting", "Full Length"), ("Camel Back", "Leg 2 KP"), ("Environment", "Water Depth"),
                  ("Acceptance Criteria", "Line Curvature")]
    for k in must_be_ok:
        assert res[k].status == OK, (k, res[k])
    # No JSON antigo não há limites do MCV nem altura do hub informada
    assert res[("Acceptance Criteria", "Axial Tension")].status == MISSING_INPUT
    assert res[("MCV", "A - F (informado)")].status == MISSING_INPUT
    # Achado real no JSON antigo: MassSubmerged (1,76 t) != MassInAir - 1,025*Volume (1,756 t)
    assert res[("MCV", "Weight in SW")].status == VERIFY
    bad = [k for k, r in res.items() if r.status not in (OK, MISSING_INPUT, "MANUAL")]
    assert bad == [("MCV", "Weight in SW")]


def test_detects_injected_defects(setup):
    case, cfg, model_path, base = setup
    data = json.loads(model_path.read_text())
    data["objects"]["Line Type"]["OD"] = 0.30              # OD trocado
    data["objects"]["MCV"]["Mass"] = 1.0                   # igual ao modelo base -> não escrito
    model_path.write_text(json.dumps(data))
    case.values["line.id"].value = case.values["line.id"].value  # sem alteração
    from cvdtools.values import Missing
    case.values["camelback.stiffness"].value = Missing("N/I")  # input N/I
    res = _by(evaluate(case, cfg.checklist, FakeModel(str(model_path)), FakeModel(str(base))))
    assert res[("Line", "Outer Diameter")].status == VERIFY
    assert "+0.02661" in res[("Line", "Outer Diameter")].comment
    assert res[("MCV", "Weight in Air")].status == CHECK_DEFAULT
    assert res[("Camel Back", "Sling Stiffness")].status == MISSING_INPUT


def test_xlsx_layout_and_no_overwrite(tmp_path, setup):
    case, cfg, model_path, base = setup
    results = evaluate(case, cfg.checklist, FakeModel(str(model_path)), FakeModel(str(base)))
    p1 = write_checklist(results, case, tmp_path / "Checklist.xlsx", {"Modelo": {"path": "x"}})
    p2 = write_checklist(results, case, tmp_path / "Checklist.xlsx", {})
    assert p2.name == "Checklist_rev1.xlsx"
    assert next_free_path(tmp_path / "Checklist.xlsx").name == "Checklist_rev2.xlsx"
    wb = load_workbook(p1)
    ws = wb["CheckList"]
    assert [c.value for c in ws[1]][:11] == ["Model Object", "Variable", "Units", "Inputs/TQF", "Inputs Sheet",
                                             "Orcaflex", "OrcaFlex Default", "Automatic Verification",
                                             "Verificado", "Status", "Comment"]
    line_od_row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 2).value == "Outer Diameter")
    assert ws.cell(line_od_row, 8).value.startswith("=IF(OR(E")
    assert ws.cell(line_od_row, 9).value is False
    assert ws.cell(line_od_row, 10).value == f'=IF(I{line_od_row},"OK","NOK")'
    assert ws.merged_cells.ranges, "grupos devem ficar mesclados na coluna A"
    info = {r[0].value: r[1].value for r in wb["Info"].iter_rows()}
    assert info["ID"] == case.case_id and "Status sugerido: OK" in info


def test_build_case_end_to_end(tmp_path, fake_orc, legacy_inputs):
    """Planilha + configuração + modelo base -> modelo, case.json e checklist relidos do disco."""
    (tmp_path / "00.Base").mkdir()
    write_base_model(tmp_path / "00.Base" / "base_input.dat")
    full_workbook(tmp_path / "geral.xlsx", legacy_inputs,
                  overrides={"mcv.axial_tension_limit": 95, "mcv.hub_height": 3.025})
    write_config(default_config(legacy_inputs), tmp_path / "cvd_config.xlsx")
    write_project_file(tmp_path, "1327", "geral.xlsx", "cvd_config.xlsx", base_dir=".")
    proj = load_project(tmp_path / "cvd_project.json")

    out = build_case(proj, CASE_ID, out_dir=str(tmp_path / "caso"))
    assert out["model"].name == "1327.GLF49_2CVD_MOBO_New_In.dat"
    assert out["checklist"].name == "Checklist_GLF49_2CVD_MOBO_New_In.xlsx"
    assert out["checklist_status"].get(VERIFY, 0) == 1   # massa submersa do MCV no JSON antigo
    assert out["checklist_status"].get(CHECK_DEFAULT, 0) == 0
    snap = json.loads(out["case_json"].read_text(encoding="utf-8"))
    assert snap["values"]["line.od"]["source"] == "input_sheet"
    assert snap["extra"]["build"]["mode"] == "fixed"
    with pytest.raises(FileExistsError):
        build_case(proj, CASE_ID, out_dir=str(tmp_path / "caso"))


def test_build_case_two_ends_end_to_end(tmp_path, fake_orc, legacy_inputs):
    """2 extremidades pela pipeline: MCV da outra ponta vem da Input Sheet, distância ajustada pela estática."""
    from openpyxl import load_workbook as _lw

    from cvdtools.io.config import load_config
    from test_phase2_builder import SpanPhysics

    fake_orc.Model = SpanPhysics
    (tmp_path / "00.Base").mkdir()
    write_base_model(tmp_path / "00.Base" / "base_input.dat")
    path = full_workbook(tmp_path / "geral.xlsx", legacy_inputs,
                         overrides={"mcv.axial_tension_limit": 95, "mcv.hub_height": 3.025, "line.total_length": 159.0})
    wb = _lw(path)
    ws = wb["Input Sheet"]
    header = [c.value for c in ws[2]]
    other = [c.value for c in ws[3]]
    other[header.index("ID")] = "OUTRA PONTA"
    for col, value in (("MCV Angle (°)", 60.0),):
        if col in header:
            other[header.index(col)] = value
    ws.append(other)
    wb.save(path)
    cfg = default_config(legacy_inputs)
    cfg.overrides[CASE_ID] = {"model.modelled_ends": 2.0, "model.end_b_case_id": "OUTRA PONTA"}
    write_config(cfg, tmp_path / "cvd_config.xlsx")
    write_project_file(tmp_path, "1327", "geral.xlsx", "cvd_config.xlsx", base_dir=".")
    proj = load_project(tmp_path / "cvd_project.json")

    out = build_case(proj, CASE_ID, out_dir=str(tmp_path / "caso"))
    o = FakeModel(str(out["model"])).objects
    assert "MCV B" in o and o["Line"]["EndBConnection"] == "MCV B"
    assert sum(o["Line"]["Length"]) == pytest.approx(159.0)
    assert out["catenary"]["tuned_status"] == "OK"
    assert o["Constraint B"]["InitialX"] == pytest.approx(160.0 + 0.5 / 15.0, abs=0.02)
    assert o["Constraint B"]["DOFFree"][0] == "No" and "MCV B Pull" not in o
    g = o["General"]   # estáticas robustas (2 extremidades) não ficam gravadas no modelo
    assert (g["StaticsMaxIterations"], g["StaticsMinDamping"], g["StaticsMaxDamping"]) == (10000, 200, 1000)
    assert any("Distância entre os hubs ajustada" in n for n in out["notes"])
    snap = json.loads(out["case_json"].read_text(encoding="utf-8"))
    assert snap["values"]["mcv_b.angle"]["source"] == "input_sheet:OUTRA PONTA"
    from cvdtools.cli import _geometry_line

    line = _geometry_line(out["catenary"])
    assert "duas extremidades" in line and "ajustada pela estática" in line


def test_geometry_line_one_end():
    from cvdtools.cli import _geometry_line

    line = _geometry_line({"mode": "fixed", "end_a_declination": 30.0, "suspended_length": 50.0, "end_b_x": 120.0})
    assert "End B X = 120.000 m" in line


def test_two_ends_checklist_rows(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.schema import ResolvedValue
    from test_phase2_builder import TWO_ENDS

    case = case_from_legacy_json(legacy_inputs)
    for k, v in TWO_ENDS.items():
        case.values[k.replace("__", ".")] = ResolvedValue(v, "override")
    base = write_base_model(tmp_path / "base.dat")
    res = build_model(case, base, tmp_path / "caso.dat")
    cfg = default_config(legacy_inputs)
    out = _by(evaluate(case, cfg.checklist, FakeModel(str(res.model_path)), FakeModel(str(base))))
    for key in (("MCV B", "Gooseneck angle"), ("MCV B", "A - F (calculado)"), ("MCV B", "F"),
                ("MCV B", "Weight in Air"), ("Line", "Comprimento total (2 extremidades)"),
                ("Line", "Extremidades com MCV")):
        assert out[key].status == OK, (key, out[key])
    one = _by(evaluate(case_from_legacy_json(legacy_inputs), cfg.checklist, FakeModel(str(base)), FakeModel(str(base))))
    assert ("MCV B", "F") not in one, "linhas de 2 extremidades só aparecem quando modeladas"


def test_mcv_mass_properties_rows(tmp_path, fake_orc, legacy_inputs):
    """0.10.0: CoG/CoV (G − E; F − D), altura B + F e inércia de caixa dos dois MCVs no checklist."""
    from cvdtools.schema import ResolvedValue
    from test_phase2_builder import TWO_ENDS

    case = case_from_legacy_json(legacy_inputs)
    values = {**TWO_ENDS, "mcv__d": -0.045, "mcv__e": 1.279, "mcv_b__d": 0.4553, "mcv_b__e": 1.9756,
              "mcv_b__mass_sub": 6.89562}
    for k, v in values.items():
        case.values[k.replace("__", ".")] = ResolvedValue(v, "override")
    base = write_base_model(tmp_path / "base.dat")
    res = build_model(case, base, tmp_path / "caso.dat")
    cfg = default_config(legacy_inputs)
    out = _by(evaluate(case, cfg.checklist, FakeModel(str(res.model_path)), FakeModel(str(base))))
    for who in ("MCV", "MCV B"):
        for var in ("CoG z (F - D)", "CoV z (F - D)", "Height (B + F)", "Inertia x (caixa)", "Inertia y (caixa)",
                    "Inertia z (caixa)", "Hub shape type"):
            assert out[(who, var)].status == OK, (who, var, out[(who, var)])
    assert out[("MCV", "CoG x (G - E)")].status == OK and out[("MCV", "CoV x (G - E)")].status == OK
    assert out[("MCV B", "CoG x -(G - E) (espelhado)")].status == OK
    assert out[("MCV B", "CoG x -(G - E) (espelhado)")].orcaflex_value == pytest.approx(-(2.1414 - 1.9756))
    assert out[("MCV", "CoG x (G - E)")].default_value == pytest.approx(0.203)   # modelo base
    for key in (("Line", "Contents Density"), ("Line", "Statics Step 1"), ("General", "Jacobian perturbation factor"),
                ("Bend Restrictor B", "Attachment Length"), ("MCV B", "Calc. Volume")):
        assert out[key].status == OK, (key, out[key])
