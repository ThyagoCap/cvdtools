"""Execução paralela das simulações e geração paralela dos estados de mar."""

import sys

import pytest

from cvdtools.dynamics import run as R
from fake_orcfxapi import FakeModel, write_base_model


class RunModel(FakeModel):
    def __init__(self, path=None, threadCount=None):
        super().__init__(path)
        self.thread_count = threadCount

    def RunSimulation(self):
        if "instavel" in str(self.path):
            raise RuntimeError("Simulation is unstable at 12.3s")
        self.objects["General"]["RanWithThreads"] = self.thread_count


def _dats(tmp_path, names):
    out = []
    for n in names:
        out.append(write_base_model(tmp_path / f"{n}.dat"))
    return out


def test_run_serial_results_and_skip(tmp_path, fake_orc):
    fake_orc.Model = RunModel
    files = _dats(tmp_path, ["a", "b", "instavel"])
    (tmp_path / "b.sim").write_text("x")
    res = R.run_simulations(files, workers=1, threads=1, log_csv=tmp_path / "run_log.csv")
    st = {r.file: r.status for r in res}
    assert st == {"a.dat": "OK", "b.dat": "PULADO", "instavel.dat": "FALHOU"}
    assert FakeModel(str(tmp_path / "a.sim")).objects["General"]["RanWithThreads"] == 1
    assert "unstable" in next(r.message for r in res if r.file == "instavel.dat")
    assert (tmp_path / "run_log.csv").exists()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="processos filhos no Windows não veem o OrcFxAPI falso")
def test_run_parallel_processes(tmp_path, fake_orc):
    fake_orc.Model = RunModel
    files = _dats(tmp_path, [f"s{i}" for i in range(4)])
    res = R.run_simulations(files, workers=3)
    assert sorted(r.status for r in res) == ["OK"] * 4
    assert all((tmp_path / f"s{i}.sim").exists() for i in range(4))


def test_collect_from_list(tmp_path):
    (tmp_path / "x.dat").write_text("")
    (tmp_path / "batch_list.txt").write_text(f"{tmp_path / 'x.dat'}\n")
    assert R.collect(list_file=tmp_path / "batch_list.txt") == [tmp_path / "x.dat"]
    assert R.collect(tmp_path) == [tmp_path / "x.dat"]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="processos filhos no Windows não veem o OrcFxAPI falso")
def test_generate_irregular_parallel(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.dynamics.seastates import generate_irregular
    from cvdtools.io.legacy import case_from_legacy_json

    case = case_from_legacy_json(legacy_inputs)
    base = write_base_model(tmp_path / "base_dyn.dat")
    files = generate_irregular(case, base, [(30.0, 8.0, [1.0, 1.5, 2.0]), (60.0, 9.0, [1.0])], tmp_path / "out",
                               workers=3)
    assert len(files) == 4 and len(list((tmp_path / "out").glob("*.dat"))) == 4
    assert [f.file for f in files] == sorted(f.file for f in files)
