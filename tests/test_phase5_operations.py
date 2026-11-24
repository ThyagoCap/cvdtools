"""Fase 5: montagem de operações sem corcova (estrutura; premissas a confirmar)."""

import pytest

from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.model.builder import build_model
from cvdtools.schema import OperationSpec
from fake_orcfxapi import FakeModel, write_base_model


def _case(legacy_inputs, supported=True):
    return case_from_legacy_json(legacy_inputs, operation=OperationSpec(
        "1st end CVD", "no_camelback", camelback=False, base_model="00.Base/base_1st_cvd.dat", supported=supported))


def test_unsupported_operation_refused(tmp_path, fake_orc, legacy_inputs):
    with pytest.raises(NotImplementedError, match="suportada"):
        build_model(_case(legacy_inputs, False), write_base_model(tmp_path / "b.dat"), tmp_path / "c.dat")


def test_no_camelback_build_without_camelback_objects(tmp_path, fake_orc, legacy_inputs):
    base = write_base_model(tmp_path / "b.dat")
    m = FakeModel(str(base))
    for k in ("Link CB 1", "Link CB 2", "Masterlink", "Winch Wire"):
        del m.objects[k]
    m.SaveData(str(base))
    case = _case(legacy_inputs)
    res = build_model(case, base, tmp_path / "c.dat")
    o = FakeModel(str(res.model_path)).objects
    assert "Masterlink" not in o and o["Line"]["EndADeclination"] == pytest.approx(180 - case.num("mcv.angle"))


def test_camelback_without_winch_is_an_error(tmp_path, fake_orc, legacy_inputs):
    base = write_base_model(tmp_path / "b.dat")
    m = FakeModel(str(base))
    del m.objects["Winch Wire"]
    m.SaveData(str(base))
    case = case_from_legacy_json(legacy_inputs)
    with pytest.raises(Exception, match="WINCH_WIRE"):
        build_model(case, base, tmp_path / "c.dat")
