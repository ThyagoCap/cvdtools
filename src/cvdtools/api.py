"""Interface Python do cvdtools: uma função por etapa, para os scripts gerados pelo ``cvdtools setup``.

Cada função chama o mesmo comando da linha de comando (``python -m cvdtools ...``), então o
comportamento, os logs e as mensagens são os mesmos. Os caminhos do caso (pasta, modelo, modelo
dinâmico) são deduzidos do ID; o projeto (``cvd_project.json``) é procurado a partir da pasta do
script que chamou, não da pasta em que o VS Code abriu o terminal.

Exemplo::

    from cvdtools import api

    api.montar_modelo("GLF27 2CVD MOBO New Out")
    api.posicoes_estaticas("GLF27 2CVD MOBO New Out")

Um erro do pipeline levanta ``CvdError``. Nos scripts, a mensagem aparece sem o "traceback" do Python.
"""

from __future__ import annotations

import inspect
import runpy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

CASE_SETTINGS_FILE = "00_caso.py"


class CvdError(RuntimeError):
    """Etapa do pipeline terminou com erro (a mensagem já foi mostrada pelo comando)."""


def _excepthook(exc_type, exc, tb):   # nos scripts: só a mensagem, sem traceback
    if issubclass(exc_type, CvdError):
        print(f"\n*** {exc}", file=sys.stderr)
        return
    _previous_hook(exc_type, exc, tb)


_previous_hook = sys.excepthook
sys.excepthook = _excepthook


# --------------------------------------------------------------------------- projeto e caso

_PROJECT_FILE: Optional[Path] = None


def usar_projeto(caminho) -> Path:
    """Fixa o ``cvd_project.json`` (arquivo ou pasta) usado pelas funções seguintes."""
    from .project import PROJECT_FILE, find_project_file

    global _PROJECT_FILE
    p = Path(caminho).resolve()
    found = p if p.is_file() else find_project_file(p)
    if found is None:
        raise CvdError(f"Não achei {PROJECT_FILE} em {p} nem nas pastas acima. Rode 'python -m cvdtools setup' "
                       "na pasta do projeto.")
    _PROJECT_FILE = found
    return found


def _caller_dir() -> Path:
    """Pasta do script que chamou a API (fora do pacote cvdtools)."""
    here = Path(__file__).resolve().parent
    for frame in inspect.stack()[1:]:
        f = Path(frame.filename)
        try:
            f = f.resolve()
        except OSError:
            continue
        if f.is_file() and here not in f.parents:
            return f.parent
    return Path.cwd()


def projeto_atual() -> Path:
    """``cvd_project.json`` em uso: o fixado por ``usar_projeto`` ou o mais próximo do script que chamou."""
    if _PROJECT_FILE is not None:
        return _PROJECT_FILE
    from .project import find_project_file

    for start in (_caller_dir(), Path.cwd()):
        found = find_project_file(start)
        if found is not None:
            return found
    raise CvdError("Não achei o cvd_project.json. Rode 'python -m cvdtools setup' na pasta do projeto, ou "
                   "deixe o script dentro dela.")


def ler_caso(script: Optional[str] = None) -> dict:
    """Configurações de ``00_caso.py`` (na pasta do ``script``, ou do script que chamou)."""
    folder = Path(script).resolve().parent if script else _caller_dir()
    try:   # 0.12.1: cvdtools atualizado -> scripts no padrão da versão instalada
        from .setup_project import refresh_scripts_if_outdated

        refresh_scripts_if_outdated(folder)
    except Exception as exc:
        print(f"Aviso: não consegui conferir a versão dos scripts ({exc})")
    path = folder / CASE_SETTINGS_FILE
    if not path.is_file():
        raise CvdError(f"Não achei {path}. Ele é criado pelo 'python -m cvdtools setup'.")
    values = runpy.run_path(str(path), run_name="cvd_settings")
    out = {k: v for k, v in values.items() if k.isupper()}
    if not str(out.get("ID") or "").strip():
        raise CvdError(f"Preencha o ID do caso em {path} (coluna ID da Input Sheet).")
    return out


@dataclass
class CasoPaths:
    id: str
    pasta: Path            # pasta do caso
    nome: str              # nome dos arquivos (<código>.<ID com _>)
    modelo: Path           # modelo montado pelo 'montar_modelo'
    modelo_dinamico: Path  # modelo dinâmico base (feito à mão a partir das posições)
    dinamica: Path         # pasta das simulações irregulares
    triagem: Path          # pasta da triagem (domínio da frequência etc.)
    resultados: Path       # planilha do pós-processamento

    def __str__(self) -> str:
        def mark(p: Path) -> str:
            return "ok" if p.exists() else "ainda não existe"

        return "\n".join([
            f"Caso:              {self.id}",
            f"Pasta do caso:     {self.pasta} ({mark(self.pasta)})",
            f"Modelo montado:    {self.modelo.name} ({mark(self.modelo)})",
            f"Modelo dinâmico:   {self.modelo_dinamico.name} ({mark(self.modelo_dinamico)})",
            f"Simulações:        {self.dinamica} ({mark(self.dinamica)})",
            f"Resultados:        {self.resultados.name} ({mark(self.resultados)})",
        ])


def caso(case_id: str, pasta: Optional[str] = None) -> CasoPaths:
    """Caminhos padrão do caso (sem ``pasta``: os mesmos que o ``build`` usa)."""
    from .inputs import slug
    from .project import load_project

    proj = load_project(projeto_atual())
    if pasta:
        folder = Path(pasta)
        if not folder.is_absolute():
            folder = proj.path.parent / folder
        folder = folder.resolve()
    elif proj.analysis_root:   # pasta do poço pela Input Sheet: precisa ler a planilha
        from .pipeline import case_output_dir, load_case

        folder = case_output_dir(proj, load_case(proj, case_id), None)
    else:
        from .pipeline import default_case_dir

        folder = default_case_dir(proj, case_id)
    name = f"{proj.project_code}.{slug(case_id)}" if proj.project_code else slug(case_id)
    dyn = folder / "02.Dynamic"
    return CasoPaths(case_id, folder, name, folder / f"{name}.dat", folder / f"{name}_dyn.dat", dyn,
                     folder / "05.Screening", dyn / f"{slug(case_id)}_resultados.xlsx")


# --------------------------------------------------------------------------- execução dos comandos

def comando(*args, detalhes: bool = True) -> int:
    """Roda ``python -m cvdtools <args>`` com o projeto atual. Erro -> ``CvdError``.

    Para comandos sem função própria aqui, ex.: ``api.comando("inputs", "list")``.
    """
    from .cli import main

    argv = [str(a) for a in args if a is not None and a != ""]
    project = projeto_atual()
    if not _accepts_project(argv):
        rc = main(argv)
    else:
        rc = main(argv + ["--project", str(project)] + (["-v"] if detalhes else []))
    if rc not in (0, None):
        raise CvdError(f"'{' '.join(argv[:2])}' terminou com erro (código {rc}). Veja as mensagens acima.")
    return rc or 0


def _accepts_project(argv: list[str]) -> bool:
    return argv[:1] not in (["build-legacy"], ["compare-models"]) and argv[:2] not in (
        ["project", "init"], ["config", "init"], ["baseline", "capture"], ["baseline", "verify"])


def _opt(flag: str, value) -> list:
    if value is None or value is False or value == "":
        return []
    if value is True:
        return [flag]
    return [flag, str(value)]


def _path(value, default: Path) -> Path:
    if value in (None, ""):
        return default
    p = Path(value)
    return p if p.is_absolute() else (projeto_atual().parent / p)


def _need(path: Path, what: str, hint: str) -> Path:
    if not Path(path).exists():
        raise CvdError(f"{what} não encontrado: {path}\n    {hint}")
    return Path(path)


# --------------------------------------------------------------------------- etapas

def conferir_inputs(case_id: str, mostrar_valores: bool = True, curvas: bool = True) -> None:
    """Erros e avisos do caso na Input Sheet; valor resolvido de cada parâmetro; curvas da biblioteca."""
    try:
        comando("inputs", "validate", "--id", case_id, detalhes=False)
        errors = False
    except CvdError:
        errors = True   # erros já listados acima; mostra os valores mesmo assim
    if mostrar_valores:
        comando("inputs", "show", case_id, detalhes=False)
    if curvas:
        try:
            comando("curves", "check", detalhes=False)
        except CvdError as exc:
            print(f"Aviso: {exc}")
    if errors:
        raise CvdError("O caso tem ERROS nos inputs (lista no início). Corrija antes de montar o modelo.")


def montar_modelo(case_id: str, sobrescrever: bool = True, pasta: Optional[str] = None,
                  checklist: bool = True) -> CasoPaths:
    """Modelo do caso + case.json + checklist (o antigo _01/_02). Devolve os caminhos do caso."""
    paths = caso(case_id, pasta)
    comando("build", case_id, *_opt("--out", str(paths.pasta) if pasta else None),
            *_opt("--overwrite", sobrescrever), *_opt("--no-checklist", not checklist))
    return paths


def diagnosticar(case_id: str, modelo: Optional[str] = None, pasta: Optional[str] = None) -> None:
    """Estática que não converge: roda variantes do modelo e aponta a causa."""
    paths = caso(case_id, pasta)
    comando("diagnose", case_id, "--model", _need(_path(modelo, paths.modelo), "Modelo",
                                                      "Rode antes o 02_montar_modelo.py."))


def gerar_checklist(case_id: str, modelo: Optional[str] = None, pasta: Optional[str] = None) -> None:
    """Checklist de um modelo já montado (por exemplo, depois de um ajuste à mão)."""
    paths = caso(case_id, pasta)
    comando("checklist", case_id, "--model", _need(_path(modelo, paths.modelo), "Modelo",
                                                       "Rode antes o 02_montar_modelo.py."), detalhes=False)


def posicoes_estaticas(case_id: str, modelo: Optional[str] = None, pasta: Optional[str] = None,
                       somente: Optional[Iterable[str]] = None, salvar_sims: bool = True) -> None:
    """Puxada zero e posições (horizontal, CB pull-in/pay-out, overpulls); salva os .sim."""
    paths = caso(case_id, pasta)
    only = ",".join(somente) if somente else None
    comando("statics", "positions", case_id,
            "--model", _need(_path(modelo, paths.modelo), "Modelo", "Rode antes o 02_montar_modelo.py."),
            *_opt("--only", only), *_opt("--no-sims", not salvar_sims))


def modelo_dinamico(case_id: str, posicao: str = "01.Horizontal", origem: Optional[str] = None,
                    pasta: Optional[str] = None, conferir_estatica: bool = True, gerar_mares: bool = True,
                    rodar: bool = False, regular: bool = False, sobrescrever: bool = False,
                    simultaneas: Optional[int] = None) -> Path:
    """Modelo dinâmico base ``<nome>_dyn.dat`` a partir da posição (padrão 01.Horizontal): MCV ancorado, sem o
    cabo do guindaste. Com ``gerar_mares``, gera em seguida os estados de mar em <caso>/02.Dynamic."""
    paths = caso(case_id, pasta)
    if origem is None:
        _need(paths.pasta / "01.Positions", "Pasta das posições", "Rode antes o 03_posicoes_estaticas.py.")
    comando("dynbase", case_id, *_opt("--position", posicao), *_opt("--source", _path(origem, None) if origem else None),
            *_opt("--out", str(paths.pasta) if pasta else None), *_opt("--no-check", not conferir_estatica),
            *_opt("--seastates", gerar_mares), *_opt("--regular", regular), *_opt("--run", rodar),
            *_opt("--overwrite", sobrescrever), *_opt("--workers", simultaneas))
    return paths.modelo_dinamico


def estados_de_mar(case_id: str, modelo_dinamico: Optional[str] = None, pasta_saida: Optional[str] = None,
                   pasta: Optional[str] = None, rodar: bool = False, regular: bool = False,
                   sobrescrever: bool = False, simultaneas: Optional[int] = None) -> None:
    """Arquivos .dat dos estados de mar da aba Mares (e, com ``rodar``, as simulações)."""
    paths = caso(case_id, pasta)
    dyn = _need(_path(modelo_dinamico, paths.modelo_dinamico), "Modelo dinâmico base",
                "Monte-o à mão a partir das posições estáticas (passo 5 do passo a passo).")
    comando("seastates", case_id, "--base-dyn", dyn, "--out", _path(pasta_saida, paths.dinamica),
            *_opt("--regular", regular), *_opt("--run", rodar), *_opt("--overwrite", sobrescrever),
            *_opt("--workers", simultaneas))


def rodar_simulacoes(pasta_sims, simultaneas: Optional[int] = None, sobrescrever: bool = False,
                     padrao: str = "*.dat") -> None:
    """Roda os .dat de uma pasta em paralelo (um processo por simulação)."""
    folder = _need(_path(pasta_sims, Path(pasta_sims)), "Pasta das simulações", "Confira o caminho.")
    comando("run", "--dir", folder, "--pattern", padrao, *_opt("--workers", simultaneas),
            *_opt("--overwrite", sobrescrever))


def pos_processar(case_id: str, pasta_sims: Optional[str] = None, pasta: Optional[str] = None,
                  janela: bool = True, titulo: str = "", simultaneas: int = 0) -> Path:
    """Extrai os resultados dos .sim para Excel; com ``janela``, também Status, Janela e Sugestão."""
    paths = caso(case_id, pasta)
    sims = _need(_path(pasta_sims, paths.dinamica), "Pasta dos .sim", "Rode antes as simulações.")
    comando("post", "--id", case_id, "--sims", sims, *_opt("--window", janela), *_opt("--title", titulo),
            *_opt("--workers", simultaneas or None))
    return Path(sims) / paths.resultados.name


def janela_adaptativa(case_id: str, rodada: str, previsao: Optional[str] = None, resultados: Optional[str] = None,
                      pasta: Optional[str] = None, titulo: str = "") -> Path:
    """Uma rodada da busca adaptativa do Hs limite: sugere e gera os .dat da rodada seguinte.

    ``rodada``: pasta dos .dat desta rodada (ex.: ``02.Dynamic/busca_1``). ``previsao``: planilha do
    ``triagem_fd`` (1ª rodada). ``resultados``: planilha do ``pos_processar`` da rodada anterior.
    """
    paths = caso(case_id, pasta)
    out = Path(rodada) if Path(rodada).is_absolute() else paths.dinamica / rodada
    dyn = _need(paths.modelo_dinamico, "Modelo dinâmico base", "Monte-o à mão a partir das posições estáticas.")
    comando("window", case_id, *_opt("--results", _path(resultados, None) if resultados else None),
            *_opt("--seed", _path(previsao, None) if previsao else None), "--mares",
            "--generate", dyn, "--gen-out", out, *_opt("--title", titulo))
    return out


def triagem_fd(case_id: str, modelo_dinamico: Optional[str] = None, saida: Optional[str] = None,
               comparar_com: Optional[str] = None, pasta: Optional[str] = None,
               rigidez: str = "model", curvatura_secante: Optional[float] = None, titulo: str = "") -> Path:
    """Janela prevista pelo domínio da frequência (minutos); opcionalmente compara com as irregulares."""
    paths = caso(case_id, pasta)
    dyn = _need(_path(modelo_dinamico, paths.modelo_dinamico), "Modelo dinâmico base",
                "Monte-o à mão a partir das posições estáticas.")
    out = _path(saida, paths.triagem / "janela_fd.xlsx")
    comando("screen", "fd", case_id, "--base-dyn", dyn, "--out", out,
            *_opt("--compare", _path(comparar_com, None) if comparar_com else None),
            *_opt("--bend-stiffness", rigidez if rigidez != "model" else None),
            *_opt("--secant-curvature", curvatura_secante), *_opt("--title", titulo))
    return out


def atualizar_configuracao(valores: Optional[dict] = None) -> None:
    """Acrescenta parâmetros novos ao cvd_config.xlsx (faz .bak antes) e grava ``valores`` na aba Defaults."""
    args = []
    for k, v in (valores or {}).items():
        args += ["--set", f"{k}={v}"]
    comando("config", "upgrade", *args, detalhes=False)
