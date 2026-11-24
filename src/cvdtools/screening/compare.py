"""Validação de uma janela PREVISTA contra a janela das irregulares (domínio do tempo).

Entrada:
- previsão: planilha com a aba ``Previsão`` (``cvd screen predict`` ou ``cvd screen fd``),
  colunas Direção (°), Tp (s) e o Hs limite contínuo (``Hs limite (contínuo, m)`` ou
  ``A*/R (m)``), ou só ``Hs prevista (m)``;
- irregulares: planilha do ``cvd post --window`` (aba ``Status``).

Para cada (Tp, direção) a irregular dá um intervalo: o maior Hs aprovado (``hs_ok``)
abaixo do menor Hs reprovado (``hs_bad``). A previsão é classificada como

- ``dentro``: hs_ok ≤ prevista < hs_bad (acerta o intervalo da grade);
- ``contra a segurança``: prevista ≥ hs_bad (prevê mais do que as irregulares aprovam);
- ``conservadora``: prevista < hs_ok;
- ``indefinido``: a irregular não tem reprovação (só um limite inferior) ou não tem aprovação.

Se a previsão tem a aba ``UF FD`` (frequency domain), também compara o UF de cada estado de
mar das irregulares com o UF previsto no mesmo (Tp, Hs, direção): razão irregular/FD por
critério, para calibrar a previsão.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

INSIDE, UNSAFE, CONSERVATIVE, UNDEFINED = "dentro", "contra a segurança", "conservadora", "indefinido"


def read_prediction(path: Path):
    import pandas as pd

    df = pd.read_excel(path, sheet_name="Previsão")
    raw_col = next((c for c in ("Hs limite (contínuo, m)", "A*/R (m)") if c in df.columns), None)
    out = {}
    for _, r in df.iterrows():
        key = (float(r["Tp (s)"]), float(r["Direção (°)"]))
        raw = r[raw_col] if raw_col else r.get("Hs prevista (m)")
        if raw is None or (isinstance(raw, float) and math.isnan(raw)):
            raw = r.get("Hs prevista (m)")
        out[key] = None if raw is None or (isinstance(raw, float) and math.isnan(raw)) else float(raw)
    return out


def td_brackets(status_df) -> dict[tuple[float, float], tuple[Optional[float], Optional[float]]]:
    """(Tp, direção) -> (maior Hs aprovado abaixo do menor reprovado, menor Hs reprovado)."""
    from ..post.window import APPROVED

    out = {}
    df = status_df.dropna(subset=["Tp (s)", "Hs (m)", "Dir (°)"])
    for (tp, d), g in df.groupby(["Tp (s)", "Dir (°)"]):
        ok = sorted(g.loc[g["Status"] == APPROVED, "Hs (m)"].astype(float))
        bad = sorted(g.loc[g["Status"] != APPROVED, "Hs (m)"].astype(float))
        hs_bad = bad[0] if bad else None
        below = [h for h in ok if hs_bad is None or h < hs_bad]
        out[(float(tp), float(d))] = (below[-1] if below else None, hs_bad)
    return out


def classify(pred: Optional[float], hs_ok: Optional[float], hs_bad: Optional[float]) -> str:
    if pred is None:
        return UNDEFINED
    if hs_bad is not None and pred >= hs_bad - 1e-9:
        return UNSAFE
    if hs_ok is not None and pred < hs_ok - 1e-9:
        return CONSERVATIVE
    if hs_bad is None or hs_ok is None:
        return UNDEFINED
    return INSIDE


def compare_limits(pred: dict, brackets: dict) -> list[dict]:
    rows = []
    for key in sorted(set(pred) | set(brackets)):
        tp, d = key
        p = pred.get(key)
        hs_ok, hs_bad = brackets.get(key, (None, None))
        rows.append({"Tp (s)": tp, "Dir (°)": d, "Hs prevista (m)": p, "Maior Hs aprovado (TD)": hs_ok,
                     "Menor Hs reprovado (TD)": hs_bad, "Classe": classify(p, hs_ok, hs_bad)})
    return rows


def compare_uf(fd_points, status_df) -> list[dict]:
    """UF irregular × UF frequency domain nos mesmos (Tp, Hs, direção), por critério."""
    keyed = {}
    for _, r in fd_points.iterrows():
        keyed[(round(float(r["Tp (s)"]), 6), round(float(r["Hs (m)"]), 6), round(float(r["Direção (°)"]), 6))] = r
    rows = []
    uf_cols = [c for c in status_df.columns if c.startswith("UF ") and c != "UF máx"]
    for _, r in status_df.dropna(subset=["Tp (s)", "Hs (m)", "Dir (°)"]).iterrows():
        k = (round(float(r["Tp (s)"]), 6), round(float(r["Hs (m)"]), 6), round(float(r["Dir (°)"]), 6))
        f = keyed.get(k)
        if f is None:
            continue
        row = {"Tp (s)": k[0], "Hs (m)": k[1], "Dir (°)": k[2], "UF TD": r.get("UF máx"), "UF FD": f.get("UF FD"),
               "Governa TD": r.get("Governa"), "Governa FD": f.get("Governa")}
        for c in uf_cols:
            name = c[3:]
            td, fd = r.get(c), f.get(f"{name}: UF")
            row[f"{name}: TD/FD"] = (float(td) / float(fd)) if _num(td) and _num(fd) and float(fd) > 0 else None
        try:
            row["UF TD/FD"] = float(row["UF TD"]) / float(row["UF FD"])
        except (TypeError, ValueError, ZeroDivisionError):
            row["UF TD/FD"] = None
        rows.append(row)
    return rows


def _num(v) -> bool:
    try:
        return v is not None and not math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def summary(limit_rows: list[dict], uf_rows: list[dict]) -> list[dict]:
    from collections import Counter

    counts = Counter(r["Classe"] for r in limit_rows)
    out = [{"Indicador": f"Pares (Tp, direção): {k}", "Valor": v} for k, v in counts.items()]
    ratios = [r["UF TD/FD"] for r in uf_rows if _num(r.get("UF TD/FD"))]
    if ratios:
        ratios.sort()
        out += [{"Indicador": "UF TD/FD: mínimo", "Valor": ratios[0]},
                {"Indicador": "UF TD/FD: mediana", "Valor": ratios[len(ratios) // 2]},
                {"Indicador": "UF TD/FD: máximo (fator de calibração conservador)", "Valor": ratios[-1]},
                {"Indicador": "Estados de mar comparados", "Valor": len(ratios)}]
    return out


def write_comparison(path: Path, limit_rows, uf_rows, summary_rows) -> Path:
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill

    path = Path(path)
    mode = "a" if path.exists() else "w"
    kw = {"if_sheet_exists": "replace"} if mode == "a" else {}
    with pd.ExcelWriter(path, engine="openpyxl", mode=mode, **kw) as w:
        pd.DataFrame(summary_rows or [{}]).to_excel(w, sheet_name="Comparação resumo", index=False)
        pd.DataFrame(limit_rows or [{}]).to_excel(w, sheet_name="Comparação limites", index=False)
        if uf_rows:
            pd.DataFrame(uf_rows).to_excel(w, sheet_name="Comparação UF", index=False)
    wb = load_workbook(path)
    ws = wb["Comparação limites"]
    fills = {INSIDE: "C6EFCE", UNSAFE: "FFC7CE", CONSERVATIVE: "FFEB9C"}
    header = [c.value for c in ws[1]]
    if "Classe" in header:
        col = header.index("Classe") + 1
        for row in ws.iter_rows(min_row=2):
            v = row[col - 1].value
            if v in fills:
                row[col - 1].fill = PatternFill("solid", fgColor=fills[v])
    wb.save(path)
    return path
