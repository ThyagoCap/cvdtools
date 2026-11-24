"""Geração dos arquivos de estados de mar (substitui ``_07`` e ``_10``/``_13``).

Os arquivos são só gerados; a execução continua no batch do OrcaFlex. Além dos
.dat/.yml, são gravados ``manifest.csv`` (parâmetros de cada arquivo) e
``batch_list.txt`` (caminhos completos, para adicionar no batch).

Correção em relação ao script antigo: o instante da maior subida/descida lido
da planilha de *wave search* era comparado como TEXTO. Agora a comparação é
numérica; a regra continua a mesma (usa o evento que ocorre por último).
"""

from __future__ import annotations

import csv
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

from .._orcaflex import require_orcfxapi
from ..log import log
from ..schema import CaseInputs

Grid = Iterable[tuple[float, float, list[float]]]  # (direção relativa, Tp, [Hs...])


@dataclass
class SeaStateFile:
    file: str
    kind: str
    heading_rel: float
    wave_direction: float
    tp: float
    hs: float
    gamma: Optional[float] = None
    wave_time_origin: Optional[float] = None
    rise_time: Optional[float] = None
    fall_time: Optional[float] = None
    event: str = ""


def jonswap_gamma(tp: float) -> float:
    """Gamma ajustado para a Bacia de Campos (mesma fórmula do _10 e do relatório)."""
    return 6.4 * tp ** (-0.491)


def expand_grid(grid: Grid) -> list[tuple[float, float, float]]:
    return [(float(d), float(tp), float(hs)) for d, tp, hss in grid for hs in hss]


def unique_states(grid: Grid) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """(combinações únicas, combinações repetidas) na ordem da aba Mares.

    Repetidas geram o MESMO arquivo; sem esta verificação a contagem dizia N arquivos e
    gravava menos (GLF27: 231 linhas, 217 arquivos).
    """
    seen, unique, dup = set(), [], []
    for d, tp, hs in expand_grid(grid):
        key = (round(d, 6), round(tp, 6), round(hs, 6))
        if key in seen:
            dup.append((d, tp, hs))
        else:
            seen.add(key)
            unique.append((d, tp, hs))
    return unique, dup


def _warn_duplicates(dup) -> None:
    if dup:
        txt = ", ".join(f"(dir {d:g}, Tp {t:g}, Hs {h:g})" for d, t, h in dup)
        log.warning("%d combinação(ões) repetida(s) na aba Mares, gerada(s) uma vez só: %s", len(dup), txt)


def irregular_name(operation_name: str, tp: float, hs: float, alpha: float) -> str:
    """Mesmo padrão do _10/_13 (os scripts de janela dependem dele)."""
    file_base = operation_name + "_dyn"
    return file_base + "%00.2fTp_%0.2fHs_%0.2fa" % (tp, hs, alpha)


def regular_name(operation_name: str, tp: float, hs: float, alpha: float) -> str:
    return operation_name + "_dyn" + "_{:.2f}".format(tp) + "Tp_" + "{:.2f}".format(hs) + "Hs_" + str(alpha) + "a"


def _to_float(token: str) -> float:
    return float(token.replace(",", "."))


def parse_wave_search(text: str, column: int = 3) -> tuple[float, float]:
    """Instantes (s) da maior subida e da maior descida na planilha de wave search.

    Usa a mesma posição de coluna do script antigo (4º campo da linha).
    """
    rise = fall = None
    for row in text.splitlines():
        if rise is None and "Largest rise" in row:
            rise = _to_float(row.split()[column])
        elif fall is None and "Largest fall" in row:
            fall = _to_float(row.split()[column])
    if rise is None or fall is None:
        raise ValueError("Linhas 'Largest rise'/'Largest fall' não encontradas na planilha de wave search.")
    return rise, fall


def choose_event(rise: float, fall: float) -> tuple[str, float]:
    """Regra do script antigo: usa o evento que ocorre por último."""
    return ("fall", fall) if fall >= rise else ("rise", rise)


def _time_step(model, case: CaseInputs) -> None:
    dt = case.num("simulation.time_step")
    if case.num("simulation.variable_time_step"):
        model.general.ImplicitUseVariableTimeStep = "Yes"
        model.general.ImplicitVariableMaxTimeStep = dt
    else:
        model.general.ImplicitUseVariableTimeStep = "No"
        model.general.ImplicitConstantTimeStep = dt


def _irregular_params(case: CaseInputs) -> dict:
    """Só números/textos (vai para outros processos)."""
    return {
        "vessel": case.role("VESSEL"),
        "operation_name": case.operation_name,
        "time_before_wave": case.num("simulation.time_before_wave"),
        "time_step": case.num("simulation.time_step"),
        "variable_time_step": bool(case.num("simulation.variable_time_step")),
        "log_interval": case.num("simulation.log_interval"),
        "wave_preview_x": case.num("simulation.wave_preview_x"),
        "wave_preview_y": case.num("simulation.wave_preview_y"),
        "build_up_periods": case.num("simulation.build_up_periods"),
        "wave_search_duration": case.num("simulation.wave_search_duration"),
    }


def _generate_one_irregular(base_dyn_model: str, out_dir: str, alpha: float, tp: float, hs: float,
                            prm: dict) -> SeaStateFile:
    """Gera UM estado de mar irregular. Função de módulo (roda em processo separado)."""
    orc = require_orcfxapi()
    name = irregular_name(prm["operation_name"], tp, hs, alpha)
    target = Path(out_dir) / f"{name}.dat"
    model = orc.Model(str(base_dyn_model), threadCount=1) if _accepts_threads(orc) else orc.Model(str(base_dyn_model))
    heading = model[prm["vessel"]].InitialHeading
    g = model.general
    if prm["variable_time_step"]:
        g.ImplicitUseVariableTimeStep = "Yes"
        g.ImplicitVariableMaxTimeStep = prm["time_step"]
    else:
        g.ImplicitUseVariableTimeStep = "No"
        g.ImplicitConstantTimeStep = prm["time_step"]
    g.TargetLogSampleInterval = prm["log_interval"]

    env = model.environment
    env.WaveType = "JONSWAP"
    env.WavePreviewPositionX = prm["wave_preview_x"]
    env.WavePreviewPositionY = prm["wave_preview_y"]
    env.WaveDirection = alpha + heading
    env.RefCurrentDirection = alpha + heading
    gamma = jonswap_gamma(tp)
    env.WaveGamma = gamma
    env.WaveTp = tp
    env.WaveHs = hs
    t_before = prm["time_before_wave"]
    g.StageDuration[0] = prm["build_up_periods"] * env.WaveTp
    g.StageDuration[1] = 2 * t_before

    env.WaveTimeOrigin = 0.0
    env.WaveSearchFrom = 0.0
    env.WaveSearchDuration = prm["wave_search_duration"]
    env.WaveSearchMinHeight = 1e9
    env.WaveSearchMinSteepness = 1e9

    with tempfile.TemporaryDirectory() as tmp:
        spreadsheet = Path(tmp) / f"{name}.txt"
        model.SaveWaveSearchSpreadsheet(str(spreadsheet))
        rise, fall = parse_wave_search(spreadsheet.read_text(encoding="utf-8", errors="replace"))
    event, t_event = choose_event(rise, fall)
    env.WaveTimeOrigin = -(t_event - t_before)
    model.SaveData(str(target))
    return SeaStateFile(target.name, "irregular", alpha, alpha + heading, tp, hs, gamma, env.WaveTimeOrigin,
                        rise, fall, event)


def _accepts_threads(orc) -> bool:
    import inspect

    try:
        return "threadCount" in inspect.signature(orc.Model).parameters
    except (TypeError, ValueError):
        return False


def default_workers() -> int:
    import os

    return max(1, (os.cpu_count() or 2) - 1)


def generate_irregular(case: CaseInputs, base_dyn_model: Path, grid: Grid, out_dir: Path,
                       overwrite: bool = False, workers: Optional[int] = None) -> list[SeaStateFile]:
    """Gera os estados de mar irregulares em paralelo (um processo por arquivo, ``workers`` ao mesmo tempo)."""
    import concurrent.futures

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    states, dup = unique_states(grid)
    _warn_duplicates(dup)
    for alpha, tp, hs in states:
        target = out_dir / f"{irregular_name(case.operation_name, tp, hs, alpha)}.dat"
        if target.exists() and not overwrite:
            raise FileExistsError(f"{target} já existe (use overwrite=True / --overwrite)")
    prm = _irregular_params(case)
    workers = max(1, min(workers or default_workers(), len(states) or 1))
    log.info("Gerando %d estado(s) de mar irregulares com %d processo(s) em paralelo", len(states), workers)
    files: list[SeaStateFile] = []
    errors: list[str] = []

    def done(sf: SeaStateFile) -> None:
        files.append(sf)
        log.info("  [%d/%d] %s (maior %s em %.1f s)", len(files), len(states), sf.file,
                 "subida" if sf.event == "rise" else "descida", sf.rise_time if sf.event == "rise" else sf.fall_time)

    if workers == 1:
        for alpha, tp, hs in states:
            done(_generate_one_irregular(str(base_dyn_model), str(out_dir), alpha, tp, hs, prm))
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_generate_one_irregular, str(base_dyn_model), str(out_dir), a, t, h, prm): (a, t, h)
                    for a, t, h in states}
            try:
                for fut in concurrent.futures.as_completed(futs):
                    try:
                        done(fut.result())
                    except Exception as exc:
                        a, t, h = futs[fut]
                        errors.append(f"dir {a:g}, Tp {t:g}, Hs {h:g}: {exc}")
                        log.error("  FALHOU dir %g, Tp %g, Hs %g: %s", a, t, h, exc)
            except KeyboardInterrupt:
                ex.shutdown(wait=False, cancel_futures=True)
                raise
    files.sort(key=lambda f: f.file)
    write_manifest(files, out_dir)
    if errors:
        log.warning("%d estado(s) de mar não gerado(s)", len(errors))
    return files


def generate_regular(case: CaseInputs, base_dyn_model: Path, grid: Grid, out_dir: Path,
                     overwrite: bool = False) -> list[SeaStateFile]:
    """Ondas regulares (Dean Stream ordem 5, H = fator x Hs) como modelos de variação .yml."""
    orc = require_orcfxapi()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    vessel = case.role("VESSEL")
    factor = case.num("simulation.regular_height_factor")
    files: list[SeaStateFile] = []
    states, dup = unique_states(grid)
    _warn_duplicates(dup)
    for alpha, tp, hs in states:
        name = regular_name(case.operation_name, tp, hs, alpha)
        target = out_dir / f"{name}.yml"
        if target.exists() and not overwrite:
            raise FileExistsError(f"{target} já existe (use overwrite=True / --overwrite)")
        model = orc.Model()
        model.NewVariationModel(str(base_dyn_model))
        heading = model[vessel].InitialHeading
        _time_step(model, case)
        model.general.StageDuration[0] = 3 * tp
        model.general.StageDuration[1] = 5 * tp
        env = model.environment
        env.WaveType = "Dean Stream"
        env.WaveStreamFunctionOrder = 5
        env.WaveDirection = alpha + heading
        env.RefCurrentDirection = alpha + heading
        env.WavePeriod = tp
        env.WaveHeight = factor * hs
        env.WaveTimeOrigin = 0.0
        model.SaveData(str(target))
        files.append(SeaStateFile(target.name, "regular", alpha, alpha + heading, tp, hs))
    write_manifest(files, out_dir)
    return files


def write_manifest(files: list[SeaStateFile], out_dir: Path) -> None:
    out_dir = Path(out_dir)
    manifest = out_dir / "manifest.csv"
    existing: dict[str, dict] = {}
    if manifest.exists():
        with open(manifest, newline="", encoding="utf-8") as f:
            existing = {r["file"]: r for r in csv.DictReader(f)}
    for sf in files:
        existing[sf.file] = {k: ("" if v is None else v) for k, v in asdict(sf).items()}
    # arquivos de rodadas anteriores que não existem mais (pasta limpa, grade mudou) saem da lista
    stale = [name for name in existing if not (out_dir / name).exists()]
    for name in stale:
        del existing[name]
    if stale:
        log.info("manifest.csv/batch_list.txt: %d arquivo(s) de rodadas anteriores não existem mais e saíram da lista",
                 len(stale))
    fields = list(asdict(files[0]).keys()) if files else list(SeaStateFile.__dataclass_fields__)
    with open(manifest, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in sorted(existing.values(), key=lambda r: r["file"]):
            w.writerow(row)
    (out_dir / "batch_list.txt").write_text(
        "\n".join(str((out_dir / r["file"]).resolve()) for r in sorted(existing.values(), key=lambda r: r["file"])) + "\n",
        encoding="utf-8")
