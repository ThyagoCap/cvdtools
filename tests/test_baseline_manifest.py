"""Testes da verificação do baseline sem OrcaFlex (manifest sintético)."""

import json

from cvdtools.regression.capture import hash_sources, sha256, verify_baseline


def _fake_code_base(tmp_path):
    cb = tmp_path / "code_base"
    (cb / "00.Base").mkdir(parents=True)
    (cb / "_01.GenerateModel.py").write_text("print('legacy')", encoding="utf-8")
    (cb / "__INPUTS.json").write_text('{"OperationName": "x"}', encoding="utf-8")
    (cb / "00.Base" / "base_input.dat").write_bytes(b"\x00binary")
    (cb / "new_code").mkdir()
    (cb / "new_code" / "ignored.py").write_text("não entra no hash", encoding="utf-8")
    return cb


def _fake_baseline(tmp_path, cb):
    bl = tmp_path / "baseline"
    bl.mkdir()
    (bl / "generated_model.yml").write_text("General: {}\n", encoding="utf-8")
    manifest = {
        "legacy_source_hashes": hash_sources(cb),
        "baseline_files": {"generated_model.yml": sha256(bl / "generated_model.yml")},
    }
    (bl / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bl


def test_hash_sources_scope(tmp_path):
    cb = _fake_code_base(tmp_path)
    keys = set(hash_sources(cb))
    assert keys == {"_01.GenerateModel.py", "__INPUTS.json", "00.Base/base_input.dat"}


def test_verify_ok(tmp_path):
    cb = _fake_code_base(tmp_path)
    bl = _fake_baseline(tmp_path, cb)
    assert verify_baseline(bl, cb) == []


def test_verify_detects_changes(tmp_path):
    cb = _fake_code_base(tmp_path)
    bl = _fake_baseline(tmp_path, cb)
    (bl / "generated_model.yml").write_text("General: {mudou: 1}\n", encoding="utf-8")
    (cb / "_01.GenerateModel.py").write_text("print('editado')", encoding="utf-8")
    (cb / "_99.Novo.py").write_text("", encoding="utf-8")
    problems = verify_baseline(bl, cb)
    assert any("baseline alterado" in p for p in problems)
    assert any("mudou desde a captura: _01" in p for p in problems)
    assert any("nova desde a captura: _99" in p for p in problems)
