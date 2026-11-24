import math

from cvdtools.regression import Tolerance, compare, format_report
from cvdtools.regression.fingerprint import load_yaml_text

# Trecho no formato texto do OrcaFlex (estrutura simplificada)
MODEL_A = """\
%YAML 1.1
# Type: Model
# Program: OrcaFlex 11.6b
---
General:
  StageDuration:
    - 15
    - 600
Environment:
  WaterDepth: 1448
  SeabedNormalStiffness: 100
LineTypes:
  New: Line Type
  Line Type:
    OD: 0.27339
    ID: 0.1524
    MassPerUnitLength: 0.1042
    EIx: 8.11
6DBuoys:
  New: MCV
  MCV:
    Mass: 2.012
    Volume: 0.25
"""


def test_identical_models_have_no_diffs():
    a = load_yaml_text(MODEL_A)
    assert compare(a, load_yaml_text(MODEL_A)) == []


def test_numeric_tolerance():
    tol = Tolerance(rel_tol=1e-6, abs_tol=0.0)
    assert tol.close(0.27784, 0.27784000000000003)
    assert not tol.close(0.27784, 0.2779)
    assert tol.close(float("nan"), float("nan"))
    assert not tol.close(math.inf, 1e308)


def test_detects_changed_value_and_path():
    a = load_yaml_text(MODEL_A)
    b = load_yaml_text(MODEL_A.replace("OD: 0.27339", "OD: 0.30000"))
    diffs = compare(a, b)
    assert len(diffs) == 1
    assert diffs[0].path == "$.LineTypes.Line Type.OD"
    assert diffs[0].expected == 0.27339 and diffs[0].actual == 0.3


def test_int_and_float_are_equal():
    assert compare({"x": 1448}, {"x": 1448.0}) == []


def test_missing_extra_and_length():
    diffs = compare({"a": 1, "b": [1, 2]}, {"b": [1], "c": 3})
    kinds = sorted(d.kind for d in diffs)
    assert kinds == ["extra", "length", "missing"]
    assert "3 diferença" in format_report(diffs)


def test_ignore_paths():
    a = load_yaml_text(MODEL_A)
    b = load_yaml_text(MODEL_A.replace("- 600", "- 300"))
    assert compare(a, b, ignore=["General.StageDuration"]) == []


def test_named_lists_compare_by_name():
    a = {"Lines": [{"Name": "Line", "EndBX": 1.0}, {"Name": "Crane Wire", "Length": 3.0}]}
    b = {"Lines": [{"Name": "Crane Wire", "Length": 3.0}, {"Name": "Line", "EndBX": 2.0}, {"Name": "Novo"}]}
    diffs = compare(a, b)
    assert {(d.path, d.kind) for d in diffs} == {("$.Lines[Line].EndBX", "value"), ("$.Lines[Novo]", "extra")}
