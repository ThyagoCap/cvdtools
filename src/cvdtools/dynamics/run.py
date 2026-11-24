"""Execução das simulações dinâmicas em paralelo (alternativa ao batch do OrcaFlex).

Cada arquivo roda num processo separado (``ProcessPoolExecutor``), com ``workers``
simulações ao mesmo tempo. Cada modelo usa ``threads`` threads do OrcaFlex (padrão 1):
com muitas simulações independentes, uma thread por simulação e uma simulação por
núcleo aproveita melhor a máquina do que poucas simulações com muitas threads.

O .sim é salvo ao lado do .dat. Simulações com .sim já existente são puladas (a
menos de ``overwrite``). Resultado de cada uma em ``run_log.csv``.
"""

from __future__ import annotations

import concurrent.futures
import csv
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

from .._orcaflex import require_orcfxapi
from ..log import log


@dataclass
class RunResult:
    file: str
    status: str          # OK | FALHOU | PULADO
    seconds: float = 0.0
    message: str = ""


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


def run_one(dat: str, threads: int = 1) -> RunResult:
    """Roda UMA simulação (função de módulo: executa em outro processo)."""
    orc = require_orcfxapi()
    t = time.perf_counter()
    path = Path(dat)
    try:
        try:
            model = orc.Model(str(path), threadCount=threads)
        except TypeError:          # OrcFxAPI sem o argumento threadCount
            model = orc.Model(str(path))
        model.RunSimulation()
        model.SaveSimulation(str(path.with_suffix(".sim")))
        return RunResult(path.name, "OK", time.perf_counter() - t)
    except Exception as exc:
        msg = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
        return RunResult(path.name, "FALHOU", time.perf_counter() - t, msg)


def collect(dat_dir: Optional[Path] = None, pattern: str = "*.dat", list_file: Optional[Path] = None) -> list[Path]:
    if list_file:
        base = Path(list_file).parent
        files = [Path(l.strip()) for l in Path(list_file).read_text(encoding="utf-8").splitlines() if l.strip()]
        return [f if f.is_absolute() else base / f for f in files if f.suffix.lower() in (".dat", ".yml")]
    return sorted(Path(dat_dir).glob(pattern))


def run_simulations(files: Iterable[Path], workers: Optional[int] = None, threads: int = 1,
                    overwrite: bool = False, log_csv: Optional[Path] = None) -> list[RunResult]:
    files = [Path(f) for f in files]
    results: list[RunResult] = []
    todo = []
    for f in files:
        if f.with_suffix(".sim").exists() and not overwrite:
            results.append(RunResult(f.name, "PULADO", 0.0, ".sim já existe"))
        else:
            todo.append(f)
    workers = max(1, min(workers or default_workers(), len(todo) or 1))
    log.info("Rodando %d simulação(ões) com %d processo(s) em paralelo e %d thread(s) do OrcaFlex cada "
             "(%d pulada(s): .sim já existe)", len(todo), workers, threads, len(files) - len(todo))
    t0 = time.perf_counter()

    def done(r: RunResult) -> None:
        results.append(r)
        n = sum(1 for x in results if x.status != "PULADO")
        if r.status == "OK":
            log.info("  [%d/%d] %s OK (%.0f s)", n, len(todo), r.file, r.seconds)
        else:
            log.error("  [%d/%d] %s FALHOU (%.0f s): %s", n, len(todo), r.file, r.seconds, r.message)

    if workers == 1:
        for f in todo:
            done(run_one(str(f), threads))
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(run_one, str(f), threads) for f in todo]
            try:
                for fut in concurrent.futures.as_completed(futs):
                    done(fut.result())
            except KeyboardInterrupt:
                log.warning("Ctrl+C: cancelando as simulações que ainda não começaram")
                ex.shutdown(wait=False, cancel_futures=True)
                raise
    log.info("Fim: %d OK, %d falharam, %d puladas em %.0f s",
             sum(r.status == "OK" for r in results), sum(r.status == "FALHOU" for r in results),
             sum(r.status == "PULADO" for r in results), time.perf_counter() - t0)
    if log_csv:
        with open(log_csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(asdict(results[0]).keys()) if results else ["file"])
            w.writeheader()
            for r in sorted(results, key=lambda r: r.file):
                w.writerow(asdict(r))
    return results
