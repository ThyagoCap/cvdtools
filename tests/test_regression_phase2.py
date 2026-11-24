"""Regressão da fase 2 contra o baseline (máquina com OrcaFlex + baseline capturado).

1. O builder novo, alimentado com o MESMO __INPUTS.json do baseline e com a
   catenária em modo "legacy", tem de gerar exatamente o modelo da pipeline antiga.
2. No modo "fixed" (correção do peso submerso) a diferença tem de ficar restrita à linha.
3. A extração nova reproduz a tabela de pós-processamento congelada (se capturada).
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from cvdtools.io.legacy import case_from_legacy_json
from cvdtools.regression import Tolerance, compare, format_report
from cvdtools.regression.fingerprint import model_to_dict

pytestmark = [pytest.mark.orcaflex, pytest.mark.baseline]

# O builder ajusta a vista padrão para mostrar MCV e hub (desde a 0.6.8); a pipeline antiga não.
IGNORE = ("DefaultView",)


def _base_model(baseline_dir, code_base):
    """Modelo base congelado na captura: edições posteriores do 00.Base não mudam estes testes.

    Quem acusa mudança nas fontes antigas é o test_orcaflex_baseline (integridade e reprodução).
    """
    frozen = baseline_dir / "base_model.yml"
    return frozen if frozen.is_file() else code_base / "00.Base" / "base_input.dat"


def _build(baseline_dir, code_base, tmp_path, mode):
    from cvdtools.model.builder import build_model

    inputs = json.loads((baseline_dir / "inputs.json").read_text(encoding="utf-8"))
    res = build_model(case_from_legacy_json(inputs), _base_model(baseline_dir, code_base),
                      tmp_path / f"novo_{mode}.dat", catenary=mode)
    return model_to_dict(res.model_path)


def test_builder_reproduces_legacy_model(baseline_dir, code_base, tmp_path):
    expected = model_to_dict(baseline_dir / "generated_model.yml")
    diffs = compare(expected, _build(baseline_dir, code_base, tmp_path, "legacy"), Tolerance(1e-9, 1e-9),
                    ignore=IGNORE)
    assert not diffs, format_report(diffs)


def test_fixed_catenary_only_changes_the_line(baseline_dir, code_base, tmp_path):
    expected = model_to_dict(baseline_dir / "generated_model.yml")
    diffs = compare(expected, _build(baseline_dir, code_base, tmp_path, "fixed"), Tolerance(1e-9, 1e-9),
                    ignore=IGNORE)
    assert diffs, "a correção da catenária deveria mover o End B da linha"
    outside = [d for d in diffs if not d.path.startswith("$.Lines[Line]")]
    assert not outside, format_report(outside)


def test_extraction_reproduces_postproc_table(baseline_dir):
    table = baseline_dir / "postproc_logs.csv"
    manifest = json.loads((baseline_dir / "manifest.json").read_text(encoding="utf-8"))
    if not table.is_file() or not manifest.get("sims_dir"):
        pytest.skip("Baseline sem tabela de pós-processamento (--postproc-table / --sims-dir)")
    from cvdtools.post.extract import extract_many

    inputs = json.loads((baseline_dir / "inputs.json").read_text(encoding="utf-8"))
    expected = pd.read_csv(table)
    sims = [Path(manifest["sims_dir"]) / n for n in expected["Simulation Name"]]
    new = extract_many(sims, case_from_legacy_json(inputs).postproc, workers=1, progress=False)
    new = new.set_index("Simulation Name").loc[expected["Simulation Name"]].reset_index()
    tol = Tolerance(rel_tol=1e-9, abs_tol=1e-9)
    problems = []
    for col in expected.columns:
        if col == "Simulation Name" or col not in new.columns:
            continue
        for i, (a, b) in enumerate(zip(expected[col], new[col])):
            if pd.isna(a) and pd.isna(b):
                continue
            if pd.isna(a) or pd.isna(b) or not tol.close(float(a), float(b)):
                problems.append(f"{expected['Simulation Name'][i]} / {col}: {a} != {b}")
    assert not problems, "\n".join(problems[:30])
