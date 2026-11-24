"""Fase 4: Status (UF) dos estados de mar, janela operacional, sugestão e limpeza.

Substitui a parte de Excel do pós-processamento (coluna Status da tabela dinâmica
"Dynamics_Tabela_Pivot") e os scripts ``_11``/``_13`` (próximos Hs a rodar) e
``_14`` (simulações que não definem o limite).

Fluxo:
1. ``criteria_postitems(case)`` cria, para cada critério de aceitação dinâmico,
   uma coluna de extração "Critério: <nome>" (máximo absoluto na simulação
   inteira; Range Graph -> máximo absoluto do envelope). O ``cvd post`` as inclui.
2. ``evaluate(df, case)`` calcula UF = valor / limite de cada critério, UF máximo,
   critério que governa e Status (UF <= ``statics.uf_limit``; falha de extração
   ou de simulação = REPROVADO).
3. ``window_limits`` -> Hs limite por (Tp, direção): maior Hs aprovado abaixo do
   menor Hs reprovado. Marca "≥" quando nenhum Hs foi reprovado (limite não
   encontrado) e avisa quando há aprovação acima de uma reprovação (não monotônico).
4. ``suggest`` -> próximos (Tp, Hs, direção) a rodar, como o _13.
5. ``cleanup_candidates`` -> simulações que não são nem o maior Hs aprovado nem o
   menor Hs reprovado, como o _14 (movidas só com ``--apply``).
"""

from __future__ import annotations

import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..schema import CaseInputs, PostItem

CRIT_PREFIX = "Critério: "
NAME_RE = re.compile(r"_dyn\s*(-?\d+(?:\.\d+)?)Tp_(-?\d+(?:\.\d+)?)Hs_(-?\d+(?:\.\d+)?)a")
APPROVED, REJECTED = "APROVADO", "REPROVADO"


# --------------------------------------------------------------------------- colunas dos critérios

def criteria_postitems(case: CaseInputs) -> list[PostItem]:
    items = []
    for c in case.criteria:
        if not c.dynamic:
            continue
        method = "Max abs" if c.type == "Time History" else "Range graph max abs"
        items.append(PostItem(f"{CRIT_PREFIX}{c.name}", method, c.obj, c.variable, c.extra, "Whole simulation",
                              template="criterio"))
    return items


# --------------------------------------------------------------------------- status

def parse_name(name: str) -> Optional[tuple[float, float, float]]:
    m = NAME_RE.search(str(name))
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def _is_false(value) -> bool:
    """True só para um 'não' explícito (bool/numpy bool False ou texto 'false'/'não'); vazio não conta."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("false", "falso", "não", "nao", "n", "0")
    try:
        if value != value:          # NaN
            return False
        return not bool(value)
    except Exception:
        return False


def evaluate(df, case: CaseInputs, uf_limit: Optional[float] = None):
    """Acrescenta Tp/Hs/Dir, UF de cada critério, UF máx., governante e Status."""
    import pandas as pd

    uf_limit = float(uf_limit if uf_limit is not None else case.num("statics.uf_limit"))
    out = df.copy()
    tps, hss, dirs = [], [], []
    for _, row in out.iterrows():
        parsed = parse_name(row.get("Simulation Name", ""))
        if parsed is None:
            parsed = (row.get("Wave Period (s)"), row.get("Wave Height (m)"), None)
        tps.append(parsed[0])
        hss.append(parsed[1])
        dirs.append(parsed[2])
    out["Tp (s)"], out["Hs (m)"], out["Dir (°)"] = tps, hss, dirs

    uf_cols = []
    missing = []
    for c in case.criteria:
        if not c.dynamic:
            continue
        col = f"{CRIT_PREFIX}{c.name}"
        try:
            limit = float(c.limit)
        except (TypeError, ValueError):
            missing.append(f"{c.name} (sem limite numérico)")
            continue
        if col not in out.columns:
            missing.append(f"{c.name} (coluna '{col}' ausente: rode 'cvd post' com os critérios)")
            continue
        ucol = f"UF {c.name}"
        out[ucol] = pd.to_numeric(out[col], errors="coerce").abs() / limit
        uf_cols.append(ucol)

    if uf_cols:
        ufs = out[uf_cols]
        out["UF máx"] = ufs.max(axis=1, skipna=False)
        out["Governa"] = ufs.fillna(math.inf).idxmax(axis=1).str.replace("UF ", "", regex=False)
    else:
        out["UF máx"] = float("nan")
        out["Governa"] = ""

    from .extract import COMPLETE_COLUMN, STATE_COLUMN

    def verdict(row) -> tuple[str, str]:
        state = row.get(STATE_COLUMN, "")
        complete = row.get(COMPLETE_COLUMN, None)
        if isinstance(state, str) and "unstable" in state.lower():
            return REJECTED, "simulação instável"
        if _is_false(complete):
            return REJECTED, "simulação incompleta"
        u = row["UF máx"]
        if u is None or (isinstance(u, float) and math.isnan(u)):
            empty = [c[3:] for c in uf_cols if pd.isna(row[c])]
            return REJECTED, ("critério sem valor: " + ", ".join(empty)) if empty else "sem critério avaliado"
        if u <= uf_limit:
            return APPROVED, ""
        return REJECTED, f"UF {u:.3f} ({row['Governa']})"

    verdicts = out.apply(verdict, axis=1, result_type="expand") if len(out) else None
    out["Status"] = verdicts[0] if verdicts is not None else []
    out["Motivo"] = verdicts[1] if verdicts is not None else []
    out.attrs["missing_criteria"] = missing
    out.attrs["uf_limit"] = uf_limit
    return out


# --------------------------------------------------------------------------- janela

@dataclass
class Limit:
    tp: float
    direction: float
    hs: Optional[float]           # None = nenhum Hs aprovado
    bounded: bool                 # há reprovação acima do limite
    min_rejected: Optional[float]
    note: str = ""

    @property
    def text(self) -> str:
        if self.hs is None:
            return f"<{self.min_rejected:g}" if self.min_rejected is not None else "-"
        return f"{self.hs:g}" if self.bounded else f"≥{self.hs:g}"


def window_limits(status_df) -> list[Limit]:
    out = []
    df = status_df.dropna(subset=["Tp (s)", "Hs (m)", "Dir (°)"])
    for (tp, d), g in df.groupby(["Tp (s)", "Dir (°)"]):
        ok = sorted(g.loc[g["Status"] == APPROVED, "Hs (m)"].astype(float))
        bad = sorted(g.loc[g["Status"] != APPROVED, "Hs (m)"].astype(float))
        min_bad = bad[0] if bad else None
        below = [h for h in ok if min_bad is None or h < min_bad]
        note = ""
        if min_bad is not None and any(h > min_bad for h in ok):
            note = f"não monotônico: aprovado acima de Hs {min_bad:g} reprovado"
        out.append(Limit(float(tp), float(d), below[-1] if below else None, min_bad is not None, min_bad, note))
    return out


def window_grid(limits: list[Limit]):
    """DataFrame Tp × direção com o texto do limite (formato da aba 'Janelas')."""
    import pandas as pd

    tps = sorted({lim.tp for lim in limits})
    dirs = sorted({lim.direction for lim in limits})
    grid = pd.DataFrame(index=tps, columns=dirs, dtype=object)
    for lim in limits:
        grid.loc[lim.tp, lim.direction] = lim.hs if (lim.hs is not None and lim.bounded) else lim.text
    grid.index.name = "Tp [s]"
    return grid


# --------------------------------------------------------------------------- sugestão (_11/_13)

def suggest(status_df, hs_min: float, hs_max: float, hs_step: float, n_steps: int = 2) -> list[tuple[float, float, float]]:
    """Próximos (Tp, Hs, Dir) a rodar, mesma regra do _13.

    Sem aprovação no par (Tp, Dir): desce a partir do menor Hs testado. Com
    aprovação: sobe a partir do maior Hs aprovado. Para ao encontrar um Hs já
    testado ou o limite [hs_min, hs_max].
    """
    out = []
    df = status_df.dropna(subset=["Tp (s)", "Hs (m)", "Dir (°)"])
    tested = {(round(float(t), 6), round(float(h), 6), round(float(d), 6))
              for t, h, d in zip(df["Tp (s)"], df["Hs (m)"], df["Dir (°)"])}
    for (tp, d), g in df.groupby(["Tp (s)", "Dir (°)"]):
        ok = g.loc[g["Status"] == APPROVED, "Hs (m)"].astype(float)
        if ok.empty:
            start, sign = float(g["Hs (m)"].astype(float).min()), -1
        else:
            start, sign = float(ok.max()), +1
        for n in range(1, n_steps + 1):
            hs = round(start + sign * n * hs_step, 6)
            if hs < hs_min - 1e-9 or hs > hs_max + 1e-9:
                break
            if (round(float(tp), 6), hs, round(float(d), 6)) in tested:
                break
            out.append((float(tp), hs, float(d)))
    return out


def suggestion_grid(suggestions: list[tuple[float, float, float]]) -> list[tuple[float, float, list[float]]]:
    """Converte em grade (direção, Tp, [Hs]) aceita pelo gerador de estados de mar."""
    by: dict[tuple[float, float], list[float]] = {}
    for tp, hs, d in suggestions:
        by.setdefault((d, tp), []).append(hs)
    return [(d, tp, sorted(hs)) for (d, tp), hs in sorted(by.items())]


# --------------------------------------------------------------------------- limpeza (_14)

def cleanup_candidates(status_df) -> list[str]:
    """Simulações que não são nem o maior Hs aprovado nem o menor Hs reprovado do par (Tp, Dir)."""
    out = []
    df = status_df.dropna(subset=["Tp (s)", "Hs (m)", "Dir (°)"])
    for _, g in df.groupby(["Tp (s)", "Dir (°)"]):
        ok = g.loc[g["Status"] == APPROVED, "Hs (m)"].astype(float)
        bad = g.loc[g["Status"] != APPROVED, "Hs (m)"].astype(float)
        keep = set()
        if not bad.empty:
            keep.add(float(bad.min()))
        if not ok.empty:
            below = ok[ok < bad.min()] if not bad.empty else ok
            if not below.empty:
                keep.add(float(below.max()))
        for _, row in g.iterrows():
            if float(row["Hs (m)"]) not in keep:
                out.append(str(row["Simulation Name"]))
    return out


def move_unused(names: list[str], sim_dir: Path, dest: Path, apply: bool = False) -> list[tuple[str, str]]:
    """Move .sim e .dat das simulações listadas. Sem ``apply`` só lista (nada muda)."""
    sim_dir, dest = Path(sim_dir), Path(dest)
    moved = []
    for name in names:
        stem = Path(name).stem
        for ext in (".sim", ".dat"):
            src = sim_dir / f"{stem}{ext}"
            if src.exists():
                if apply:
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest / src.name))
                moved.append((str(src), str(dest / src.name)))
    return moved


# --------------------------------------------------------------------------- gravação

def write_window(xlsx: Path, status_df, limits: list[Limit], suggestions, case: CaseInputs,
                 title: str = "", search=None) -> Path:
    """Grava abas Status, Janela (formato 'Janelas') e Sugestão (e Busca, se houver); outras abas são preservadas."""
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    xlsx = Path(xlsx)
    xlsx.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if xlsx.exists() else "w"
    kw = {"if_sheet_exists": "replace"} if mode == "a" else {}
    with pd.ExcelWriter(xlsx, engine="openpyxl", mode=mode, **kw) as w:
        status_df.to_excel(w, sheet_name="Status", index=False)
        pd.DataFrame([{"Tp (s)": t, "Hs (m)": h, "Dir (°)": d} for t, h, d in suggestions]
                     or [{"Tp (s)": None, "Hs (m)": None, "Dir (°)": None}]).to_excel(w, sheet_name="Sugestão",
                                                                                     index=False)
        pd.DataFrame([{"Tp (s)": lim.tp, "Dir (°)": lim.direction, "Hs limite (m)": lim.hs,
                       "Limite encontrado": "S" if lim.bounded else "N", "Menor Hs reprovado": lim.min_rejected,
                       "Texto": lim.text, "Obs.": lim.note} for lim in limits]
                     or [{}]).to_excel(w, sheet_name="Limites", index=False)

        uf_pivot(status_df).to_excel(w, sheet_name="UF")
        if search is not None:
            search.to_excel(w, sheet_name="Busca", index=False)

    wb = load_workbook(xlsx)
    _color_uf_sheet(wb["UF"], float(status_df.attrs.get("uf_limit", 1.0)))
    if "Janela" in wb.sheetnames:
        del wb["Janela"]
    ws = wb.create_sheet("Janela", 0)
    grid = window_grid(limits)
    bold = Font(bold=True)
    ws.cell(2, 2, title or case.case_id).font = bold
    ws.cell(3, 4, case.operation.operation_type).font = bold
    ws.cell(4, 4, "Direction [°]").font = bold
    ws.cell(5, 4, "Tp [s]").font = bold
    ws.cell(1, 2, f"UF limite {status_df.attrs.get('uf_limit', '')}; '≥' = nenhum Hs reprovado; '<x' = nenhum aprovado")
    for j, d in enumerate(grid.columns):
        ws.cell(4, 6 + j, float(d)).font = bold
    green, red = PatternFill("solid", fgColor="C6EFCE"), PatternFill("solid", fgColor="FFC7CE")
    for i, tp in enumerate(grid.index):
        ws.cell(5 + i, 5, float(tp)).font = bold
        for j, d in enumerate(grid.columns):
            v = grid.loc[tp, d]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            c = ws.cell(5 + i, 6 + j, v)
            c.alignment = Alignment(horizontal="center")
            if isinstance(v, (int, float)):
                c.fill = green
            elif isinstance(v, str) and v.startswith("<"):
                c.fill = red
    wb.save(xlsx)
    return xlsx


def uf_pivot(status_df):
    """UF máximo por (Tp, Hs) × direção: o 'mapa' da campanha."""
    import pandas as pd

    df = status_df.dropna(subset=["Tp (s)", "Hs (m)", "Dir (°)"])
    if df.empty:
        return pd.DataFrame()
    piv = df.pivot_table(index=["Tp (s)", "Hs (m)"], columns="Dir (°)", values="UF máx", aggfunc="max")
    return piv.round(3)


def _color_uf_sheet(ws, uf_limit: float) -> None:
    from openpyxl.styles import PatternFill

    green, yellow, red = (PatternFill("solid", fgColor=c) for c in ("C6EFCE", "FFEB9C", "FFC7CE"))
    for row in ws.iter_rows(min_row=2, min_col=3):
        for c in row:
            if isinstance(c.value, (int, float)):
                c.fill = red if c.value > uf_limit else (yellow if c.value > 0.9 * uf_limit else green)


def read_results(path: Path, sheet: str = "Logs"):
    import pandas as pd

    path = Path(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path, sheet_name=sheet)
