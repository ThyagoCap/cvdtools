"""v0.7.4: extração robusta para a campanha real (range graph com Extra 'End A', estado da simulação,
Status só pelos critérios, .dat sem .sim e 'cvd post --window')."""

import json
from pathlib import Path

import pandas as pd

from cvdtools.dynamics.seastates import irregular_name
from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.post import window as W
from cvdtools.post.extract import COMPLETE_COLUMN, STATE_COLUMN, extract_many, item_notes, missing_sims
from cvdtools.schema import PostItem
from fake_orcfxapi import write_base_model


def _sim(path, results, **extra):
    write_base_model(path)
    data = json.loads(path.read_text())
    data["results"] = results
    data.update(extra)
    path.write_text(json.dumps(data))
    return path


def test_range_graph_with_end_extra_uses_whole_line(tmp_path, fake_orc):
    """Critério 'Curvature' (Range Graph Max, Extra End A) quebrava o 4º argumento do RangeGraph."""
    item = PostItem("Critério: Curvature", "Range graph max abs", "Line", "Curvature", "End A", "Whole simulation")
    pair = PostItem("Clear", "Range graph max", "Line", "Seabed clearance", [0, 29], "Whole simulation")
    _sim(tmp_path / "a.sim", {"Line|Curvature|range": [0.1, -0.4, 0.3], "Line|Seabed clearance|range": [1.0, 2.0]})
    df = extract_many([tmp_path / "a.sim"], [item, pair], workers=1, progress=False)
    row = df.iloc[0]
    assert row["Critério: Curvature"] == 0.4
    assert row["Clear"] == 2.0
    assert "_errors" not in df.columns or pd.isna(row.get("_errors"))
    notes = item_notes([item, pair])
    assert len(notes) == 1 and "linha inteira" in notes[0]


def test_time_history_with_arclength_pair_is_an_item_error(tmp_path, fake_orc):
    bad = PostItem("T", "Max", "Line", "Effective tension", [0, 29], "Whole simulation")
    _sim(tmp_path / "a.sim", {})
    df = extract_many([tmp_path / "a.sim"], [bad], workers=1, progress=False)
    assert pd.isna(df.iloc[0]["T"]) and "só vale para os métodos 'Range graph'" in df.iloc[0]["_errors"]


def test_state_columns_and_status(tmp_path, fake_orc, legacy_inputs):
    case = case_from_legacy_json(legacy_inputs)
    items = W.criteria_postitems(case) + [PostItem("Info quebrada", "Max", "Objeto Inexistente", "X")]
    res = {f"{c.obj}|{c.variable}|{'EndA' if c.extra == 'End A' else 'None'}": [0.1] for c in case.criteria}
    res.update({f"{c.obj}|{c.variable}|range": [0.1] for c in case.criteria})
    name = irregular_name(case.operation_name, 7.0, 1.0, 0.0)
    _sim(tmp_path / f"{name}.sim", res)
    name2 = irregular_name(case.operation_name, 7.0, 1.5, 0.0)
    _sim(tmp_path / f"{name2}.sim", res, state="SimulationStoppedUnstable", complete=False)
    df = extract_many(sorted(tmp_path.glob("*.sim")), items, workers=1, progress=False)
    assert set(df[STATE_COLUMN]) == {"SimulationStopped", "SimulationStoppedUnstable"}
    st = W.evaluate(df, case)
    by = st.set_index("Hs (m)")
    # coluna informativa quebrada não reprova; simulação instável reprova
    assert by.loc[1.0, "Status"] == W.APPROVED, by.loc[1.0, "Motivo"]
    assert by.loc[1.5, "Status"] == W.REJECTED and by.loc[1.5, "Motivo"] == "simulação instável"
    assert bool(by.loc[1.0, COMPLETE_COLUMN]) is True


def test_missing_criterion_value_rejects_with_reason(legacy_inputs):
    case = case_from_legacy_json(legacy_inputs)
    row = {"Simulation Name": irregular_name(case.operation_name, 7.0, 1.0, 0.0) + ".sim"}
    for c in case.criteria:
        if c.dynamic:
            row[f"{W.CRIT_PREFIX}{c.name}"] = 0.1 * float(c.limit)
    row[f"{W.CRIT_PREFIX}Curvature"] = None
    st = W.evaluate(pd.DataFrame([row]), case)
    assert st.loc[0, "Status"] == W.REJECTED and st.loc[0, "Motivo"] == "critério sem valor: Curvature"


def test_missing_sims(tmp_path):
    for n in ("a.dat", "b.dat", "c.dat", "a.sim", "C.SIM", "outro.txt"):
        (tmp_path / n).write_text("x")
    assert missing_sims(tmp_path) == ["b.dat"]


def test_uf_pivot_and_window_sheets(tmp_path, legacy_inputs):
    case = case_from_legacy_json(legacy_inputs)
    rows = []
    for hs, f in ((1.0, 0.5), (1.5, 0.95), (2.0, 1.3)):
        row = {"Simulation Name": irregular_name(case.operation_name, 7.0, hs, 30.0) + ".sim"}
        for c in case.criteria:
            if c.dynamic:
                row[f"{W.CRIT_PREFIX}{c.name}"] = f * float(c.limit) * (1 if c.name == "Axial Tension" else 0.5)
        rows.append(row)
    st = W.evaluate(pd.DataFrame(rows), case)
    piv = W.uf_pivot(st)
    assert piv.loc[(7.0, 2.0), 30.0] == 1.3
    out = W.write_window(tmp_path / "r.xlsx", st, W.window_limits(st), [], case)
    from openpyxl import load_workbook
    wb = load_workbook(out)
    assert {"Janela", "UF", "Limites", "Status", "Sugestão"} <= set(wb.sheetnames)
    fills = {c.value: c.fill.fgColor.rgb for row in wb["UF"].iter_rows(min_row=2, min_col=3) for c in row
             if isinstance(c.value, float)}
    assert fills[1.3].endswith("FFC7CE") and fills[0.95].endswith("FFEB9C") and fills[0.5].endswith("C6EFCE")


def test_cli_post_window_end_to_end(tmp_path, fake_orc, legacy_inputs, capsys):
    from cvdtools import cli

    case = case_from_legacy_json(legacy_inputs)
    res = {f"{c.obj}|{c.variable}|{'EndA' if c.extra == 'End A' else 'None'}": [0.1 * float(c.limit)]
           for c in case.criteria}
    res.update({f"{c.obj}|{c.variable}|range": [0.1 * float(c.limit)] for c in case.criteria})
    sims = tmp_path / "02.Dynamic"
    sims.mkdir()
    for hs in (1.0, 1.5):
        name = irregular_name(case.operation_name, 7.0, hs, 0.0)
        _sim(sims / f"{name}.sim", res)
        (sims / f"{name}.dat").write_text("x")
    (sims / (irregular_name(case.operation_name, 7.0, 2.0, 0.0) + ".dat")).write_text("x")   # não rodou
    js = tmp_path / "inputs.json"
    js.write_text(json.dumps(legacy_inputs), encoding="utf-8")
    rc = cli.main(["post", "--sims", str(sims), "--json", str(js), "--window", "--workers", "1"])
    out = capsys.readouterr().out
    assert rc == 0, out
    xlsx = next(sims.glob("*_resultados.xlsx"))
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    assert {"Logs", "Notas", "Janela", "UF", "Status"} <= set(wb.sheetnames)
    notas = [r for r in wb["Notas"].iter_rows(values_only=True)]
    assert any(r[0] == "Sem .sim (não rodou ou falhou)" and r[1].endswith("2.00Hs_0.00a.dat") for r in notas[1:])
    assert "2 aprovada(s), 0 reprovada(s)" in out


def test_manifest_drops_files_that_no_longer_exist(tmp_path):
    from cvdtools.dynamics.seastates import SeaStateFile, write_manifest

    fields = SeaStateFile.__dataclass_fields__
    def sf(name):
        return SeaStateFile(**{k: (name if k == "file" else None) for k in fields})
    (tmp_path / "a.dat").write_text("x")
    (tmp_path / "b.dat").write_text("x")
    write_manifest([sf("a.dat"), sf("b.dat")], tmp_path)
    (tmp_path / "b.dat").unlink()            # rodada anterior apagada
    (tmp_path / "c.dat").write_text("x")
    write_manifest([sf("c.dat")], tmp_path)
    listed = (tmp_path / "batch_list.txt").read_text().splitlines()
    assert [Path(p).name for p in listed if p.strip()] == ["a.dat", "c.dat"]


def test_console_log_follows_current_stderr(monkeypatch):
    """O handler do console não fica preso a um stderr fechado (ruído 'Logging error' no pytest)."""
    import io
    import sys

    from cvdtools.log import log, setup_logging

    first = io.StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    setup_logging()
    first.close()
    second = io.StringIO()
    monkeypatch.setattr(sys, "stderr", second)
    log.info("mensagem depois da troca")
    assert "mensagem depois da troca" in second.getvalue()
