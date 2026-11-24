"""Duas extremidades modeladas: a linha com o comprimento real e um MCV fixo no hub de cada ponta.

Caso típico: 2ª extremidade (CVD/DCVD) de um jumper curto, em que a 1ª ponta já está
conectada no hub da outra estrutura. O que muda em relação ao modelo de 1 extremidade:

* **Linha** com ``line.total_length``: EF (1 segmento) + trecho do BR (20) + complemento na
  ponta A, trecho do meio (fino perto das pontas, mais grosso no centro) e trecho do BR (20)
  + EF (1) na ponta B. Os números de segmentos do EF e do BR são os dois primeiros de
  ``model.line_segments``. Nos trechos dos BRs a seção é do **tipo da linha flexível**; o BR é
  o anexo *stiffener* de cada ponta, como no modelo base (desde a 0.10.0; antes a seção era do
  tipo do BR e o BR da ponta A ficava duplicado).
* **BR da ponta B**: tipo de stiffener ``Bend Restrictor B`` (cópia do da ponta A, com
  ``br_b.length``, conexão no arc length 0 a partir do End B do stiffener) anexado a
  ``ef_b.length`` do End B da linha, com o nome ``Bend Restrictor Element B``.
* **MCV B**: o objeto do papel MCV_B; se o modelo base não tem, é criado como cópia do
  MCV, mas massa, volume, altura, CoG, CoV e inércia vêm das cotas ``mcv_b.*`` (ver
  ``mcvprops``), nunca do MCV A. Fica no hub B, com a geometria espelhada (gooseneck
  voltado para o MCV A; CoG e CoV com x = −(G − E)).
* **Hub B**: o objeto do papel HUB_B; se não existe, cópia do hub, deslocada para o hub B.
* **End B da linha** conectado ao gooseneck do MCV B: ponto (−G, 0, F) no MCV B,
  declinação = ângulo do gooseneck (a linha chega subindo, na convenção End A → End B do
  OrcaFlex) e a mesma rigidez de conexão do End A.
* **Distância entre os hubs**: ``model.end_distance`` se informada; senão, estimada pela
  catenária geométrica de cada ponta (trecho apoiado reto no solo) e ajustada pela
  estática até a **menor tração efetiva do trecho entre os BRs** ser
  ``model.end_b_tension_target`` (0,5 kN), o mesmo critério do End B no modelo de 1
  extremidade.
* **MCV B livre em X** (``model.end_b_x_free``, padrão ``montagem``): o MCV B fica num
  *constraint* com só o X livre e é puxado para longe do MCV A por um tirante macio
  (``MCV B Pull``) com tração = ``model.end_b_tension_target``. Uma estática só já dá a
  distância de equilíbrio (tração horizontal da linha = alvo), sem a busca por várias
  estáticas, e a linha não fica comprimida enquanto o solver procura o equilíbrio.
  ``montagem``: livre só na estática do ajuste da distância (sem a corcova); no modelo real o
  X fica fixo nessa posição e o tirante sai, e a corcova entra com o MCV B fixo (como o End B
  do modelo de 1 extremidade). ``sempre``: livre também na estática final, nas posições e na
  dinâmica.
  ``nao``: MCV B ancorado e distância por busca (comportamento até a 0.9.1).

O MCV B e o hub B não entram na translação em X das posições estáticas (ver
``view.root_points``): o vão entre os hubs é um dado do layout.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from ..log import log
from ..schema import CaseInputs, MissingInputError
from ..values import is_number

END_STIFFNESS_SUFFIXES = ("xBendingStiffness", "yBendingStiffness", "TwistingStiffness")


def _num_or(case: CaseInputs, key: str, fallback_key: str) -> float:
    v = case.get(key)
    return float(v) if is_number(v) else case.num(fallback_key)


def end_b_dims(case: CaseInputs) -> dict[str, float]:
    keys = ("angle", "a", "b", "c", "f", "g", "mass_air", "volume", "base_size")
    return {k: case.num(f"mcv_b.{k}") for k in keys}


def complementary_length(ef_len: float, br_len: float) -> float:
    c = math.ceil(ef_len + br_len) - (ef_len + br_len)
    return c + 0.5 if c < 0.5 else c


def line_sections(case: CaseInputs) -> list[tuple[str, float, int]]:
    """[(papel do tipo de linha, comprimento, nº de segmentos)] da linha com as duas pontas."""
    total = case.num("line.total_length")
    ef_a, br_a = case.num("ef.length"), case.num("br.length")
    ef_b, br_b = _num_or(case, "ef_b.length", "ef.length"), _num_or(case, "br_b.length", "br.length")
    segs = [int(round(x)) for x in case.lst("model.line_segments")]
    if len(segs) < 3:
        raise MissingInputError("model.line_segments precisa de pelo menos EF;BR;complemento")
    n_ef, n_br, n_compl = segs[0], segs[1], segs[2]
    compl = complementary_length(ef_a, br_a)
    middle = total - (ef_a + br_a + compl) - (br_b + ef_b)
    if middle <= 1.0:
        raise MissingInputError(
            f"Linha de {total:g} m curta demais para EF+BR nas duas pontas ({ef_a + br_a + compl + br_b + ef_b:.2f} m)")
    fine_len = case.num("model.two_ends_fine_length")
    fine_seg = case.num("model.two_ends_fine_segment")
    coarse_seg = case.num("model.two_ends_coarse_segment")
    # trechos dos BRs com o tipo da linha flexível: o BR é o anexo stiffener (como no modelo base)
    out = [("EF_TYPE", ef_a, n_ef), ("LINE_TYPE", br_a, n_br), ("LINE_TYPE", compl, n_compl)]
    if middle <= 2 * fine_len + coarse_seg:
        out.append(("LINE_TYPE", middle, max(1, math.ceil(middle / fine_seg))))
    else:
        centre = middle - 2 * fine_len
        out += [("LINE_TYPE", fine_len, max(1, math.ceil(fine_len / fine_seg))),
                ("LINE_TYPE", centre, max(1, math.ceil(centre / coarse_seg))),
                ("LINE_TYPE", fine_len, max(1, math.ceil(fine_len / fine_seg)))]
    out += [("LINE_TYPE", br_b, n_br), ("EF_TYPE", ef_b, n_ef)]
    return out


def free_span(case: CaseInputs) -> tuple[float, float]:
    """Trecho da linha entre os BRs (arc length a partir do End A), onde a tração mínima é medida."""
    total = case.num("line.total_length")
    s0 = case.num("ef.length") + case.num("br.length")
    s1 = total - _num_or(case, "ef_b.length", "ef.length") - _num_or(case, "br_b.length", "br.length")
    return s0, s1


def estimate_distance(case: CaseInputs) -> dict[str, float]:
    """Distância entre as origens dos MCVs: catenária geométrica em cada ponta + trecho reto no solo."""
    from .builder import catenary_geometric

    total = case.num("line.total_length")
    od = case.num("line.od")
    b = end_b_dims(case)
    ga, gb = case.num("mcv.g"), b["g"]
    ends = {}
    try:
        for tag, height, angle in (("a", case.num("mcv.a") - od / 2, case.num("mcv.angle")),
                                   ("b", b["a"] - od / 2, b["angle"])):
            ends[tag] = catenary_geometric(0.0, height, 90.0 - angle, total)
        seabed = total - ends["a"]["suspended_length"] - ends["b"]["suspended_length"]
    except ValueError:
        ends = {t: {"suspended_length": total / 2, "x_span": 0.0} for t in ("a", "b")}
        seabed = 0.0
    if seabed > 0:
        d = ga + ends["a"]["x_span"] + seabed + ends["b"]["x_span"] + gb
        note = ""
    else:   # linha não chega a tocar o solo com o ângulo do gooseneck: chute e a estática acerta
        d = ga + gb + 0.8 * total
        note = "linha curta: trecho suspenso estimado maior que a linha; distância inicial = 0,8 L"
    return {"distance": d, "suspended_a": ends["a"]["suspended_length"], "suspended_b": ends["b"]["suspended_length"],
            "seabed_length": max(seabed, 0.0), "note": note}


# --------------------------------------------------------------------------- objetos da ponta B

def _has(model, name: str) -> bool:
    if not name:
        return False
    try:
        model[name]
        return True
    except Exception:
        return False


def _ensure_clone(model, source_name: str, name: str, notes: list[str], what: str):
    if _has(model, name):
        return model[name]
    if not _has(model, source_name):
        raise MissingInputError(f"Para criar o {what} ('{name}') o modelo base precisa ter '{source_name}'")
    clone = model[source_name].CreateClone(name)
    notes.append(f"{what} '{name}' criado como cópia de '{source_name}'")
    return clone


def end_b_names(case: CaseInputs) -> tuple[str, str]:
    return case.roles.get("MCV_B") or "MCV B", case.roles.get("HUB_B") or "Hub Height B"


def end_b_support_names(case: CaseInputs) -> tuple[str, str]:
    """(constraint do MCV B, tirante que aplica a tração alvo)."""
    return case.roles.get("CONSTRAINT_B") or "Constraint B", case.roles.get("END_B_PULL") or "MCV B Pull"


X_FREE_MODES = {"montagem": "montagem", "estatica": "montagem", "sim": "montagem", "s": "montagem",
                "sempre": "sempre", "nao": "nao", "n": "nao", "no": "nao", "fixo": "nao", "ancorado": "nao"}


def x_free_mode(case: CaseInputs) -> str:
    """``montagem`` | ``sempre`` | ``nao`` (ver docstring do módulo).

    Com ``model.end_distance`` informada (ou ``model.tune_end_b`` = 0) a distância é dado: ``nao``.
    """
    if is_number(case.get("model.end_distance")) or case.flag("model.tune_end_b") is False:
        return "nao"
    try:
        v = case.get("model.end_b_x_free")
    except KeyError:
        v = None
    text = str(v).strip().lower().replace("ã", "a").replace("á", "a") if isinstance(v, str) else ""
    if is_number(v):
        text = "montagem" if float(v) else "nao"
    if not text:
        return "montagem"
    if text not in X_FREE_MODES:
        raise MissingInputError(f"model.end_b_x_free = {v!r}: use montagem, sempre ou nao")
    return X_FREE_MODES[text]


def _set_first(obj, names, value) -> str | None:
    """Grava ``value`` no primeiro nome de dado aceito (e conferido na leitura); devolve o nome."""
    for name in names:
        try:
            setattr(obj, name, value)
            if str(getattr(obj, name)).lower() == str(value).lower():
                return name
        except Exception:
            continue
    return None


def configure_x_free_support(model, case: CaseInputs, notes: list[str], orc=None) -> None:
    """Constraint (só X livre) segurando o MCV B e tirante que o puxa com a tração alvo (+X).

    O OrcFxAPI usado: ``CreateObject(ObjectType.Constraint/Link)``, ``InitialX/Y/Z``,
    ``InitialAzimuth/Declination/Gamma`` e ``DOFFree[0..5]`` (X, Y, Z, Rx, Ry, Rz) no constraint;
    ``EndA/EndBConnection``, ``EndA/EndBX/Y/Z``, ``UnstretchedLength`` e ``Stiffness`` no tirante.
    """
    if orc is None:
        from .._orcaflex import require_orcfxapi

        orc = require_orcfxapi()
    b = end_b_dims(case)
    mcv_b_name, _ = end_b_names(case)
    con_name, pull_name = end_b_support_names(case)
    if not _has(model, con_name):
        model.CreateObject(orc.ObjectType.Constraint, con_name)
        notes.append(f"Constraint '{con_name}' criado para o MCV B (só o X livre)")
    con = model[con_name]
    height = b["a"] - b["f"]              # origem do MCV B acima do fundo (como no MCV ancorado)
    conn = _set_first(con, ("InFrameConnection", "Connection"), "Anchored")
    con.InitialY = 0.0
    if conn:
        con.InitialZ = height
    else:   # sem conexão "Anchored" no constraint: fixo no espaço, fundo plano na lâmina d'água
        con.InitialZ = height - float(model.environment.WaterDepth)
        notes.append(f"AVISO: '{con_name}' ficou com conexão Fixed (Z global = fundo plano na LDA); confira")
    for item in ("InitialAzimuth", "InitialDeclination", "InitialGamma"):
        try:
            setattr(con, item, 0.0)
        except Exception:
            pass
    for j in range(6):
        con.DOFFree[j] = "Yes" if j == 0 else "No"

    mcv_b = model[mcv_b_name]
    mcv_b.Connection = con_name
    mcv_b.InitialX, mcv_b.InitialY, mcv_b.InitialZ = 0.0, 0.0, 0.0

    target = case.num("model.end_b_tension_target")
    pull = case.num("model.end_b_pull_length")
    if not _has(model, pull_name):
        model.CreateObject(orc.ObjectType.Link, pull_name)
    link = model[pull_name]
    # tirante macio: comprimento natural = metade da distância à âncora -> deformação 1 -> tração = Stiffness
    link.UnstretchedLength = pull / 2.0
    link.Stiffness = target
    link.EndAConnection = mcv_b_name
    link.EndAX, link.EndAY, link.EndAZ = 0.0, 0.0, 0.0
    link.EndBConnection = "Anchored"
    link.EndBY, link.EndBZ = 0.0, height
    notes.append(f"MCV B livre em X ({x_free_mode(case)}), puxado por '{pull_name}' com {target:g} kN "
                 f"(âncora {pull:g} m além do MCV B; a tração varia {200 / pull:.1f}% por metro de deslocamento)")


def fix_end_b(model, case: CaseInputs, x_global: float) -> None:
    """Fixa o MCV B em ``x_global`` (todos os graus de liberdade presos) e retira o tirante."""
    con_name, pull_name = end_b_support_names(case)
    if not _has(model, con_name):
        return
    con = model[con_name]
    con.InitialX = x_global
    con.DOFFree[0] = "No"
    if _has(model, pull_name):
        model.DestroyObject(model[pull_name])


def configure_line_two_ends(model, case: CaseInputs) -> list[tuple[str, float, int]]:
    sections = line_sections(case)
    line = model[case.role("LINE")]
    line.TopEnd = "End A"
    line.Length = [length for _, length, _ in sections]
    line.LineType = [case.role(role) for role, _, _ in sections]
    line.NumberOfSegments = [n for _, _, n in sections]
    _set_stiffener_a(model, case, line)
    from .builder import configure_line_statics_and_contents

    configure_line_statics_and_contents(line, case)
    return sections


def br_b_names(case: CaseInputs) -> tuple[str, str]:
    """(tipo de stiffener do BR da ponta B, nome do anexo)."""
    return case.roles.get("BR_B") or "Bend Restrictor B", case.roles.get("BR_ELEMENT_B") or "Bend Restrictor Element B"


def _attachment_rows(line) -> list[dict]:
    cols = ("AttachmentType", "Attachmentz", "AttachmentzRelativeTo", "AttachmentName")
    try:
        types = list(line.AttachmentType)
    except Exception:
        return []
    rows = [{"AttachmentType": t} for t in types]
    for col in cols[1:]:
        try:
            values = list(getattr(line, col))
        except Exception:
            values = []
        for i, row in enumerate(rows):
            row[col] = values[i] if i < len(values) else None
    return rows


def _write_attachment_rows(line, rows: list[dict]) -> None:
    """Regrava a tabela de anexos da linha. x e y não são escritos: para stiffeners o OrcaFlex os fixa em 0
    (não editáveis)."""
    defaults = {"AttachmentzRelativeTo": "End A", "AttachmentName": ""}
    line.AttachmentType = [r["AttachmentType"] for r in rows]   # acerta o nº de linhas da tabela
    for col in ("Attachmentz", "AttachmentzRelativeTo", "AttachmentName"):
        values = [r.get(col) if r.get(col) is not None else defaults.get(col, 0.0) for r in rows]
        try:
            setattr(line, col, values)
        except Exception as exc:
            if col == "AttachmentName":   # nome do anexo é só rótulo
                log.warning("  Nomes dos anexos da linha não gravados: %s", exc)
            else:
                raise


def _set_stiffener_a(model, case: CaseInputs, line) -> None:
    """Anexo do BR da ponta A a ``ef.length`` do End A (como o configure_line de 1 extremidade)."""
    br_a = case.role("BR")
    rows = _attachment_rows(line)
    idx = next((i for i, r in enumerate(rows) if r["AttachmentType"] == br_a), None)
    if idx is None:
        line.Attachmentz[0] = case.num("ef.length")
        return
    rows[idx].update(Attachmentz=case.num("ef.length"), AttachmentzRelativeTo="End A")
    _write_attachment_rows(line, rows)


def configure_end_b_stiffener(model, case: CaseInputs, notes: list[str]) -> None:
    """BR da ponta B: stiffener com o comprimento ``br_b.length`` anexado a ``ef_b.length`` do End B."""
    br_a = case.role("BR")
    type_b, element_b = br_b_names(case)
    if not _has(model, br_a):
        notes.append(f"AVISO: tipo de stiffener '{br_a}' (papel BR) não existe: BR da ponta B não modelado")
        return
    st = _ensure_clone(model, br_a, type_b, notes, "tipo de stiffener do BR B")
    st.Length = _num_or(case, "br_b.length", "br.length")
    for item, value in (("ConnectionArclength", 0.0), ("ConnectionArclengthRelativeTo", "End B")):
        try:
            setattr(st, item, value)
        except Exception as exc:
            notes.append(f"AVISO: {type_b}.{item} não gravado ({exc}); confira no OrcaFlex")
    line = model[case.role("LINE")]
    rows = [r for r in _attachment_rows(line) if r["AttachmentType"] != type_b]
    rows.append({"AttachmentType": type_b, "Attachmentz": _num_or(case, "ef_b.length", "ef.length"),
                 "AttachmentzRelativeTo": "End B", "AttachmentName": element_b})
    _write_attachment_rows(line, rows)
    notes.append(f"BR da ponta B: stiffener '{type_b}' ({st.Length:g} m) anexado a {rows[-1]['Attachmentz']:g} m do "
                 f"End B ('{element_b}'); trechos dos BRs com o tipo da linha flexível")


def configure_end_b(model, case: CaseInputs, notes: list[str]) -> None:
    """MCV B (espelhado, ancorado), hub B e End B da linha no gooseneck do MCV B. Posição em X: ``place_end_b``."""
    b = end_b_dims(case)
    mcv_b_name, hub_b_name = end_b_names(case)
    mcv_b = _ensure_clone(model, case.role("MCV"), mcv_b_name, notes, "MCV B")
    mode = x_free_mode(case)
    mcv_b.Connection = "Anchored"
    mcv_b.Mass = b["mass_air"]
    mcv_b.Volume = b["volume"]
    from .mcvprops import apply_mcv_properties, mcv_dims, mcv_properties

    # propriedades do MCV da outra extremidade pelas cotas dele (nunca as do MCV A copiado)
    apply_mcv_properties(mcv_b, mcv_properties(mcv_dims(case, "mcv_b"), mirror=True), "MCV B", notes)
    h = b["base_size"] / 2
    a, bb, c, f, g, base = b["a"], b["b"], b["c"], b["f"], b["g"], b["base_size"]
    # mesma geometria do configure_mcv, espelhada em x: gooseneck voltado para o MCV A
    mcv_b.VertexX = [-v for v in (h, h, -h, -h, h, h, -h, -h, 0.0, 0.0, g - c, g)]
    mcv_b.VertexY = [h, -h, -h, h, h, -h, -h, h, 0.0, 0.0, 0.0, 0.0]
    mcv_b.VertexZ = [0.0, 0.0, 0.0, 0.0, base, base, base, base, base, f + bb, f + bb, f]
    mcv_b.InitialY = 0.0
    mcv_b.InitialZ = a - f

    if _has(model, case.roles.get("HUB", "")):
        hub_b = _ensure_clone(model, case.role("HUB"), hub_b_name, notes, "hub B")
        hub_b.Length = a - f
        from .builder import text_param

        hub_b.ShapeType = text_param(case, "model.hub_shape_type", "Drawing")
    else:
        notes.append("Sem objeto do hub no modelo base: hub B não desenhado")

    line = model[case.role("LINE")]
    line.EndBConnection = mcv_b_name
    line.EndBX = -g
    line.EndBY = 0.0
    line.EndBZ = f
    line.EndBAzimuth = 0.0
    line.EndBDeclination = b["angle"]      # chega subindo: 90° − (90° − ângulo)
    line.EndBGamma = 0.0
    copied, missing = [], []
    for suffix in END_STIFFNESS_SUFFIXES:
        try:
            setattr(line, f"EndB{suffix}", getattr(line, f"EndA{suffix}"))
            copied.append(suffix)
        except Exception:
            missing.append(suffix)
    if missing:
        notes.append(f"AVISO: rigidez de conexão do End B não copiada do End A para {', '.join(missing)} "
                     f"(dado não encontrado); confira no OrcaFlex")
    configure_end_b_stiffener(model, case, notes)
    if mode != "nao":
        configure_x_free_support(model, case, notes)


def _supported(model, case: CaseInputs) -> bool:
    """MCV B conectado ao constraint (modos montagem/sempre)?"""
    mcv_b_name, _ = end_b_names(case)
    con_name, _ = end_b_support_names(case)
    try:
        return _has(model, con_name) and str(model[mcv_b_name].Connection) == con_name
    except Exception:
        return False


def current_distance(model, case: CaseInputs) -> float:
    from .view import _get

    mcv_b_name, _ = end_b_names(case)
    con_name, _ = end_b_support_names(case)
    holder = model[con_name] if _supported(model, case) else model[mcv_b_name]
    return _get(holder, "InitialX", 0.0) - _get(model[case.role("MCV")], "InitialX", 0.0)


def place_end_b(model, case: CaseInputs, distance: float) -> None:
    """MCV B e hub B a ``distance`` (m, +X) do MCV A."""
    from .view import _get, hub_x

    mcv_b_name, hub_b_name = end_b_names(case)
    con_name, pull_name = end_b_support_names(case)
    x_a = _get(model[case.role("MCV")], "InitialX", 0.0)
    if _supported(model, case):
        model[con_name].InitialX = x_a + distance
        if _has(model, pull_name):
            model[pull_name].EndBX = x_a + distance + case.num("model.end_b_pull_length")
    else:
        model[mcv_b_name].InitialX = x_a + distance
    if _has(model, hub_b_name):
        try:
            model[hub_b_name].OriginX = hub_x(model, case) + distance
        except Exception as exc:   # o hub B é só desenho: não impede a montagem
            log.warning("  Hub B não reposicionado (%s); confira no OrcaFlex", str(exc).strip().splitlines()[-1]
                        if str(exc).strip() else type(exc).__name__)


# --------------------------------------------------------------------------- ajuste pela estática

def min_span_tension(line, orc, s0: float, s1: float, arcs=None) -> float:
    """Menor tração efetiva estática entre os arc lengths s0 e s1 (nós da linha)."""
    if arcs is None:
        try:
            arcs = [float(s) for s in line.NodeArclengths if s0 - 1e-6 <= float(s) <= s1 + 1e-6]
        except Exception:
            arcs = []
    if not arcs:
        n = max(2, int(math.ceil(s1 - s0)))
        arcs = [s0 + (s1 - s0) * i / n for i in range(n + 1)]
    return min(float(line.StaticResult("Effective tension", orc.oeArcLength(s))) for s in arcs)


def min_span_tension_at(line, orc, s0: float, s1: float) -> tuple[float, float]:
    """(menor tração efetiva, arc length onde ocorre) entre s0 e s1."""
    try:
        arcs = [float(s) for s in line.NodeArclengths if s0 - 1e-6 <= float(s) <= s1 + 1e-6]
    except Exception:
        arcs = []
    if not arcs:
        n = max(2, int(math.ceil(s1 - s0)))
        arcs = [s0 + (s1 - s0) * i / n for i in range(n + 1)]
    return min((float(line.StaticResult("Effective tension", orc.oeArcLength(s))), s) for s in arcs)


def _zero_seabed_friction(work, case: CaseInputs) -> None:
    """Atrito com o fundo zero na cópia do ajuste (0.10.3).

    Na estática o atrito puxa cada nó para a posição inicial: com o MCV B livre em X, a linha
    apoiada fica comprimida (0.10.2: −43 kN entre os BRs com tirante de 0,5 kN) e a tração alvo
    perde o sentido. Sem atrito, a tração no trecho apoiado é a do tirante. O modelo real mantém o atrito.
    """
    names = []
    for role in ("LINE_TYPE", "EF_TYPE"):
        try:
            name = case.role(role)
            lt = work[name]
        except Exception:
            continue
        if name in names:
            continue
        for attr in ("SeabedLateralFrictionCoefficient", "SeabedAxialFrictionCoefficient"):
            try:
                setattr(lt, attr, 0.0)
            except Exception as exc:
                log.debug("  %s.%s não alterado: %s", name, attr, exc)
        names.append(name)
    if names:
        log.info("  Ajuste dos hubs sem atrito com o fundo (%s); o modelo final mantém o atrito", ", ".join(names))


def tune_end_distance(case: CaseInputs, model_path: Path) -> dict:
    """Ajusta a distância entre os hubs até a menor tração do trecho livre atingir o alvo.

    Roda numa cópia sem o sistema da corcova (como o ajuste do End B de 1 extremidade); no
    modelo real só a posição do MCV B e do hub B muda.
    """
    from .._orcaflex import calculate_statics_robust, require_orcfxapi, save_work_copy
    from ..statics.solver import NotBracketed, Search, TooManyEvaluations
    from .endb import _strip_camelback
    from .view import _get

    orc = require_orcfxapi()
    model_path = Path(model_path)
    work = orc.Model(str(model_path))
    removed = _strip_camelback(work, case) if case.operation.camelback else []
    if removed:
        log.info("  Modelo de ajuste sem: %s", ", ".join(removed))
    max_it = int(case.num("model.end_b_statics_max_iterations"))
    try:
        if work.general.StaticsMaxIterations > max_it:
            work.general.StaticsMaxIterations = max_it
    except Exception:
        pass
    mcv_b_name, _ = end_b_names(case)
    line = work[case.role("LINE")]
    mcv_b = work[mcv_b_name]
    x_a = _get(work[case.role("MCV")], "InitialX", 0.0)
    d0 = current_distance(work, case)
    target = case.num("model.end_b_tension_target")
    s0, s1 = free_span(case)
    total = case.num("line.total_length")
    ga, gb = case.num("mcv.g"), case.num("mcv_b.g")
    if case.flag("model.end_b_tune_without_friction") is not False:
        _zero_seabed_friction(work, case)
    save_work_copy(work, model_path, "_ajuste_hubs")
    if _supported(work, case):
        return _tune_x_free(case, model_path, work, orc, x_a, d0, target, s0, s1, removed)

    def tension(d: float) -> float:
        place_end_b(work, case, d)
        t = time.perf_counter()
        try:
            calculate_statics_robust(work, case, f"distância {d:.3f} m")   # cópia descartada
        except Exception as exc:
            log.warning("  Distância %.3f m: estática não convergiu (%.1fs): %s", d, time.perf_counter() - t,
                        str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__)
            return float("nan")
        value = min_span_tension(line, orc, s0, s1)
        log.info("  Distância entre os hubs %.3f m -> menor tração entre os BRs %.3f kN (alvo %g) [%.1fs]", d, value,
                 target, time.perf_counter() - t)
        return value - target

    search = Search(tension, xtol=case.num("statics.pull_tolerance"), max_evals=int(case.num("statics.max_evaluations")))
    result = {"end_distance_start": d0, "end_tension_target": target, "removed_for_tuning": removed,
              "free_span": [s0, s1]}
    log.info("  Primeira estática do ajuste (distância %.3f m) ...", d0)
    try:
        f0 = search.eval(d0)
        direction = -1 if (math.isnan(f0) or f0 > 0) else +1   # esticada demais (ou sem convergir): aproxima
        d = search.solve(d0, 0.5, ga + gb + 1.0, ga + gb + total, direction)
        f = search.eval(d)
        result.update(end_distance=d, end_tension=f + target, evaluations=len(search.history), status="OK")
    except (NotBracketed, TooManyEvaluations) as exc:
        d = d0
        result.update(end_distance=d0, status="FALHOU", message=str(exc), evaluations=len(search.history))

    real = orc.Model(str(model_path))
    place_end_b(real, case, d)
    real.SaveData(str(model_path))
    return result


def _tune_x_free(case: CaseInputs, model_path: Path, work, orc, x_a: float, d0: float, target: float,
                 s0: float, s1: float, removed: list[str]) -> dict:
    """Uma estática com o MCV B livre em X: o tirante impõe a tração alvo e a distância sai do equilíbrio."""
    from .._orcaflex import calculate_statics_robust

    mcv_b_name, _ = end_b_names(case)
    result = {"end_distance_start": d0, "end_tension_target": target, "removed_for_tuning": removed,
              "free_span": [s0, s1], "x_free": True}
    log.info("  Estática com o MCV B livre em X (distância inicial %.3f m, tração alvo %g kN) ...", d0, target)
    t = time.perf_counter()
    try:
        calculate_statics_robust(work, case, "MCV B livre em X")   # cópia descartada: não precisa voltar
    except Exception as exc:
        msg = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
        log.warning("  Estática com o MCV B livre em X não convergiu (%.1fs): %s", time.perf_counter() - t, msg)
        result.update(end_distance=d0, status="FALHOU", message=msg, evaluations=1)
        _save_tuned(case, model_path, orc, x_a, d0)
        return result
    d = float(work[mcv_b_name].StaticResult("X")) - x_a
    value, arc = min_span_tension_at(work[case.role("LINE")], orc, s0, s1)
    log.info("  Equilíbrio: distância entre os hubs %.3f m, menor tração entre os BRs %.3f kN em s = %.2f m "
             "(alvo %g) [%.1fs]", d, value, arc, target, time.perf_counter() - t)
    if value < 0:
        log.warning("  Linha comprimida entre os BRs (%.3f kN): confira atrito, comprimento da linha e distância",
                    value)
    result.update(end_distance=d, end_tension=value, evaluations=1, status="OK")
    _save_tuned(case, model_path, orc, x_a, d)
    return result


def _save_tuned(case: CaseInputs, model_path: Path, orc, x_a: float, d: float) -> None:
    """Grava a distância no modelo real. Modo ``montagem``: MCV B fixo nessa posição e sem o tirante.

    A corcova entra depois com o MCV B fixo, como o End B do modelo de 1 extremidade: ela
    levanta a linha e aumenta a tração, o que acontece na operação com a 1ª ponta no hub.
    """
    real = orc.Model(str(model_path))
    place_end_b(real, case, d)
    if x_free_mode(case) == "montagem":
        fix_end_b(real, case, x_a + d)
        log.info("  MCV B fixado a %.3f m do MCV A; tirante da tração alvo retirado do modelo", d)
    real.SaveData(str(model_path))
