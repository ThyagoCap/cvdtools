"""Modelo base do OrcaFlex baixado de um repositório git (``base_repo`` no ``cvd_project.json``).

O repositório tem a pasta ``00.Base`` com o modelo base (``base_input.yml`` ou ``.dat``), como a aba
Operações cita. O ``setup`` (e o ``update``, que roda o setup) clona ou atualiza esse repositório em
``<pasta de projeto>/.cvd_base``; o build também baixa, se a pasta ainda não existir. Assim a equipe não
precisa receber a pasta ``00.Base``: basta ter acesso ao repositório (mesmo login do GitHub do cvdtools).

A cópia local é só leitura: se alguém a alterou, ela não é sobrescrita (o setup avisa).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable


class BaseRepoError(RuntimeError):
    pass


def _git() -> str:
    git = shutil.which("git")
    if not git:
        raise BaseRepoError("Git não encontrado. Instale o Git for Windows (git-scm.com) e rode de novo.")
    return git


def _run(args: list, cwd: Path | None = None, capture: bool = True) -> str:
    proc = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=capture, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() if capture else ""
        raise BaseRepoError(f"'{' '.join(str(a) for a in args[1:3])}' falhou (código {proc.returncode})"
                            + (f": {detail}" if detail else ""))
    return (proc.stdout or "").strip() if capture else ""


def sync_base(url: str, dest: Path, ref: str = "", say: Callable[[str], None] = print) -> str:
    """Clona (1ª vez) ou atualiza ``url`` em ``dest``. Devolve um resumo do que aconteceu."""
    git, dest = _git(), Path(dest)
    hint = (" Confira o endereço em 'base_repo' no cvd_project.json, o login do GitHub e se você tem acesso ao "
            "repositório.")
    if not (dest / ".git").is_dir():
        if dest.exists() and any(dest.iterdir()):
            raise BaseRepoError(f"{dest} existe e não é uma cópia do repositório do modelo base. Apague ou renomeie "
                                "a pasta e rode de novo.")
        say(f"  Baixando o modelo base de {url} ...")
        try:
            _run([git, "clone", "--depth", "1", *(["--branch", ref] if ref else []), url, str(dest)],
                 capture=False)   # sem capturar: a janela de login do Git pode aparecer
        except BaseRepoError as exc:
            raise BaseRepoError(f"Não consegui baixar o modelo base ({exc}).{hint}") from None
        return f"baixado em {dest} (versão {_run([git, 'rev-parse', '--short', 'HEAD'], dest)})"
    if _run([git, "status", "--porcelain"], dest):
        return (f"NÃO atualizado: a cópia em {dest} tem alterações locais. O modelo base é mantido no repositório; "
                f"apague a pasta {dest.name} para baixar de novo")
    old = _run([git, "rev-parse", "--short", "HEAD"], dest)
    try:
        _run([git, "fetch", "--depth", "1", "origin", ref or "HEAD"], dest)
    except BaseRepoError as exc:
        raise BaseRepoError(f"Não consegui atualizar o modelo base ({exc}).{hint}") from None
    _run([git, "reset", "--hard", "FETCH_HEAD"], dest)
    new = _run([git, "rev-parse", "--short", "HEAD"], dest)
    return f"já atualizado (versão {new})" if new == old else f"atualizado: versão {old} -> {new}"
