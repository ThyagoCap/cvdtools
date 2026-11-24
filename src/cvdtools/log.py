"""Mensagens de progresso da pipeline.

- No console: nível escolhido na linha de comando (padrão: progresso; ``-v``: detalhe
  de cada cálculo; ``-q``: só avisos e erros). Cada linha tem a hora e o tempo
  decorrido desde o início do comando.
- Em arquivo: ``build`` e ``statics`` gravam sempre um log completo (nível detalhe)
  na pasta de saída (``cvd_build.log`` / ``cvd_statics.log``), útil para investigar
  depois uma rodada longa ou com erro.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from pathlib import Path

LOGGER_NAME = "cvdtools"
log = logging.getLogger(LOGGER_NAME)
_T0 = time.perf_counter()


class _Elapsed(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.elapsed = f"{time.perf_counter() - _T0:7.1f}s"
        return super().format(record)


class _CurrentStderr(logging.StreamHandler):
    """Escreve no ``sys.stderr`` do momento, não no da criação.

    O pytest (e outros) trocam o ``sys.stderr``; um handler preso ao stream antigo gera
    "Logging error ... I/O operation on closed file" nos testes seguintes.
    """

    def __init__(self):
        super().__init__(sys.stderr)

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


FMT = "%(asctime)s [%(elapsed)s] %(levelname)-7s %(message)s"
DATEFMT = "%H:%M:%S"


def setup_logging(verbosity: int = 0, quiet: bool = False) -> None:
    """verbosity 0 = progresso (INFO), 1+ = detalhe (DEBUG); quiet = só avisos."""
    global _T0
    _T0 = time.perf_counter()
    level = logging.WARNING if quiet else (logging.DEBUG if verbosity >= 1 else logging.INFO)
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        if getattr(h, "_cvd_console", False):
            log.removeHandler(h)
    handler = _CurrentStderr()
    handler.setLevel(level)
    handler.setFormatter(_Elapsed(FMT, DATEFMT))
    handler._cvd_console = True
    log.addHandler(handler)
    log.propagate = False


@contextlib.contextmanager
def log_to_file(path: Path):
    """Grava tudo (nível detalhe) em ``path`` enquanto o bloco roda."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_Elapsed(FMT, DATEFMT))
    log.addHandler(handler)
    if log.level == logging.NOTSET or log.level > logging.DEBUG:
        log.setLevel(logging.DEBUG)
    log.info("Log completo em %s", path)
    try:
        yield path
    except Exception:
        log.exception("Erro")
        raise
    finally:
        log.removeHandler(handler)
        handler.close()


@contextlib.contextmanager
def step(message: str, level: int = logging.INFO):
    """Registra início e fim (com duração) de uma etapa."""
    log.log(level, "%s ...", message)
    t = time.perf_counter()
    try:
        yield
    except Exception as exc:
        log.error("%s: FALHOU após %.1fs (%s)", message, time.perf_counter() - t, exc)
        raise
    log.log(level, "%s: ok (%.1fs)", message, time.perf_counter() - t)
