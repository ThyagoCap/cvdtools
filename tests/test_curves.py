"""Biblioteca de curvas externas: formato, validação, referência, resolução, builder e checklist."""

import json

import pytest

from cvdtools.curves import Curve, CurveLibrary, check_curve, curve_from_sheet, write_curve_template
from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.inputs import resolve_curves
from cvdtools.model.builder import build_model
from cvdtools.schema import MissingInputError, ResolvedValue
from cvdtools.values import CurveRef, parse_cell
from fake_orcfxapi import FakeModel, write_base_model

HYST = Curve("WSI152_P0", "bending", [0, 0.00342, 0.01335, 0.27435], [0, 5.855, 15.043, 130.42], "1/m", "kN.m",
             load_case="Pressão interna 0 bar", hysteretic=True, orcaflex_name="Line Hysteresis P0",
             source="WSI 152.1524 Rev.03", aliases=["Histerese - WSI WSI 152.1524.xlsx [15]"], sheet="WSI152_P0")
VERT = Curve("BR_VERT", "bending", [0, 0.25, 0.27], [0, 0.1, 124], "1/m", "kN.m", load_case="-",
             source="CB-BR Rev.02", sheet="BR_VERT")
ROPE = Curve("EXT_ROPE", "axial", [0, 1.0, 3.0], [0, 18.1, 3600], "%", "kN", load_case="novo",
             source="Catálogo", aliases=["Winch Extension Stiffness [10]"], sheet="EXT_ROPE")


@pytest.fixture
def lib(tmp_path):
    write_curve_template(tmp_path / "cvd_curves.xlsx", [HYST, VERT, ROPE])
    return CurveLibrary.load([tmp_path / "cvd_curves.xlsx"], tmp_path)


def test_roundtrip_and_units(lib):
    assert set(lib.curves) == {"WSI152_P0", "BR_VERT", "EXT_ROPE"}
    assert lib.issues == []
    x, y = lib.curves["EXT_ROPE"].si()
    assert x == pytest.approx([0, 0.01, 0.03]) and y == [0, 18.1, 3600]
    assert lib.curves["WSI152_P0"].hysteretic and lib.curves["WSI152_P0"].name == "Line Hysteresis P0"


@pytest.mark.parametrize("curve, fragment", [
    (Curve("A", "bending", [0, 2, 1], [0, 1, 2], "1/m", "kN.m"), "estritamente crescente"),
    (Curve("B", "axial", [0, 1], [0, 1], "%", "%MBL"), "MBL"),
    (Curve("C", "axial", [0, 1], [0, 1], "%", "kN", hysteretic=True), "Histerese"),
    (Curve("D", "bending", [0, 1], [0, 1], "graus", "kN.m"), "Unidade X"),
    (Curve("E", "torsion", [0, 1], [0, 1], "-", "-"), "Tipo"),
])
def test_check_curve_errors(curve, fragment):
    assert any(fragment in i.message and i.level == "ERRO" for i in check_curve(curve))


def test_percent_mbl():
    c = Curve("M", "axial", [0, 1], [0, 50], "%", "%MBL", mbl=3610.1)
    assert c.si()[1] == pytest.approx([0, 1805.05])


def test_lookup_forms(lib, tmp_path):
    assert lib.lookup("curva:BR_VERT").id == "BR_VERT"
    assert lib.lookup("curve: BR_VERT").id == "BR_VERT"
    assert lib.lookup("histerese - wsi  WSI 152.1524.xlsx [15]").id == "WSI152_P0", "alias ignora caixa/espaços"
    write_curve_template(tmp_path / "fornecedor.xlsx", [Curve("X1", "axial", [0, 1], [0, 2], "-", "kN", sheet="X1")])
    assert lib.lookup("fornecedor.xlsx#X1").id == "X1"
    with pytest.raises(KeyError, match="não está registrado"):
        lib.lookup("Histerese - WSI WSI 152.1524.xlsx [9]")
    with pytest.raises(KeyError, match="não existe"):
        lib.lookup("curva:NADA")


def test_alias_conflict(tmp_path):
    other = Curve("H2", "bending", [0, 1], [0, 1], "1/m", "kN.m", aliases=list(HYST.aliases), sheet="H2")
    write_curve_template(tmp_path / "a.xlsx", [HYST])
    write_curve_template(tmp_path / "b.xlsx", [other])
    lib = CurveLibrary.load([tmp_path / "a.xlsx", tmp_path / "b.xlsx"])
    assert any("Alias" in i.message for i in lib.issues)


def test_curve_from_sheet(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Winch Extension Stiffness"
    ws.append([None])
    ws.append([None, "Elongation (%)", "MBF (%)", "Actual Force (kN)"])
    for e, f in ((0.02, 18.1), (0.09, 36.3), (0.16, 54.4)):
        ws.append([None, e, None, f])
    wb.save(tmp_path / "g.xlsx")
    c = curve_from_sheet(tmp_path / "g.xlsx", "Winch Extension Stiffness", "EXT", x_col=2, y_col=4, first_row=3)
    assert c.x == [0.0, 0.02, 0.09, 0.16] and c.y == [0.0, 18.1, 36.3, 54.4], "insere (0,0)"


# --------------------------------------------------------------------------- resolução

def _case(legacy_inputs, **values):
    case = case_from_legacy_json(legacy_inputs)
    for k, v in values.items():
        case.values[k] = ResolvedValue(v, "input_sheet", v)
    return case


def test_parse_curva_prefix():
    assert parse_cell("curva:BR_VERT") == CurveRef("curva:BR_VERT")


def test_resolution(lib, legacy_inputs):
    case = _case(legacy_inputs, **{"line.eix": CurveRef("Histerese - WSI WSI 152.1524.xlsx [15]"),
                                   "winch_ext.ea": "curva:EXT_ROPE",
                                   "br.eix": CurveRef("curva:EXT_ROPE")})
    resolve_curves(case, lib)
    assert case.get("line.eix").id == "WSI152_P0"
    assert case.get("winch_ext.ea").id == "EXT_ROPE"
    assert "exige 'bending'" in case.meta["curve_errors"]["br.eix"]
    assert set(case.curves()) == {"line.eix", "winch_ext.ea"}


def test_resolution_without_library(legacy_inputs):
    case = _case(legacy_inputs, **{"line.eix": CurveRef("Histerese [15]")})
    resolve_curves(case, None)
    assert "não tem biblioteca" in case.meta["curve_errors"]["line.eix"]


# --------------------------------------------------------------------------- builder e checklist

def test_builder_writes_curves(tmp_path, fake_orc, lib, legacy_inputs):
    case = _case(legacy_inputs, **{"line.eix": lib.curves["WSI152_P0"], "br.eix": lib.curves["BR_VERT"]})
    res = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")
    o = FakeModel(str(res.model_path)).objects
    hyst = o["Line Hysteresis P0"]
    assert hyst["_type"] == "BendingStiffness", "criada com o tipo da curva-molde"
    assert hyst["IndependentValue"] == [0, 0.00342, 0.01335, 0.27435] and hyst["Hysteretic"] == "Yes"
    assert o["Line Type"]["EIx"] == "Line Hysteresis P0"
    assert o["Bend Restrictor Type"]["EIx"] == "BR_VERT"
    assert o["BR_VERT"]["DependentValue"] == [0, 0.1, 124]
    assert o["Bend Restrictor Stiffness Curve"]["IndependentValue"] == [0.0, 1.0, 2.0], \
        "com curva de vértebra, a curva calculada não é mais escrita"
    assert any("WSI152_P0" in n and "criada" in n for n in res.notes)


def test_builder_axial_curve_needs_template(tmp_path, fake_orc, lib, legacy_inputs):
    case = _case(legacy_inputs, **{"model.winch_extension": 1.0, "winch_ext.length": 400.0, "winch_ext.od": 0.084,
                                   "winch_ext.mass": 0.003, "winch_ext.ea": lib.curves["EXT_ROPE"]})
    case.roles["CURVE_TEMPLATE_AXIAL"] = ""
    with pytest.raises(MissingInputError, match="CURVE_TEMPLATE_AXIAL"):
        build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")
    base = write_base_model(tmp_path / "b2.dat")
    data = json.loads(base.read_text())
    data["objects"]["Axial Template"] = {"_type": "AxialStiffness", "IndependentValue": [0, 1], "DependentValue": [0, 1]}
    base.write_text(json.dumps(data))
    case.roles["CURVE_TEMPLATE_AXIAL"] = "Axial Template"
    o = FakeModel(str(build_model(case, base, tmp_path / "c2.dat").model_path)).objects
    assert o["EXT_ROPE"]["_type"] == "AxialStiffness"
    assert o["EXT_ROPE"]["IndependentValue"] == pytest.approx([0, 0.01, 0.03])
    assert o["Winch Extension Wire Type"]["EA"] == "EXT_ROPE"


def test_checklist_compares_curve_tables(tmp_path, fake_orc, lib, legacy_inputs):
    from cvdtools.io.config import default_config
    from cvdtools.model.checklist import OK, VERIFY, evaluate

    case = _case(legacy_inputs, **{"line.eix": lib.curves["WSI152_P0"]})
    base = write_base_model(tmp_path / "b.dat")
    path = build_model(case, base, tmp_path / "c.dat").model_path
    rows = default_config(legacy_inputs).checklist
    res = {(r.row.group, r.row.variable): r for r in evaluate(case, rows, FakeModel(str(path)), FakeModel(str(base)))}
    assert res[("Line", "Bending Stiffness")].status == OK
    assert res[("Curvas", "Line Hysteresis P0 ← line.eix")].status == OK
    data = json.loads(path.read_text())
    data["objects"]["Line Hysteresis P0"]["DependentValue"][2] = 16.0
    path.write_text(json.dumps(data))
    res = {(r.row.group, r.row.variable): r for r in evaluate(case, rows, FakeModel(str(path)), FakeModel(str(base)))}
    assert res[("Curvas", "Line Hysteresis P0 ← line.eix")].status == VERIFY
