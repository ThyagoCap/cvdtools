"""0.9.0: busca adaptativa do Hs limite (irregulares no domínio do tempo) e FD com rigidez histerética."""

import json
import math

import pandas as pd
import pytest

from cvdtools.post import adaptive as A
from cvdtools.post.window import APPROVED, REJECTED

HS_MIN, HS_MAX, STEP, UF = 0.5, 3.5, 0.25, 1.0


def _df(rows):
    """rows: (Tp, Dir, Hs, UF ou None, aprovado)."""
    return pd.DataFrame([{"Tp (s)": t, "Dir (°)": d, "Hs (m)": h, "UF máx": (math.nan if u is None else u),
                          "Status": APPROVED if ok else REJECTED, "Simulation Name": f"s{t}_{d}_{h}"}
                         for t, d, h, u, ok in rows])


def _by_pair(searches):
    return {(s.tp, s.direction): s for s in searches}


def test_estimate_limit_interpolates_and_scales():
    est, src = A.estimate_limit([(1.0, 0.5, True), (2.0, 1.2, False)], 1.0)
    assert est == pytest.approx(1.0 + 0.5 / 0.7) and "interpolação" in src
    est, src = A.estimate_limit([(1.0, 0.5, True)], 1.0)
    assert est == pytest.approx(2.0) and "proporcional" in src
    # dois aprovados: extrapola pela reta dos dois mais próximos do limite
    est, _ = A.estimate_limit([(1.0, 0.4, True), (1.5, 0.6, True)], 1.0)
    assert est == pytest.approx(2.5)
    assert A.estimate_limit([(2.0, math.inf, False)], 1.0) == (None, "")


def test_plan_states_and_next_hs():
    df = _df([
        (8, 0, 1.0, 0.5, True),                              # aberto: estimativa 2.0 -> 2.0 e 2.25
        (9, 0, 1.5, 0.9, True), (9, 0, 1.75, 1.05, False),   # fechado
        (10, 0, 3.5, 0.8, True),                             # Hs máx aprovada
        (11, 0, 0.5, 1.4, False),                            # reprova já em Hs mín
        (12, 0, 2.0, None, False),                           # só instável: bisseção 0.5..2.0
    ])
    s = _by_pair(A.plan(df, HS_MIN, HS_MAX, STEP, UF))
    assert s[(8, 0)].status == A.OPEN and s[(8, 0)].next == [2.0, 2.25]
    assert s[(9, 0)].status == A.CLOSED and s[(9, 0)].next == []
    assert s[(10, 0)].status == A.ABOVE_MAX
    assert s[(11, 0)].status == A.BELOW_MIN
    assert s[(12, 0)].source.startswith("bisseção") and s[(12, 0)].next == [1.25, 1.5]
    assert sorted(A.suggestions(list(s.values()))) == [(8, 2.0, 0), (8, 2.25, 0), (12, 1.25, 0), (12, 1.5, 0)]


def test_plan_never_repeats_or_leaves_bracket():
    # estimativa (UF proporcional) cairia acima do menor reprovado: só sobra a grade dentro do intervalo
    df = _df([(8, 0, 1.0, 0.2, True), (8, 0, 1.5, None, False)])
    s = A.plan(df, HS_MIN, HS_MAX, STEP, UF)[0]
    assert s.next == [1.25]
    df = _df([(8, 0, 1.0, 0.2, True), (8, 0, 1.25, None, False)])
    assert A.plan(df, HS_MIN, HS_MAX, STEP, UF)[0].status == A.CLOSED


def test_seed_neighbour_and_mares_pairs():
    df = _df([(8, 0, 1.0, 0.5, True)])
    s = _by_pair(A.plan(df, HS_MIN, HS_MAX, STEP, UF, seeds={(10.0, 90.0): 1.9},
                        pairs=[(9.0, 0.0), (9.0, 180.0)]))
    assert s[(10, 90)].source == "previsão" and s[(10, 90)].next == [2.0, 2.25]
    assert s[(9, 0)].source == "vizinho (Tp 8 s)" and s[(9, 0)].estimate == pytest.approx(2.0)
    assert s[(9, 180)].source == "meio da faixa"
    one = _by_pair(A.plan(df, HS_MIN, HS_MAX, STEP, UF, per_pair=1))
    assert one[(8, 0)].next == [2.0]


@pytest.mark.parametrize("uf_of, seed, expected", [
    (lambda hs: 0.3 + 0.35 * hs, 1.0, (2.0, 2.25)),                    # linear, previsão ruim
    (lambda hs: 0.12 * hs ** 2 + 0.1 * hs, 3.0, (2.5, 2.75)),          # rigidez caindo com a amplitude
    (lambda hs: 0.9 * hs - 0.2, None, (1.25, 1.5)),                    # sem previsão
])
def test_search_converges_with_few_runs(uf_of, seed, expected):
    """Laço gerar -> rodar -> avaliar: fecha o intervalo da grade em poucas simulações."""
    rows = []
    seeds = {(10.0, 45.0): seed} if seed else None
    pairs = [(10.0, 45.0)]
    for _ in range(10):
        df = _df(rows) if rows else None
        s = A.plan(df, HS_MIN, HS_MAX, STEP, UF, seeds=seeds, pairs=pairs)[0]
        if not s.next:
            break
        for hs in s.next:
            u = uf_of(hs)
            rows.append((10.0, 45.0, hs, u, u <= UF))
    assert s.status == A.CLOSED
    assert (s.hs_ok, s.hs_bad) == expected
    assert len(rows) <= 6, rows


def test_search_table_columns():
    t = A.search_table(A.plan(_df([(8, 0, 1.0, 0.5, True)]), HS_MIN, HS_MAX, STEP, UF))
    assert {"Situação", "Hs limite estimado (m)", "Próximos Hs (m)", "Estimativa por"} <= set(t.columns)
    assert t.iloc[0]["Próximos Hs (m)"] == "2; 2.25"


def test_cli_window_adaptive_and_first_round(tmp_path, fake_orc, legacy_inputs, capsys, monkeypatch):
    """'cvd window' usa a busca adaptativa (aba Busca) e, sem --results, faz a 1ª rodada a partir do --seed."""
    from cvdtools import cli
    from cvdtools.io.legacy import case_from_legacy_json

    case = case_from_legacy_json(legacy_inputs)
    monkeypatch.setattr(cli, "_project", lambda args: None)
    import cvdtools.pipeline as pipeline
    monkeypatch.setattr(pipeline, "load_case", lambda proj, cid: case)

    crit = [c for c in case.criteria if c.dynamic and isinstance(c.limit, (int, float))]
    rows = []
    for hs, frac in ((1.0, 0.5), (1.5, 0.75)):
        r = {"Simulation Name": f"{case.operation_name}_dyn7.00Tp_{hs:.2f}Hs_0.00a"}
        for c in crit:
            r[f"Critério: {c.name}"] = frac * float(c.limit)
        rows.append(r)
    res = tmp_path / "res.xlsx"
    pd.DataFrame(rows).to_excel(res, sheet_name="Logs", index=False)
    assert cli.main(["window", "X", "--results", str(res), "--hs-min", "0.5", "--hs-max", "3.5",
                     "--hs-step", "0.25", "--uf-limit", "1.0"]) == 0
    out = capsys.readouterr().out
    assert "Busca adaptativa: 1 aberto" in out
    sug = pd.read_excel(res, sheet_name="Sugestão")
    assert sorted(sug["Hs (m)"]) == [2.0, 2.25]          # UF = 0.5 Hs -> limite em 2.0

    pred = tmp_path / "prev.xlsx"
    pd.DataFrame([{"Direção (°)": 90.0, "Tp (s)": 9.0, "Hs limite (contínuo, m)": 1.6}]).to_excel(
        pred, sheet_name="Previsão", index=False)
    assert cli.main(["window", "X", "--seed", str(pred), "--hs-min", "0.5", "--hs-max", "3.5",
                     "--hs-step", "0.25", "--uf-limit", "1.0"]) == 0
    first = pd.read_excel(tmp_path / "prev_busca.xlsx", sheet_name="Sugestão")
    assert sorted(first["Hs (m)"]) == [1.5, 1.75]
    assert cli.main(["window", "X"]) == 1


# --------------------------------------------------------------------------- FD com histerese

def test_secant_ei():
    from cvdtools.screening.frequency import secant_ei

    x, y = [0.0, 0.01, 0.1], [0.0, 50.0, 140.0]
    assert secant_ei(x, y, 0.01) == pytest.approx(5000.0)
    assert secant_ei(x, y, 0.05) == pytest.approx((50 + 0.04 * 90 / 0.09) / 0.05)
    assert secant_ei(x, y, 0.2) == pytest.approx((140 + 0.1 * 1000) / 0.2)       # extrapola o último trecho
    with pytest.raises(ValueError):
        secant_ei(x, y, 0.0)


def _hyst_model(path):
    from fake_orcfxapi import write_base_model

    write_base_model(path)
    data = json.loads(path.read_text())
    o = data["objects"]
    o["Line"]["LineType"] = ["EndFitting Type", "Bend Restrictor Type", "Line Type", "Line Type"]
    o["Line Type"]["EIx"] = "Line Hysteresis"
    o["Line Hysteresis"] = {"_type": "BendingStiffness", "IndependentValue": [0.0, 0.01, 0.1],
                            "DependentValue": [0.0, 50.0, 140.0], "Hysteretic": "Yes"}
    path.write_text(json.dumps(data))
    return path


def test_fake_fd_rejects_hysteresis_like_orcaflex(tmp_path, fake_orc):
    from fake_orcfxapi import FakeModel

    m = FakeModel(str(_hyst_model(tmp_path / "dyn.dat")))
    m.RunSimulation()                                            # domínio do tempo: ok
    m.objects["General"]["DynamicsSolutionMethod"] = "Frequency domain"
    with pytest.raises(RuntimeError, match="hysteretic bend stiffness, line type 'Line Type'"):
        m.RunSimulation()


def test_fd_hysteresis_fails_early_and_secant_runs(tmp_path, fake_orc, legacy_inputs):
    from dataclasses import asdict

    from cvdtools.io.legacy import case_from_legacy_json
    from cvdtools.screening.frequency import (HystereticFDError, bend_curves, criteria_for_fd, fd_pair,
                                              linearise_bend_stiffness, run_fd_screening)
    from fake_orcfxapi import FakeModel
    from test_screening_fd import FDPhysics

    base = _hyst_model(tmp_path / "dyn.dat")
    m = FakeModel(str(base))
    assert bend_curves(m, "Line") == [("Line Type", "Line Hysteresis", True)]
    notes = linearise_bend_stiffness(m, "Line", 0.01)
    assert m.objects["Line Type"]["EIx"] == pytest.approx(5000.0) and "histerética" in notes[0]

    runs = []

    class Counting(FDPhysics):
        def RunSimulation(self):
            runs.append(1)
            super().RunSimulation()

    fake_orc.Model = Counting
    case = case_from_legacy_json(legacy_inputs)
    case.roles["LINE"] = "Line"
    grid = [(0.0, 10.0, [1.5, 2.0]), (30.0, 10.0, [1.5])]
    with pytest.raises(HystereticFDError, match="--bend-stiffness secant"):
        run_fd_screening(case, base, grid, 2.0)
    assert runs == []                                             # falhou antes de qualquer análise
    with pytest.raises(ValueError, match="secant-curvature"):
        run_fd_screening(case, base, grid, 2.0, bend="secant")

    res = run_fd_screening(case, base, grid, 2.0, bend="secant", secant_curvature=0.05)
    assert len(res) == 2 and all(r["hs_limit"] is not None for r in res) and runs
    assert any("EI secante" in n for n in res[0]["notes"])

    crits = [asdict(c) for c in criteria_for_fd(case)]
    r = fd_pair(str(base), crits, "Island Performer", 0.0, 10.0, [2.0], 2.0, 3.5, 3.0, 1.0, line="Line")
    assert r["hs_limit"] is None and "secant" in r["error"] and r["points"] == []


def test_cli_screen_fd_stops_with_hysteresis(tmp_path, fake_orc, legacy_inputs, capsys, monkeypatch):
    import types

    from cvdtools import cli
    from cvdtools.io.legacy import case_from_legacy_json
    from test_screening_fd import FDPhysics

    fake_orc.Model = FDPhysics
    case = case_from_legacy_json(legacy_inputs)
    case.roles["LINE"] = "Line"
    monkeypatch.setattr(cli, "_project", lambda args: types.SimpleNamespace(config="x"))
    import cvdtools.io.config as config
    import cvdtools.pipeline as pipeline
    monkeypatch.setattr(pipeline, "load_case", lambda proj, cid: case)
    monkeypatch.setattr(config, "load_config", lambda path: types.SimpleNamespace(
        sea_grid=[(d, tp, [1.5, 2.0]) for d in (0.0, 90.0) for tp in (8.0, 10.0)]))
    base = _hyst_model(tmp_path / "dyn.dat")
    rc = cli.main(["screen", "fd", "X", "--base-dyn", str(base), "--out", str(tmp_path / "fd.xlsx"), "--workers", "1"])
    out = capsys.readouterr().out
    assert rc == 2 and "2 direção(ões), 2 Tp, 4 par(es), 8 Hs" in out and "--bend-stiffness secant" in out
    assert not (tmp_path / "fd.xlsx").exists()


def test_config_mares_formulas_without_cached_values(tmp_path, legacy_inputs):
    """GLF27: aba Mares com fórmulas, salva pelo openpyxl (config upgrade) -> sem valores guardados."""
    from openpyxl import load_workbook

    from cvdtools.io.config import default_config, load_config, write_config

    cfg = default_config(legacy_inputs)
    path = write_config(cfg, tmp_path / "cvd_config.xlsx") or tmp_path / "cvd_config.xlsx"
    wb = load_workbook(path)
    ws = wb["Mares"]
    ws.delete_rows(2, ws.max_row)
    for i, tp in enumerate((5, 6)):
        ws.append([0, tp, 1.5])                                  # linhas 2-3
    for i in range(2):
        ws.append([f"=A{2 + i}", f"=B{2 + i}", f"=C{2 + i}+0.5"])  # linhas 4-5: Hs 2.0
    for i in range(4):
        ws.append([f"=A{2 + i}+30", f"=$B{2 + i}", f"=C{2 + i}"])  # linhas 6-9: direção 30
    ws.append(["=A2+30", "=B2", "=SUM(C2:C3)"])                   # função: não suportada fora do Excel
    wb.save(path)
    with pytest.raises(ValueError, match="Abra e salve a planilha no Excel"):
        load_config(path)
    wb = load_workbook(path)
    wb["Mares"].delete_rows(10, 1)
    wb.save(path)
    grid = load_config(path).sea_grid
    assert sorted((d, tp, h[0]) for d, tp, h in grid) == sorted(
        [(0.0, 5.0, 1.5), (0.0, 6.0, 1.5), (0.0, 5.0, 2.0), (0.0, 6.0, 2.0),
         (30.0, 5.0, 1.5), (30.0, 6.0, 1.5), (30.0, 5.0, 2.0), (30.0, 6.0, 2.0)])


def test_write_fd_has_notes_sheet(tmp_path):
    from openpyxl import load_workbook

    from cvdtools.screening.frequency import write_fd

    res = [{"direction": 0.0, "tp": 10.0, "hs_limit": 2.1, "points": [], "error": "", "notes": ["nota A"]},
           {"direction": 30.0, "tp": 10.0, "hs_limit": 1.9, "points": [], "error": "", "notes": ["nota A"]}]
    path = write_fd(tmp_path / "fd.xlsx", res, 0.25, "t", "op")
    rows = list(load_workbook(path)["Notas FD"].iter_rows(values_only=True))
    assert rows == [("Nota",), ("nota A",)]


def test_cli_window_generate_skips_existing_dat(tmp_path, fake_orc, legacy_inputs, capsys, monkeypatch):
    from cvdtools import cli
    from cvdtools.dynamics.seastates import irregular_name
    from cvdtools.io.legacy import case_from_legacy_json
    from fake_orcfxapi import write_base_model

    case = case_from_legacy_json(legacy_inputs)
    monkeypatch.setattr(cli, "_project", lambda args: None)
    import cvdtools.pipeline as pipeline
    monkeypatch.setattr(pipeline, "load_case", lambda proj, cid: case)
    crit = [c for c in case.criteria if c.dynamic and isinstance(c.limit, (int, float))]
    res = tmp_path / "res.xlsx"
    pd.DataFrame([{"Simulation Name": irregular_name(case.operation_name, 7.0, 1.0, 0.0),
                   **{f"Critério: {c.name}": 0.5 * float(c.limit) for c in crit}}]).to_excel(
        res, sheet_name="Logs", index=False)
    gen = tmp_path / "iter"
    gen.mkdir()
    (gen / f"{irregular_name(case.operation_name, 7.0, 2.0, 0.0)}.dat").write_text("x")   # não rodou
    base = write_base_model(tmp_path / "dyn.dat")
    assert cli.main(["window", "X", "--results", str(res), "--hs-min", "0.5", "--hs-max", "3.5", "--hs-step", "0.25",
                     "--uf-limit", "1.0", "--generate", str(base), "--gen-out", str(gen)]) == 0
    out = capsys.readouterr().out
    assert "1 sugerido(s) já têm .dat" in out and "1 .dat gerado(s)" in out
    assert (gen / f"{irregular_name(case.operation_name, 7.0, 2.25, 0.0)}.dat").exists()
