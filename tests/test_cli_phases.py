"""Linha de comando das fases 1 e 2 (com OrcFxAPI falso)."""

import json

from cvdtools.cli import main
from fake_orcfxapi import write_base_model
from helpers import CASE_ID, full_workbook


def _setup(tmp_path, monkeypatch, legacy_inputs):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "legacy.json").write_text(json.dumps(legacy_inputs), encoding="utf-8")
    (tmp_path / "00.Base").mkdir()
    write_base_model(tmp_path / "00.Base" / "base_input.dat")
    full_workbook(tmp_path / "geral.xlsx", legacy_inputs, overrides={"line.mbr": 2.27})
    assert main(["config", "init", "--out", "cvd_config.xlsx", "--from-json", "legacy.json"]) == 0
    assert main(["project", "init", "--code", "1327", "--workbook", "geral.xlsx", "--base-dir", "."]) == 0


def test_cli_flow(tmp_path, monkeypatch, capsys, fake_orc, legacy_inputs):
    _setup(tmp_path, monkeypatch, legacy_inputs)
    assert main(["inputs", "list"]) == 0
    assert CASE_ID in capsys.readouterr().out

    assert main(["inputs", "validate", "--out", "validacao.xlsx"]) == 0   # só avisos
    assert (tmp_path / "validacao.xlsx").is_file()

    assert main(["inputs", "show", CASE_ID]) == 0
    out = capsys.readouterr().out
    assert "environment.lda" in out and "input_sheet" in out

    assert main(["inputs", "diff-legacy", CASE_ID, "--json", "legacy.json"]) == 0
    assert "line.mbr" in capsys.readouterr().out

    assert main(["snapshot", CASE_ID, "--out", "caso", "--legacy-json"]) == 0
    legacy_back = json.loads((tmp_path / "caso" / "__INPUTS.json").read_text(encoding="utf-8"))
    assert legacy_back["LineTypeDims"]["xMinRadius"] == 2.27

    assert main(["build", CASE_ID, "--out", "caso"]) == 0
    out = capsys.readouterr().out
    assert "Checklist:" in out and "1327.GLF49_2CVD_MOBO_New_In.dat" in out
    assert main(["build", CASE_ID, "--out", "caso"]) == 2, "não sobrescreve sem --overwrite"
    assert main(["build", CASE_ID, "--out", "caso", "--overwrite", "--no-checklist"]) == 0

    assert main(["checklist", CASE_ID, "--model", "caso/1327.GLF49_2CVD_MOBO_New_In.dat"]) == 0
    assert (tmp_path / "caso" / "Checklist_GLF49_2CVD_MOBO_New_In_rev1.xlsx").is_file()

    assert main(["build-legacy", "--json", "legacy.json", "--base", "00.Base/base_input.dat", "--out", "leg.dat"]) == 0


def test_cli_without_project_gives_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["inputs", "list"]) == 2
    assert "cvd_project.json" in capsys.readouterr().err


def test_default_case_dir_without_analysis_root(tmp_path):
    from cvdtools.pipeline import default_case_dir
    from cvdtools.project import Project

    proj = Project(tmp_path / "cvd_project.json", "1327", tmp_path / "w.xlsx", tmp_path / "c.xlsx", None, tmp_path)
    assert default_case_dir(proj, "GLF27 2CVD MOBO New Out") == (tmp_path / "casos" / "GLF27_2CVD_MOBO_New_Out").resolve()
