"""Arquivo de projeto ``cvd_project.json``: evita repetir caminhos na linha de comando.

Exemplo (caminhos relativos ao próprio arquivo)::

    {
      "project_code": "1327",
      "workbook": "../1327.planilha_geral_offline.xlsx",
      "config": "cvd_config.xlsx",
      "analysis_root": "../..",
      "base_dir": "..",
      "curves": ["cvd_curves.xlsx"]
    }

``base_dir`` é a pasta a partir da qual o "Modelo base" da aba Operações é resolvido.

Com ``"base_repo": "<endereço git>"`` (e, opcional, ``"base_ref": "<branch ou tag>"``), o modelo base vem
desse repositório, baixado em ``<pasta do projeto>/.cvd_base`` (ver :mod:`cvdtools.base_repo`); ``base_dir``
é ignorado.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_FILE = "cvd_project.json"
BASE_CACHE = ".cvd_base"   # cópia local do repositório do modelo base (base_repo)


@dataclass
class Project:
    path: Optional[Path]
    project_code: str
    workbook: Path
    config: Path
    analysis_root: Optional[Path]
    base_dir: Path
    curves: list = None   # planilhas de curvas (cvd_curves.xlsx)
    base_repo: str = ""   # repositório git do modelo base (baixado em <projeto>/.cvd_base)
    base_ref: str = ""    # branch ou tag desse repositório (vazio = padrão)

    def curve_library(self):
        """Biblioteca de curvas do projeto (None se o projeto não declara nenhuma)."""
        from .curves import CurveLibrary

        if not self.curves:
            return None
        root = self.path.parent if self.path else Path.cwd()
        return CurveLibrary.load([Path(p) for p in self.curves], base_dir=root)

    def sync_base(self, say=print) -> str:
        """Baixa ou atualiza o repositório do modelo base (``base_repo``) em ``base_dir``."""
        from .base_repo import sync_base

        return sync_base(self.base_repo, self.base_dir, self.base_ref, say)

    def base_model(self, relative: str) -> Path:
        """Caminho do modelo base. Com ``base_repo``, baixa o repositório se ainda não foi baixado. Se o arquivo
        citado não existe mas há o mesmo nome com a outra extensão (.dat <-> .yml), usa esse."""
        if self.base_repo and not (self.base_dir / ".git").is_dir():
            from .log import log

            log.info("Modelo base: %s", self.sync_base(say=log.info))
        p = Path(relative)
        p = p if p.is_absolute() else (self.base_dir / p).resolve()
        if not p.exists():
            for ext in (".yml", ".dat"):
                alt = p.with_suffix(ext)
                if alt != p and alt.exists():
                    return alt
            where = f"repositório {self.base_repo}" if self.base_repo else f"base_dir = {self.base_dir}"
            raise FileNotFoundError(f"Modelo base não encontrado: {p} ({where}). Confira a coluna 'Modelo base' da "
                                    f"aba Operações do cvd_config.xlsx.")
        return p

    def case_dir(self, well: str, folder: str) -> Optional[Path]:
        """Procura <analysis_root>/*<GLF49>*/<Folder> (ex.: 05.GLF49/02.CVD2-MOBO Out)."""
        if not self.analysis_root or not well or not folder:
            return None
        token = well.replace("-", "").replace(" ", "")
        for d in sorted(self.analysis_root.iterdir()):
            if d.is_dir() and token.lower() in d.name.replace("-", "").lower():
                return d / folder
        return None


def find_project_file(start: Path) -> Optional[Path]:
    start = Path(start).resolve()
    for d in (start, *start.parents):
        if (d / PROJECT_FILE).is_file():
            return d / PROJECT_FILE
    return None


def load_project(path: Optional[Path] = None, workbook: Optional[str] = None,
                 config: Optional[str] = None, start: Optional[Path] = None) -> Project:
    data: dict = {}
    root = Path.cwd()
    if path is None:
        path = find_project_file(start or Path.cwd())
    if path is not None:
        path = Path(path).resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        root = path.parent

    def rel(v):
        return (root / v).resolve() if v else None

    wb = Path(workbook).resolve() if workbook else rel(data.get("workbook"))
    cfg = Path(config).resolve() if config else rel(data.get("config"))
    if wb is None or cfg is None:
        raise FileNotFoundError(
            f"Planilha geral e/ou configuração não definidas. Crie um {PROJECT_FILE} "
            "('cvd project init') ou use --workbook e --config.")
    curves = data.get("curves") or []
    if isinstance(curves, str):
        curves = [curves]
    base_repo = str(data.get("base_repo") or "").strip()
    base_dir = (root / BASE_CACHE).resolve() if base_repo else (rel(data.get("base_dir")) or root)
    return Project(path, str(data.get("project_code", "")), wb, cfg, rel(data.get("analysis_root")),
                   base_dir, [str(rel(c)) for c in curves], base_repo, str(data.get("base_ref") or "").strip())


def write_project_file(directory: Path, project_code: str, workbook: str, config: str,
                       analysis_root: str = "", base_dir: str = "", curves: Optional[list] = None) -> Path:
    path = Path(directory) / PROJECT_FILE
    if path.exists():
        raise FileExistsError(f"{path} já existe")
    data = {"project_code": project_code, "workbook": workbook, "config": config,
            "analysis_root": analysis_root, "base_dir": base_dir, "curves": list(curves or [])}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
