"""``python -m cvdtools setup``: prepara (ou atualiza) uma pasta de projeto com um comando só.

O que faz, sem nunca apagar nem sobrescrever trabalho do usuário:

1. ``cvd_project.json``: acha a planilha geral (aba "Input Sheet") e a pasta ``00.Base`` nesta pasta
   ou nas de cima; pergunta só o que não conseguir deduzir. Com ``--base-repo <endereço git>`` (ou herdado
   do ``cvd_project.json`` do ``--config-from``), o modelo base vem desse repositório, baixado/atualizado em
   ``.cvd_base`` a cada setup (e update).
2. ``cvd_config.xlsx``: cria com os padrões ou, se já existe, acrescenta os parâmetros novos
   (``config upgrade``, com .bak).
3. ``cvd_curves.xlsx``: cria a biblioteca de curvas vazia, se não existe.
   Com ``--config-from <pasta>``, configuração e curvas que faltam são copiadas de outra pasta de projeto
   (padrão do projeto para toda a equipe).
4. ``scripts/``: um script por etapa (``00_caso.py``, ``01_...``). Script que o usuário editou não é
   trocado: a versão nova fica ao lado como ``<nome>.novo.py``.
5. ``.vscode/settings.json``: aponta o VS Code para o Python em que o cvdtools está instalado.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .project import PROJECT_FILE

SCRIPTS_DIR = "scripts"
STATE_FILE = ".cvd_scripts.json"          # hash de cada script gerado (para saber se o usuário editou)
USER_FILES = {"00_caso.py"}               # guardam o caso do usuário: nunca trocados depois de criados
DEFAULT_REPO = "https://github.com/ThyagoCap/cvdtools.git"


def _sha(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def repo_url() -> str:
    """URL do git de onde o cvdtools foi instalado (pip guarda em direct_url.json), ou o padrão."""
    try:
        from importlib.metadata import distribution

        data = json.loads(distribution("cvdtools").read_text("direct_url.json") or "{}")
        if data.get("vcs_info") and data.get("url"):
            return data["url"]
    except Exception:
        pass
    return DEFAULT_REPO


def install_command() -> str:
    return f'python -m pip install --upgrade "cvdtools[orcaflex] @ git+{repo_url()}"'


# --------------------------------------------------------------------------- descoberta

def _has_input_sheet(path: Path) -> bool:
    try:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True)
        try:
            return "Input Sheet" in wb.sheetnames
        finally:
            wb.close()
    except Exception:
        return False


def find_workbooks(folder: Path, levels: int = 3) -> list[Path]:
    """Planilhas com aba "Input Sheet" nesta pasta e até ``levels`` pastas acima (mais perto primeiro)."""
    found: list[Path] = []
    for d in [folder, *list(folder.parents)[:levels]]:
        for p in sorted(d.glob("*.xls[xm]")):
            if p.name.startswith("~$") or p.name in ("cvd_config.xlsx", "cvd_curves.xlsx"):
                continue
            if _has_input_sheet(p):
                found.append(p)
        if found:
            break
    return found


def find_base_dir(folder: Path, levels: int = 3) -> Optional[Path]:
    """Pasta que contém ``00.Base`` (onde fica o modelo base da aba Operações)."""
    for d in [folder, *list(folder.parents)[:levels]]:
        if (d / "00.Base").is_dir():
            return d
    return None


def guess_project_code(workbook: Path) -> str:
    m = re.match(r"(\d{3,5})[._ -]", workbook.name)
    return m.group(1) if m else ""


def _rel(target: Path, start: Path) -> str:
    import os

    try:
        return Path(os.path.relpath(target, start)).as_posix()
    except ValueError:   # outro drive no Windows
        return str(target)


# --------------------------------------------------------------------------- etapas

class Setup:
    def __init__(self, folder: Path, ask: Callable[[str, str], str], say: Callable[[str], None] = print,
                 workbook: Optional[str] = None, code: Optional[str] = None, vscode: bool = True,
                 config_from: Optional[str] = None, base_repo: Optional[str] = None,
                 base_ref: Optional[str] = None):
        self.folder = Path(folder).resolve()
        self.config_from = _source_folder(config_from) if config_from else None
        self.ask, self.say = ask, say
        self.workbook_arg, self.code_arg, self.vscode = workbook, code, vscode
        self.base_repo_arg = (base_repo or "").strip() or None
        self.base_ref_arg = base_ref.strip() if base_ref is not None else None
        self.notes: list[str] = []

    def run(self) -> int:
        self.say(f"cvdtools {__version__}: preparando {self.folder}")
        self.folder.mkdir(parents=True, exist_ok=True)
        project = self.project_file()
        self.base_model(project)
        self.config(project)
        self.curves(project)
        self.scripts()
        if self.vscode:
            self.vscode_settings()
        self.say("")
        for n in self.notes:
            self.say(f"  ! {n}")
        self.say("Pronto. Próximo passo: abra esta pasta no VS Code, preencha o ID em scripts/00_caso.py e rode "
                 "os scripts na ordem (botão Run Python File, no canto superior direito).")
        return 0

    # 1 ---------------------------------------------------------------- cvd_project.json
    def project_file(self) -> dict:
        path = self.folder / PROJECT_FILE
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.say(f"  {PROJECT_FILE}: já existe (mantido)")
            wb = (self.folder / data.get("workbook", "")).resolve() if data.get("workbook") else None
            if wb is None or not wb.is_file():
                self.notes.append(f"A planilha geral do {PROJECT_FILE} não foi encontrada ({wb}). Corrija o "
                                  "campo 'workbook' no arquivo.")
            if self.base_repo_arg or self.base_ref_arg is not None:
                repo = self.base_repo_arg or data.get("base_repo", "")
                ref = self.base_ref_arg if self.base_ref_arg is not None else data.get("base_ref", "")
                if (repo, ref) != (data.get("base_repo", ""), data.get("base_ref", "")):
                    data["base_repo"], data["base_ref"] = repo, ref
                    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    self.say(f"  {PROJECT_FILE}: modelo base passa a vir de {repo}" + (f" ({ref})" if ref else ""))
            return data
        wb = self._choose_workbook()
        code = self.code_arg if self.code_arg is not None else guess_project_code(wb)
        if not code:
            code = self.ask("Código do projeto (prefixo dos nomes dos arquivos, ex.: 1327; Enter = nenhum)", "")
        repo, ref = self._base_repo_choice()
        base = None if repo else find_base_dir(self.folder)
        if base is None and not repo:
            self.notes.append("Não achei a pasta 00.Base (modelo base do OrcaFlex) nesta pasta nem nas de cima. "
                              f"Ajuste 'base_dir' no {PROJECT_FILE} (ou use 'setup --base-repo <endereço git>') e a "
                              "coluna 'Modelo base' da aba Operações.")
        data = {"project_code": code, "workbook": _rel(wb, self.folder), "config": "cvd_config.xlsx",
                "analysis_root": "", "base_dir": _rel(base, self.folder) if base else ".",
                "curves": ["cvd_curves.xlsx"]}
        if repo:
            data.update(base_repo=repo, base_ref=ref)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        where = f"repositório {repo}" if repo else f"00.Base em {base if base else '?'}"
        self.say(f"  {PROJECT_FILE}: criado (planilha {wb.name}, código '{code}', {where})")
        return data

    def _base_repo_choice(self) -> tuple[str, str]:
        """Repositório do modelo base: o de ``--base-repo`` ou o do projeto de referência (``--config-from``)."""
        if self.base_repo_arg:
            return self.base_repo_arg, self.base_ref_arg or ""
        if self.config_from and (self.config_from / PROJECT_FILE).is_file():
            try:
                ref_data = json.loads((self.config_from / PROJECT_FILE).read_text(encoding="utf-8"))
            except ValueError:
                return "", ""
            repo = str(ref_data.get("base_repo") or "").strip()
            if repo:
                self.say(f"  modelo base: repositório copiado de {self.config_from / PROJECT_FILE}")
                return repo, str(ref_data.get("base_ref") or "").strip()
        return "", ""

    # 1b --------------------------------------------------------------- modelo base (base_repo)
    def base_model(self, project: dict) -> None:
        repo = str(project.get("base_repo") or "").strip()
        if not repo:
            return
        from .base_repo import BaseRepoError, sync_base
        from .project import BASE_CACHE

        try:
            result = sync_base(repo, self.folder / BASE_CACHE, str(project.get("base_ref") or "").strip(), self.say)
        except BaseRepoError as exc:
            self.notes.append(str(exc))
            return
        self.say(f"  modelo base ({BASE_CACHE}): {result}")
        if result.startswith("NÃO"):
            self.notes.append(f"Modelo base {result}.")

    def _choose_workbook(self) -> Path:
        if self.workbook_arg:
            p = Path(self.workbook_arg)
            p = (p if p.is_absolute() else self.folder / p).resolve()
            if not p.is_file():
                raise FileNotFoundError(f"Planilha geral não encontrada: {p}")
            return p
        found = find_workbooks(self.folder)
        if len(found) == 1:
            return found[0]
        if found:
            self.say("  Planilhas com a aba 'Input Sheet':")
            for i, p in enumerate(found, 1):
                self.say(f"    {i}. {p}")
            answer = self.ask("Qual é a planilha geral? (número)", "1")
            try:
                return found[int(answer) - 1]
            except (ValueError, IndexError):
                raise ValueError(f"Resposta inválida: {answer!r}")
        answer = self.ask("Não achei a planilha geral (aba 'Input Sheet'). Caminho do arquivo", "")
        p = Path(answer.strip().strip('"'))
        p = (p if p.is_absolute() else self.folder / p).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Planilha geral não encontrada: {p}")
        return p

    # 2 ---------------------------------------------------------------- cvd_config.xlsx
    def config(self, project: dict) -> None:
        from .io.config import default_config, upgrade_config, write_config

        path = (self.folder / project.get("config", "cvd_config.xlsx")).resolve()
        if not path.is_file() and self._copy_from(path.name, path):
            pass
        elif not path.is_file():
            write_config(default_config(None), path)
            self.say(f"  {path.name}: criado com os padrões (revise as abas Defaults, Papéis e Mares)")
            self.notes.append(f"Revise o {path.name}: nomes dos objetos do modelo base (aba Papéis), padrões do "
                              "projeto (Defaults) e grade de estados de mar (Mares).")
            return
        try:
            changes = upgrade_config(path, {})
        except PermissionError:
            self.notes.append(f"Não consegui gravar o {path.name}: feche-o no Excel e rode o setup de novo.")
            return
        self.say(f"  {path.name}: " + (f"{len(changes)} atualização(ões) (cópia .bak feita)" if changes
                                       else "já atualizado"))
        for c in changes:
            self.say(f"      {c}")

    # 3 ---------------------------------------------------------------- cvd_curves.xlsx
    def curves(self, project: dict) -> None:
        from .curves import write_curve_template

        for name in project.get("curves") or []:
            path = (self.folder / name).resolve()
            if path.is_file():
                self.say(f"  {path.name}: já existe (mantido)")
            elif self._copy_from(Path(name).name, path):
                pass
            else:
                write_curve_template(path, [])
                self.say(f"  {path.name}: criado vazio (curvas de histerese e rigidez axial)")

    def _copy_from(self, name: str, dest: Path) -> bool:
        """Copia ``name`` da pasta de projeto de referência (``--config-from``), se houver."""
        if not self.config_from:
            return False
        src = self.config_from / name
        if not src.is_file():
            self.notes.append(f"--config-from: {src} não existe; {dest.name} criado com os padrões.")
            return False
        import shutil

        shutil.copy2(src, dest)
        self.say(f"  {dest.name}: copiado de {src}")
        return True

    # 4 ---------------------------------------------------------------- scripts
    def scripts(self) -> None:
        target = self.folder / SCRIPTS_DIR
        target.mkdir(exist_ok=True)
        state_path = target / STATE_FILE
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        hashes = state.get("hashes", {})
        created, updated, kept = [], [], []
        templates = resources.files("cvdtools") / "templates" / "scripts"
        for item in sorted(templates.iterdir(), key=lambda t: t.name):
            if not item.is_file() or item.name.startswith(("_", ".")) or item.name.endswith(".pyc"):
                continue
            text = item.read_text(encoding="utf-8").replace("{install_command}", install_command())
            dest = target / item.name
            new_hash = _sha(text)
            previous = hashes.get(item.name)            # versão que o setup gravou (ou ofereceu) da última vez
            if not dest.exists():
                dest.write_text(text, encoding="utf-8")
                created.append(item.name)
            else:
                current = _sha(dest.read_text(encoding="utf-8"))
                if current == new_hash or previous == new_hash:
                    pass                                # igual ao modelo, ou modelo não mudou desde a última vez
                elif current == previous and item.name not in USER_FILES:
                    dest.write_text(text, encoding="utf-8")   # não editado: troca pela versão nova
                    updated.append(item.name)
                else:                                   # editado pelo usuário: versão nova ao lado
                    alt = dest.with_name(dest.stem + ".novo" + dest.suffix)
                    alt.write_text(text, encoding="utf-8")
                    self.notes.append(f"scripts/{item.name} foi editado: a versão nova está em scripts/{alt.name} "
                                      "(compare e apague a que não quiser).")
            hashes[item.name] = new_hash
        state_path.write_text(json.dumps({"version": __version__, "hashes": hashes}, indent=2), encoding="utf-8")
        parts = []
        if created:
            parts.append(f"{len(created)} criado(s)")
        if updated:
            parts.append(f"{len(updated)} atualizado(s)")
        self.say(f"  {SCRIPTS_DIR}/: " + (", ".join(parts) if parts else "já atualizados"))

    # 5 ---------------------------------------------------------------- VS Code
    def vscode_settings(self) -> None:
        path = self.folder / ".vscode" / "settings.json"
        data: dict = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError:
                self.notes.append(f"{path} não é um JSON simples (tem comentários?): não alterado. Escolha o Python "
                                  f"no VS Code (Ctrl+Shift+P > Python: Select Interpreter): {sys.executable}")
                return
        wanted = {"python.defaultInterpreterPath": sys.executable,
                  "python.terminal.executeInFileDir": True}
        current = data.get("python.defaultInterpreterPath")
        if current and current != sys.executable and Path(current).is_file():
            # pasta compartilhada: outro engenheiro já apontou para o Python dele, que existe nesta máquina
            self.say(f"  .vscode/settings.json: mantido (Python {current})")
            return
        if all(data.get(k) == v for k, v in wanted.items()):
            self.say("  .vscode/settings.json: já aponta para este Python")
            return
        data.update(wanted)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.say(f"  .vscode/settings.json: VS Code usa {sys.executable}")


def _source_folder(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    return p.parent if p.is_file() else p


def refresh_scripts_if_outdated(scripts_dir: Path, say: Callable[[str], None] = print) -> bool:
    """Scripts gerados por outra versão do cvdtools: atualiza os não editados (como o ``setup``). True se mudou.

    Chamado pelos scripts (``api.ler_caso``): depois de atualizar o cvdtools, a pasta fica no padrão da versão
    instalada sem o usuário precisar lembrar do ``setup``.
    """
    state_path = Path(scripts_dir) / STATE_FILE
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if state.get("version") == __version__:
        return False
    old = state.get("version", "?")
    s = Setup(Path(scripts_dir).parent, ask=lambda q, d: d, say=lambda m: None, vscode=False)
    s.scripts()
    say(f"cvdtools {__version__} (scripts eram da {old}): scripts atualizados."
        + "".join(f"\n  ! {n}" for n in s.notes)
        + "\n  Para acrescentar os parâmetros novos ao cvd_config.xlsx: python -m cvdtools setup")
    return True


def update(folder: Optional[str] = None, ref: str = "", setup: bool = True) -> int:
    """``python -m cvdtools update``: instala a versão do GitHub (``ref``: tag ou branch) e roda o ``setup``."""
    import subprocess

    try:
        from importlib.metadata import distribution

        info = json.loads(distribution("cvdtools").read_text("direct_url.json") or "{}")
    except Exception:
        info = {}
    if info.get("dir_info", {}).get("editable"):
        print("cvdtools instalado em modo de desenvolvimento (pip install -e): atualize com 'git pull' na pasta do "
              f"código ({info.get('url', '')}).")
        return 1
    url = repo_url()
    spec = f"cvdtools[orcaflex] @ git+{url}" + (f"@{ref}" if ref else "")
    print(f"Instalando {spec} ...")
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade", spec])
    if rc != 0:
        print("A instalação falhou (veja as mensagens acima). Confira o login do GitHub e o Git for Windows.")
        return rc
    target = Path(folder).resolve() if folder else Path.cwd()
    if setup and (target / PROJECT_FILE).is_file():
        # processo novo: roda o setup da versão recém-instalada
        return subprocess.call([sys.executable, "-m", "cvdtools", "setup", "--folder", str(target)])
    if setup:
        print(f"Sem {PROJECT_FILE} em {target}: rode 'python -m cvdtools setup' na pasta do projeto.")
    return 0


def _console_ask(question: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {question}{suffix}: ")
    except EOFError:
        answer = ""
    return answer.strip() or default


def run_setup(folder: Optional[str] = None, workbook: Optional[str] = None, code: Optional[str] = None,
              vscode: bool = True, ask: Callable[[str, str], str] = _console_ask,
              config_from: Optional[str] = None, base_repo: Optional[str] = None,
              base_ref: Optional[str] = None) -> int:
    return Setup(Path(folder) if folder else Path.cwd(), ask, workbook=workbook, code=code, vscode=vscode,
                 config_from=config_from, base_repo=base_repo, base_ref=base_ref).run()
