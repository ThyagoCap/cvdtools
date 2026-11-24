"""Triagem por frequency domain e comparação com as irregulares (OrcFxAPI falso)."""

import types

import pandas as pd
import pytest

from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.post import window as W
from cvdtools.screening import compare as C
from cvdtools.screening.frequency import _limit_from, FDResult, fd_pair, run_fd_screening, write_fd
from fake_orcfxapi import FakeModel, write_base_model


class FDPhysics(FakeModel):
    """Valor de cada critério = estático + MPM, MPM = k(Tp) x Hs (linear); 'End Force' não existe em FD."""

    STATIC = {"Effective tension": 20.0, "x bend moment": 30.0, "y bend moment": 0.0, "x shear force": 5.0,
              "y shear force": 0.0, "x curvature": 0.05, "y curvature": 0.0, "Bend moment": 10.0, "Shear force": 2.0}
    K = {"Effective tension": 30.0, "x bend moment": 10.0, "y bend moment": 5.0, "x shear force": 2.0,
         "y shear force": 1.0, "x curvature": 0.02, "y curvature": 0.01, "Bend moment": 4.0, "Shear force": 1.0}

    def Reset(self):
        self.ran = False

    def RunSimulation(self):
        assert self.objects["General"]["DynamicsSolutionMethod"] == "Frequency domain"
        self.check_frequency_domain()
        self.ran = True

    def _factor(self):
        env = self.objects["Environment"]
        return env["WaveHs"] * (1.0 + 0.02 * (env["WaveTp"] - 10.0))

    def fd_result(self, name, var, extra):
        if var in ("End Force", "End Moment", "Curvature"):
            raise ValueError(f"{var} não disponível no domínio da frequência")
        return types.SimpleNamespace(StaticValue=self.STATIC[var], StdDev=1.0, Tz=8.0)

    def fd_mpm(self, name, var, extra, hours):
        assert hours == 3.0
        return self.K[var] * self._factor()

    def fd_range(self, name, var, rng, hours):
        if var == "Curvature":
            raise ValueError("Curvature não disponível")
        s, k = self.STATIC[var], self.K[var] * self._factor()
        return types.SimpleNamespace(StaticValue=[s, 0.5 * s], MPM=[0.5 * k, k])


@pytest.fixture
def case(legacy_inputs):
    return case_from_legacy_json(legacy_inputs)


def test_limit_from_secant():
    pts = [FDResult(0, 10, 1.0, 0.5), FDResult(0, 10, 2.0, 0.8)]
    assert _limit_from(pts, 1.0) == pytest.approx(2.0 + (1.0 - 0.8) * 1.0 / 0.3)
    assert _limit_from([FDResult(0, 10, 2.0, 0.5)], 1.0) == pytest.approx(4.0)


def test_fd_pair_finds_limit_and_uses_components(tmp_path, fake_orc, case):
    from dataclasses import asdict

    from cvdtools.screening.frequency import criteria_for_fd

    fake_orc.Model = FDPhysics
    base = write_base_model(tmp_path / "dyn.dat")
    crits = [asdict(c) for c in criteria_for_fd(case)]
    r = fd_pair(str(base), crits, "Island Performer", 0.0, 10.0, [1.5, 2.0, 2.5], 2.0, 3.5, 3.0, 1.0)
    assert r["hs_limit"] is not None, r
    # Axial Tension: End Force indisponível -> Effective tension: 20 + 30 Hs = 95 -> Hs = 2.5 (governa)
    assert r["hs_limit"] == pytest.approx(2.5, abs=0.02)
    p = {round(q["hs"], 3): q for q in r["points"]}
    v = p[2.0]["values"]["Axial Tension"]
    assert v[4] == "Effective tension" and v[2] == pytest.approx(80.0)
    # Bend moment: End Moment -> x/y bend moment combinados (raiz da soma dos quadrados)
    bm = p[2.0]["values"]["Bend moment"]
    assert bm[4] == "x bend moment ⊕ y bend moment" and bm[2] == pytest.approx(((30 + 20) ** 2 + 10 ** 2) ** 0.5)


def test_run_write_and_compare(tmp_path, fake_orc, case):
    fake_orc.Model = FDPhysics
    base = write_base_model(tmp_path / "dyn.dat")
    grid = [(0.0, 10.0, [1.5, 2.0, 2.5]), (30.0, 10.0, [1.5, 2.0, 2.5])]
    res = run_fd_screening(case, base, grid, hs_ref=2.0, workers=1)
    out = write_fd(tmp_path / "fd.xlsx", res, 0.25, "teste", "2nd end CVD")
    pred = C.read_prediction(out)
    assert pred[(10.0, 0.0)] == pytest.approx(2.5, abs=0.02)
    # irregulares: em 0° aprova 2.0 e reprova 2.5 (FD diz 2.5: limite exatamente no reprovado -> contra a segurança)
    rows = []
    for d, hs, uf in ((0.0, 1.5, 0.6), (0.0, 2.0, 0.9), (0.0, 2.5, 1.1), (30.0, 1.5, 0.5), (30.0, 2.0, 0.7),
                      (30.0, 2.5, 0.95)):
        rows.append({"Tp (s)": 10.0, "Hs (m)": hs, "Dir (°)": d, "UF máx": uf, "UF Axial Tension": uf,
                     "Status": W.APPROVED if uf <= 1 else W.REJECTED, "Governa": "Axial Tension"})
    st = pd.DataFrame(rows)
    limits = C.compare_limits(pred, C.td_brackets(st))
    by = {(r["Tp (s)"], r["Dir (°)"]): r["Classe"] for r in limits}
    assert by[(10.0, 0.0)] in (C.UNSAFE, C.INSIDE)
    assert by[(10.0, 30.0)] == C.UNDEFINED          # irregular sem reprovação
    uf_rows = C.compare_uf(pd.read_excel(out, sheet_name="UF FD"), st)
    assert len(uf_rows) == 6
    r0 = next(r for r in uf_rows if r["Dir (°)"] == 0.0 and r["Hs (m)"] == 2.0)
    assert r0["UF FD"] == pytest.approx(80.0 / 95.0, rel=1e-3) and r0["UF TD/FD"] == pytest.approx(0.9 / (80 / 95), rel=1e-3)
    summ = C.summary(limits, uf_rows)
    C.write_comparison(out, limits, uf_rows, summ)
    from openpyxl import load_workbook
    assert {"Comparação resumo", "Comparação limites", "Comparação UF", "UF FD", "Janela prevista"} <= set(
        load_workbook(out).sheetnames)


def test_classify():
    assert C.classify(2.1, 2.0, 2.5) == C.INSIDE
    assert C.classify(2.6, 2.0, 2.5) == C.UNSAFE
    assert C.classify(1.8, 2.0, 2.5) == C.CONSERVATIVE
    assert C.classify(None, 2.0, 2.5) == C.UNDEFINED
    assert C.classify(3.0, 2.5, None) == C.UNDEFINED


def test_criterion_without_fd_is_reported_not_fatal(tmp_path, fake_orc, case):
    from dataclasses import asdict

    from cvdtools.screening.frequency import criteria_for_fd

    class NoBR(FDPhysics):
        def fd_range(self, name, var, rng, hours):
            if name == "Bend Restrictor Element":
                raise ValueError("objeto sem resultado")
            return super().fd_range(name, var, rng, hours)

    fake_orc.Model = NoBR
    base = write_base_model(tmp_path / "dyn.dat")
    r = fd_pair(str(base), [asdict(c) for c in criteria_for_fd(case)], "Island Performer", 0.0, 10.0, [2.0], 2.0,
                3.5, 3.0, 1.0)
    assert r["hs_limit"] == pytest.approx(2.5, abs=0.02)
    p = r["points"][0]
    assert "Bend moment BR" in p["error"] and p["values"]["Bend moment BR"][4].startswith("sem FD")
