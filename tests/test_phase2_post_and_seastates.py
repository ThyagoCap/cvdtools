"""Fase 2: extração de resultados e geração de mares (com OrcFxAPI falso)."""

import csv
import json

import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook

from cvdtools.dynamics.seastates import (choose_event, generate_irregular, generate_regular, irregular_name,
                                         jonswap_gamma, parse_wave_search)
from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.post.extract import extract_many, write_results
from cvdtools.schema import PostItem
from fake_orcfxapi import FakeModel, write_base_model


def _sim(path, results):
    write_base_model(path)
    data = json.loads(path.read_text())
    data["results"] = results
    path.write_text(json.dumps(data))
    return path


ITEMS = [
    PostItem("Wave Height (m)", "Get data", "Environment", "WaveHs"),
    PostItem("T static", "Static result", "Line", "Effective tension", "End A"),
    PostItem("T max", "Max", "Line", "Effective tension", "End A", "Whole simulation"),
    PostItem("T min static", "Min", "Line", "Effective tension", "End A", ""),
    PostItem("Clear min static", "Range graph min", "Line", "Seabed clearance", [0, 29]),
    PostItem("Clear max dyn", "Range graph max", "Line", "Seabed clearance", [0, 29], "Whole simulation"),
    PostItem("Quebrado", "Max", "Objeto Inexistente", "X"),
]


def test_extract_semantics(tmp_path, fake_orc):
    sims = tmp_path / "sims"
    sims.mkdir()
    _sim(sims / "a.sim", {"Line|Effective tension|EndA": [10.0, 30.0, 5.0], "Line|Seabed clearance|range": [2.0, 7.0, 1.0]})
    _sim(sims / "b.sim", {"Line|Effective tension|EndA": [11.0, 12.0]})
    (sims / "corrompido.sim").write_text("isto não é um modelo")
    df = extract_many(sorted(sims.glob("*.sim")), ITEMS, workers=1, progress=False)
    a = df[df["Simulation Name"] == "a.sim"].iloc[0]
    assert a["Wave Height (m)"] == 1.0
    assert a["T static"] == 10.0          # 1º ponto = estática
    assert a["T max"] == 30.0             # simulação inteira
    assert a["T min static"] == 10.0      # Min com período vazio = estática
    assert a["Clear min static"] == 2.0   # instante -T0
    assert a["Clear max dyn"] == 7.0
    assert pd.isna(a["Quebrado"]) and "Quebrado" in a["_errors"]
    assert list(df.columns[:len(ITEMS) + 1]) == ["Simulation Name"] + [i.label for i in ITEMS]
    assert [p for p, _ in df.attrs["failed"]] == [str(sims / "corrompido.sim")]


def test_write_results_keeps_other_sheets(tmp_path):
    xlsx = tmp_path / "res.xlsx"
    wb = Workbook()
    wb.active.title = "Dynamics_Tabela_Pivot"
    wb.active["A1"] = "pivot do usuário"
    wb.save(xlsx)
    write_results(pd.DataFrame({"Simulation Name": ["a.sim"], "x": [1.0]}), xlsx)
    names = load_workbook(xlsx).sheetnames
    assert "Dynamics_Tabela_Pivot" in names and "Logs" in names
    assert list(tmp_path.glob("res_*.csv")), "backup CSV"


def test_items_from_legacy_json_cover_all(legacy_inputs):
    items = case_from_legacy_json(legacy_inputs).postproc
    assert len(items) == len(legacy_inputs["postProcess"])
    assert sum(1 for i in items if i.is_static) == sum(
        1 for v in legacy_inputs["postProcess"].values() if v["Simulation Period"] == "")


# --------------------------------------------------------------------------- mares

def test_wave_search_parsing_is_numeric():
    text = "Largest rise at 1050,25 s\nLargest fall at 980,5 s\n"
    rise, fall = parse_wave_search(text)
    assert (rise, fall) == (1050.25, 980.5)
    # Como texto, "980,5" > "1050,25" e o script antigo escolheria a descida; o correto é a subida (mais tarde)
    assert "980,5" > "1050,25"
    assert choose_event(rise, fall) == ("rise", 1050.25)
    with pytest.raises(ValueError):
        parse_wave_search("nada aqui")


def test_names_match_old_scripts():
    assert irregular_name("1327.2CVD_no_flut", 5.0, 3.0, 0.0) == "1327.2CVD_no_flut_dyn5.00Tp_3.00Hs_0.00a"
    assert jonswap_gamma(10.0) == pytest.approx(6.4 * 10 ** -0.491)


def test_generate_irregular(tmp_path, fake_orc, legacy_inputs):
    base = write_base_model(tmp_path / "dyn.dat")
    case = case_from_legacy_json(legacy_inputs)
    files = generate_irregular(case, base, [(30.0, 8.0, [1.0, 2.0])], tmp_path / "out", workers=1)
    assert [f.file for f in files] == [
        "1327.2CVD_no_flut_dyn8.00Tp_1.00Hs_30.00a.dat", "1327.2CVD_no_flut_dyn8.00Tp_2.00Hs_30.00a.dat"]
    o = FakeModel(str(tmp_path / "out" / files[1].file)).objects
    env, gen = o["Environment"], o["General"]
    assert env["WaveDirection"] == 30.0 + 30.0          # relativa + aproamento da embarcação
    assert env["WaveType"] == "JONSWAP" and env["WaveHs"] == 2.0
    assert env["WaveTimeOrigin"] == pytest.approx(-(902.0 - 300.0))   # descida (mais tarde) - 300 s
    assert gen["StageDuration"] == [24.0, 600.0]
    assert gen["ImplicitUseVariableTimeStep"] == "Yes"  # JSON antigo: useVariableTimeStep = "True"
    rows = list(csv.DictReader(open(tmp_path / "out" / "manifest.csv", encoding="utf-8")))
    assert [r["event"] for r in rows] == ["fall", "fall"]
    assert len((tmp_path / "out" / "batch_list.txt").read_text().split()) == 2
    with pytest.raises(FileExistsError):
        generate_irregular(case, base, [(30.0, 8.0, [1.0])], tmp_path / "out", workers=1)
    generate_irregular(case, base, [(60.0, 8.0, [1.0])], tmp_path / "out", workers=1)
    rows = list(csv.DictReader(open(tmp_path / "out" / "manifest.csv", encoding="utf-8")))
    assert len(rows) == 3, "manifest acumula as gerações"


def test_generate_regular(tmp_path, fake_orc, legacy_inputs):
    base = write_base_model(tmp_path / "dyn.dat")
    case = case_from_legacy_json(legacy_inputs)
    files = generate_regular(case, base, [(0.0, 6.0, [1.5])], tmp_path / "reg")
    o = FakeModel(str(tmp_path / "reg" / files[0].file)).objects
    assert files[0].file == "1327.2CVD_no_flut_dyn_6.00Tp_1.50Hs_0.0a.yml"
    assert o["Environment"]["WaveHeight"] == pytest.approx(1.9 * 1.5)
    assert o["General"]["StageDuration"] == [18.0, 30.0]


def test_duplicate_grid_rows_generated_once(tmp_path, fake_orc, legacy_inputs, caplog):
    from cvdtools.dynamics.seastates import unique_states

    grid = [(0.0, 5.0, [1.5]), (0.0, 5.0, [1.5]), (0.0, 5.0, [2.0]), (30.0, 5.0, [1.5])]
    uniq, dup = unique_states(grid)
    assert len(uniq) == 3 and dup == [(0.0, 5.0, 1.5)]
    case = case_from_legacy_json(legacy_inputs)
    base = write_base_model(tmp_path / "base_dyn.dat")
    files = generate_irregular(case, base, grid, tmp_path / "out", workers=1)
    assert len(files) == 3 == len(list((tmp_path / "out").glob("*.dat")))
    assert "repetida" in caplog.text
