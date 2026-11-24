"""missing_data.json: inputs faltando viram um arquivo para o usuário preencher (com OrcFxAPI falso)."""

import json

import pytest
from openpyxl import load_workbook

from cvdtools.io.config import default_config, load_config, set_config_values, write_config
from cvdtools.missing_data import FILE_NAME
from cvdtools.pipeline import build_case
from cvdtools.project import load_project, write_project_file
from cvdtools.schema import MissingInputError
from fake_orcfxapi import write_base_model
from helpers import CASE_ID, full_workbook


def _project(tmp_path, legacy_inputs, drop=(), overrides=None, other_row=False):
    (tmp_path / "00.Base").mkdir()
    write_base_model(tmp_path / "00.Base" / "base_input.dat")
    path = full_workbook(tmp_path / "geral.xlsx", legacy_inputs,
                         overrides={"mcv.axial_tension_limit": 95, "mcv.hub_height": 3.025, "line.total_length": 159.0})
    if other_row:
        wb = load_workbook(path)
        ws = wb["Input Sheet"]
        header = [c.value for c in ws[2]]
        row = [c.value for c in ws[3]]
        row[header.index("ID")] = "OUTRA PONTA"
        row[header.index("Location")] = "PLET"
        ws.append(row)
        wb.save(path)
    cfg = default_config(legacy_inputs)
    for k in drop:
        cfg.defaults.pop(k, None)
    if overrides:
        cfg.overrides[CASE_ID] = overrides
    write_config(cfg, tmp_path / "cvd_config.xlsx")
    write_project_file(tmp_path, "1327", "geral.xlsx", "cvd_config.xlsx", base_dir=".")
    return load_project(tmp_path / "cvd_project.json")


def _items(path):
    return {i["parametro"]: i for i in json.loads(path.read_text(encoding="utf-8"))["itens"]}


def _fill(path, values):
    doc = json.loads(path.read_text(encoding="utf-8"))
    for i in doc["itens"]:
        if i["parametro"] in values:
            i["valor"] = values[i["parametro"]]
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_missing_defaults_round_trip(tmp_path, fake_orc, legacy_inputs):
    proj = _project(tmp_path, legacy_inputs, drop=("environment.lda", "camelback.sling_length"))
    out = tmp_path / "caso"
    with pytest.raises(MissingInputError, match=FILE_NAME):
        build_case(proj, CASE_ID, out_dir=str(out))
    md = out / FILE_NAME
    items = _items(md)
    assert set(items) == {"environment.lda", "camelback.sling_length"}
    assert all(i["destino"] == "Defaults" and i["valor"] is None for i in items.values())

    # o arquivo precisa ser de fato preenchido: rodar de novo sem valores continua parando
    with pytest.raises(MissingInputError, match="ainda não foi preenchido"):
        build_case(proj, CASE_ID, out_dir=str(out))
    assert not list(tmp_path.glob("cvd_config.bak_*.xlsx"))

    # preenchimento parcial: grava o que veio e gera o arquivo de novo só com o que falta
    _fill(md, {"environment.lda": 1448})
    with pytest.raises(MissingInputError, match="Ainda faltam"):
        build_case(proj, CASE_ID, out_dir=str(out))
    assert set(_items(md)) == {"camelback.sling_length"}
    assert load_config(tmp_path / "cvd_config.xlsx").defaults["environment.lda"] == 1448
    assert len(list(out.glob("missing_data.aplicado_*.json"))) == 1

    _fill(md, {"camelback.sling_length": "7,5"})
    res = build_case(proj, CASE_ID, out_dir=str(out))
    assert res["model"].exists()
    assert load_config(tmp_path / "cvd_config.xlsx").defaults["camelback.sling_length"] == 7.5
    assert not md.exists() and len(list(out.glob("missing_data.aplicado_*.json"))) == 2
    assert list(tmp_path.glob("cvd_config.bak_*.xlsx"))


def test_invalid_value_is_kept_with_error(tmp_path, fake_orc, legacy_inputs):
    proj = _project(tmp_path, legacy_inputs, drop=("environment.lda",))
    out = tmp_path / "caso"
    with pytest.raises(MissingInputError):
        build_case(proj, CASE_ID, out_dir=str(out))
    _fill(out / FILE_NAME, {"environment.lda": "muito fundo"})
    with pytest.raises(MissingInputError, match="ERRO"):
        build_case(proj, CASE_ID, out_dir=str(out))
    item = _items(out / FILE_NAME)["environment.lda"]
    assert item["valor"] == "muito fundo" and "número" in item["erro"]


def test_unreadable_file_is_not_overwritten(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.missing_data import MissingDataError

    proj = _project(tmp_path, legacy_inputs, drop=("environment.lda",))
    out = tmp_path / "caso"
    out.mkdir()
    (out / FILE_NAME).write_text('{"caso": "x", "itens": [', encoding="utf-8")
    with pytest.raises(MissingDataError, match="JSON válido"):
        build_case(proj, CASE_ID, out_dir=str(out))
    assert (out / FILE_NAME).read_text(encoding="utf-8") == '{"caso": "x", "itens": ['


def test_end_b_id_is_asked_and_goes_to_overrides(tmp_path, fake_orc, legacy_inputs):
    from test_phase2_builder import SpanPhysics

    fake_orc.Model = SpanPhysics
    proj = _project(tmp_path, legacy_inputs, overrides={"model.modelled_ends": 2.0}, other_row=True)
    out = tmp_path / "caso"
    with pytest.raises(MissingInputError, match="model.end_b_case_id"):
        build_case(proj, CASE_ID, out_dir=str(out))
    items = _items(out / FILE_NAME)
    assert list(items) == ["model.end_b_case_id"]          # as cotas do MCV B não são pedidas uma a uma
    assert items["model.end_b_case_id"]["destino"] == "Overrides"
    assert items["model.end_b_case_id"]["sugestoes"] == ["OUTRA PONTA"]

    _fill(out / FILE_NAME, {"model.end_b_case_id": "NAO EXISTE"})
    with pytest.raises(MissingInputError):
        build_case(proj, CASE_ID, out_dir=str(out))
    assert "não encontrado" in _items(out / FILE_NAME)["model.end_b_case_id"]["erro"]

    _fill(out / FILE_NAME, {"model.end_b_case_id": "OUTRA PONTA"})
    res = build_case(proj, CASE_ID, out_dir=str(out))
    assert res["model"].exists()
    cfg = load_config(tmp_path / "cvd_config.xlsx")
    assert cfg.overrides[CASE_ID]["model.end_b_case_id"] == "OUTRA PONTA"
    assert "model.end_b_case_id" not in cfg.defaults or not cfg.defaults["model.end_b_case_id"]


def test_other_end_id_column_is_first_suggestion(tmp_path, fake_orc, legacy_inputs):
    proj = _project(tmp_path, legacy_inputs, overrides={"model.modelled_ends": 2.0}, other_row=True)
    wb = load_workbook(proj.workbook)
    ws = wb["Input Sheet"]
    col = ws.max_column + 1
    ws.cell(2, col, "Other End ID")
    ws.cell(3, col, "OUTRA PONTA X")
    wb.save(proj.workbook)
    out = tmp_path / "caso"
    with pytest.raises(MissingInputError):
        build_case(proj, CASE_ID, out_dir=str(out))
    item = _items(out / FILE_NAME)["model.end_b_case_id"]
    assert item["sugestoes"] == ["OUTRA PONTA X", "OUTRA PONTA"]
    assert item["valor"] is None   # sugestão não é aplicada sozinha


def test_set_config_values_updates_existing_override(tmp_path, legacy_inputs):
    cfg = default_config(legacy_inputs)
    cfg.overrides[CASE_ID] = {"environment.lda": 100.0}
    write_config(cfg, tmp_path / "c.xlsx")
    changes = set_config_values(tmp_path / "c.xlsx", {"environment.friction_axial": 0.35},
                                {CASE_ID: {"environment.lda": 200.0, "line.od": 0.3}}, comment="teste")
    assert len(changes) == 3
    back = load_config(tmp_path / "c.xlsx")
    assert back.overrides[CASE_ID] == {"environment.lda": 200.0, "line.od": 0.3}
    assert back.defaults["environment.friction_axial"] == 0.35
    ws = load_workbook(tmp_path / "c.xlsx")["Overrides"]
    assert sum(1 for r in ws.iter_rows(min_row=2, values_only=True) if r[0]) == 2
