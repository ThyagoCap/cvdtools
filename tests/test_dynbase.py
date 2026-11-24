"""0.12.0: modelo dinâmico base a partir da posição 01.Horizontal (MCV ancorado, sem cabo do guindaste)."""

import json
from pathlib import Path

import pytest

from cvdtools.cli import main
from fake_orcfxapi import FakeModel, write_base_model
from helpers import CASE_ID


def _horizontal(case_dir: Path, base: Path) -> Path:
    pos = case_dir / "01.Positions"
    pos.mkdir(parents=True)
    m = FakeModel(str(base))
    m.objects["MCV"].update(Connection="Free", InitialX=0.0, InitialY=0.0, InitialZ=-1440.0)
    m.SaveData(str(pos / "01.Horizontal.dat"))
    return pos / "01.Horizontal.dat"


def test_make_dynamic_base(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.dynamics.dynbase import make_dynamic_base
    from cvdtools.io.legacy import case_from_legacy_json

    case = case_from_legacy_json(legacy_inputs)
    base = write_base_model(tmp_path / "base.dat")
    case_dir = tmp_path / "caso"
    _horizontal(case_dir, base)
    hub = FakeModel(str(base)).objects[case.role("HUB")]["Length"]
    out = make_dynamic_base(case, case_dir, "1327.TESTE")
    assert out == case_dir / "1327.TESTE_dyn.dat"
    dyn = json.loads(out.read_text(encoding="utf-8"))["objects"]
    mcv = dyn[case.role("MCV")]
    assert mcv["Connection"] == "Anchored"
    assert mcv["InitialZ"] == hub, "0.12.1: Z ancorado = altura do hub, exata"
    assert case.roles["CRANE_WIRE"] not in dyn, "cabo do guindaste apagado"


def test_missing_position_is_clear(tmp_path, fake_orc, legacy_inputs):
    from cvdtools.dynamics.dynbase import make_dynamic_base
    from cvdtools.io.legacy import case_from_legacy_json

    with pytest.raises(FileNotFoundError, match="01.Horizontal"):
        make_dynamic_base(case_from_legacy_json(legacy_inputs), tmp_path, "x")
