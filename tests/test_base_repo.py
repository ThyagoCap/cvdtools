"""0.14.0: modelo base baixado de um repositório git (base_repo no cvd_project.json)."""

import json
import shutil
import subprocess

import pytest

from cvdtools.base_repo import BaseRepoError, sync_base
from cvdtools.project import BASE_CACHE, load_project
from cvdtools.setup_project import run_setup
from helpers import full_workbook

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="precisa do git")
NO = lambda q, d: d   # noqa: E731


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def base_repo(tmp_path):
    """Repositório com 00.Base/base_input.yml; devolve (pasta, endereço file://)."""
    repo = tmp_path / "base_repo"
    (repo / "00.Base").mkdir(parents=True)
    (repo / "00.Base" / "base_input.yml").write_text("General:\n  versao: 1\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "v1")
    return repo, repo.as_uri()


def _commit_v2(repo):
    (repo / "00.Base" / "base_input.yml").write_text("General:\n  versao: 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "v2")


def test_sync_clones_updates_and_keeps_local_edits(tmp_path, base_repo):
    repo, url = base_repo
    dest = tmp_path / "proj" / BASE_CACHE
    assert sync_base(url, dest, say=lambda m: None).startswith("baixado")
    model = dest / "00.Base" / "base_input.yml"
    assert "versao: 1" in model.read_text(encoding="utf-8")
    assert sync_base(url, dest).startswith("já atualizado")

    _commit_v2(repo)
    assert sync_base(url, dest).startswith("atualizado")
    assert "versao: 2" in model.read_text(encoding="utf-8")

    model.write_text("mexido à mão\n", encoding="utf-8")
    assert sync_base(url, dest).startswith("NÃO atualizado")
    assert model.read_text(encoding="utf-8") == "mexido à mão\n"


def test_sync_reports_bad_address(tmp_path):
    with pytest.raises(BaseRepoError, match="base_repo"):
        sync_base((tmp_path / "nao_existe").as_uri(), tmp_path / "dest", say=lambda m: None)


def _project_file(folder, **extra):
    data = {"project_code": "1327", "workbook": "geral.xlsx", "config": "cvd_config.xlsx", "base_dir": ".",
            "curves": [], **extra}
    (folder / "cvd_project.json").write_text(json.dumps(data), encoding="utf-8")
    return load_project(folder / "cvd_project.json")


def test_project_downloads_on_first_use_and_accepts_yml_for_dat(tmp_path, base_repo):
    _, url = base_repo
    folder = tmp_path / "proj"
    folder.mkdir()
    proj = _project_file(folder, base_repo=url)
    assert proj.base_dir == (folder / BASE_CACHE).resolve()
    # a aba Operações cita o .dat; o repositório tem o .yml
    path = proj.base_model("00.Base/base_input.dat")
    assert path == (folder / BASE_CACHE / "00.Base" / "base_input.yml").resolve()
    with pytest.raises(FileNotFoundError, match="Operações"):
        proj.base_model("00.Base/outro_modelo.dat")


def test_base_dir_without_repo_is_unchanged(tmp_path):
    (tmp_path / "00.Base").mkdir()
    (tmp_path / "00.Base" / "base_input.dat").write_text("x", encoding="utf-8")
    proj = _project_file(tmp_path)
    assert not proj.base_repo
    assert proj.base_model("00.Base/base_input.dat") == (tmp_path / "00.Base" / "base_input.dat").resolve()


def test_setup_with_base_repo_and_inherited_by_config_from(tmp_path, base_repo, legacy_inputs, monkeypatch):
    _, url = base_repo
    full_workbook(tmp_path / "1327.planilha_geral.xlsx", legacy_inputs)   # sem 00.Base em lugar nenhum
    monkeypatch.chdir(tmp_path)
    ref = tmp_path / "referencia"
    ref.mkdir()
    assert run_setup(str(ref), ask=NO, vscode=False, base_repo=url) == 0
    data = json.loads((ref / "cvd_project.json").read_text(encoding="utf-8"))
    assert data["base_repo"] == url
    assert (ref / BASE_CACHE / "00.Base" / "base_input.yml").is_file()

    novo = tmp_path / "novo"
    novo.mkdir()
    assert run_setup(str(novo), ask=NO, vscode=False, config_from=str(ref)) == 0
    assert json.loads((novo / "cvd_project.json").read_text(encoding="utf-8"))["base_repo"] == url
    assert (novo / BASE_CACHE / "00.Base" / "base_input.yml").is_file()


def test_setup_adds_base_repo_to_existing_project(tmp_path, base_repo, legacy_inputs, monkeypatch):
    _, url = base_repo
    full_workbook(tmp_path / "1327.planilha_geral.xlsx", legacy_inputs)
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "proj"
    folder.mkdir()
    assert run_setup(str(folder), ask=NO, vscode=False) == 0
    assert "base_repo" not in json.loads((folder / "cvd_project.json").read_text(encoding="utf-8"))
    assert run_setup(str(folder), ask=NO, vscode=False, base_repo=url, base_ref="main") == 0
    data = json.loads((folder / "cvd_project.json").read_text(encoding="utf-8"))
    assert (data["base_repo"], data["base_ref"]) == (url, "main")
    assert load_project(folder / "cvd_project.json").base_model("00.Base/base_input.dat").suffix == ".yml"
