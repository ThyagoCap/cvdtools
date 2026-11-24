"""Montagem do modelo OrcaFlex de um caso (substitui ``_01.GenerateModel.py``).

A lógica é a mesma do script antigo, com estas diferenças:

1. Os objetos são encontrados pelos papéis da aba ``Papéis`` (não por nomes fixos).
2. Dois modos (``catenary=``):

   - ``"fixed"`` (padrão), geometria correta:

     * declinação do End A = 180° − ângulo do MCV (o gooseneck de 60° sai a 30°
       abaixo da horizontal; é o que o modelo base já usa);
     * catenária inextensível com toque horizontal no solo, calculada só pela
       geometria: altura do End A acima do solo h = A − OD/2 e inclinação
       θ = declinação − 90°:  a = h/(secθ − 1),  s = a·tanθ,  x = a·asinh(tanθ).
       Não depende de peso nem de tração; o End B fica em
       ``EndBX = G + x + (L − s)`` (trecho apoiado reto no solo).

   - ``"legacy"`` reproduz o ``_01`` exatamente, inclusive os erros conhecidos
     (declinação = 90° + ângulo; empuxo com ``OD*2``; peso sem g; comprimento
     suspenso Δtan/(H·w); tração horizontal arbitrária de 0,5 kN). Serve só para a
     regressão contra o baseline.

   Propriedades afetadas pelo modo: ``{LINE}.EndADeclination`` e ``{LINE}.EndBX``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from .. import params as P
from .._orcaflex import require_orcfxapi
from ..schema import CaseInputs, MissingInputError
from ..log import log, step
from ..values import is_number

STEEL_E = 2.07e8        # kPa (kN/m²)
STEEL_POISSON = 0.3
SEAWATER_DENSITY = 1.025  # t/m³
GRAVITY = 9.80665         # m/s²


@dataclass
class BuildResult:
    model_path: Path
    catenary: dict[str, float]
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- catenária

def catenary_estimate(g: float, angle_deg: float, mass: float, od: float, ea: float,
                      horizontal_tension: float, mode: str = "fixed") -> dict[str, float]:
    """Comprimento suspenso ``l`` e X do toque no solo ``x_bottom`` (mesma fórmula do _01)."""
    t_top = -math.tan(math.radians(angle_deg))
    t_bottom = 0.0
    if mode == "legacy":
        sub_weight = mass - math.pi * od * 2 / 4 * SEAWATER_DENSITY      # reproduz o script antigo
    elif mode == "fixed":
        sub_weight = (mass - math.pi * od ** 2 / 4 * SEAWATER_DENSITY) * GRAVITY  # kN/m
    else:
        raise ValueError(f"Modo de catenária desconhecido: {mode!r} (use 'fixed' ou 'legacy')")
    if sub_weight == 0:
        raise ValueError("Peso submerso nulo: a linha é neutra e a catenária não se aplica.")
    if mode == "fixed" and sub_weight < 0:
        raise ValueError(f"Peso submerso negativo ({sub_weight:.4f} kN/m): linha flutua; revise massa e OD.")

    if mode == "legacy":
        l = abs((t_top - t_bottom) / horizontal_tension / sub_weight)   # erro original: divide por H
    else:
        l = abs(t_top - t_bottom) * horizontal_tension / sub_weight      # s = (H/w)·tanθ
    x_bottom = g + l * (
        1 / (t_bottom - t_top)
        * math.log((abs(t_top) + math.sqrt(1 + t_top ** 2)) / (abs(t_bottom) + math.sqrt(1 + t_bottom ** 2)))
        + horizontal_tension / ea
    )
    return {"sub_weight": sub_weight, "suspended_length": l, "x_bottom": x_bottom}


def catenary_geometric(g: float, height: float, theta_deg: float, line_length: float) -> dict[str, float]:
    """Catenária inextensível do toque no solo (horizontal) até o End A.

    ``height``: altura do End A acima da linha de centro apoiada no solo (m)
    ``theta_deg``: inclinação da linha no End A abaixo da horizontal (0 < θ < 90)
    """
    if not 0.0 < theta_deg < 90.0:
        raise ValueError(f"Inclinação da linha no End A = {theta_deg:g}°: precisa estar entre 0 e 90° abaixo da horizontal")
    if height <= 0:
        raise ValueError(f"Altura do End A acima do solo = {height:g} m: precisa ser positiva")
    theta = math.radians(theta_deg)
    a = height / (1.0 / math.cos(theta) - 1.0)
    t = math.tan(theta)
    s = a * t
    x = a * math.asinh(t)
    if s >= line_length:
        raise ValueError(f"Trecho suspenso estimado ({s:.1f} m) maior que a linha modelada ({line_length:.1f} m)")
    return {"catenary_parameter": a, "suspended_length": s, "x_span": x, "x_bottom": g + x,
            "height": height, "theta": theta_deg, "end_b_x": g + x + (line_length - s)}


def end_a_declination(angle_deg: float, mode: str) -> float:
    return 90.0 + angle_deg if mode == "legacy" else 180.0 - angle_deg


# --------------------------------------------------------------------------- partes do modelo

def text_param(case: CaseInputs, key: str, default: str) -> str:
    v = case.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else default


def configure_mcv(model, case: CaseInputs, mode: str = "fixed", notes: list[str] | None = None) -> None:
    notes = notes if notes is not None else []
    a, b, c, f, g = (case.num(k) for k in ("mcv.a", "mcv.b", "mcv.c", "mcv.f", "mcv.g"))
    base = case.num("mcv.base_size")

    hub = model[case.role("HUB")]
    hub.Length = a - f
    if mode != "legacy":
        hub.ShapeType = text_param(case, "model.hub_shape_type", "Drawing")

    mcv = model[case.role("MCV")]
    mcv.Mass = case.num("mcv.mass_air")
    mcv.Volume = case.num("mcv.volume")
    h = base / 2
    mcv.VertexX = [h, h, -h, -h, h, h, -h, -h, 0.0, 0.0, g - c, g]
    mcv.VertexY = [h, -h, -h, h, h, -h, -h, h, 0.0, 0.0, 0.0, 0.0]
    mcv.VertexZ = [0.0, 0.0, 0.0, 0.0, base, base, base, base, base, f + b, f + b, f]
    mcv.InitialZ = a - f
    if mode != "legacy":   # o modo legacy reproduz o _01, que herdava CoG/inércia do modelo base
        from .mcvprops import apply_mcv_properties, mcv_dims, mcv_properties

        apply_mcv_properties(mcv, mcv_properties(mcv_dims(case, "mcv")), "MCV", notes)

    crane = model[case.role("CRANE_WIRE")]
    crane.EndBX = g - c
    crane.EndBY = 0.0
    crane.EndBZ = f + b
    crane.StaticsStep1 = "Catenary"

    if _has(model, case.roles.get("WINCH_WIRE", "")):
        model[case.role("WINCH_WIRE")].StaticsStep1 = "Catenary"
    elif case.operation.camelback:
        raise MissingInputError(f"Papel WINCH_WIRE -> '{case.roles.get('WINCH_WIRE')}' não existe no modelo base")

    line = model[case.role("LINE")]
    line.EndAConnection = case.role("MCV")
    line.EndAX = g
    line.EndAY = 0.0
    line.EndAZ = f
    line.EndAAzimuth = 0.0
    line.EndADeclination = end_a_declination(case.num("mcv.angle"), mode)
    line.EndAGamma = 0.0


# --------------------------------------------------------------------------- curvas externas

def write_curve(model, case: CaseInputs, curve, orc, notes: list[str]) -> str:
    """Grava uma curva da biblioteca como variable data do modelo e devolve o nome.

    Se já existe um objeto com o nome da curva, só a tabela é substituída. Senão o
    objeto é criado com o MESMO tipo da curva-modelo indicada nos papéis
    (CURVE_TEMPLATE_BENDING / CURVE_TEMPLATE_AXIAL), sem depender de nomes da API.
    """
    from ..curves import KINDS

    name = curve.name
    try:
        obj = model[name]
        created = False
    except Exception:
        template_role = KINDS[curve.kind]["template_role"]
        template_name = case.roles.get(template_role, "")
        if not template_name:
            # Sem curva-molde: tenta os nomes de ObjectType mais prováveis da API (não confirmados na documentação)
            for candidate in KINDS[curve.kind].get("api_types", []):
                otype = getattr(getattr(orc, "ObjectType", None), candidate, None)
                if otype is not None:
                    model.CreateObject(otype, name)
                    obj = model[name]
                    break
            else:
                obj = None
            if obj is not None:
                x, y = curve.si()
                obj.IndependentValue = x
                obj.DependentValue = y
                notes.append(f"Curva {curve.id} criada como '{name}' ({len(x)} pontos) via ObjectType.{candidate}")
                return name
            raise MissingInputError(
                f"Para criar a curva '{name}' ({KINDS[curve.kind]['label']}) o modelo base precisa ter uma curva do "
                f"mesmo tipo para servir de modelo: crie uma (Variable data) e informe o nome no papel {template_role}, "
                f"ou crie no modelo base uma curva chamada '{name}'.")
        try:
            template = model[template_name]
        except Exception:
            raise MissingInputError(f"Papel {template_role} aponta para '{template_name}', que não existe no modelo base")
        model.CreateObject(template.type, name)
        obj = model[name]
        created = True
    x, y = curve.si()
    obj.IndependentValue = x
    obj.DependentValue = y
    if KINDS[curve.kind]["hysteretic"]:
        obj.Hysteretic = "Yes" if curve.hysteretic else "No"
    notes.append(f"Curva {curve.id} ({curve.load_case or 'sem load case'}) {'criada' if created else 'atualizada'} "
                 f"como '{name}' ({len(x)} pontos)")
    return name


def set_stiffness(model, case: CaseInputs, lt, attr: str, key: str, orc, notes: list[str]) -> None:
    """EA/EIx de um tipo de linha: número, nome de curva já existente ou curva da biblioteca."""
    value = case.num_or_name(key)
    if hasattr(value, "si"):
        value = write_curve(model, case, value, orc, notes)
    setattr(lt, attr, value)


def configure_line_type(model, case: CaseInputs, role: str, prefix: str, orc=None, notes=None) -> None:
    notes = notes if notes is not None else []
    lt = model[case.role(role)]
    lt.OD = case.num(f"{prefix}.od")
    lt.ID = case.num(f"{prefix}.id")
    lt.MassPerUnitLength = case.num(f"{prefix}.mass")
    lt.xMinRadius = case.num("br.locking_radius" if prefix == "br" else f"{prefix}.mbr")
    set_stiffness(model, case, lt, "EA", f"{prefix}.ea", orc, notes)
    set_stiffness(model, case, lt, "EIx", f"{prefix}.eix", orc, notes)
    lt.SeabedLateralFrictionCoefficient = case.num("environment.friction_lateral")
    lt.SeabedAxialFrictionCoefficient = case.num("environment.friction_axial")


def configure_bend_restrictor(model, case: CaseInputs) -> None:
    """Curva de rigidez do BR: se ``br.eix`` é uma curva da biblioteca (momento resistente da vértebra),
    ela já foi gravada por ``configure_line_type``; senão a curva é calculada como no _01
    (travamento em 1/MBR e aço depois disso)."""
    model[case.role("BR")].Length = case.num("br.length")
    if hasattr(case.get("br.eix"), "si"):
        return
    od, id_, mbr = case.num("br.od"), case.num("br.id"), case.num("br.locking_radius")
    inertia = math.pi / 4 * ((od / 2) ** 4 - (id_ / 2) ** 4)
    curve = model[case.role("BR_STIFFNESS")]
    curve.IndependentValue = [0.0, 1 / mbr, 1 + 1 / mbr]
    curve.DependentValue = [0.0, 0.01, 0.01 + STEEL_E * inertia]


def configure_end_fitting(model, case: CaseInputs) -> None:
    od, id_ = case.num("ef.od"), case.num("ef.id")
    inertia = math.pi * (od ** 4 - id_ ** 4) / 64
    area = math.pi * (od ** 2 - id_ ** 2) / 4
    polar = math.pi * (od ** 4 - id_ ** 4) / 32
    shear_mod = STEEL_E / (2 * (1 + STEEL_POISSON))
    ef = model[case.role("EF_TYPE")]
    ef.EIx = STEEL_E * inertia
    ef.EA = STEEL_E * area
    ef.GJ = shear_mod * polar


def configure_camelback(model, case: CaseInputs) -> None:
    for role, kp in (("CB_LINK_1", "camelback.leg1_kp"), ("CB_LINK_2", "camelback.leg2_kp")):
        link = model[case.role(role)]
        link.EndAZ = case.num(kp)
        link.UnstretchedLength = case.num("camelback.sling_length")
        link.Stiffness = case.num("camelback.stiffness")


def configure_floaters(model, case: CaseInputs, orc) -> int:
    buoy = case.lst("floaters.buoyancy")
    kps = case.lst("floaters.kps")
    slings = case.lst("floaters.sling_length")
    stiff = case.lst("floaters.sling_stiffness")
    if not (len(buoy) == len(kps) == len(slings) == len(stiff)):
        raise MissingInputError(f"Listas de flutuadores com tamanhos diferentes: {[len(buoy), len(kps), len(slings), len(stiff)]}")
    lda = case.num("environment.lda")
    line_name = case.role("LINE")
    for i in range(len(buoy)):
        link_name, floater_name = f"Floater Link {i + 1}", f"Floater {i + 1}"
        model.CreateObject(orc.ObjectType.Link, link_name)
        model.CreateObject(orc.ObjectType.Buoy3D, floater_name)

        link = model[link_name]
        link.UnstretchedLength = slings[i]
        link.Stiffness = stiff[i]
        link.EndAConnection = line_name
        link.EndAX, link.EndAY, link.EndAZ = 0.0, 0.0, kps[i]
        link.EndBConnection = floater_name
        link.EndBX, link.EndBY, link.EndBZ = 0.0, 0.0, 0.0

        floater = model[floater_name]
        floater.Mass = 1e-6
        floater.Volume = buoy[i] / SEAWATER_DENSITY
        floater.Height = 2.0
        floater.PenWidth = 8
        floater.InitialX = kps[i]
        floater.InitialY = 0.0
        floater.InitialZ = -lda + slings[i]
    return len(buoy)


def configure_winch_wire(model, case: CaseInputs, notes: list[str], orc=None) -> None:
    """Premissa ``model.winch_extension``: cabo do guincho com uma ou duas seções.

    O comprimento total do cabo é preservado (só muda a divisão entre as seções),
    para não alterar a geometria do modelo base.
    """
    ext = case.flag("model.winch_extension")
    wire = model[case.role("WINCH_WIRE")]
    lengths = [float(x) for x in wire.Length]
    n = len(lengths)
    if ext is None:
        notes.append(f"Premissa model.winch_extension não definida: cabo do guincho mantido como no modelo base "
                     f"({n} seção(ões)), extensão NÃO configurada")
        return
    total = sum(lengths)
    if ext:
        ext_type = case.role("WINCH_EXT_TYPE")
        ext_len = case.num("winch_ext.length")
        if n == 1:
            wire.NumberOfSections = 2
            wire.LineType[1] = ext_type
            wire.NumberOfSegments[1] = int(round(case.num("winch_ext.segments")))
            notes.append("Seção de extensão criada no cabo do guincho")
        elif n == 2:
            if wire.LineType[1] != ext_type:
                raise MissingInputError(
                    f"A 2ª seção do cabo do guincho usa '{wire.LineType[1]}', mas o papel WINCH_EXT_TYPE é '{ext_type}'")
        else:
            raise MissingInputError(f"Cabo do guincho com {n} seções no modelo base; esperado 1 ou 2")
        if ext_len >= total:
            raise MissingInputError(f"Extensão ({ext_len:g} m) maior que o cabo inteiro ({total:g} m)")
        wire.Length[0] = total - ext_len
        wire.Length[1] = ext_len

        lt = model[ext_type]
        lt.OD = case.num("winch_ext.od")
        lt.ID = case.num("winch_ext.id")
        lt.MassPerUnitLength = case.num("winch_ext.mass")
        set_stiffness(model, case, lt, "EA", "winch_ext.ea", orc, notes)
        if is_number(case.get("winch_ext.mbr")):
            lt.xMinRadius = case.num("winch_ext.mbr")
        eix = case.get("winch_ext.eix")
        if is_number(eix) or (isinstance(eix, str) and eix) or hasattr(eix, "si"):
            set_stiffness(model, case, lt, "EIx", "winch_ext.eix", orc, notes)
        lt.SeabedLateralFrictionCoefficient = case.num("environment.friction_lateral")
        lt.SeabedAxialFrictionCoefficient = case.num("environment.friction_axial")
    else:
        if n == 2:
            wire.Length[0] = total
            wire.NumberOfSections = 1
            notes.append("Seção de extensão removida do cabo do guincho (comprimento total mantido)")
        elif n != 1:
            raise MissingInputError(f"Cabo do guincho com {n} seções no modelo base; esperado 1 ou 2")


def configure_line_statics_and_contents(line, case: CaseInputs) -> None:
    """Etapa 1 da estática (``model.line_statics_step1``) e conteúdo da linha flexível (``line.contents_density``)."""
    line.StaticsStep1 = text_param(case, "model.line_statics_step1", "Quick")
    line.ContentsDensity = case.num("line.contents_density")


def configure_general(model, case: CaseInputs) -> None:
    """Jacobian perturbation factor e passo de tempo da dinâmica (o mesmo dos .dat de mar)."""
    from ..dynamics.seastates import _time_step

    model.general.JacobianPerturbationFactor = case.num("model.jacobian_perturbation_factor")
    _time_step(model, case)


def configure_line(model, case: CaseInputs, mode: str = "fixed") -> None:
    ef_len, br_len = case.num("ef.length"), case.num("br.length")
    tail = case.lst("model.line_tail_lengths")
    segments = [int(round(x)) for x in case.lst("model.line_segments")]
    if len(segments) != 3 + len(tail):
        raise MissingInputError(
            f"model.line_segments precisa de {3 + len(tail)} valores (EF, BR, complemento + {len(tail)} seções finais)")

    complementary = math.ceil(ef_len + br_len) - (ef_len + br_len)
    if complementary < 0.5:
        complementary += 0.5

    line = model[case.role("LINE")]
    line.TopEnd = "End A"
    line.Length = [ef_len, br_len, complementary, *tail]
    line.NumberOfSegments = segments
    line.Attachmentz[0] = ef_len
    if mode == "legacy":
        line.StaticsStep1 = "Catenary"
    else:
        configure_line_statics_and_contents(line, case)


# --------------------------------------------------------------------------- montagem

def _has(model, name: str) -> bool:
    if not name:
        return False
    try:
        model[name]
        return True
    except Exception:
        return False


def required_keys(case: CaseInputs) -> list[str]:
    return P.required_keys(case.requirement_groups())


def build_model(case: CaseInputs, base_model: Path, out_path: Path, catenary: str = "fixed") -> BuildResult:
    """Abre o modelo base, aplica o caso e salva em ``out_path`` (.dat ou .yml)."""
    orc = require_orcfxapi()
    if not case.operation.camelback and not case.operation.supported:
        raise NotImplementedError(
            f"'{case.operation.operation_type}' não está marcada como suportada na aba Operações. Operações sem "
            "corcova (fase 5) precisam de um modelo base próprio: informe 'Modelo base' e 'Suportado (S/N) = S'.")
    two_ends = case.modelled_ends() == 2
    if two_ends and catenary != "fixed":
        raise ValueError("Duas extremidades só no modo 'fixed' (o 'legacy' reproduz o _01, que tem 1 extremidade)")
    missing = case.missing(required_keys(case))
    if missing:
        lines = "\n".join(f"  - {k}: {v}  ({P.get(k).description})" for k, v in missing.items())
        raise MissingInputError(f"Caso {case.case_id}: faltam inputs obrigatórios:\n{lines}")

    base_model, out_path = Path(base_model), Path(out_path)
    with step(f"Abrindo modelo base {base_model.name}"):
        model = orc.Model(str(base_model))
    notes: list[str] = []
    log.info("Configurando o modelo (modo %s)", catenary)

    lda_change = case.num("environment.lda") - model.environment.WaterDepth
    env = model.environment
    env.WaterDepth = case.num("environment.lda")
    env.SeabedNormalStiffness = case.num("environment.seabed_normal_stiffness")
    env.SeabedShearStiffness = case.num("environment.seabed_shear_stiffness")

    if catenary not in ("fixed", "legacy"):
        raise ValueError(f"Modo desconhecido: {catenary!r} (use 'fixed' ou 'legacy')")
    with step("  MCV, hub e conexões", logging.DEBUG):
        configure_mcv(model, case, catenary, notes)
    if catenary != "legacy":
        configure_general(model, case)
    for role, prefix in (("LINE_TYPE", "line"), ("CRANE_WIRE_TYPE", "crane_wire"), ("WINCH_WIRE_TYPE", "winch_wire"),
                         ("BR_TYPE", "br"), ("EF_TYPE", "ef")):
        with step(f"  Tipo de linha {case.role(role)}", logging.DEBUG):
            configure_line_type(model, case, role, prefix, orc, notes)
    with step("  Restritor, end fitting, corcova", logging.DEBUG):
        configure_bend_restrictor(model, case)
        configure_end_fitting(model, case)
        if case.operation.camelback:
            configure_camelback(model, case)
    n_floaters = configure_floaters(model, case, orc)
    if n_floaters:
        notes.append(f"{n_floaters} flutuador(es) criado(s)")
    if two_ends:
        from .twoends import configure_line_two_ends

        sections = configure_line_two_ends(model, case)
        notes.append("Linha com as duas extremidades: " + " + ".join(f"{length:.2f} m/{n}" for _, length, n in sections)
                     + f" = {sum(length for _, length, _ in sections):.2f} m (comprimento/segmentos por seção)")
    else:
        configure_line(model, case, catenary)
    has_winch = _has(model, case.roles.get("WINCH_WIRE", ""))
    if has_winch:
        configure_winch_wire(model, case, notes, orc)
    elif case.operation.camelback:
        raise MissingInputError(f"Papel WINCH_WIRE -> '{case.roles.get('WINCH_WIRE')}' não existe no modelo base")

    # profundidade diferente do modelo base: objetos pendurados acompanham o fundo
    if _has(model, case.roles.get("MASTERLINK", "")):
        model[case.role("MASTERLINK")].InitialZ = model[case.role("MASTERLINK")].InitialZ - lda_change
    model[case.role("CRANE_WIRE")].Length[0] = model[case.role("CRANE_WIRE")].Length[0] + lda_change
    if has_winch:
        model[case.role("WINCH_WIRE")].Length[0] = model[case.role("WINCH_WIRE")].Length[0] + lda_change

    line = model[case.role("LINE")]
    if two_ends:
        from .twoends import configure_end_b, estimate_distance, place_end_b

        configure_end_b(model, case, notes)
        est = estimate_distance(case)
        given = case.get("model.end_distance")
        distance = float(given) if is_number(given) else est["distance"]
        place_end_b(model, case, distance)
        cat = {"mode": catenary, "two_ends": True, "line_length": case.num("line.total_length"),
               "end_distance": distance, "end_distance_source": "model.end_distance" if is_number(given) else "estimativa",
               "end_distance_estimate": est["distance"], "suspended_a": est["suspended_a"],
               "suspended_b": est["suspended_b"], "seabed_length": est["seabed_length"], "lda_change": lda_change,
               "end_a_declination": line.EndADeclination}
        if est["note"]:
            notes.append(est["note"])
        log.info("  Duas extremidades: distância entre os hubs %.3f m (%s); trechos suspensos estimados %.2f / %.2f m",
                 distance, cat["end_distance_source"], est["suspended_a"], est["suspended_b"])
        return _finish(model, case, out_path, cat, notes)
    total_length = line.CumulativeLength[-1]
    if catenary == "legacy":
        cat = catenary_estimate(
            g=case.num("mcv.g"), angle_deg=case.num("mcv.angle"), mass=case.num("line.mass"),
            od=case.num("line.od"), ea=case.num("line.ea"),
            horizontal_tension=case.num("model.catenary_horizontal_tension"), mode="legacy")
        line.EndBX = cat["x_bottom"] + (total_length - cat["suspended_length"])
    else:
        cat = catenary_geometric(
            g=case.num("mcv.g"), height=case.num("mcv.a") - case.num("line.od") / 2,
            theta_deg=end_a_declination(case.num("mcv.angle"), "fixed") - 90.0, line_length=total_length)
        line.EndBX = cat["end_b_x"]
    cat.update({"line_length": total_length, "end_b_x": line.EndBX, "mode": catenary, "lda_change": lda_change,
                "end_a_declination": line.EndADeclination})

    log.info("  Declinação End A %.1f°, trecho suspenso %.3f m, End B X %.3f m", line.EndADeclination,
             cat["suspended_length"], line.EndBX)
    return _finish(model, case, out_path, cat, notes)


def _finish(model, case: CaseInputs, out_path: Path, cat: dict, notes: list[str]) -> BuildResult:
    for n in notes:
        log.info("  %s", n)
    from .view import set_default_view

    view = set_default_view(model, case)
    if "error" not in view:
        log.info("  Vista padrão: centro (%.1f, %.1f, %.1f) m, tamanho %.1f m (MCV e hub)", *view["centre"],
                 view["size"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with step(f"Salvando {out_path.name}"):
        model.SaveData(str(out_path))
    return BuildResult(out_path, cat, notes)
