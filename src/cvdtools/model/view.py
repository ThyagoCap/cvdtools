"""Vista padrão do OrcaFlex e translação horizontal do modelo.

- ``set_default_view``: vista padrão (aba General) centrada no MCV e no hub, de lado
  (azimute 270°, elevação 0°), com tamanho que engloba os dois.
- ``shift_model_x``: translada o modelo em X (MCV, masterlink, pontas fixas/ancoradas
  das linhas) para alinhar o MCV com o hub. Usa ``OrcFxAPI.MoveObjects``, que também
  leva as formas iniciais das linhas; se não estiver disponível, edita as coordenadas.
"""

from __future__ import annotations

from ..log import log
from ..schema import CaseInputs


def _get(obj, name, default=0.0):
    try:
        return float(getattr(obj, name))
    except Exception:
        return default


def _try(model, name):
    try:
        return model[name] if name else None
    except Exception:
        return None


def mcv_global_position(model, case: CaseInputs) -> tuple[float, float, float]:
    """Posição global da origem do MCV a partir dos dados (InitialZ é relativo ao fundo se ancorado)."""
    mcv = model[case.role("MCV")]
    x, y, z = _get(mcv, "InitialX"), _get(mcv, "InitialY"), _get(mcv, "InitialZ")
    if str(getattr(mcv, "Connection", "")).lower() == "anchored":
        z -= _get(model.environment, "WaterDepth")
    return x, y, z


def hub_x(model, case: CaseInputs) -> float:
    hub = _try(model, case.roles.get("HUB", ""))
    return _get(hub, "OriginX", 0.0) if hub is not None else 0.0


def set_default_view(model, case: CaseInputs, margin: float = 2.5, min_size: float = 15.0) -> dict:
    """Vista padrão englobando MCV e hub."""
    depth = _get(model.environment, "WaterDepth")
    mcv = model[case.role("MCV")]
    x, y, z = mcv_global_position(model, case)
    try:
        vx = [float(v) for v in mcv.VertexX]
        vz = [float(v) for v in mcv.VertexZ]
        width = (max(vx) - min(vx)) if vx else 0.0
        top = z + (max(vz) if vz else 0.0)
    except Exception:
        width, top = 0.0, z
    seabed = -depth
    hx = hub_x(model, case)
    height = max(top - seabed, 1.0)
    extent = max(height, width, abs(x - hx) + width)
    size = max(min_size, margin * extent)
    centre = [(x + hx) / 2, y, seabed + height / 2]
    g = model.general
    view = {"centre": centre, "size": size}
    errors = []
    # Nomes dos dados: no .yml aparecem agrupados (DefaultViewCentre: [x, y, z]); na API cada componente
    # tem o próprio nome. Tenta as formas conhecidas, nesta ordem.
    attempts = [
        lambda: [setattr(g, f"DefaultViewCentre{c}", v) for c, v in zip("XYZ", centre)],
        lambda: setattr(g, "DefaultViewCentre", centre),
    ]
    for attempt in attempts:
        try:
            attempt()
            break
        except Exception as exc:
            errors.append(str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__)
    else:
        view["error"] = "centro: " + " | ".join(errors)
    for name, value in (("DefaultViewSize", size), ("DefaultViewAzimuth", 270.0), ("DefaultViewElevation", 0.0)):
        try:
            setattr(g, name, value)
        except Exception as exc:
            view.setdefault("error", "")
            view["error"] += f" {name}: {exc}"
    if "error" in view:
        log.warning("  Vista padrão incompleta: %s", view["error"])
    return view


def _connection(obj, end: str = "") -> str:
    name = f"End{end}Connection" if end else "Connection"
    try:
        return str(getattr(obj, name))
    except Exception:
        return ""


ROOT = ("fixed", "anchored", "free")


def probe_points(obj, orc, candidates=range(0, 4)) -> list[int]:
    """Índices de ponto aceitos pelo MoveObjects para ``obj`` (teste com deslocamento nulo).

    A documentação do OrcFxAPI não diz se os índices começam em 0 ou 1, nem quantos pontos cada
    tipo tem; testar com deslocamento zero não muda o modelo.
    """
    ok = []
    zero = orc.MoveObjectDisplacementSpecification([0.0, 0.0, 0.0])
    for i in candidates:
        try:
            orc.MoveObjects(zero, [orc.MoveObjectPoint(obj, i)])
            ok.append(i)
        except Exception:
            continue
    return ok


def root_points(model, case: CaseInputs, orc=None) -> list[tuple]:
    """Pontos "raiz" (fixos, ancorados ou livres) de todos os objetos, exceto o hub do MCV A.

    Pontos conectados a outro objeto (ex.: End B do cabo do guindaste no MCV, lingas no masterlink)
    acompanham o objeto pai e não entram, senão seriam deslocados duas vezes. Devolve
    [(objeto, índice ou None, rótulo, conexão)], índice None quando não há OrcFxAPI para testar.
    """
    # 0.11.5: só o hub do MCV A fica; o resto (inclusive MCV B, hub B e o suporte do MCV B, com 2 extremidades)
    # anda junto, como corpo rígido, e o equilíbrio não muda. Até a 0.11.4 o MCV B ficava: o vão entre os hubs
    # mudava e o MCV A não chegava ao hub (GLF27: MCV em X = 2,03 m).
    skip = {case.roles.get("HUB", "")} - {""}
    out = []
    for obj in list(getattr(model, "objects", [])):
        name = getattr(obj, "name", "")
        tname = str(getattr(obj, "typeName", ""))
        if name in skip or tname in ("General", "Environment") or "type" in tname.lower() or "data" in tname.lower():
            continue
        if tname == "Line" or tname == "Link" or tname == "Winch":
            ends = [("A", _connection(obj, "A")), ("B", _connection(obj, "B"))]
            idx = probe_points(obj, orc) if orc is not None else []
            if orc is not None and len(idx) < 2:
                continue
            for k, (end, conn) in enumerate(ends):
                if conn.lower() in ROOT:
                    out.append((obj, idx[k] if idx else None, f"{name} End {end}", conn))
        else:
            conn = _connection(obj)
            if conn.lower() not in ROOT:
                continue
            idx = probe_points(obj, orc) if orc is not None else []
            if orc is not None and not idx:
                continue
            out.append((obj, idx[0] if idx else None, name, conn))
    return out


def _movable_points(model, case: CaseInputs):
    """Alternativa sem ``model.objects`` (modelos de teste): papéis conhecidos."""
    pts = []
    for role in ("MCV", "MASTERLINK"):
        obj = _try(model, case.roles.get(role, ""))
        if obj is not None and _connection(obj).lower() in ROOT:
            pts.append((obj, 0, role, _connection(obj)))
    for role in ("CRANE_WIRE", "WINCH_WIRE", "LINE"):
        line = _try(model, case.roles.get(role, ""))
        if line is None:
            continue
        for idx, end in ((0, "A"), (1, "B")):
            conn = _connection(line, end)
            if conn.lower() in ROOT:
                pts.append((line, idx, f"{role} End {end}", conn))
    return pts


def shift_model_x(model, case: CaseInputs, dx: float, orc=None) -> str:
    """Translada em X todo o modelo, exceto o hub (pontos raiz; o resto acompanha os pais)."""
    has_move = orc is not None and hasattr(orc, "MoveObjects") and hasattr(orc, "MoveObjectPoint")
    objs = list(getattr(model, "objects", []) or [])
    real_objects = bool(objs) and all(hasattr(o, "typeName") for o in objs)
    pts = root_points(model, case, orc if has_move else None) if real_objects else _movable_points(model, case)
    log.info("  Pontos transladados (todos os objetos exceto o hub; os conectados acompanham): %s",
             ", ".join(f"{label} [{conn}]" for _, _, label, conn in pts) or "-")
    if has_move and pts and all(i is not None for _, i, _, _ in pts):
        try:
            orc.MoveObjects(orc.MoveObjectDisplacementSpecification([dx, 0.0, 0.0]),
                            [orc.MoveObjectPoint(obj, i) for obj, i, _, _ in pts])
            return "MoveObjects"
        except Exception as exc:
            log.warning("  MoveObjects falhou (%s); movendo pelas coordenadas", str(exc).strip().splitlines()[-1])
    for obj, _, label, _ in pts:
        if " End " in label:
            end = label.rsplit(" ", 1)[-1]
            setattr(obj, f"End{end}X", _get(obj, f"End{end}X") + dx)
        else:
            obj.InitialX = _get(obj, "InitialX") + dx
    return "coordenadas"
