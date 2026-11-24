"""Fase 6: triagem semi-dinâmica, resposta da embarcação e janela prevista."""

import math

import pandas as pd
import pytest

from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.post import window as W
from cvdtools.screening import predict as P
from cvdtools.screening import semidynamic as S
from cvdtools.screening import vessel as V
from fake_orcfxapi import FakeModel, write_base_model


@pytest.fixture
def case(legacy_inputs):
    return case_from_legacy_json(legacy_inputs)


def test_payout_matches_legacy_law():
    t, v = S.payout_history(10.0, 0.5, 0.01)
    assert t[:2] == [-10.0, -0.01] and t[2] == 0.0
    assert v[2] == pytest.approx(0.5 * 2 * math.pi / 10)
    i = t.index(7.5)
    assert v[i] != 0 and v[i + 1] == 0.0            # 3/4 de ciclo
    assert t[-1] == pytest.approx(20.0)
    # deslocamento integrado no 1º quarto de ciclo = amplitude
    disp = sum(v[k] * 0.01 for k in range(2, t.index(2.5) + 2))
    assert disp == pytest.approx(0.5, rel=0.02)


def test_name_roundtrip(case):
    n = S.semidynamic_name(case.operation_name, 7.0, 0.8)
    assert S.parse_name(n + ".sim") == (7.0, 0.8)


def test_generate(tmp_path, fake_orc, case):
    base = write_base_model(tmp_path / "b.dat")
    m = FakeModel(str(base))
    m.objects["PayoutRate"] = {"IndependentValue": [], "DependentValue": []}
    m.SaveData(str(base))
    files = S.generate(case, base, tmp_path / "semi", tps=[7.0], amps=[0.5, 1.0])
    assert len(files) == 2
    o = FakeModel(str(files[1])).objects
    assert o["General"]["StageDuration"][1] == 14.0
    assert max(o["PayoutRate"]["DependentValue"]) == pytest.approx(2 * math.pi / 7)


def _semi_results(case, ufs: dict):
    rows = []
    for (tp, amp), uf in ufs.items():
        row = {"Simulation Name": S.semidynamic_name(case.operation_name, tp, amp) + ".sim"}
        for c in case.criteria:
            if c.dynamic:
                row[f"{W.CRIT_PREFIX}{c.name}"] = (uf if c.name == "Axial Tension" else 0.1) * float(c.limit)
        rows.append(row)
    return pd.DataFrame(rows)


def test_critical_amplitude(case):
    df = _semi_results(case, {(7.0, 0.2): 0.4, (7.0, 0.4): 0.8, (7.0, 0.6): 1.2,
                              (8.0, 0.2): 1.5, (8.0, 0.4): 2.0,
                              (9.0, 0.2): 0.2, (9.0, 0.4): 0.3, (9.0, 0.6): 0.5})
    st = S.evaluate(df, case)
    crits = {c.tp: c for c in S.critical_amplitudes(st, 1.0)}
    assert crits[7.0].amplitude == pytest.approx(0.5) and crits[7.0].governing == "Axial Tension"
    assert crits[8.0].status == "ACIMA" and crits[8.0].amplitude is None
    assert crits[9.0].status == "ABAIXO"
    assert crits[7.0].poly2_r2 == pytest.approx(1.0)


def test_critical_csv_roundtrip(tmp_path):
    crits = [S.CriticalAmplitude(7.0, 0.5, "OK", "x"), S.CriticalAmplitude(8.0, None, "ACIMA")]
    back = S.read_critical(S.write_critical(tmp_path / "c.csv", crits))
    assert back[0].amplitude == 0.5 and back[1].amplitude is None and back[1].status == "ACIMA"


def _all_results_sheet():
    """Imita a aba 'All results' do NsgOrcFx: linha 2 marca o bloco, linha 3 os GLs."""
    header1 = [None] * 12
    header1[4] = "Significant Amplitude"
    header1[6] = "Maximum Amplitude"
    header2 = [None, "Dir", "Hs", "Tp", "Surge [m]", "Sway [m]", "Surge [m]", "Sway [m]", "Heave [m]", "Roll", "", ""]
    rows = [[None] * 12, header1, header2]
    for d in (0.0, 90.0):
        for hs in (1.0, 2.0):
            for k, tp in enumerate((7.0, 8.0)):
                r = [None] * 12
                if k == 0:
                    r[1], r[2] = d, hs
                r[3] = tp
                r[8] = -(0.4 if d == 0 else 0.8) * hs * (1 if tp == 7 else 1.25)
                rows.append(r)
    return pd.DataFrame(rows)


def test_parse_all_results_and_ratio():
    df = V.parse_all_results(_all_results_sheet())
    assert len(df) == 8
    r = V.response_per_hs(df)
    assert r[(0.0, 7.0)] == pytest.approx(0.4) and r[(90.0, 8.0)] == pytest.approx(1.0)


def test_predict_and_seed():
    crits = [S.CriticalAmplitude(7.0, 0.5, "OK"), S.CriticalAmplitude(9.0, 0.9, "OK"),
             S.CriticalAmplitude(10.0, None, "ACIMA")]
    resp = {(0.0, 7.0): 0.4, (0.0, 8.0): 0.5, (0.0, 10.0): 0.3, (0.0, 12.0): 0.3}
    pred = {(p.direction, p.tp): p for p in P.predict(crits, resp, 0.25, 2.5)}
    assert pred[(0.0, 7.0)].hs == 1.25                       # 0.5/0.4 = 1.25
    assert pred[(0.0, 8.0)].hs == 1.25 and "interpolada" in pred[(0.0, 8.0)].note   # 0.7/0.5 = 1.4 -> 1.25
    assert pred[(0.0, 10.0)].hs == 0.0
    assert pred[(0.0, 12.0)].hs is None
    grid = P.seed_grid(list(pred.values()), 0.25, 0.25, 2.5)
    assert (0.0, 7.0, [1.0, 1.25, 1.5]) in grid
    assert (0.0, 10.0, [0.25]) in grid


def test_write_prediction(tmp_path):
    crits = [S.CriticalAmplitude(7.0, 0.5, "OK")]
    pred = P.predict(crits, {(0.0, 7.0): 0.4, (30.0, 7.0): 0.5}, 0.25, 2.5)
    path = P.write_prediction(tmp_path / "p.xlsx", pred, crits, "GLF", "2nd end CVD")
    from openpyxl import load_workbook

    ws = load_workbook(path)["Janela prevista"]
    assert ws.cell(5, 5).value == 7.0 and ws.cell(5, 6).value == 1.25 and ws.cell(5, 7).value == 1.0
