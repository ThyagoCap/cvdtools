"""Captura do baseline a partir da pipeline antiga (somente leitura).

Os scripts antigos são COPIADOS para uma pasta temporária e executados lá.
Nenhum arquivo da pasta de origem é alterado. O que fica salvo no baseline:

- ``inputs.json``             cópia do __INPUTS.json usado
- ``base_model.yml``          modelo base (00.Base/base_input.dat) em texto
- ``generated_model.yml``     modelo gerado pelo _01.GenerateModel.py
- ``generated_statics.json``  resultados estáticos do modelo gerado
- ``adjusted_model.yml``      (opcional) modelo após o _02.AdjustWireWinch.py
- ``adjusted_statics.json``   (opcional) resultados estáticos do modelo ajustado
- ``postproc_logs.csv``       (opcional) tabela "Logs" de um pós-processamento dinâmico
- ``sims_index.csv``          (opcional) lista dos .sim que geraram essa tabela
- ``legacy_stdout/*.txt``     saída de console dos scripts antigos
- ``manifest.json``           hashes das fontes, versões e data da captura
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .. import __version__
from .._orcaflex import orcaflex_version, require_orcfxapi
from .fingerprint import save_model_yaml

LEGACY_GENERATE = "_01.GenerateModel.py"
LEGACY_ADJUST = "_02.AdjustWireWinch.py"
LEGACY_INPUTS = "__INPUTS.json"
LEGACY_BASE_MODEL = Path("00.Base") / "base_input.dat"

SOURCE_PATTERNS = ("*.py", "*.json", "*.ipynb", "*.xlsx", "00.Base/*")


# --------------------------------------------------------------------------- utilidades

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_sources(code_base: Path) -> dict[str, str]:
    """Hash de todos os arquivos relevantes da pasta da pipeline antiga."""
    hashes: dict[str, str] = {}
    for pattern in SOURCE_PATTERNS:
        for p in sorted(code_base.glob(pattern)):
            if p.is_file() and not p.name.startswith("~$"):
                hashes[p.relative_to(code_base).as_posix()] = sha256(p)
    return hashes


def _run_legacy(script: str, workdir: Path, log_dir: Path, tag: str) -> None:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")  # evita erro de console cp1252 no Windows
    proc = subprocess.run(
        [sys.executable, script],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{tag}.txt").write_text(
        f"$ {script}\n[returncode={proc.returncode}]\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{script} falhou (código {proc.returncode}). Veja {log_dir / (tag + '.txt')}")


# --------------------------------------------------------------------------- extração estática
# Reproduz as MESMAS chamadas OrcFxAPI dos scripts antigos (_03 checkCriteria e
# os itens estáticos de _12), para que o baseline reflita a pipeline atual.

def _extra(orc, value):
    if value == "End A":
        return orc.oeEndA
    if value == "End B":
        return orc.oeEndB
    if value == "Touchdown":
        return orc.oeTouchdown
    if isinstance(value, list) and len(value) == 2:
        return orc.arSpecifiedArclengths(value[0], value[1])
    if value in ("", None):
        return None
    raise ValueError(f"OrcaFlex Extra não reconhecido: {value!r}")


def extract_static_results(model, inputs: dict) -> dict[str, Any]:
    """Calcula a estática e extrai critérios e itens estáticos do pós-processamento."""
    orc = require_orcfxapi()
    static = orc.Period(orc.PeriodNum.StaticState)

    out: dict[str, Any] = {"acceptanceCriteria": {}, "postProcessStatic": {}}

    try:
        model.CalculateStatics()
    except Exception as exc:  # não-convergência fica registrada no baseline
        out["statics_error"] = str(exc)
        return out

    for key, val in inputs.get("acceptanceCriteria", {}).items():
        try:
            obj = model[val["OrcaFlex Parent"]]
            if val["Type"] == "Time History":
                res = obj.TimeHistory(val["OrcaFlex Variable Name"], static, _extra(orc, val["OrcaFlex Extra"]))[0]
            elif val["Type"] == "Range Graph Max":
                res = max(obj.RangeGraph(val["OrcaFlex Variable Name"], static).Mean)
            else:
                raise ValueError(f"Tipo de critério desconhecido: {val['Type']}")
            out["acceptanceCriteria"][key] = {
                "value": float(res),
                "limit": val["Limit"],
                "uf": float(res) / val["Limit"],
            }
        except Exception as exc:
            out["acceptanceCriteria"][key] = {"error": str(exc)}

    for key, val in inputs.get("postProcess", {}).items():
        method = val["OrcaFlex Method"]
        period = val.get("Simulation Period", "")
        if period not in ("", None) and method != "Get data":
            continue  # itens dinâmicos ficam para o baseline de pós-processamento
        try:
            parent = val["OrcaFlex Parent"]
            obj = model.general if parent == "General" else (
                model.environment if parent == "Environment" else model[parent])
            var = val["OrcaFlex Variable Name"]
            extra = _extra(orc, val["OrcaFlex Extra"])
            if method == "Get data":
                try:
                    res = getattr(obj, var)
                except Exception:
                    res = obj.GetData(var, -1)
            elif method == "Static result":
                res = obj.TimeHistory(var, static, extra)[0]
            elif method == "Range graph min":
                # Num modelo só com estática, Min/Max do range graph vêm indefinidos (2e307);
                # o valor estático está em Mean (mesmo uso do _03).
                res = min(obj.RangeGraph(var, static, None, extra).Mean)
            elif method == "Range graph max":
                res = max(obj.RangeGraph(var, static, None, extra).Mean)
            else:
                continue
            out["postProcessStatic"][key] = float(res) if isinstance(res, (int, float)) else res
        except Exception as exc:
            out["postProcessStatic"][key] = {"error": str(exc)}

    return out


def _winch_state(model) -> dict[str, Any]:
    state: dict[str, Any] = {}
    try:
        wire = model["Winch Wire"]
        state["WinchWire.Length[0]"] = float(wire.Length[0])
        state["WinchWire.EndAInitialArcLength"] = float(wire.EndAInitialArcLength)
    except Exception as exc:
        state["error"] = str(exc)
    return state


# --------------------------------------------------------------------------- captura

def capture_baseline(
    code_base: Path,
    out_dir: Path,
    with_adjust: bool = True,
    postproc_table: Path | None = None,
    postproc_sheet: str = "Logs",
    sims_dir: Path | None = None,
    keep_workdir: bool = False,
) -> Path:
    """Executa a pipeline antiga numa cópia temporária e grava o baseline em ``out_dir``."""
    orc = require_orcfxapi()
    code_base = Path(code_base).resolve()
    out_dir = Path(out_dir).resolve()

    if out_dir.exists() and any(out_dir.iterdir()):
        if (out_dir / "manifest.json").exists():
            raise FileExistsError(f"{out_dir} já tem um baseline completo. Um baseline não é sobrescrito; use outra pasta.")
        # Captura anterior interrompida (sem manifest): não é um baseline válido, pode ser refeita
        print(f"Pasta {out_dir} tem uma captura incompleta (sem manifest.json); refazendo.")
        shutil.rmtree(out_dir)
    if out_dir == code_base or (code_base / "00.Base") in (out_dir, *out_dir.parents):
        raise ValueError("A pasta de saída não pode ser a pasta da pipeline antiga nem ficar dentro de 00.Base.")

    for required in (LEGACY_GENERATE, LEGACY_INPUTS, LEGACY_BASE_MODEL):
        if not (code_base / required).is_file():
            raise FileNotFoundError(f"Não encontrei {required} em {code_base}")

    hashes_before = hash_sources(code_base)
    out_dir.mkdir(parents=True, exist_ok=True)
    logs = out_dir / "legacy_stdout"

    inputs = json.loads((code_base / LEGACY_INPUTS).read_text(encoding="utf-8"))
    op_name = inputs["OperationName"]

    adjust_error = None
    tmp = Path(tempfile.mkdtemp(prefix="cvd_baseline_"))
    try:
        # Cópia das fontes antigas (a origem não é tocada)
        (tmp / "00.Base").mkdir()
        shutil.copy2(code_base / LEGACY_BASE_MODEL, tmp / LEGACY_BASE_MODEL)
        shutil.copy2(code_base / LEGACY_INPUTS, tmp / LEGACY_INPUTS)
        shutil.copy2(code_base / LEGACY_GENERATE, tmp / LEGACY_GENERATE)
        if with_adjust:
            shutil.copy2(code_base / LEGACY_ADJUST, tmp / LEGACY_ADJUST)

        shutil.copy2(code_base / LEGACY_INPUTS, out_dir / "inputs.json")
        save_model_yaml(code_base / LEGACY_BASE_MODEL, out_dir / "base_model.yml")

        # 1) Geração do modelo
        _run_legacy(LEGACY_GENERATE, tmp, logs, "01_generate")
        generated = tmp / f"{op_name}.dat"
        save_model_yaml(generated, out_dir / "generated_model.yml")
        model = orc.Model(str(generated))
        _dump(out_dir / "generated_statics.json", extract_static_results(model, inputs))

        # 2) Ajuste do cabo do guincho
        if with_adjust:
            adjusted = tmp / f"{op_name}_adjusted.dat"
            try:
                _run_legacy(LEGACY_ADJUST, tmp, logs, "02_adjust_run1")
                if not adjusted.exists():
                    # Com EndAInitialArcLength != 0 o _02 só zera o arc length e
                    # salva o modelo de entrada; é preciso rodá-lo de novo.
                    _run_legacy(LEGACY_ADJUST, tmp, logs, "02_adjust_run2")
                if not adjusted.exists():
                    raise RuntimeError("O _02.AdjustWireWinch.py não gerou o modelo ajustado.")
                save_model_yaml(adjusted, out_dir / "adjusted_model.yml")
                model = orc.Model(str(adjusted))
                result = extract_static_results(model, inputs)
                result["winch"] = _winch_state(model)
                _dump(out_dir / "adjusted_statics.json", result)
            except RuntimeError as exc:
                # O ajuste do cabo é opcional no fluxo (costuma ser feito à mão);
                # a falha fica registrada e o restante do baseline é mantido.
                adjust_error = str(exc)
                print(f"AVISO: ajuste do cabo do guincho (_02) falhou e ficou fora do baseline: {exc}")

        # 3) Pós-processamento dinâmico já existente (opcional)
        if postproc_table is not None:
            _capture_postproc(Path(postproc_table), postproc_sheet, out_dir)
        if sims_dir is not None:
            _index_sims(Path(sims_dir), out_dir)

    finally:
        if keep_workdir:
            print(f"Pasta temporária mantida em {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    hashes_after = hash_sources(code_base)
    if hashes_after != hashes_before:
        raise RuntimeError("ATENÇÃO: arquivos da pasta de origem mudaram durante a captura.")

    manifest = {
        "case": op_name,
        "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "cvdtools_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "orcaflex_dll": orcaflex_version(),
        "code_base": str(code_base),
        "with_adjust": with_adjust,
        "adjust_error": adjust_error,
        "postproc_table": str(postproc_table) if postproc_table else None,
        "sims_dir": str(sims_dir) if sims_dir else None,
        "legacy_source_hashes": hashes_before,
        "baseline_files": {
            p.relative_to(out_dir).as_posix(): sha256(p)
            for p in sorted(out_dir.rglob("*")) if p.is_file()
        },
    }
    _dump(out_dir / "manifest.json", manifest)
    return out_dir


def _dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _capture_postproc(table: Path, sheet: str, out_dir: Path) -> None:
    import pandas as pd

    if table.suffix.lower() == ".csv":
        df = pd.read_csv(table)
    else:
        df = pd.read_excel(table, sheet_name=sheet)
    if "Simulation Name" in df.columns:
        df = df.sort_values("Simulation Name").reset_index(drop=True)
    df.to_csv(out_dir / "postproc_logs.csv", index=False)


def _index_sims(sims_dir: Path, out_dir: Path) -> None:
    import csv

    rows = [
        {"file": p.name, "bytes": p.stat().st_size, "mtime": p.stat().st_mtime}
        for p in sorted(sims_dir.glob("*.sim"))
    ]
    with open(out_dir / "sims_index.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "bytes", "mtime"])
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------- verificação

def verify_baseline(baseline_dir: Path, code_base: Path | None = None) -> list[str]:
    """Confere a integridade do baseline e se a pipeline antiga mudou desde a captura.

    Devolve uma lista de problemas (vazia = tudo certo). Não precisa de OrcaFlex.
    """
    baseline_dir = Path(baseline_dir)
    problems: list[str] = []
    manifest_path = baseline_dir / "manifest.json"
    if not manifest_path.is_file():
        return [f"manifest.json não encontrado em {baseline_dir}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for rel, digest in manifest["baseline_files"].items():
        p = baseline_dir / rel
        if not p.is_file():
            problems.append(f"Arquivo do baseline ausente: {rel}")
        elif sha256(p) != digest:
            problems.append(f"Arquivo do baseline alterado: {rel}")

    if code_base is not None:
        current = hash_sources(Path(code_base))
        for rel, digest in manifest["legacy_source_hashes"].items():
            if rel not in current:
                problems.append(f"Fonte antiga removida: {rel}")
            elif current[rel] != digest:
                problems.append(f"Fonte antiga mudou desde a captura: {rel}")
        for rel in current.keys() - manifest["legacy_source_hashes"].keys():
            problems.append(f"Fonte antiga nova desde a captura: {rel}")
    return problems
