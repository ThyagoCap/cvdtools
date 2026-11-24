"""Estática interrompível (Ctrl+C) e estática final com posições calculadas."""

import json
import os
import signal
import sys

import pytest

from cvdtools._orcaflex import calculate_statics
from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.model.builder import build_model
from fake_orcfxapi import FakeModel, write_base_model


class SlowModel:
    """Chama o tratador de progresso como o OrcaFlex; cancela quando ele devolve True."""

    def __init__(self, ctrl_c_at=None):
        self.ctrl_c_at = ctrl_c_at
        self.calls = 0
        self.staticsProgressHandler = None

    def CalculateStatics(self):
        for i in range(1000):
            if self.ctrl_c_at is not None and i == self.ctrl_c_at:
                os.kill(os.getpid(), signal.SIGINT)
            self.calls += 1
            if self.staticsProgressHandler and self.staticsProgressHandler(self, f"iteração {i}"):
                raise RuntimeError("Statics cancelled")


def test_statics_runs_and_restores_handler():
    m = SlowModel()
    old = signal.getsignal(signal.SIGINT)
    calculate_statics(m)
    assert m.calls == 1000 and m.staticsProgressHandler is None
    assert signal.getsignal(signal.SIGINT) is old


@pytest.mark.skipif(sys.platform.startswith("win"), reason="os.kill(SIGINT) não simula Ctrl+C no Windows")
def test_ctrl_c_cancels_statics():
    m = SlowModel(ctrl_c_at=10)
    with pytest.raises((KeyboardInterrupt, RuntimeError)):
        calculate_statics(m)
    assert m.calls < 1000, "a estática deveria ter sido cancelada pelo tratador de progresso"


class FinalPhysics(FakeModel):
    fail = False

    def CalculateStatics(self):
        if FinalPhysics.fail:
            raise RuntimeError("Statics failed to converge")
        self.objects["Line"]["StartingShape"] = "calculada"

    def UseCalculatedPositions(self, setLinesToUserSpecifiedStartingShape=False):
        assert setLinesToUserSpecifiedStartingShape is True
        self.objects["Line"]["UsesCalculatedPositions"] = True


def test_finalize_statics(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.model.endb import finalize_statics

    fake_orc.Model = FinalPhysics
    case = case_from_legacy_json(legacy_inputs)
    path = build_model(case, write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat").model_path
    FinalPhysics.fail = False
    assert finalize_statics(case, path)["status"] == "OK"
    assert FakeModel(str(path)).objects["Line"]["UsesCalculatedPositions"] is True
    FinalPhysics.fail = True
    path2 = build_model(case, write_base_model(tmp_path / "b2.dat"), tmp_path / "c2.dat").model_path
    before = path2.read_text()
    res = finalize_statics(case, path2)
    assert res["status"] == "FALHOU" and "converge" in res["message"]
    assert path2.read_text() == before, "modelo não é alterado se a estática falha"
    FinalPhysics.fail = False
