"""Fluxo completo da captura com um OrcFxAPI falso (roda sem OrcaFlex).

Confere: cópia para pasta temporária, execução dos scripts antigos, arquivos do
baseline, manifest, rodada dupla do _02 e que a pasta de origem não é alterada.
"""

import json
import sys
import types
from pathlib import Path

import pytest

from cvdtools.regression import capture as cap
from cvdtools.regression.capture import capture_baseline, hash_sources, verify_baseline


class _FakeResult(list):
    @property
    def Mean(self):
        return self

    Min = Mean
    Max = Mean


class _FakeObject:
    def __init__(self, name):
        self.name = name
        self.Length = [100.0]
        self.EndAInitialArcLength = 0.0

    def TimeHistory(self, var, period=None, extra=None):
        return [42.0]

    def RangeGraph(self, var, period=None, objectExtra=None, arclengthRange=None):
        return _FakeResult([1.0, 3.0, 2.0])

    def GetData(self, name, index):
        return 7.0


class _FakeModel:
    def __init__(self, path=None):
        self.path = path
        self.general = _FakeObject("General")
        self.environment = _FakeObject("Environment")

    def __getitem__(self, name):
        return _FakeObject(name)

    def SaveData(self, out):
        Path(out).write_text(f"General:\n  Source: {Path(self.path).name}\n", encoding="utf-8")

    def CalculateStatics(self):
        pass


def _fake_orcfxapi():
    m = types.ModuleType("OrcFxAPI")
    m.Model = _FakeModel
    m.Period = lambda x: ("period", x)
    m.PeriodNum = types.SimpleNamespace(StaticState="static")
    m.oeEndA, m.oeEndB, m.oeTouchdown = "A", "B", "TDP"
    m.arSpecifiedArclengths = lambda a, b: (a, b)
    m.DLLVersion = lambda: "fake"
    return m


LEGACY_01 = """
import json
inputs = json.load(open("__INPUTS.json"))
open(inputs["OperationName"] + ".dat", "w").write("gerado")
print("modelo gerado")
"""

# Imita o comportamento do _02 real: na 1ª rodada só zera o arc length e sai.
LEGACY_02 = """
import json, os
inputs = json.load(open("__INPUTS.json"))
flag = "ran_once.flag"
if not os.path.exists(flag):
    open(flag, "w").write("1")
else:
    open(inputs["OperationName"] + "_adjusted.dat", "w").write("ajustado")
"""


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "OrcFxAPI", _fake_orcfxapi())
    cb = tmp_path / "_code_base"
    (cb / "00.Base").mkdir(parents=True)
    (cb / "00.Base" / "base_input.dat").write_text("base", encoding="utf-8")
    (cb / "_01.GenerateModel.py").write_text(LEGACY_01, encoding="utf-8")
    (cb / "_02.AdjustWireWinch.py").write_text(LEGACY_02, encoding="utf-8")
    inputs = {
        "OperationName": "1327.2CVD_no_flut",
        "acceptanceCriteria": {
            "Axial Tension": {"Type": "Time History", "OrcaFlex Parent": "Line",
                              "OrcaFlex Variable Name": "End Force", "OrcaFlex Extra": "End A", "Limit": 84.0},
            "Curvature BR": {"Type": "Range Graph Max", "OrcaFlex Parent": "BR",
                             "OrcaFlex Variable Name": "Curvature", "OrcaFlex Extra": "", "Limit": 6.0},
        },
        "postProcess": {
            "Wave Height (m)": {"OrcaFlex Method": "Get data", "OrcaFlex Parent": "Environment",
                                "OrcaFlex Variable Name": "WaveHs", "OrcaFlex Extra": "", "Simulation Period": ""},
            "Clearance min static": {"OrcaFlex Method": "Range graph min", "OrcaFlex Parent": "Line",
                                     "OrcaFlex Variable Name": "Seabed clearance", "OrcaFlex Extra": [0.0, 29.0],
                                     "Simulation Period": ""},
            "Tension max dyn": {"OrcaFlex Method": "Max", "OrcaFlex Parent": "Line",
                                "OrcaFlex Variable Name": "Effective tension", "OrcaFlex Extra": "End A",
                                "Simulation Period": "Whole simulation"},
        },
    }
    (cb / "__INPUTS.json").write_text(json.dumps(inputs), encoding="utf-8")
    return cb


def test_capture_end_to_end(fake_env, tmp_path):
    cb = fake_env
    before = hash_sources(cb)
    out = capture_baseline(cb, tmp_path / "baseline" / "case")

    assert hash_sources(cb) == before, "a pasta de origem não pode mudar"
    assert not (cb / "1327.2CVD_no_flut.dat").exists(), "o script antigo deve rodar na cópia"

    for name in ("inputs.json", "base_model.yml", "generated_model.yml", "generated_statics.json",
                 "adjusted_model.yml", "adjusted_statics.json", "manifest.json"):
        assert (out / name).is_file(), name
    assert (out / "legacy_stdout" / "02_adjust_run2.txt").is_file(), "o _02 deve rodar duas vezes"

    statics = json.loads((out / "generated_statics.json").read_text(encoding="utf-8"))
    assert statics["acceptanceCriteria"]["Axial Tension"]["uf"] == pytest.approx(42.0 / 84.0)
    assert statics["acceptanceCriteria"]["Curvature BR"]["value"] == 3.0
    assert statics["postProcessStatic"]["Clearance min static"] == 1.0  # min(Mean)
    assert statics["postProcessStatic"]["Wave Height (m)"] == 7.0
    assert "Tension max dyn" not in statics["postProcessStatic"], "itens dinâmicos ficam fora"

    adjusted = json.loads((out / "adjusted_statics.json").read_text(encoding="utf-8"))
    assert adjusted["winch"]["WinchWire.Length[0]"] == 100.0

    assert verify_baseline(out, cb) == []


def test_capture_refuses_to_overwrite_complete_baseline(fake_env, tmp_path):
    out = capture_baseline(fake_env, tmp_path / "baseline")
    with pytest.raises(FileExistsError):
        capture_baseline(fake_env, out)


def test_capture_redoes_incomplete_baseline(fake_env, tmp_path):
    out = tmp_path / "baseline"
    out.mkdir()
    (out / "resto_de_captura_interrompida.txt").write_text("x")
    capture_baseline(fake_env, out)
    assert (out / "manifest.json").is_file()
    assert not (out / "resto_de_captura_interrompida.txt").exists()


def test_adjust_failure_does_not_lose_baseline(fake_env, tmp_path):
    (fake_env / "_02.AdjustWireWinch.py").write_text("raise RuntimeError('Length: Default value not allowed')",
                                                     encoding="utf-8")
    out = capture_baseline(fake_env, tmp_path / "bl")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert "falhou" in manifest["adjust_error"]
    assert (out / "generated_model.yml").is_file() and not (out / "adjusted_model.yml").exists()
    assert verify_baseline(out, fake_env) == []


def test_capture_refuses_output_inside_base(fake_env):
    with pytest.raises(ValueError):
        capture_baseline(fake_env, fake_env / "00.Base" / "bl")


def test_legacy_failure_is_reported(fake_env, tmp_path):
    (fake_env / "_01.GenerateModel.py").write_text("raise SystemExit(3)", encoding="utf-8")
    with pytest.raises(RuntimeError, match="falhou"):
        capture_baseline(fake_env, tmp_path / "bl", with_adjust=False)
    assert (tmp_path / "bl" / "legacy_stdout" / "01_generate.txt").is_file()
