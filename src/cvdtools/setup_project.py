"""``python -m cvdtools setup``: prepara (ou atualiza) uma pasta de projeto com um comando só.

O que faz, sem nunca apagar nem sobrescrever trabalho do usuário:

1. ``cvd_project.json``: acha a planilha geral (aba "Input Sheet") e a pasta ``00.Base`` nesta pasta
   ou nas de cima; pergunta só o que não conseguir deduzir.
2. ``cvd_config.xlsx``: cria com os padrões ou, se já existe, acrescenta os parâmetros novos
   (``config upgrade``, com .bak).
3. ``cvd_curves.xlsx``: cria a biblioteca de curvas vazia, se não existe.
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
DEFAULT_REPO = "https://github.com/<ORG>/cvdtools.git"


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
                 workbook: Optional[str] = None, code: Optional[str] = None, vscode: bool = True):
        self.folder = Path(folder).resolve()
        self.ask, self.say = ask, say
        self.workbook_arg, self.code_arg, self.vscode = workbook, code, vscode
        self.notes: list[str] = []

    def run(self) -> int:
        self.say(f"cvdtools {__version__}: preparando {self.folder}")
        self.folder.mkdir(parents=True, exist_ok=True)
        project = self.project_file()
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
            return data
        wb = self._choose_workbook()
        code = self.code_arg if self.code_arg is not None else guess_project_code(wb)
        if not code:
            code = self.ask("Código do projeto (prefixo dos nomes dos arquivos, ex.: 1327; Enter = nenhum)", "")
        base = find_base_dir(self.folder)
        if base is None:
            self.notes.append("Não achei a pasta 00.Base (modelo base do OrcaFlex) nesta pasta nem nas de cima. "
                              f"Ajuste 'base_dir' no {PROJECT_FILE} e a coluna 'Modelo base' da aba Operações.")
        data = {"project_code": code, "workbook": _rel(wb, self.folder), "config": "cvd_config.xlsx",
                "analysis_root": "", "base_dir": _rel(base, self.folder) if base else ".",
                "curves": ["cvd_curves.xlsx"]}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.say(f"  {PROJECT_FILE}: criado (planilha {wb.name}, código '{code}', 00.Base em "
                 f"{base if base else '?'})")
        return data

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
        if not path.is_file():
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
            else:
                write_curve_template(path, [])
                self.say(f"  {path.name}: criado vazio (curvas de histerese e rigidez axial)")

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


def _console_ask(question: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {question}{suffix}: ")
    except EOFError:
        answer = ""
    return answer.strip() or default


def run_setup(folder: Optional[str] = None, workbook: Optional[str] = None, code: Optional[str] = None,
              vscode: bool = True, ask: Callable[[str, str], str] = _console_ask) -> int:
    return Setup(Path(folder) if folder else Path.cwd(), ask, workbook=workbook, code=code, vscode=vscode).run()
