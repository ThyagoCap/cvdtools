"""Propriedades de massa do MCV calculadas pelas cotas da Input Sheet (aba "MCVs" da planilha geral).

Cotas (desenho da aba MCVs): F = altura da conexão da linha, B = altura acima de F até o topo,
G = distância horizontal do eixo do MCV até a conexão da linha, D e E = distâncias da conexão
até o centro de gravidade.

* **CoG = CoV** = (G − E; 0; F − D), na convenção da aba "MCVs" (linha "CoG"). Sem D ou E
  (N/I), valem os valores alternativos da própria aba: D = 0,3 m e E = G − 0,3 m.
* **Altura** = B + F (linha "Height [B + F]").
* **Inércia** de uma caixa com a massa no ar, em torno do CoG:
  Lx = G + base/2 (do fundo da base até a ponta do gooseneck), Ly = base (lado da base
  desenhada, 0,6 m) e Lz = B + F;  Ixx = m(Ly² + Lz²)/12, Iyy = m(Lx² + Lz²)/12,
  Izz = m(Lx² + Ly²)/12. Reproduz o Modelo 2 do GLF27 (0,62 / 1,22 / 0,72 t·m² para o MCV
  MOBO de 2,002 t).
* MCV da outra extremidade (2 extremidades): mesmas fórmulas com as cotas dele; o desenho é
  espelhado em x (gooseneck para −X), então CoG e CoV também: x = −(G − E).

Os nomes dos dados no OrcFxAPI são procurados numa lista de candidatos e conferidos com
``DataNameValid`` (no OrcaFlex real) antes de gravar; se nenhum valer, o build avisa.
"""

from __future__ import annotations

from typing import Optional

from ..schema import CaseInputs
from ..values import is_number

ALT_D = 0.3          # aba MCVs: valor alternativo de D sem input
ALT_E_FROM_G = 0.3   # aba MCVs: E alternativo = G − 0,3

# grandeza -> candidatos de nomes (X, Y, Z) no OrcFxAPI, na ordem de preferência
VECTOR_NAMES: dict[str, list[tuple[str, str, str]]] = {
    "cog": [("CentreOfMassX", "CentreOfMassY", "CentreOfMassZ")],
    "cov": [("CentreOfVolumeX", "CentreOfVolumeY", "CentreOfVolumeZ")],
    "inertia": [("MomentOfInertiaX", "MomentOfInertiaY", "MomentOfInertiaZ"),
                ("MomentsOfInertiaX", "MomentsOfInertiaY", "MomentsOfInertiaZ"),
                ("MassMomentOfInertiaX", "MassMomentOfInertiaY", "MassMomentOfInertiaZ")],
}
LABELS = {"cog": "CoG", "cov": "CoV", "inertia": "inércia"}


def _opt(case: CaseInputs, key: str) -> Optional[float]:
    try:
        v = case.get(key)
    except KeyError:
        return None
    return float(v) if is_number(v) else None


def mcv_dims(case: CaseInputs, prefix: str = "mcv") -> dict:
    """Cotas e massa do MCV (``mcv`` = ponta A, ``mcv_b`` = outra extremidade)."""
    dims = {k: case.num(f"{prefix}.{k}") for k in ("b", "f", "g", "mass_air")}
    dims["base"] = case.num(f"{prefix}.base_size")
    dims["d"] = _opt(case, f"{prefix}.d")
    dims["e"] = _opt(case, f"{prefix}.e")
    return dims


def mcv_properties(dims: dict, mirror: bool = False) -> dict:
    """CoG, CoV, altura e inércia de caixa (ver docstring do módulo). Puro, testável."""
    b, f, g, m, base = dims["b"], dims["f"], dims["g"], dims["mass_air"], dims["base"]
    notes = []
    d, e = dims.get("d"), dims.get("e")
    if d is None:
        d = ALT_D
        notes.append(f"cota D sem input: CoG/CoV com D = {ALT_D:g} m (valor alternativo da aba MCVs)")
    if e is None:
        e = g - ALT_E_FROM_G
        notes.append(f"cota E sem input: CoG/CoV com E = G − {ALT_E_FROM_G:g} m (valor alternativo da aba MCVs)")
    sign = -1.0 if mirror else 1.0
    centre = (sign * (g - e), 0.0, f - d)
    height = b + f
    lx, ly, lz = g + base / 2.0, base, height
    inertia = (m * (ly ** 2 + lz ** 2) / 12.0, m * (lx ** 2 + lz ** 2) / 12.0, m * (lx ** 2 + ly ** 2) / 12.0)
    return {"cog": centre, "cov": centre, "height": height, "inertia": inertia, "box": (lx, ly, lz), "notes": notes}


# --------------------------------------------------------------------------- leitura/gravação no OrcFxAPI

def _valid(obj, name: str) -> Optional[bool]:
    """True/False no OrcFxAPI real (``DataNameValid``); None quando não há como saber (testes)."""
    if not hasattr(type(obj), "DataNameValid"):
        return None
    try:
        return bool(obj.DataNameValid(name))
    except Exception:
        return False


def set_vector(obj, kind: str, values) -> Optional[str]:
    """Grava (x, y, z) no primeiro trio de nomes aceito; devolve o nome-base usado (ou None)."""
    for names in VECTOR_NAMES[kind]:
        if any(_valid(obj, n) is False for n in names):
            continue
        try:
            for n, v in zip(names, values):
                setattr(obj, n, float(v))
            if all(abs(float(getattr(obj, n)) - float(v)) <= 1e-9 * max(1.0, abs(float(v)))
                   for n, v in zip(names, values)):
                return names[0][:-1]
        except Exception:
            continue
    return None


def get_vector(obj, kind: str) -> Optional[tuple[float, float, float]]:
    for names in VECTOR_NAMES[kind]:
        if any(_valid(obj, n) is False for n in names):
            continue
        try:
            return tuple(float(getattr(obj, n)) for n in names)
        except Exception:
            continue
    return None


def apply_mcv_properties(obj, props: dict, label: str, notes: list[str]) -> None:
    """Grava altura, CoG, CoV e inércia no 6D buoy do MCV; o que não puder ser gravado vira AVISO."""
    obj.Height = props["height"]
    failed = [LABELS[k] for k in ("cog", "cov", "inertia") if set_vector(obj, k, props[k]) is None]
    cog, inertia = props["cog"], props["inertia"]
    notes.append(f"{label}: CoG = CoV = ({cog[0]:.4f}; 0; {cog[2]:.4f}) m, altura {props['height']:.4f} m, inércia "
                 f"de caixa {inertia[0]:.3f} / {inertia[1]:.3f} / {inertia[2]:.3f} t·m² "
                 f"(caixa {props['box'][0]:.3f} × {props['box'][1]:.3f} × {props['box'][2]:.3f} m)")
    for n in props["notes"]:
        notes.append(f"AVISO: {label}: {n}")
    if failed:
        notes.append(f"AVISO: {label}: {', '.join(failed)} não gravado(s) (nome do dado não aceito pelo OrcFxAPI); "
                     f"confira no OrcaFlex")


def box_ixx(mass: float, base: float, b: float, f: float) -> float:
    return mass * (base ** 2 + (b + f) ** 2) / 12.0


def box_iyy(mass: float, g: float, base: float, b: float, f: float) -> float:
    return mass * ((g + base / 2.0) ** 2 + (b + f) ** 2) / 12.0


def box_izz(mass: float, g: float, base: float) -> float:
    return mass * ((g + base / 2.0) ** 2 + base ** 2) / 12.0
