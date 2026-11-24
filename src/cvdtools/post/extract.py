"""Extração de resultados de simulações (substitui ``_09`` e ``_12``).

Mesma semântica do bloco ``postProcess`` do JSON antigo:

==================  ====================  =====================================================
Método              Período vazio         Período "Whole simulation"
==================  ====================  =====================================================
Get data            dado de entrada (atributo do objeto)
Static result       TimeHistory(var, estática, extra)[0]
Min / Max           min/max da TimeHistory na estática    min/max da TimeHistory na simulação toda
Range graph min/max RangeGraph no instante -T0 (estática)  RangeGraph de 0 até o fim do estágio 1
==================  ====================  =====================================================

Diferenças em relação aos scripts antigos (sem efeito nos valores):
- as instruções não são alteradas a cada arquivo (no _12 a cópia rasa do
  dicionário fazia o console acusar "Error! Verify item..." a partir do 2º .sim);
- uma falha num item vira célula vazia e uma linha no log, sem perder o arquivo inteiro;
- ao gravar no Excel, só a aba 'Logs' é substituída (o _12 apagava as demais abas).
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import logging
import os
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from .._orcaflex import require_orcfxapi
from ..schema import PostItem

log = logging.getLogger(__name__)

SORT_COLUMNS = ["Wave Period (s)", "Wave Height (m)", "Wave Direction (deg)"]
STATE_COLUMN = "Estado da simulação"
COMPLETE_COLUMN = "Simulação completa"


END_EXTRAS = ("End A", "End B", "Touchdown")


def _extra(orc, value):
    """Extra de série temporal (``objectExtra``)."""
    if value == "End A":
        return orc.oeEndA
    if value == "End B":
        return orc.oeEndB
    if value == "Touchdown":
        return orc.oeTouchdown
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return orc.oeArcLength(float(value))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        raise ValueError(f"Extra {list(value)} (trecho de arc length) só vale para os métodos 'Range graph'")
    if value in ("", None):
        return None
    raise ValueError(f"OrcaFlex Extra não reconhecido: {value!r}")


def _range_extra(orc, value):
    """Trecho de arc length do range graph (``arclengthRange``).

    'End A', 'End B' e 'Touchdown' não definem trecho num range graph: são
    ignorados (envelope da linha inteira) e o caso é anotado por ``item_notes``.
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return orc.arSpecifiedArclengths(float(value[0]), float(value[1]))
    if value in ("", None) or value in END_EXTRAS:
        return None
    raise ValueError(f"OrcaFlex Extra não reconhecido para range graph: {value!r}")


def item_notes(items: list[PostItem]) -> list[str]:
    """Interpretações feitas na extração que o usuário deve conhecer."""
    notes = []
    for i in items:
        if i.method.startswith("Range graph") and i.extra in END_EXTRAS:
            notes.append(f"'{i.label}': Extra '{i.extra}' não define trecho num range graph; usado o envelope da "
                         f"linha inteira (para limitar o trecho, use 'início;fim' em metros de arc length).")
    return notes


def extract_item(model, item: PostItem, orc=None) -> Any:
    orc = orc or require_orcfxapi()
    obj = model[item.obj]
    static = orc.Period(orc.PeriodNum.StaticState)
    m = item.method

    if m == "Get data":
        try:
            return getattr(obj, item.variable)
        except Exception:
            return obj.GetData(item.variable, -1)
    if m == "Static result":
        return obj.TimeHistory(item.variable, static, _extra(orc, item.extra))[0]
    if m in ("Min", "Max", "Max abs"):
        period = static if item.is_static else None  # None = simulação inteira (como no _12)
        series = obj.TimeHistory(item.variable, period, _extra(orc, item.extra))
        if m == "Max abs":
            return max(abs(v) for v in series)
        return min(series) if m == "Min" else max(series)
    if m in ("Range graph min", "Range graph max", "Range graph max abs"):
        stages = model.general.StageDuration
        if item.is_static:
            period = orc.SpecifiedPeriod(-stages[0], -stages[0])
        else:
            period = orc.SpecifiedPeriod(0.0, stages[1])
        rg = obj.RangeGraph(item.variable, period, None, _range_extra(orc, item.extra))
        if m == "Range graph max abs":
            return max(max(rg.Max), -min(rg.Min))
        return min(rg.Min) if m == "Range graph min" else max(rg.Max)
    raise ValueError(f"Método desconhecido: {m}")


def _simulation_state(model) -> tuple[str, object]:
    """(estado, simulação completa) do .sim; vazio se a versão da API não informa."""
    try:
        state = model.state
        state = getattr(state, "name", str(state))
    except Exception:
        state = ""
    try:
        complete = bool(model.simulationComplete)
    except Exception:
        complete = None
    return state, complete


def extract_file(sim_path: str, items: list[dict]) -> dict[str, Any]:
    """Abre um .sim e extrai todos os itens. Função de topo (usada pelos processos filhos)."""
    orc = require_orcfxapi()
    model = orc.Model(sim_path)
    row: dict[str, Any] = {"Simulation Name": Path(sim_path).name}
    state, complete = _simulation_state(model)
    row[STATE_COLUMN] = state
    row[COMPLETE_COLUMN] = complete
    errors: list[str] = []
    for raw in items:
        item = PostItem(**raw)
        try:
            value = extract_item(model, item, orc)
            row[item.label] = float(value) if isinstance(value, (int, float)) else value
        except Exception as exc:
            row[item.label] = None
            errors.append(f"{item.label}: {str(exc).strip() or type(exc).__name__}")
    if errors:
        row["_errors"] = " | ".join(errors)
    return row


def extract_many(sim_paths: Iterable[Path], items: list[PostItem], workers: int = 1,
                 progress: bool = True):
    import pandas as pd

    sim_paths = [str(p) for p in sim_paths]
    payload = [asdict(i) for i in items]
    rows: list[dict] = []
    failed: list[tuple[str, str]] = []

    bar = None
    if progress:
        try:
            from tqdm import tqdm
            bar = tqdm(total=len(sim_paths))
        except ImportError:
            bar = None

    if workers <= 1:
        for p in sim_paths:
            try:
                rows.append(extract_file(p, payload))
            except Exception:
                failed.append((p, traceback.format_exc()))
            if bar:
                bar.update(1)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(extract_file, p, payload): p for p in sim_paths}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    rows.append(fut.result())
                except Exception:
                    failed.append((futures[fut], traceback.format_exc()))
                if bar:
                    bar.update(1)
    if bar:
        bar.close()

    for path, tb in failed:
        log.error("Falha ao abrir %s:\n%s", path, tb)

    columns = ["Simulation Name"] + [i.label for i in items] + [STATE_COLUMN, COMPLETE_COLUMN]
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = None
    extra_cols = [c for c in df.columns if c not in columns]
    df = df[columns + extra_cols]
    sort_cols = [c for c in SORT_COLUMNS if c in df.columns]
    df = df.sort_values(sort_cols or ["Simulation Name"]).reset_index(drop=True)
    df.attrs["failed"] = failed
    return df


def find_sims(directory: Path, pattern: str = "*.sim") -> list[Path]:
    return sorted(Path(directory).glob(pattern))


def missing_sims(directory: Path, pattern: str = "*.sim") -> list[str]:
    """.dat da pasta (com o mesmo filtro) que ainda não têm .sim: não rodaram ou falharam.

    Comparação sem diferenciar maiúsculas (como no Windows), em qualquer sistema.
    """
    import fnmatch

    directory = Path(directory)
    pat = pattern.lower()
    dat_pat = pat[:-4] + ".dat" if pat.endswith(".sim") else "*.dat"
    files = [p for p in directory.iterdir() if p.is_file()]
    have = {p.stem.lower() for p in files if fnmatch.fnmatch(p.name.lower(), pat)}
    return sorted(p.name for p in files if fnmatch.fnmatch(p.name.lower(), dat_pat) and p.stem.lower() not in have)


def write_sheet(xlsx_path: Path, sheet: str, df) -> None:
    """Grava (ou substitui) uma aba, preservando as demais."""
    import pandas as pd

    xlsx_path = Path(xlsx_path)
    mode = "a" if xlsx_path.exists() else "w"
    kw = {"if_sheet_exists": "replace"} if mode == "a" else {}
    with pd.ExcelWriter(xlsx_path, engine="openpyxl", mode=mode, **kw) as w:
        df.to_excel(w, sheet_name=sheet, index=False)


def write_results(df, xlsx_path: Path, sheet: str = "Logs", csv_backup: bool = True) -> Path:
    """Grava a tabela na aba ``sheet``; outras abas do arquivo são preservadas."""
    import pandas as pd

    xlsx_path = Path(xlsx_path)
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    if xlsx_path.exists():
        with pd.ExcelWriter(xlsx_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
            df.to_excel(w, sheet_name=sheet, index=False)
    else:
        df.to_excel(xlsx_path, sheet_name=sheet, index=False)
    if csv_backup:
        stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        df.to_csv(xlsx_path.with_name(f"{xlsx_path.stem}_{stamp}.csv"), index=False)
    return xlsx_path


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)
