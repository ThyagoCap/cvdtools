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
    with pytest.raises(api.CvdError, match="Pasta das posições"):
        runpy.run_path(str(scripts / "04_estados_de_mar.py"), run_name="__main__")

    # 0.12.0: com a posição 01.Horizontal salva, o 04 cria o <nome>_dyn.dat e gera os estados de mar
    from fake_orcfxapi import FakeModel

    pos = paths.pasta / "01.Positions"
    pos.mkdir()
    FakeModel(str(paths.modelo)).SaveData(str(pos / "01.Horizontal.dat"))
    script = (scripts / "04_estados_de_mar.py").read_text(encoding="utf-8").replace("SIMULTANEAS = None", "SIMULTANEAS = 1")
    (scripts / "04_estados_de_mar.py").write_text(script, encoding="utf-8")
    runpy.run_path(str(scripts / "04_estados_de_mar.py"), run_name="__main__")
    assert paths.modelo_dinamico.is_file()
    assert (paths.dinamica / "manifest.csv").is_file() and list(paths.dinamica.glob("*.dat"))


def test_api_errors_are_cvd_errors(project, monkeypatch):
    monkeypatch.setattr(api, "_PROJECT_FILE", None)
    api.usar_projeto(project)
    with pytest.raises(api.CvdError, match="terminou com erro"):
        api.comando("inputs", "show", "ID QUE NÃO EXISTE")
    monkeypatch.setattr(api, "_PROJECT_FILE", None)
    with pytest.raises(api.CvdError, match="cvd_project.json"):
        api.usar_projeto(project.parent.parent)


def test_scripts_refresh_after_library_update(project, capsys):
    """0.12.1: scripts gerados por outra versão são atualizados quando um script roda (sem o usuário rodar setup)."""
    import cvdtools.setup_project as sp

    scripts = project / "scripts"
    state = json.loads((scripts / ".cvd_scripts.json").read_text(encoding="utf-8"))
    state["version"] = "0.0.1"
    old = scripts / "05_rodar_simulacoes.py"
    state["hashes"]["05_rodar_simulacoes.py"] = sp._sha("# versão antiga\n")
    old.write_text("# versão antiga\n", encoding="utf-8")
    (scripts / ".cvd_scripts.json").write_text(json.dumps(state), encoding="utf-8")
    assert sp.refresh_scripts_if_outdated(scripts)
    assert "scripts atualizados" in capsys.readouterr().out
    assert "rodar_simulacoes" in old.read_text(encoding="utf-8")
    assert not sp.refresh_scripts_if_outdated(scripts), "mesma versão: nada a fazer"


def test_setup_copies_config_from_reference_project(project, tmp_path):
    """0.12.1: --config-from copia cvd_config.xlsx e cvd_curves.xlsx do projeto de referência."""
    ref_config = (project / "cvd_config.xlsx").read_bytes()
    new = tmp_path / "outro"
    new.mkdir()
    assert run_setup(str(new), ask=NO, vscode=False, config_from=str(project)) == 0
    assert (new / "cvd_config.xlsx").read_bytes() == ref_config
    assert (new / "cvd_curves.xlsx").is_file()


def test_update_installs_from_github_then_runs_setup(project, monkeypatch):
    import subprocess

    import cvdtools.setup_project as sp

    calls = []
    monkeypatch.setattr(subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(sp, "repo_url", lambda: "https://github.com/ThyagoCap/cvdtools.git")

    class Dist:
        def read_text(self, name):
            return json.dumps({"url": "https://github.com/ThyagoCap/cvdtools.git", "vcs_info": {"vcs": "git"}})

    import importlib.metadata as md

    monkeypatch.setattr(md, "distribution", lambda name: Dist())
    assert sp.update(str(project), ref="v0.12.1") == 0
    assert calls[0][-1] == "cvdtools[orcaflex] @ git+https://github.com/ThyagoCap/cvdtools.git@v0.12.1"
    assert calls[1][-3:] == ["setup", "--folder", str(project.resolve())]

    class Editable(Dist):
        def read_text(self, name):
            return json.dumps({"url": "file:///c/new_code", "dir_info": {"editable": True}})

    monkeypatch.setattr(md, "distribution", lambda name: Editable())
    calls.clear()
    assert sp.update(str(project)) == 1 and calls == [], "instalação -e: não mexe (git pull)"
