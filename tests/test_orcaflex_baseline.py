"""Testes que só rodam na máquina com OrcaFlex e/ou com o baseline capturado."""

import json

import pytest

from cvdtools.regression import Tolerance, compare, format_report
from cvdtools.regression.capture import verify_baseline
from cvdtools.regression.fingerprint import model_to_dict


@pytest.mark.orcaflex
def test_base_model_roundtrip(code_base):
    """O .yml gerado do modelo base é estável (mesmo modelo -> mesmo dicionário)."""
    path = code_base / "00.Base" / "base_input.dat"
    assert compare(model_to_dict(path), model_to_dict(path)) == []


@pytest.mark.baseline
def test_baseline_integrity(baseline_dir, code_base):
    problems = verify_baseline(baseline_dir, code_base)
    assert not problems, "\n".join(problems)


@pytest.mark.baseline
def test_baseline_contents(baseline_dir):
    for name in ("inputs.json", "base_model.yml", "generated_model.yml", "generated_statics.json"):
        assert (baseline_dir / name).is_file(), name
    statics = json.loads((baseline_dir / "generated_statics.json").read_text(encoding="utf-8"))
    assert "statics_error" not in statics, statics.get("statics_error")


@pytest.mark.orcaflex
@pytest.mark.baseline
def test_legacy_generation_is_reproducible(baseline_dir, code_base, tmp_path):
    """Rodar a pipeline antiga de novo reproduz o baseline (mostra que ele é determinístico).

    Nas fases seguintes, este mesmo teste compara o modelo gerado pelo código NOVO.
    """
    from cvdtools.regression.capture import capture_baseline

    fresh = capture_baseline(code_base, tmp_path / "fresh", with_adjust=False)
    diffs = compare(
        model_to_dict(baseline_dir / "generated_model.yml"),
        model_to_dict(fresh / "generated_model.yml"),
        Tolerance(rel_tol=1e-9, abs_tol=1e-9),
    )
    assert not diffs, format_report(diffs)
