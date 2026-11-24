"""Modelo dinâmico base (``<nome>_dyn.dat``) a partir de uma posição estática (0.12.0).

Passo que antes era feito à mão no OrcaFlex:

1. abre a posição (padrão ``01.Positions/01.Horizontal``; prefere o ``.sim``, cuja estática está
   calculada, e grava as posições calculadas como forma inicial; sem ``.sim``, usa o ``.dat`` salvo
   pelo ``statics positions``, que já tem as posições calculadas);
2. trava o MCV: ``Connection = Anchored``, com X, Y e rotações como estão e Z exatamente na altura do hub
   (comprimento do shape do hub = A − F, medido a partir do fundo, como no build);
3. apaga o cabo do guindaste;
4. mostra o movimento da embarcação herdado do modelo (não muda; avisa se não houver RAOs);
5. confere a estática (opcional) e salva ``<pasta do caso>/<nome>_dyn.dat``.

Estágios, onda, passo de tempo e direção são escritos depois, em cada estado de mar, pelo ``seastates``.

Depois disso o ``seastates`` gera os estados de mar a partir desse arquivo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..log import log
from ..schema import CaseInputs


def _f(obj, name, default=float("nan")) -> float:
    try:
        return float(getattr(obj, name))
    except Exception:
        return default


def default_source(case_dir: Path, position: str = "01.Horizontal") -> Path:
    """``.sim`` da posição, se existir; senão o ``.dat``."""
    folder = Path(case_dir) / "01.Positions"
    for ext in (".sim", ".dat"):
        p = folder / f"{position}{ext}"
        if p.is_file():
            return p
    raise FileNotFoundError(f"Posição '{position}' não encontrada em {folder} (.sim ou .dat). Rode antes o "
                            "'statics positions' (script 03).")


def hub_height(model, case: CaseInputs) -> float:
    """Altura do hub acima do fundo = comprimento do shape do hub (A − F, como no build)."""
    try:
        return float(model[case.role("HUB")].Length)
    except Exception:
        return case.num("mcv.a") - case.num("mcv.f")


def anchor_mcv(model, case: CaseInputs) -> dict:
    """MCV ``Anchored`` com Z exatamente na altura do hub (0.12.1); X, Y e rotações como estão.

    Devolve {"x", "y", "z_global", "z_anchored", "dz"}: ``z_global`` é onde o MCV estava (Z global, MCV livre)
    e ``dz`` a correção vertical aplicada (MCV ancorado − posição da estática).
    """
    mcv = model[case.role("MCV")]
    conn = str(getattr(mcv, "Connection", ""))
    depth = _f(model.environment, "WaterDepth", 0.0)
    x, y, z = _f(mcv, "InitialX"), _f(mcv, "InitialY"), _f(mcv, "InitialZ")
    z_global = z - depth if conn.lower() == "anchored" else z
    if conn.lower() not in ("anchored", "free"):
        log.warning("  MCV estava com Connection = '%s' (esperado Free); Z tratado como global", conn)
    h = hub_height(model, case)
    mcv.Connection = "Anchored"
    mcv.InitialZ = h   # ancorado: Z medido a partir do fundo = altura do hub
    return {"x": x, "y": y, "z_global": z_global, "z_anchored": h, "dz": (h - depth) - z_global}


def _report_vessel_motion(model, case: CaseInputs) -> None:
    """Mostra o movimento da embarcação herdado do modelo (o código não muda); avisa se não há RAOs."""
    name = case.roles.get("VESSEL", "")
    try:
        v = model[name]
    except Exception:
        log.warning("  Embarcação '%s' não encontrada: confira o movimento no modelo dinâmico", name)
        return
    vals = {}
    for attr in ("PrimaryMotion", "SuperimposedMotion"):
        try:
            vals[attr] = str(getattr(v, attr))
        except Exception:
            vals[attr] = "?"
    log.info("  Embarcação '%s' (como no modelo): PrimaryMotion = %s, SuperimposedMotion = %s", name,
             vals["PrimaryMotion"], vals["SuperimposedMotion"])
    if vals["SuperimposedMotion"].strip().lower() == "none":
        log.warning("  Embarcação sem movimento sobreposto (RAOs): a dinâmica não terá movimento de onda da "
                    "embarcação. Ajuste no modelo base ou no <nome>_dyn.dat")


def make_dynamic_base(case: CaseInputs, case_dir: Path, name: str, source: Optional[Path] = None,
                      position: str = "01.Horizontal", out: Optional[Path] = None, check_statics: bool = True,
                      overwrite: bool = True) -> Path:
    """Cria ``<case_dir>/<name>_dyn.dat`` a partir da posição ``position`` (ou do arquivo ``source``)."""
    from .._orcaflex import calculate_statics_robust, require_orcfxapi, restore_statics_settings, \
        use_calculated_positions

    orc = require_orcfxapi()
    src = Path(source) if source else default_source(case_dir, position)
    out = Path(out) if out else Path(case_dir) / f"{name}_dyn.dat"
    if out.exists() and not overwrite:
        raise FileExistsError(f"{out} já existe (use sobrescrever)")
    log.info("Modelo dinâmico base a partir de %s", src)
    model = orc.Model(str(src))
    if src.suffix.lower() == ".sim":
        # estática calculada no .sim: posições calculadas viram a forma inicial (linhas: user specified)
        use_calculated_positions(model)

    info = anchor_mcv(model, case)
    log.info("  MCV '%s' ancorado em X = %.4f, Y = %.4f m e Z = altura do hub, %.4f m acima do fundo (na estática "
             "estava %+.4f m dessa altura)", case.role("MCV"), info["x"], info["y"], info["z_anchored"], -info["dz"])

    crane = case.roles.get("CRANE_WIRE", "")
    if crane:
        try:
            model.DestroyObject(model[crane])
            log.info("  Cabo do guindaste '%s' apagado", crane)
        except KeyError:
            log.info("  Cabo do guindaste '%s' não está no modelo (nada a apagar)", crane)
        except Exception as exc:
            raise RuntimeError(f"Não consegui apagar o cabo do guindaste '{crane}': {exc}") from exc

    _report_vessel_motion(model, case)

    if check_statics:
        original = calculate_statics_robust(model, case, "modelo dinâmico base")
        try:
            z = float(model[case.role("MCV")].StaticResult("Z"))
            expected = info["z_global"] + info["dz"]
            if abs(z - expected) > 1e-3:
                log.warning("  MCV na estática em Z = %.4f m, esperado %.4f m (altura do hub; diferença %.4f m): "
                            "confira a conexão do MCV no modelo dinâmico", z, expected, z - expected)
        except Exception:
            pass
        use_calculated_positions(model)
        restore_statics_settings(model, original)
        log.info("  Estática do modelo dinâmico base convergiu; posições gravadas como forma inicial")

    try:
        from ..model.view import set_default_view

        set_default_view(model, case)
    except Exception:
        pass
    out.parent.mkdir(parents=True, exist_ok=True)
    model.SaveData(str(out))
    log.info("  Salvo: %s", out)
    return out
