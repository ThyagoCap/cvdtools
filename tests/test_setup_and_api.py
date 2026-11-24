"""0.11.0: 'cvdtools setup' (pasta de projeto em um comando) e scripts por etapa (cvdtools.api)."""

import json
import runpy

import pytest

from cvdtools import api
from cvdtools.cli import main
from cvdtools.setup_project import run_setup
from fake_orcfxapi import write_base_model
from helpers import CASE_ID, full_workbook

NO = lambda q, d: d   # noqa: E731  (setup sem perguntas: aceita o padrão)


@pytest.fixture
def project(tmp_path, monkeypatch, legacy_inputs):
    """<tmp>/1327.planilha_geral.xlsx + <tmp>/00.Base + pasta de projeto <tmp>/cvd (setup rodado)."""
    (tmp_path / "00.Base").mkdir()
    write_base_model(tmp_path / "00.Base" / "base_input.dat")
    full_workbook(tmp_path / "1327.planilha_geral.xlsx", legacy_inputs)
    folder = tmp_path / "cvd"
    folder.mkdir()
    monkeypatch.chdir(tmp_path)            # o setup e a API não dependem da pasta atual
    monkeypatch.setattr(api, "_PROJECT_FILE", None)
    assert run_setup(str(folder), ask=NO) == 0
    return folder


def test_setup_creates_project_folder(project, tmp_path):
    data = json.loads((project / "cvd_project.json").read_text(encoding="utf-8"))
    assert data["project_code"] == "1327", "código tirado do nome da planilha"
    assert data["workbook"] == "../1327.planilha_geral.xlsx" and data["base_dir"] == ".."
    for name in ("cvd_config.xlsx", "cvd_curves.xlsx", "scripts/00_caso.py", "scripts/02_montar_modelo.py",
                 "scripts/LEIA-ME.md", ".vscode/settings.json"):
        assert (project / name).is_file(), name
    leia = (project / "scripts" / "LEIA-ME.md").read_text(encoding="utf-8")
    assert "{install_command}" not in leia and "pip install --upgrade" in leia
    vs = json.loads((project / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    assert vs["python.defaultInterpreterPath"]


def test_setup_is_idempotent_and_keeps_user_edits(project, monkeypatch, capsys):
    scripts = project / "scripts"
    (scripts / "02_montar_modelo.py").write_text("# meu script\n", encoding="utf-8")
    capsys.readouterr()
    assert run_setup(str(project), ask=NO) == 0
    out = capsys.readouterr().out
    assert "já existe (mantido)" in out and "já atualizados" in out
    assert (scripts / "02_montar_modelo.py").read_text(encoding="utf-8") == "# meu script\n"
    assert not (scripts / "02_montar_modelo.novo.py").exists(), "modelo não mudou: nada a oferecer"

    # versão nova do modelo (aqui, outra URL de instalação no LEIA-ME e no texto dos scripts)
    import cvdtools.setup_project as sp

    monkeypatch.setattr(sp, "install_command", lambda: "pip install OUTRA-URL")
    (scripts / "LEIA-ME.md").write_text("editado\n", encoding="utf-8")
    assert run_setup(str(project), ask=NO) == 0
    assert (scripts / "LEIA-ME.novo.md").read_text(encoding="utf-8").count("OUTRA-URL") == 1
    assert (scripts / "LEIA-ME.md").read_text(encoding="utf-8") == "editado\n"


def test_setup_updates_unedited_scripts(project, monkeypatch):
    import cvdtools.setup_project as sp

    monkeypatch.setattr(sp, "install_command", lambda: "pip install OUTRA-URL")
    assert run_setup(str(project), ask=NO) == 0
    assert "OUTRA-URL" in (project / "scripts" / "LEIA-ME.md").read_text(encoding="utf-8")
    assert not (project / "scripts" / "LEIA-ME.novo.md").exists()


def test_setup_asks_when_several_workbooks(tmp_path, legacy_inputs, monkeypatch):
    full_workbook(tmp_path / "a.xlsx", legacy_inputs)
    full_workbook(tmp_path / "b.xlsx", legacy_inputs)
    asked = []

    def ask(q, d):
        asked.append(q)
        return "2" if "planilha" in q else ""

    assert run_setup(str(tmp_path), ask=ask, vscode=False) == 0
    data = json.loads((tmp_path / "cvd_project.json").read_text(encoding="utf-8"))
    assert data["workbook"] == "b.xlsx" and data["project_code"] == ""
    assert not (tmp_path / ".vscode").exists()


def test_scripts_run_the_pipeline(project, tmp_path, fake_orc, legacy_inputs, capsys):
    """Os scripts gerados rodam como no VS Code (__main__), lendo o ID do 00_caso.py."""
    (tmp_path / "legacy.json").write_text(json.dumps(legacy_inputs), encoding="utf-8")
    (project / "cvd_config.xlsx").unlink()
    assert main(["config", "init", "--out", str(project / "cvd_config.xlsx"), "--from-json",
                 str(tmp_path / "legacy.json")]) == 0   # configuração com os papéis do modelo base de teste
    scripts = project / "scripts"
    with pytest.raises(SystemExit):   # ID vazio: mensagem clara
        runpy.run_path(str(scripts / "00_caso.py"), run_name="__main__")
    with pytest.raises(api.CvdError, match="Preencha o ID"):
        runpy.run_path(str(scripts / "02_montar_modelo.py"), run_name="__main__")

    settings = scripts / "00_caso.py"
    settings.write_text(settings.read_text(encoding="utf-8").replace('ID = ""', f'ID = "{CASE_ID}"'),
                        encoding="utf-8")
    runpy.run_path(str(scripts / "02_montar_modelo.py"), run_name="__main__")
    api.usar_projeto(project)   # daqui (pasta tests/) o projeto mais próximo seria outro
    paths = api.caso(CASE_ID)
    assert paths.pasta == (project / "casos" / "GLF49_2CVD_MOBO_New_In").resolve()
    assert paths.modelo.is_file() and paths.modelo.name == "1327.GLF49_2CVD_MOBO_New_In.dat"
    assert "Modelo montado" in capsys.readouterr().out

    # etapa que depende de um arquivo que ainda não existe: erro com a dica do que fazer
    with pytest.raises(api.CvdError, match="Modelo dinâmico base"):
        runpy.run_path(str(scripts / "04_estados_de_mar.py"), run_name="__main__")


def test_api_errors_are_cvd_errors(project, monkeypatch):
    monkeypatch.setattr(api, "_PROJECT_FILE", None)
    api.usar_projeto(project)
    with pytest.raises(api.CvdError, match="terminou com erro"):
        api.comando("inputs", "show", "ID QUE NÃO EXISTE")
    monkeypatch.setattr(api, "_PROJECT_FILE", None)
    with pytest.raises(api.CvdError, match="cvd_project.json"):
        api.usar_projeto(project.parent.parent)
