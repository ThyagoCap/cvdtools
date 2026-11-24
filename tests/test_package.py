import subprocess
import sys

import cvdtools
from cvdtools.cli import main


def test_version():
    assert cvdtools.__version__ == "0.12.1"


def test_import_without_orcaflex():
    # O pacote precisa importar mesmo sem OrcFxAPI
    import cvdtools.regression  # noqa: F401
    import cvdtools.regression.capture  # noqa: F401
    import cvdtools.regression.fingerprint  # noqa: F401


def test_cli_help_runs(capsys):
    assert main([]) == 0
    assert "baseline" in capsys.readouterr().out


def test_cli_module_entrypoint():
    proc = subprocess.run([sys.executable, "-m", "cvdtools.cli", "--version"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert cvdtools.__version__ in proc.stdout
