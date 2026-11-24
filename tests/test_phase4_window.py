"""Fase 4: Status por UF, janela, sugestão (_13) e limpeza (_14)."""

import pandas as pd
import pytest

from cvdtools.dynamics.seastates import irregular_name
from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.post import window as W


@pytest.fixture
def case(legacy_inputs):
    return case_from_legacy_json(legacy_inputs)


def _results(case, table):
    """table: {(tp, dir): {hs: fator}} -> DataFrame como o do 'cvd post' (valores = fator × limite)."""
    rows = []
    for (tp, d), hs_map in table.items():
        for hs, factor in hs_map.items():
            row = {"Simulation Name": irregular_name(case.operation_name, tp, hs, d) + ".sim"}
            for c in case.criteria:
                if c.dynamic:
                    row[f"{W.CRIT_PREFIX}{c.name}"] = factor * float(c.limit) * (0.5 if c.name != "Axial Tension" else 1)
            rows.append(row)
    return pd.DataFrame(rows)


def test_criteria_postitems_methods(case):
    items = W.criteria_postitems(case)
    by = {i.label: i for i in items}
    assert by["Critério: Axial Tension"].method == "Max abs"
    assert by["Critério: Curvature BR"].method == "Range graph max abs"
    assert all(i.period == "Whole simulation" for i in items)


def test_parse_name_roundtrip(case):
    n = irregular_name(case.operation_name, 7.0, 1.25, -30.0)
    assert W.parse_name(n + ".sim") == (7.0, 1.25, -30.0)


def test_evaluate_and_window(case):
    df = _results(case, {
        (7.0, 0.0): {0.5: 0.6, 1.0: 0.9, 1.5: 1.2},   # limite 1.0
        (7.0, 30.0): {0.5: 0.4, 1.0: 0.7},            # nada reprovado -> ≥1
        (8.0, 0.0): {0.5: 1.3},                       # nada aprovado -> <0.5
        (8.0, 30.0): {0.5: 0.5, 1.0: 1.1, 1.5: 0.9},  # não monotônico
    })
    st = W.evaluate(df, case)
    assert st.loc[0, "Governa"] == "Axial Tension"
    assert list(st["Status"][:3]) == [W.APPROVED, W.APPROVED, W.REJECTED]
    lims = {(lim.tp, lim.direction): lim for lim in W.window_limits(st)}
    assert lims[(7.0, 0.0)].hs == 1.0 and lims[(7.0, 0.0)].bounded
    assert lims[(7.0, 30.0)].text == "≥1"
    assert lims[(8.0, 0.0)].hs is None and lims[(8.0, 0.0)].text == "<0.5"
    assert lims[(8.0, 30.0)].hs == 0.5 and "monotônico" in lims[(8.0, 30.0)].note
    grid = W.window_grid(list(lims.values()))
    assert grid.loc[7.0, 0.0] == 1.0 and grid.loc[7.0, 30.0] == "≥1"


def test_missing_value_rejects(case):
    df = _results(case, {(7.0, 0.0): {0.5: 0.5}})
    df.loc[0, "Critério: Axial Tension"] = None
    st = W.evaluate(df, case)
    assert st.loc[0, "Status"] == W.REJECTED


def test_suggest_like_13(case):
    df = _results(case, {
        (7.0, 0.0): {0.5: 0.6, 1.0: 0.9},             # sobe: 1.25, 1.5
        (8.0, 0.0): {1.0: 1.3},                       # desce: 0.75, 0.5
        (9.0, 0.0): {0.5: 0.5, 0.75: 1.2},            # 0.75 já testado -> nada
    })
    st = W.evaluate(df, case)
    s = W.suggest(st, 0.25, 2.5, 0.25, 2)
    assert (7.0, 1.25, 0.0) in s and (7.0, 1.5, 0.0) in s
    assert (8.0, 0.75, 0.0) in s and (8.0, 0.5, 0.0) in s
    assert not [x for x in s if x[0] == 9.0]
    grid = W.suggestion_grid(s)
    assert (0.0, 7.0, [1.25, 1.5]) in grid


def test_cleanup_like_14(case, tmp_path):
    df = _results(case, {(7.0, 0.0): {0.5: 0.6, 1.0: 0.9, 1.5: 1.2, 2.0: 1.4}})
    st = W.evaluate(df, case)
    names = W.cleanup_candidates(st)
    hs = sorted(W.parse_name(n)[1] for n in names)
    assert hs == [0.5, 2.0]
    for n in st["Simulation Name"]:
        (tmp_path / n).write_text("x")
    dry = W.move_unused(names, tmp_path, tmp_path / "Unused")
    assert len(dry) == 2 and not (tmp_path / "Unused").exists()
    W.move_unused(names, tmp_path, tmp_path / "Unused", apply=True)
    assert len(list((tmp_path / "Unused").glob("*.sim"))) == 2


def test_write_window(case, tmp_path):
    df = _results(case, {(7.0, 0.0): {0.5: 0.6, 1.0: 1.2}, (7.0, 30.0): {0.5: 0.6}})
    st = W.evaluate(df, case)
    out = tmp_path / "res.xlsx"
    df.to_excel(out, sheet_name="Logs", index=False)
    W.write_window(out, st, W.window_limits(st), W.suggest(st, 0.25, 2.5, 0.25), case, "GLF49 teste")
    from openpyxl import load_workbook

    wb = load_workbook(out)
    assert {"Logs", "Status", "Janela", "Sugestão", "Limites"} <= set(wb.sheetnames)
    ws = wb["Janela"]
    assert ws.cell(4, 4).value == "Direction [°]" and ws.cell(5, 5).value == 7.0
    assert ws.cell(5, 6).value == 0.5


def test_extract_max_abs(fake_orc):
    import types

    from cvdtools.post.extract import extract_item
    from cvdtools.schema import PostItem

    class Obj:
        def TimeHistory(self, var, period, extra):
            return [1.0, -5.0, 3.0]

        def RangeGraph(self, var, period, a, extra):
            return types.SimpleNamespace(Max=[2.0, 4.0], Min=[-7.0, 0.0], Mean=[0, 0])

    m = {"x": Obj()}

    class M(dict):
        general = types.SimpleNamespace(StageDuration=[10.0, 100.0])

    mm = M(m)
    assert extract_item(mm, PostItem("a", "Max abs", "x", "v", "", "Whole simulation"), fake_orc) == 5.0
    assert extract_item(mm, PostItem("b", "Range graph max abs", "x", "v", "", "Whole simulation"), fake_orc) == 7.0
