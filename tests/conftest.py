import os
import sys
from pathlib import Path

import pytest

from cvdtools._orcaflex import has_orcfxapi

sys.path.insert(0, str(Path(__file__).parent))  # para importar fake_orcfxapi

NEW_CODE = Path(__file__).resolve().parents[1]
# new_code fica dentro de _code_base; a pipeline antiga é a pasta-mãe
CODE_BASE = Path(os.environ.get("CVD_CODE_BASE", NEW_CODE.parent))
BASELINE_DIR = Path(os.environ.get("CVD_BASELINE", NEW_CODE / "baseline" / "GLF49_2CVD_no_flut"))


@pytest.fixture
def fake_orc(monkeypatch):
    """Instala um OrcFxAPI falso (tests/fake_orcfxapi.py) durante o teste."""
    import sys

    from fake_orcfxapi import make_module

    module = make_module()
    monkeypatch.setitem(sys.modules, "OrcFxAPI", module)
    return module


@pytest.fixture
def legacy_inputs():
    import json

    return json.loads((Path(__file__).parent / "data" / "legacy_inputs_glf49.json").read_text(encoding="utf-8"))


@pytest.fixture
def real_workbook():
    wb = CODE_BASE / "1327.planilha_geral_offline.xlsx"
    if not wb.is_file():
        pytest.skip(f"Planilha geral não encontrada em {wb}")
    return wb


@pytest.fixture
def code_base():
    if not (CODE_BASE / "__INPUTS.json").is_file():
        pytest.skip(f"Pipeline antiga não encontrada em {CODE_BASE}")
    return CODE_BASE


@pytest.fixture
def baseline_dir():
    if not (BASELINE_DIR / "manifest.json").is_file():
        pytest.skip(f"Baseline ainda não capturado em {BASELINE_DIR} (rode 'cvd baseline capture')")
    return BASELINE_DIR


def pytest_collection_modifyitems(config, items):
    if has_orcfxapi():
        return
    skip = pytest.mark.skip(reason="OrcFxAPI indisponível")
    for item in items:
        if "orcaflex" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _log_propagates():
    """setup_logging (chamado pelo cli.main) desliga a propagação; o caplog dos testes seguintes precisa dela."""
    from cvdtools.log import log

    log.propagate = True
    yield
    log.propagate = True
