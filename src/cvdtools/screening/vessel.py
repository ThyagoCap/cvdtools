"""Resposta vertical da embarcação no ponto de içamento (substitui ``_18``).

O cálculo usa o NsgOrcFx (``VesselResponseList.ProcessExtremeResponses``), como o
_18; aqui só a leitura do resultado é refeita e testada. Da aba "All results" sai
a máxima amplitude de heave no ponto, por direção, Hs e Tp.

Para a janela prevista o que interessa é a resposta por metro de Hs
(``heave_max / Hs``): com RAOs lineares ela não depende de Hs. Se várias Hs
foram calculadas, usa-se a MAIOR razão (conservador).
"""

from __future__ import annotations

from pathlib import Path

from ..log import log
from ..schema import CaseInputs


def run(case: CaseInputs, dyn_model: Path, out_file: Path) -> Path:
    try:
        import NsgOrcFx as ofx
        from NsgOrcFx.vessel import VesselResponseList
    except Exception as exc:
        raise RuntimeError("NsgOrcFx não está instalado (pip install NsgOrcFx); ele calcula a resposta da "
                           "embarcação como no _18") from exc
    tps = case.lst("screening.tps")
    dirs = case.lst("screening.directions")
    hss = case.lst("screening.response_hs")
    model = ofx.Model(str(dyn_model))
    model.environment.WaveType = "JONSWAP"
    model.environment.UserSpecifiedRandomWaveSeeds = "Yes"
    model.environment.WaveSeed = 1
    responses = VesselResponseList()
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    responses.ProcessExtremeResponses(model=model, vesselName=case.role("VESSEL"),
                                      position=list(case.lst("screening.response_point")),
                                      waveDirsHsTp={d: [(h, t) for h in hss for t in tps] for d in dirs},
                                      outFile=str(out_file))
    log.info("Resposta da embarcação gravada em %s", out_file)
    return out_file


def read_max_heave(path: Path):
    """Tabela (direção, Hs, Tp, heave máx.) da aba 'All results' do NsgOrcFx (mesma leitura do _18)."""
    import pandas as pd

    xls = pd.ExcelFile(path)
    if "Summary" in xls.sheet_names:
        df = pd.read_excel(path, sheet_name="Summary")
        if {"Wave direction (deg)", "Wave height (m)", "Wave period (s)"} <= set(df.columns):
            return df
    sheet = "All results" if "All results" in xls.sheet_names else xls.sheet_names[0]
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    return parse_all_results(raw)


def parse_all_results(raw):
    import pandas as pd

    start = None
    for j in range(raw.shape[1]):
        v = raw.iloc[1, j]
        if pd.notna(v) and str(v).strip() == "Maximum Amplitude":
            start = j
            break
    if start is None:
        raise ValueError("Bloco 'Maximum Amplitude' não encontrado na linha 2 da aba de resultados")
    col = start + 2
    if str(raw.iloc[2, col]).strip() != "Heave [m]":
        raise ValueError(f"Coluna de heave inesperada: {raw.iloc[2, col]!r}")
    rows, d_cur, h_cur = [], None, None
    for i in range(3, len(raw)):
        r = raw.iloc[i]
        d = pd.to_numeric(r.iloc[1], errors="coerce")
        h = pd.to_numeric(r.iloc[2], errors="coerce")
        if pd.notna(d):
            d_cur = float(d)
        if pd.notna(h):
            h_cur = float(h)
        tp = pd.to_numeric(r.iloc[3], errors="coerce")
        heave = pd.to_numeric(r.iloc[col], errors="coerce")
        if d_cur is None or h_cur is None or pd.isna(tp) or pd.isna(heave):
            continue
        rows.append({"Wave direction (deg)": d_cur, "Wave height (m)": h_cur, "Wave period (s)": float(tp),
                     "Max heave displacement (m)": abs(float(heave))})
    if not rows:
        raise ValueError("Nenhum resultado de heave lido")
    df = pd.DataFrame(rows)
    return (df.groupby(["Wave direction (deg)", "Wave height (m)", "Wave period (s)"], as_index=False)
            ["Max heave displacement (m)"].max())


def response_per_hs(df) -> dict[tuple[float, float], float]:
    """{(direção, Tp): heave máx. por metro de Hs} (maior razão entre as Hs calculadas)."""
    out: dict[tuple[float, float], float] = {}
    for _, r in df.iterrows():
        hs = float(r["Wave height (m)"])
        if hs <= 0:
            continue
        key = (float(r["Wave direction (deg)"]), float(r["Wave period (s)"]))
        out[key] = max(out.get(key, 0.0), float(r["Max heave displacement (m)"]) / hs)
    return out
