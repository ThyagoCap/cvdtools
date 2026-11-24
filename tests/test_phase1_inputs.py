"""Fase 1: valores especiais, leitura da Input Sheet, configuração, resolução e validação."""

import json

import pytest
from openpyxl import Workbook

from cvdtools import params as P
from cvdtools.inputs import resolve_case
from cvdtools.io.config import default_config, load_config, write_config
from cvdtools.io.legacy import case_from_legacy_json, case_to_legacy_json, diff_case_vs_legacy
from cvdtools.io.snapshot import write_snapshot
from cvdtools.io.workbook import read_input_sheet
from cvdtools.validation import ERROR, INFO, WARNING, validate_sheet
from cvdtools.values import CurveRef, Missing, parse_cell, parse_list

# --------------------------------------------------------------------------- valores


@pytest.mark.parametrize("raw, expected", [
    (None, Missing("vazio")), ("N/I", Missing("N/I")), ("Script", Missing("Script")), ("-", Missing("-")),
    ("?", Missing("?")), (60, 60.0), ("0,25", 0.25), ("1e7", 1e7),
    ("Histerese - WSI WSI 152.1524.xlsx [15]", CurveRef("Histerese - WSI WSI 152.1524.xlsx [15]")),
    ("Winch Extension Stiffness [10]", CurveRef("Winch Extension Stiffness [10]")),
    ("texto livre", "texto livre"),
])
def test_parse_cell(raw, expected):
    assert parse_cell(raw) == expected


def test_parse_list():
    assert parse_list("1,5; 2;3") == [1.5, 2.0, 3.0]
    assert parse_list(None) == []
    assert parse_list("N/I") == Missing("N/I")


def test_param_registry_consistent():
    assert len(P.BY_KEY) == len(P.PARAMS), "chaves duplicadas"
    cols = [p.input_column for p in P.PARAMS if p.input_column]
    assert len(cols) == len(set(cols)), "coluna da Input Sheet mapeada duas vezes"


# --------------------------------------------------------------------------- planilha sintética

HEADER = ["", "Item", "Well", "Line Description", "Inputs sheet", "Length (m)", "Operation Type", "Location", "ID",
          "Folder", "MCV Angle (°)", "MCV A (m)", "Line OD (m)", "Line EIx (kN.m²)", "BR Length (m)",
          "MCV Axial Tension (kN)", "Coluna Nova"]


def make_sheet(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Input Sheet"
    ws.append([None])
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


ROWS = [
    ["", 1, "GLF-49", "Flow", "INPUTS", 1100, "2nd end CVD", "MOBO New Out", "GLF49 2CVD MOBO New Out",
     "02.CVD2-MOBO Out", 60, 4.5, 0.27, "Histerese - WSI [15]", "N/I", "Script", "x"],
    ["", 2, None, None, None, 1159, "1st end CVD", "MOBO New In", "GLF49 2CVD MOBO New In",
     "01.CVD2-MOBO In", 60, 4.5, 0.27, 8.11, 7.0, 95, None],
    ["", 3, None, None, None, 1159, "2nd end CVD", "XT", "GLF40 2CVD XT", "02.CVD2-XT", 45, 3.0, 0.3, 8.0, 2.0, 50, None],
    ["", 4, None, None, None, 1159, "2nd end CVD", "XT", "GLF40 2CVD XT", "02.CVD2-XT", 45, 3.0, 0.3, 8.0, 2.0, 50, None],
]


@pytest.fixture
def sheet(tmp_path):
    return read_input_sheet(make_sheet(tmp_path / "geral.xlsx", ROWS))


@pytest.fixture
def cfg(legacy_inputs):
    return default_config(legacy_inputs)


def test_read_input_sheet(sheet):
    assert sheet.header_row == 2
    assert sheet.ids()[:2] == ["GLF49 2CVD MOBO New Out", "GLF49 2CVD MOBO New In"]
    row = sheet.find("GLF49 2CVD MOBO New In")
    assert row.meta["well"] == "GLF-49", "Well deve ser herdado da linha anterior (célula mesclada)"
    assert row.values["mcv.angle"] == 60.0
    assert sheet.find("GLF49 2CVD MOBO New Out").values["line.eix"] == CurveRef("Histerese - WSI [15]")
    assert sheet.unmapped_columns() == ["Coluna Nova"]
    with pytest.raises(ValueError, match="aparece 2 vezes"):
        sheet.find("GLF40 2CVD XT")
    with pytest.raises(KeyError):
        sheet.find("NAO EXISTE")


def test_config_roundtrip(tmp_path, cfg):
    path = write_config(cfg, tmp_path / "cfg.xlsx")
    loaded = load_config(path)
    assert loaded.defaults["environment.lda"] == 1448.0
    assert loaded.defaults["br.eix"] == "Bend Restrictor Stiffness Curve"
    assert loaded.defaults["model.line_tail_lengths"] == [50.0, 300.0]
    assert loaded.roles["LINE"] == "Line"
    assert len(loaded.postproc["camelback"]) == len(cfg.postproc["camelback"])
    assert {i.obj for i in loaded.postproc["camelback"]} >= {"{ENVIRONMENT}", "{VESSEL}", "{LINE}"}
    assert [c.limit for c in loaded.criteria["camelback"]] == [c.limit for c in cfg.criteria["camelback"]]
    assert loaded.operation("2nd end CVD").camelback and loaded.operation("2nd end CVD").supported
    assert not loaded.operation("Handshake").supported
    assert len(loaded.sea_grid) == 77
    with pytest.raises(FileExistsError):
        write_config(cfg, path)


def test_config_reports_bad_rows(tmp_path, cfg):
    from openpyxl import load_workbook

    path = write_config(cfg, tmp_path / "cfg.xlsx")
    wb = load_workbook(path)
    wb["Overrides"].append(["X", "parametro.inexistente", 1, ""])
    wb["PostProc"].append(["camelback", "Ruim", "Média", "{LINE}", "X", "", "", ""])
    wb.save(path)
    with pytest.raises(ValueError) as exc:
        load_config(path)
    assert "Overrides" in str(exc.value) and "PostProc" in str(exc.value)


def test_resolution_precedence(sheet, cfg):
    cfg.overrides["GLF49 2CVD MOBO New In"] = {"line.od": 0.3, "camelback.leg1_kp": 99.0}
    case = resolve_case(sheet, cfg, "GLF49 2CVD MOBO New In", "1327")
    assert case.operation_name == "1327.GLF49_2CVD_MOBO_New_In"
    assert (case.get("line.od"), case.values["line.od"].source) == (0.3, "override")
    assert case.values["line.od"].raw_input == 0.27
    assert (case.get("mcv.angle"), case.values["mcv.angle"].source) == (60.0, "input_sheet")
    assert (case.get("environment.lda"), case.values["environment.lda"].source) == (1448.0, "default")
    assert case.get("camelback.leg1_kp") == 99.0
    # Template camelback não se aplica a "1st end CVD" -> sem critérios
    assert case.criteria == []


def test_criteria_limit_from_parameter(sheet, cfg):
    spec = cfg.criteria["camelback"][0]
    spec.limit_source, spec.limit = "mcv.axial_tension_limit", None
    curv = next(c for c in cfg.criteria["camelback"] if c.name == "Curvature")
    curv.limit_source, curv.transform, curv.limit = "line.mbr", "inverse", None
    cfg.defaults["line.mbr"] = 2.0
    case = resolve_case(sheet, cfg, "GLF49 2CVD MOBO New Out")
    axial = case.criteria[0]
    assert axial.limit == Missing("Script") and "input_sheet" not in axial.limit_origin
    assert next(c for c in case.criteria if c.name == "Curvature").limit == pytest.approx(0.5)
    assert {c.obj for c in case.criteria} <= {"Line", "Bend Restrictor Element"}


def test_validation(sheet, cfg):
    issues = validate_sheet(sheet, cfg)
    by = {(i.case_id, i.field, i.level) for i in issues}
    assert ("GLF40 2CVD XT", "ID", ERROR) in by
    assert ("GLF49 2CVD MOBO New In", "Operation Type", ERROR) in by     # ID 2CVD x 1st end CVD
    assert ("GLF49 2CVD MOBO New In", "Folder", WARNING) in by           # pasta CVD2 x 1st end CVD
    assert ("GLF49 2CVD MOBO New Out", "In/Out", WARNING) not in by      # ID/Location/Folder = Out
    assert ("GLF49 2CVD MOBO New In", "In/Out", WARNING) not in by
    assert ("GLF49 2CVD MOBO New Out", "br.length", ERROR) in by
    eix = next(i for i in issues if i.case_id == "GLF49 2CVD MOBO New Out" and i.field == "line.eix")
    assert eix.level == ERROR and "curva externa" in eix.message


def test_legacy_roundtrip(legacy_inputs):
    case = case_from_legacy_json(legacy_inputs)
    assert case.get("br.eix") == "Bend Restrictor Stiffness Curve"
    assert case.get("simulation.variable_time_step") == 1.0
    back = case_to_legacy_json(case)
    for section in ("EnvironmentDims", "MCVDims", "LineTypeDims", "BendRestrictorDims", "EndFittingDims",
                    "CraneWireDims", "WinchWireDims", "CamelBackDims", "FloaterDims"):
        assert back[section] == legacy_inputs[section], section
    assert back["acceptanceCriteria"] == legacy_inputs["acceptanceCriteria"]
    assert back["postProcess"] == legacy_inputs["postProcess"]
    assert diff_case_vs_legacy(case, legacy_inputs) == []


def test_snapshot(tmp_path, sheet, cfg):
    case = resolve_case(sheet, cfg, "GLF49 2CVD MOBO New Out")
    path = write_snapshot(case, tmp_path / "caso", workbook=sheet.path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["case_id"] == "GLF49 2CVD MOBO New Out"
    assert data["values"]["line.eix"]["value"] == {"curve": "Histerese - WSI [15]"}
    assert data["sources"]["workbook"]["sha256"]
    assert data["sources"]["config"] is None


# --------------------------------------------------------------------------- planilha real (se existir)

def test_real_workbook(real_workbook, cfg):
    """A planilha real é lida e validada sem quebrar.

    Só confere a estrutura: os dados mudam quando a planilha é corrigida, e o que está
    errado nela é assunto do ``cvd inputs validate``, não dos testes.
    """
    sheet = read_input_sheet(real_workbook)
    assert len(sheet.rows) >= 30
    assert sheet.unmapped_columns() == []
    issues = validate_sheet(sheet, cfg)
    assert all(i.level in (ERROR, WARNING, INFO) for i in issues)
    case = resolve_case(sheet, cfg, "GLF49 2CVD MOBO New In", "1327")
    assert case.num("line.ea") > 0


# --------------------------------------------------------------------------- premissas

PREMISE_ROWS = [
    ["", 1, "GLF-27", "Jumper", "INPUTS", 159, "2nd end CVD", "MOBO", "GLF27 2CVD MOBO", "02.CVD2-MOBO",
     60, 4.5, 0.27, 8.0, 7.0, 95, None],
    ["", 2, None, None, None, 159, "1st end DCVD", "PLET", "GLF27 1DCVD PLET", "01.DCVD1-PLET",
     45, 3.2, 0.27, 8.0, 7.0, 95, None],
    ["", 3, "GLF-49", "Flow", "INPUTS", 1100, "2nd end CVD", "MOBO", "GLF49 2CVD MOBO", "02.CVD2-MOBO",
     60, 4.5, 0.27, 8.0, 7.0, 95, None],
]


@pytest.fixture
def psheet(tmp_path):
    return read_input_sheet(make_sheet(tmp_path / "p.xlsx", PREMISE_ROWS))


def _issues(sheet, cfg, cid):
    from cvdtools.validation import validate_case
    return {(i.field, i.level): i for i in validate_case(sheet, cfg, cid)}


def test_winch_extension_must_be_decided(psheet, cfg):
    assert ("model.winch_extension", WARNING) in _issues(psheet, cfg, "GLF49 2CVD MOBO")
    cfg.defaults["model.winch_extension"] = 1.0
    issues = _issues(psheet, cfg, "GLF49 2CVD MOBO")
    assert ("model.winch_extension", WARNING) not in issues
    assert ("winch_ext.od", ERROR) in issues, "com extensão, os dados da extensão passam a ser obrigatórios"
    case = resolve_case(psheet, cfg, "GLF49 2CVD MOBO")
    assert case.premises()[0][1] == "sim"


def test_short_line_asks_to_confirm_ends(psheet, cfg):
    assert ("model.modelled_ends", WARNING) in _issues(psheet, cfg, "GLF27 2CVD MOBO")       # 159 m
    assert ("model.modelled_ends", WARNING) not in _issues(psheet, cfg, "GLF49 2CVD MOBO")   # 1100 m
    cfg.overrides["GLF27 2CVD MOBO"] = {"model.modelled_ends": 1.0}                          # confirmado
    assert ("model.modelled_ends", WARNING) not in _issues(psheet, cfg, "GLF27 2CVD MOBO")


def test_two_ends_resolves_other_mcv(psheet, cfg):
    cfg.overrides["GLF27 2CVD MOBO"] = {"model.modelled_ends": 2.0, "model.end_b_case_id": "GLF27 1DCVD PLET",
                                        "mcv_b.g": 9.9}
    case = resolve_case(psheet, cfg, "GLF27 2CVD MOBO")
    assert case.get("mcv_b.angle") == 45.0 and case.get("mcv_b.a") == 3.2
    assert case.values["mcv_b.a"].source == "input_sheet:GLF27 1DCVD PLET"
    assert case.get("mcv_b.g") == 9.9 and case.values["mcv_b.g"].source == "override"
    issues = _issues(psheet, cfg, "GLF27 2CVD MOBO")
    assert ("model.end_distance", ERROR) not in issues   # v0.8: opcional (vazio = ajustada pela estática)
    assert ("Operações", ERROR) not in issues            # sem modelo base próprio: MCV B e hub B são copiados
    assert ("Operações", INFO) in issues
    assert ("model.end_b_case_id", ERROR) not in issues


def test_two_ends_without_other_id(psheet, cfg):
    cfg.overrides["GLF27 2CVD MOBO"] = {"model.modelled_ends": 2.0}
    assert ("model.end_b_case_id", ERROR) in _issues(psheet, cfg, "GLF27 2CVD MOBO")


def test_config_upgrade(tmp_path, cfg):
    from openpyxl import load_workbook

    from cvdtools.io.config import upgrade_config

    path = write_config(cfg, tmp_path / "cfg.xlsx")
    wb = load_workbook(path)        # simula configuração antiga: sem as linhas/colunas novas
    ws = wb["Defaults"]
    for row in list(ws.iter_rows(min_row=2)):
        if str(row[0].value).startswith(("model.winch", "model.modelled", "winch_ext.")):
            ws.delete_rows(row[0].row)
    wb["Papéis"].delete_rows(wb["Papéis"].max_row)
    wb["Operações"].delete_cols(6)
    wb.save(path)

    changes = upgrade_config(path, {"model.winch_extension": "S"})
    assert any("model.winch_extension = S" in c for c in changes)
    assert any("Papéis: +" in c for c in changes)
    assert any("Operações: + coluna" in c for c in changes)
    assert list(tmp_path.glob("cfg.bak_*.xlsx"))
    loaded = load_config(path)
    assert loaded.defaults["model.winch_extension"] == 1.0
    assert loaded.defaults["model.modelled_ends"] == 1.0
    assert upgrade_config(path) == []


def test_other_end_candidates(tmp_path):
    from cvdtools.validation import other_end_candidates

    rows = [
        ["", 1, "GLF-27", "Jumper PLET-MOBO", "", 159, "1st end DCVD", "PLET", "A1", "", 60, 1, 1, 1, 1, 1, None],
        ["", 2, None, None, None, None, "2nd end DCVD", "MOBO", "A2", "", 60, 1, 1, 1, 1, 1, None],
        ["", 3, None, "Jumper XT-MOBO", None, 267, "1st end DCVD", "XT", "B1", "", 60, 1, 1, 1, 1, 1, None],
        ["", 4, "GLF-28", "Jumper PLET-MOBO", None, 108, "1st end DCVD", "MOBO", "C1", "", 60, 1, 1, 1, 1, 1, None],
    ]
    sheet = read_input_sheet(make_sheet(tmp_path / "c.xlsx", rows))
    assert sheet.find("A2").values["line.total_length"] == 159.0, "comprimento mesclado é herdado"
    assert other_end_candidates(sheet, "A1") == ["A2"]
    assert other_end_candidates(sheet, "B1") == []


def test_counterpart_suggestion_first(tmp_path):
    from cvdtools.validation import other_end_candidates

    rows = [
        ["", 1, "GLF-27", "Jumper PLET-MOBO", "", 159, "1st end DCVD", "PLET", "P1", "", 60, 1, 1, 1, 1, 1, None],
        ["", 2, None, None, None, None, "2nd end CVD", "MOBO New", "P2", "", 60, 1, 1, 1, 1, 1, None],
        ["", 3, None, None, None, None, "2nd end DCVD", "MOBO Old", "P3", "", 60, 1, 1, 1, 1, 1, None],
    ]
    sheet = read_input_sheet(make_sheet(tmp_path / "c.xlsx", rows))
    assert other_end_candidates(sheet, "P1", "2nd end DCVD") == ["P3", "P2"]
    assert other_end_candidates(sheet, "P1") == ["P2", "P3"]


def test_two_ends_error_carries_suggestion(psheet, cfg):
    cfg.overrides["GLF27 1DCVD PLET"] = {"model.modelled_ends": 2.0}
    msg = _issues(psheet, cfg, "GLF27 1DCVD PLET")[("model.end_b_case_id", ERROR)].message
    assert "Candidatas (mesma linha): GLF27 2CVD MOBO" in msg or "Sugestão" in msg


@pytest.mark.parametrize("raw, expected", [(1.0, 1.0), (0, 0.0), ("1.0", 1.0), ("S", 1.0), ("N", 0.0), ("Sim", 1.0),
                                           ("não", 0.0), (True, 1.0), ("0,0", 0.0), (None, None)])
def test_bool_config_values(raw, expected):
    from cvdtools.io.config import _param_value
    v = _param_value("model.tune_end_b", raw)
    assert (v == expected) if expected is not None else isinstance(v, Missing)
