"""Planilha de configuração da pipeline (``cvd_config.xlsx``).

Abas:

- ``Defaults``   valores de projeto para parâmetros que não estão na Input Sheet
- ``Overrides``  correções por caso (ID + parâmetro), com prioridade máxima
- ``Operações``  tipo de operação -> template, corcova, modelo base, suportado
- ``Papéis``     papel lógico ({LINE}, {MCV}...) -> nome do objeto no modelo base
- ``PostProc``   o que extrair das simulações (por template)
- ``Critérios``  critérios de aceitação (por template); limite fixo ou vindo de um parâmetro
- ``Checklist``  linhas do checklist de verificação do modelo
- ``Mares``      grade de estados de mar (direção relativa, Tp, lista de Hs)

``write_config`` gera o arquivo (opcionalmente a partir de um ``__INPUTS.json``
antigo); ``load_config`` lê de volta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import params as P
from ..schema import ChecklistRow, CriterionSpec, OperationSpec, PostItem
from ..values import CurveRef, Missing, Value, parse_cell, parse_list

# Colunas acrescentadas depois da primeira versão: podem faltar em configurações antigas
OPTIONAL_COLUMNS = {"Operações": ["Modelo base (2 extremidades)", "Outra extremidade (sugestão)"]}

# Sugestão da outra extremidade: a operação do outro lado (premissa do 1349; editável na aba Operações)
DEFAULT_COUNTERPARTS = {
    "1st end DCVD": "2nd end DCVD", "2nd end DCVD": "1st end DCVD",
    "1st end CVD": "2nd end CVD", "2nd end CVD": "1st end CVD",
    "1st end Pull-in": "2nd end Pull-in", "2nd end Pull-in": "1st end Pull-in",
}

SHEETS = {
    "Defaults": ["Parâmetro", "Valor", "Unidade", "Descrição"],
    "Overrides": ["ID", "Parâmetro", "Valor", "Comentário"],
    "Operações": ["Operation Type", "Template", "Corcova (S/N)", "Modelo base", "Suportado (S/N)",
                  "Modelo base (2 extremidades)", "Outra extremidade (sugestão)"],
    "Papéis": ["Papel", "Objeto OrcaFlex", "Descrição"],
    "PostProc": ["Template", "Rótulo", "Método", "Objeto", "Variável", "Extra", "Período", "Unidade"],
    "Critérios": ["Template", "Critério", "Tipo", "Objeto", "Variável", "Extra", "Limite",
                  "Fonte do limite", "Transformação", "Estático (S/N)", "Dinâmico (S/N)", "Fonte sugerida"],
    "Checklist": ["Objeto", "Variável", "Unidade", "Input", "OrcaFlex", "Tol. abs", "Tol. rel", "Aplica a"],
    "Mares": ["Direção (°)", "Tp (s)", "Hs (m)"],
}

DEFAULT_ROLES: list[tuple[str, str, str]] = [
    ("VESSEL", "Island Performer", "Embarcação"),
    ("PAYOUT_RATE", "PayoutRate", "Variable data (tempo × velocidade) do pay-out do guincho na triagem semi-dinâmica"),
    ("ENVIRONMENT", "Environment", "Ambiente"),
    ("GENERAL", "General", "Dados gerais"),
    ("MCV", "MCV", "Corpo 6D do MCV"),
    ("HUB", "Hub Height", "Linha que representa a altura do hub"),
    ("LINE", "Line", "Linha flexível"),
    ("LINE_TYPE", "Line Type", "Tipo da linha flexível"),
    ("BR", "Bend Restrictor", "Objeto do restritor de curvatura"),
    ("BR_TYPE", "Bend Restrictor Type", "Tipo do restritor"),
    ("BR_STIFFNESS", "Bend Restrictor Stiffness Curve", "Curva de rigidez do restritor"),
    ("BR_ELEMENT", "Bend Restrictor Element", "Elemento usado nos resultados do BR"),
    ("EF_TYPE", "EndFitting Type", "Tipo do end fitting"),
    ("CRANE_WIRE", "Crane Wire", "Cabo do guindaste"),
    ("CRANE_WIRE_TYPE", "Crane Wire Type", "Tipo do cabo do guindaste"),
    ("WINCH_WIRE", "Winch Wire", "Cabo do guincho"),
    ("WINCH_WIRE_TYPE", "Winch Wire Type", "Tipo do cabo do guincho"),
    ("WINCH_EXT_TYPE", "Winch Extension Wire Type", "Tipo da extensão do cabo do guincho"),
    ("MASTERLINK", "Masterlink", "Masterlink do guindaste"),
    ("CB_LINK_1", "Link CB 1", "Linga 1 da corcova"),
    ("CB_LINK_2", "Link CB 2", "Linga 2 da corcova"),
    ("CRANE_TIP", "PontaGuindastePrincipal", "Ponta do guindaste principal"),
    ("CURVE_TEMPLATE_BENDING", "Bend Restrictor Stiffness Curve",
     "Curva momento × curvatura existente no modelo base (molde para criar curvas da biblioteca)"),
    ("CURVE_TEMPLATE_AXIAL", "", "Curva tração × deformação existente no modelo base (molde); vazio = não há"),
    ("MCV_B", "MCV B", "MCV da outra extremidade (só no modelo base de 2 extremidades)"),
    ("HUB_B", "Hub Height B", "Hub da outra extremidade (só no modelo base de 2 extremidades)"),
    ("BR_B", "Bend Restrictor B", "Tipo de stiffener do BR da outra extremidade (2 extremidades; criado pelo build)"),
    ("BR_ELEMENT_B", "Bend Restrictor Element B", "Anexo (stiffener) do BR da outra extremidade (criado pelo build)"),
    ("CONSTRAINT_B", "Constraint B", "Constraint do MCV da outra extremidade, livre só em X (criado pelo build)"),
    ("END_B_PULL", "MCV B Pull", "Tirante que aplica a tração alvo no MCV B livre em X (criado pelo build)"),
]

OPERATION_TYPES = [
    ("1st end DCVD", "camelback", True, True),
    ("2nd end CVD", "camelback", True, True),
    ("1st end CVD", "", False, False),
    ("2nd end DCVD", "", False, False),
    ("Line to Hang-off", "", False, False),
    ("Handshake", "", False, False),
    ("Overboarding", "", False, False),
    ("1st end Pull-in", "", False, False),
    ("2nd end Pull-in", "", False, False),
    ("2nd end Pull-out", "", False, False),
    ("Line lay", "", False, False),
    ("UTA Instl. (1st end)", "", False, False),
]

# Fonte sugerida do limite para os critérios do JSON antigo (informativo)
SUGGESTED_SOURCES = {
    "Axial Tension": "mcv.axial_tension_limit",
    "Bend moment": "mcv.bend_moment_limit",
    "Shear force": "mcv.shear_force_limit",
    "Curvature": "line.mbr (inverse)",
    "Bend moment BR": "br.bend_moment_limit",
    "Shear force BR": "br.shear_force_limit",
    "Curvature BR": "br.locking_radius (inverse)",
}

DEFAULT_CHECKLIST: list[tuple] = [
    # Objeto, Variável, Unidade, Input, OrcaFlex, tol abs, tol rel, aplica a
    ("Environment", "Water Depth", "m", "environment.lda", "{ENVIRONMENT}.WaterDepth", 1e-3, 0, "all"),
    ("Environment", "Seabed Normal Stiffness", "kN/m/m²", "environment.seabed_normal_stiffness",
     "{ENVIRONMENT}.SeabedNormalStiffness", 1e-6, 1e-6, "all"),
    ("Environment", "Seabed Shear Stiffness", "kN/m/m²", "environment.seabed_shear_stiffness",
     "{ENVIRONMENT}.SeabedShearStiffness", 1e-6, 1e-6, "all"),
    ("Environment", "Lateral Friction", "-", "environment.friction_lateral",
     "{LINE_TYPE}.SeabedLateralFrictionCoefficient", 1e-6, 0, "all"),
    ("Environment", "Axial Friction", "-", "environment.friction_axial",
     "{LINE_TYPE}.SeabedAxialFrictionCoefficient", 1e-6, 0, "all"),
    ("Line", "Outer Diameter", "m", "line.od", "{LINE_TYPE}.OD", 1e-5, 0, "all"),
    ("Line", "Inner Diameter", "m", "line.id", "{LINE_TYPE}.ID", 1e-5, 0, "all"),
    ("Line", "Weight in Air", "t/m", "line.mass", "{LINE_TYPE}.MassPerUnitLength", 1e-6, 1e-4, "all"),
    ("Line", "Weight in SW", "t/m", "wet_weight(line.mass, line.od)", "wet_weight({LINE_TYPE})", 1e-6, 1e-4, "all"),
    ("Line", "MBR", "m", "line.mbr", "{LINE_TYPE}.xMinRadius", 1e-4, 0, "all"),
    ("Line", "Axial Stiffness", "kN", "line.ea", "{LINE_TYPE}.EA", 1e-3, 1e-6, "all"),
    ("Line", "Bending Stiffness", "kN.m²", "line.eix", "{LINE_TYPE}.EIx", 1e-4, 1e-6, "all"),
    ("End Fitting", "Full Length", "m", "ef.length", "{LINE}.Length[0]", 1e-4, 0, "all"),
    ("End Fitting", "Outer Diameter", "m", "ef.od", "{EF_TYPE}.OD", 1e-5, 0, "all"),
    ("End Fitting", "Inner Diameter", "m", "ef.id", "{EF_TYPE}.ID", 1e-5, 0, "all"),
    ("End Fitting", "Weight in Air", "t/m", "ef.mass", "{EF_TYPE}.MassPerUnitLength", 1e-6, 1e-4, "all"),
    ("Bend Restrictor", "Full Length", "m", "br.length", "{LINE}.Length[1]", 1e-4, 0, "all"),
    ("Bend Restrictor", "Attachment Length", "m", "br.length", "{BR}.Length", 1e-4, 0, "all"),
    ("Bend Restrictor", "Outer Diameter", "m", "br.od", "{BR_TYPE}.OD", 1e-5, 0, "all"),
    ("Bend Restrictor", "Inner Diameter", "m", "br.id", "{BR_TYPE}.ID", 1e-5, 0, "all"),
    ("Bend Restrictor", "Weight in Air", "t/m", "br.mass", "{BR_TYPE}.MassPerUnitLength", 1e-6, 1e-4, "all"),
    ("Bend Restrictor", "Locking Radius", "m", "br.locking_radius", "{BR_TYPE}.xMinRadius", 1e-4, 0, "all"),
    ("Bend Restrictor", "Locking Curvature", "1/m", "inverse(br.locking_radius)",
     "{BR_STIFFNESS}.IndependentValue[1]", 1e-6, 1e-6, "all"),
    ("MCV", "Gooseneck angle", "°", "mcv.angle", "angle_from_declination({LINE})", 1e-4, 0, "all"),
    ("MCV", "A - F (calculado)", "m", "diff(mcv.a, mcv.f)", "{HUB}.Length", 1e-4, 0, "all"),
    ("MCV", "A - F (informado)", "m", "mcv.hub_height", "{HUB}.Length", 1e-3, 0, "all"),
    ("MCV", "F", "m", "mcv.f", "{LINE}.EndAZ", 1e-4, 0, "all"),
    ("MCV", "G", "m", "mcv.g", "{LINE}.EndAX", 1e-4, 0, "all"),
    ("MCV", "Weight in Air", "t", "mcv.mass_air", "{MCV}.Mass", 1e-4, 1e-4, "all"),
    ("MCV", "Weight in SW", "t", "mcv.mass_sub", "submerged_mass({MCV})", 1e-3, 1e-3, "all"),
    ("MCV", "Calc. Volume", "m³", "mcv.volume", "{MCV}.Volume", 1e-5, 1e-4, "all"),
    ("MCV", "CoG x (G - E)", "m", "diff(mcv.g, mcv.e)", "cog_x({MCV})", 1e-4, 0, "all"),
    ("MCV", "CoG z (F - D)", "m", "diff(mcv.f, mcv.d)", "cog_z({MCV})", 1e-4, 0, "all"),
    ("MCV", "CoV x (G - E)", "m", "diff(mcv.g, mcv.e)", "cov_x({MCV})", 1e-4, 0, "all"),
    ("MCV", "CoV z (F - D)", "m", "diff(mcv.f, mcv.d)", "cov_z({MCV})", 1e-4, 0, "all"),
    ("MCV", "Height (B + F)", "m", "sum(mcv.b, mcv.f)", "{MCV}.Height", 1e-4, 0, "all"),
    ("MCV", "Inertia x (caixa)", "t.m²", "box_ixx(mcv.mass_air, mcv.base_size, mcv.b, mcv.f)", "inertia_x({MCV})",
     1e-4, 1e-4, "all"),
    ("MCV", "Inertia y (caixa)", "t.m²", "box_iyy(mcv.mass_air, mcv.g, mcv.base_size, mcv.b, mcv.f)",
     "inertia_y({MCV})", 1e-4, 1e-4, "all"),
    ("MCV", "Inertia z (caixa)", "t.m²", "box_izz(mcv.mass_air, mcv.g, mcv.base_size)", "inertia_z({MCV})",
     1e-4, 1e-4, "all"),
    ("MCV", "Hub shape type", "-", "model.hub_shape_type", "{HUB}.ShapeType", 0, 0, "all"),
    ("Line", "Contents Density", "t/m³", "line.contents_density", "{LINE}.ContentsDensity", 1e-6, 0, "all"),
    ("Line", "Statics Step 1", "-", "model.line_statics_step1", "{LINE}.StaticsStep1", 0, 0, "all"),
    ("General", "Jacobian perturbation factor", "-", "model.jacobian_perturbation_factor",
     "{GENERAL}.JacobianPerturbationFactor", 1e-12, 0, "all"),
    ("General", "Time step", "s", "simulation.time_step", "time_step({GENERAL})", 1e-9, 0, "all"),
    ("Crane Wire", "Outer Diameter", "m", "crane_wire.od", "{CRANE_WIRE_TYPE}.OD", 1e-5, 0, "all"),
    ("Crane Wire", "Weight in Air", "t/m", "crane_wire.mass", "{CRANE_WIRE_TYPE}.MassPerUnitLength", 1e-6, 1e-4, "all"),
    ("Crane Wire", "Axial Stiffness", "kN", "crane_wire.ea", "{CRANE_WIRE_TYPE}.EA", 1e-3, 1e-6, "all"),
    ("Winch Wire", "Outer Diameter", "m", "winch_wire.od", "{WINCH_WIRE_TYPE}.OD", 1e-5, 0, "camelback"),
    ("Winch Wire", "Weight in Air", "t/m", "winch_wire.mass", "{WINCH_WIRE_TYPE}.MassPerUnitLength", 1e-6, 1e-4, "camelback"),
    ("Winch Wire", "Axial Stiffness", "kN", "winch_wire.ea", "{WINCH_WIRE_TYPE}.EA", 1e-3, 1e-6, "camelback"),
    ("Winch Wire", "Nº de seções", "-", "winch_sections(model.winch_extension)", "section_count({WINCH_WIRE})",
     0, 0, "all"),
    ("Winch Extension", "Length", "m", "winch_ext.length", "{WINCH_WIRE}.Length[1]", 1e-3, 0, "winch_extension"),
    ("Winch Extension", "Outer Diameter", "m", "winch_ext.od", "{WINCH_EXT_TYPE}.OD", 1e-5, 0, "winch_extension"),
    ("Winch Extension", "Weight in Air", "t/m", "winch_ext.mass", "{WINCH_EXT_TYPE}.MassPerUnitLength", 1e-6, 1e-4,
     "winch_extension"),
    ("Winch Extension", "Axial Stiffness", "kN", "winch_ext.ea", "{WINCH_EXT_TYPE}.EA", 1e-3, 1e-6, "winch_extension"),
    ("Line", "Extremidades com MCV", "-", "model.modelled_ends", "modelled_ends({LINE})", 0, 0, "all"),
    ("Line", "Comprimento total (2 extremidades)", "m", "line.total_length", "line_length({LINE})", 1e-3, 0,
     "two_ends"),
    ("MCV B", "Gooseneck angle", "°", "mcv_b.angle", "{LINE}.EndBDeclination", 1e-4, 0, "two_ends"),
    ("MCV B", "A - F (calculado)", "m", "diff(mcv_b.a, mcv_b.f)", "{HUB_B}.Length", 1e-4, 0, "two_ends"),
    ("MCV B", "F", "m", "mcv_b.f", "{LINE}.EndBZ", 1e-4, 0, "two_ends"),
    ("MCV B", "Weight in Air", "t", "mcv_b.mass_air", "{MCV_B}.Mass", 1e-4, 1e-4, "two_ends"),
    ("MCV B", "Weight in SW", "t", "mcv_b.mass_sub", "submerged_mass({MCV_B})", 1e-3, 1e-3, "two_ends"),
    ("MCV B", "Calc. Volume", "m³", "mcv_b.volume", "{MCV_B}.Volume", 1e-5, 1e-4, "two_ends"),
    ("MCV B", "CoG x -(G - E) (espelhado)", "m", "diff(mcv_b.e, mcv_b.g)", "cog_x({MCV_B})", 1e-4, 0, "two_ends"),
    ("MCV B", "CoG z (F - D)", "m", "diff(mcv_b.f, mcv_b.d)", "cog_z({MCV_B})", 1e-4, 0, "two_ends"),
    ("MCV B", "CoV x -(G - E) (espelhado)", "m", "diff(mcv_b.e, mcv_b.g)", "cov_x({MCV_B})", 1e-4, 0, "two_ends"),
    ("MCV B", "CoV z (F - D)", "m", "diff(mcv_b.f, mcv_b.d)", "cov_z({MCV_B})", 1e-4, 0, "two_ends"),
    ("MCV B", "Height (B + F)", "m", "sum(mcv_b.b, mcv_b.f)", "{MCV_B}.Height", 1e-4, 0, "two_ends"),
    ("MCV B", "Inertia x (caixa)", "t.m²", "box_ixx(mcv_b.mass_air, mcv_b.base_size, mcv_b.b, mcv_b.f)",
     "inertia_x({MCV_B})", 1e-4, 1e-4, "two_ends"),
    ("MCV B", "Inertia y (caixa)", "t.m²", "box_iyy(mcv_b.mass_air, mcv_b.g, mcv_b.base_size, mcv_b.b, mcv_b.f)",
     "inertia_y({MCV_B})", 1e-4, 1e-4, "two_ends"),
    ("MCV B", "Inertia z (caixa)", "t.m²", "box_izz(mcv_b.mass_air, mcv_b.g, mcv_b.base_size)", "inertia_z({MCV_B})",
     1e-4, 1e-4, "two_ends"),
    ("MCV B", "Hub shape type", "-", "model.hub_shape_type", "{HUB_B}.ShapeType", 0, 0, "two_ends"),
    ("Bend Restrictor B", "Attachment Length", "m", "first(br_b.length, br.length)", "{BR_B}.Length", 1e-4, 0,
     "two_ends"),
    ("Camel Back", "Leg 1 KP", "m", "camelback.leg1_kp", "{CB_LINK_1}.EndAZ", 1e-4, 0, "camelback"),
    ("Camel Back", "Leg 2 KP", "m", "camelback.leg2_kp", "{CB_LINK_2}.EndAZ", 1e-4, 0, "camelback"),
    ("Camel Back", "Sling Length", "m", "camelback.sling_length", "{CB_LINK_1}.UnstretchedLength", 1e-4, 0, "camelback"),
    ("Camel Back", "Sling Stiffness", "kN", "camelback.stiffness", "{CB_LINK_1}.Stiffness", 1e-3, 1e-6, "camelback"),
    ("Acceptance Criteria", "Axial Tension", "kN", "mcv.axial_tension_limit", "criterion(Axial Tension)", 1e-6, 0, "all"),
    ("Acceptance Criteria", "Bending Moment", "kN.m", "mcv.bend_moment_limit", "criterion(Bend moment)", 1e-6, 0, "all"),
    ("Acceptance Criteria", "Shear Force", "kN", "mcv.shear_force_limit", "criterion(Shear force)", 1e-6, 0, "all"),
    ("Acceptance Criteria", "Line Curvature", "1/m", "inverse(line.mbr)", "criterion(Curvature)", 1e-4, 0, "all"),
    ("Acceptance Criteria", "BR Bending Moment", "kN.m", "br.bend_moment_limit", "criterion(Bend moment BR)", 1e-6, 0, "all"),
    ("Acceptance Criteria", "BR Shear Force", "kN", "br.shear_force_limit", "criterion(Shear force BR)", 1e-6, 0, "all"),
    ("Acceptance Criteria", "Tilt Angle", "°", "mcv.tolerance_angle", "", 0, 0, "all"),
]

DEFAULT_SEA_GRID = [(d, tp, [3.0]) for d in (0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0)
                    for tp in [float(t) for t in range(5, 16)]]


# --------------------------------------------------------------------------- modelo em memória

@dataclass
class ProjectConfig:
    path: Optional[Path] = None
    defaults: dict[str, Value] = field(default_factory=dict)
    overrides: dict[str, dict[str, Value]] = field(default_factory=dict)
    operations: dict[str, OperationSpec] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)
    postproc: dict[str, list[PostItem]] = field(default_factory=dict)
    criteria: dict[str, list[CriterionSpec]] = field(default_factory=dict)
    checklist: list[ChecklistRow] = field(default_factory=list)
    sea_grid: list[tuple[float, float, list[float]]] = field(default_factory=list)

    def operation(self, op_type: str) -> OperationSpec:
        if op_type in self.operations:
            return self.operations[op_type]
        return OperationSpec(op_type, supported=False)


def _yes(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    text = str(v).strip().lower()
    try:
        return float(text.replace(",", ".")) != 0
    except ValueError:
        return text in ("s", "sim", "y", "yes", "true", "verdadeiro", "x")


def _param_value(key: str, raw: Any) -> Value:
    kind = P.get(key).kind
    if kind == P.LIST:
        return parse_list(raw)
    if kind == P.BOOL:
        if isinstance(raw, Missing) or raw is None or str(raw).strip() == "":
            return Missing("vazio")
        return 1.0 if _yes(raw) else 0.0
    if kind == P.TEXT:
        return str(raw) if raw is not None else Missing("vazio")
    v = parse_cell(raw)
    if kind == P.CURVE_OR_NUMBER and isinstance(v, CurveRef):
        # Na configuração, texto em EIx/EA é o NOME de uma curva já existente no modelo
        return v.text
    return v


def default_config(legacy_inputs: Optional[dict] = None) -> ProjectConfig:
    """Configuração inicial; com ``legacy_inputs`` copia valores, PostProc e Critérios do JSON antigo."""
    cfg = ProjectConfig()
    for p in P.PARAMS:
        if p.input_column:
            continue  # vem da Input Sheet; só entra em Defaults se o usuário quiser
        value = p.default
        if legacy_inputs and p.legacy:
            section, fld = p.legacy
            if fld in legacy_inputs.get(section, {}):
                value = legacy_inputs[section][fld]
        cfg.defaults[p.key] = _param_value(p.key, value) if value is not None else Missing("vazio")

    for op, tpl, cb, sup in OPERATION_TYPES:
        cfg.operations[op] = OperationSpec(op, tpl, cb, "00.Base/base_input.dat" if sup else "", sup,
                                           counterpart=DEFAULT_COUNTERPARTS.get(op, ""))
    cfg.roles = {r: name for r, name, _ in DEFAULT_ROLES}
    cfg.checklist = [ChecklistRow(*row) for row in DEFAULT_CHECKLIST]
    cfg.sea_grid = list(DEFAULT_SEA_GRID)

    if legacy_inputs:
        name_to_role = {name: f"{{{role}}}" for role, name in cfg.roles.items()}
        for label, it in legacy_inputs.get("postProcess", {}).items():
            cfg.postproc.setdefault("camelback", []).append(PostItem(
                label=label, method=it["OrcaFlex Method"],
                obj=name_to_role.get(it["OrcaFlex Parent"], it["OrcaFlex Parent"]),
                variable=it["OrcaFlex Variable Name"], extra=it["OrcaFlex Extra"],
                period=it.get("Simulation Period", ""), template="camelback"))
        for name, it in legacy_inputs.get("acceptanceCriteria", {}).items():
            cfg.criteria.setdefault("camelback", []).append(CriterionSpec(
                name=name, type=it["Type"],
                obj=name_to_role.get(it["OrcaFlex Parent"], it["OrcaFlex Parent"]),
                variable=it["OrcaFlex Variable Name"], extra=it["OrcaFlex Extra"],
                limit=float(it["Limit"]), template="camelback"))
    return cfg


# --------------------------------------------------------------------------- escrita

def write_config(cfg: ProjectConfig, path: Path | str) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} já existe. A configuração não é sobrescrita automaticamente.")
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name, rows):
        ws = wb.create_sheet(name)
        ws.append(SHEETS[name])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="305496")
        for r in rows:
            ws.append(list(r))
        for i, _ in enumerate(SHEETS[name], start=1):
            width = max(len(str(c.value or "")) for c in ws[get_column_letter(i)])
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)
        ws.freeze_panes = "A2"
        return ws

    def cell(v):
        if isinstance(v, Missing):
            return None
        if isinstance(v, list):
            return ";".join(f"{x:g}" for x in v)
        if isinstance(v, CurveRef):
            return v.text
        return v

    sheet("Defaults", [(k, cell(v), P.get(k).unit, P.get(k).description) for k, v in cfg.defaults.items()])
    sheet("Overrides", [(cid, k, cell(v), "") for cid, kv in cfg.overrides.items() for k, v in kv.items()])
    sheet("Operações", [(o.operation_type, o.template, "S" if o.camelback else "N", o.base_model,
                         "S" if o.supported else "N", o.base_model_two_ends, o.counterpart)
                        for o in cfg.operations.values()])
    role_desc = {r: d for r, _, d in DEFAULT_ROLES}
    sheet("Papéis", [(r, n, role_desc.get(r, "")) for r, n in cfg.roles.items()])
    sheet("PostProc", [(i.template, i.label, i.method, i.obj, i.variable,
                        f"{i.extra[0]:g};{i.extra[1]:g}" if isinstance(i.extra, list) else i.extra,
                        i.period, i.unit) for items in cfg.postproc.values() for i in items])
    sheet("Critérios", [(c.template, c.name, c.type, c.obj, c.variable,
                         f"{c.extra[0]:g};{c.extra[1]:g}" if isinstance(c.extra, list) else c.extra,
                         c.limit, c.limit_source, c.transform, "S" if c.static else "N",
                         "S" if c.dynamic else "N", SUGGESTED_SOURCES.get(c.name, ""))
                        for items in cfg.criteria.values() for c in items])
    sheet("Checklist", [(r.group, r.variable, r.unit, r.input_expr, r.orcaflex_expr, r.abs_tol, r.rel_tol,
                         r.applies_to) for r in cfg.checklist])
    sheet("Mares", [(d, tp, ";".join(f"{h:g}" for h in hs)) for d, tp, hs in cfg.sea_grid])

    readme = wb.create_sheet("LEIA-ME", 0)
    for line in (__doc__ or "").strip().splitlines():
        readme.append([line])
    readme.column_dimensions["A"].width = 110
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# --------------------------------------------------------------------------- leitura

def load_config(path: Path | str) -> ProjectConfig:
    from openpyxl import load_workbook

    path = Path(path)
    wb = load_workbook(path, data_only=True)
    missing = [s for s in SHEETS if s not in wb.sheetnames]
    if missing:
        raise KeyError(f"{path.name} não tem as abas {missing}. Gere um modelo com 'cvd config init'.")
    formulas = load_workbook(path, data_only=False)
    rows = {name: _rows(wb[name], SHEETS[name], OPTIONAL_COLUMNS.get(name, []), formulas[name])
            for name in SHEETS}

    cfg = ProjectConfig(path=path)
    errors: list[str] = []

    for i, r in rows["Defaults"]:
        key = r["Parâmetro"]
        if not key:
            continue
        try:
            cfg.defaults[key] = _param_value(key, r["Valor"])
        except KeyError as exc:
            errors.append(f"Defaults linha {i}: {exc}")
    for i, r in rows["Overrides"]:
        if not r["ID"] or not r["Parâmetro"]:
            continue
        try:
            cfg.overrides.setdefault(str(r["ID"]).strip(), {})[r["Parâmetro"]] = _param_value(r["Parâmetro"], r["Valor"])
        except KeyError as exc:
            errors.append(f"Overrides linha {i}: {exc}")
    for _, r in rows["Operações"]:
        if r["Operation Type"]:
            op = str(r["Operation Type"]).strip()
            cfg.operations[op] = OperationSpec(op, str(r["Template"] or ""), _yes(r["Corcova (S/N)"]),
                                               str(r["Modelo base"] or ""), _yes(r["Suportado (S/N)"]),
                                               str(r.get("Modelo base (2 extremidades)") or ""),
                                               str(r.get("Outra extremidade (sugestão)") or "").strip())
    for _, r in rows["Papéis"]:
        if r["Papel"]:
            cfg.roles[str(r["Papel"]).strip().strip("{}")] = str(r["Objeto OrcaFlex"] or "").strip()
    for i, r in rows["PostProc"]:
        if not r["Rótulo"]:
            continue
        try:
            item = PostItem(label=str(r["Rótulo"]), method=r["Método"], obj=str(r["Objeto"]),
                            variable=str(r["Variável"]), extra=r["Extra"], period=r["Período"],
                            template=str(r["Template"] or ""), unit=str(r["Unidade"] or ""))
            cfg.postproc.setdefault(item.template, []).append(item)
        except ValueError as exc:
            errors.append(f"PostProc linha {i}: {exc}")
    for i, r in rows["Critérios"]:
        if not r["Critério"]:
            continue
        try:
            limit = r["Limite"]
            spec = CriterionSpec(
                name=str(r["Critério"]), type=str(r["Tipo"]), obj=str(r["Objeto"]), variable=str(r["Variável"]),
                extra=r["Extra"], limit=float(limit) if limit not in (None, "") else None,
                limit_source=str(r["Fonte do limite"] or "").strip(),
                transform=str(r["Transformação"] or "").strip().lower(),
                static=_yes(r["Estático (S/N)"] if r["Estático (S/N)"] is not None else "S"),
                dynamic=_yes(r["Dinâmico (S/N)"] if r["Dinâmico (S/N)"] is not None else "S"),
                template=str(r["Template"] or ""))
            if spec.limit_source:
                P.get(spec.limit_source)
            if spec.type not in ("Time History", "Range Graph Max"):
                raise ValueError(f"Tipo {spec.type!r} inválido (use 'Time History' ou 'Range Graph Max')")
            cfg.criteria.setdefault(spec.template, []).append(spec)
        except (ValueError, KeyError) as exc:
            errors.append(f"Critérios linha {i}: {exc}")
    for _, r in rows["Checklist"]:
        if r["Objeto"] and r["Variável"]:
            cfg.checklist.append(ChecklistRow(
                str(r["Objeto"]), str(r["Variável"]), str(r["Unidade"] or ""), str(r["Input"] or ""),
                str(r["OrcaFlex"] or ""), float(r["Tol. abs"] or 0), float(r["Tol. rel"] or 0),
                str(r["Aplica a"] or "all")))
    for i, r in rows["Mares"]:
        if r["Direção (°)"] is None or r["Tp (s)"] is None:
            continue
        hs = parse_list(r["Hs (m)"])
        if isinstance(hs, Missing):
            errors.append(f"Mares linha {i}: Hs inválido")
            continue
        cfg.sea_grid.append((float(r["Direção (°)"]), float(r["Tp (s)"]), hs))

    if errors:
        raise ValueError(f"Erros em {path.name}:\n  - " + "\n  - ".join(errors))
    return cfg


def _rows(ws, header: list[str], optional: list[str] = (), ws_formulas=None):
    """Linhas da aba como dicionários. Célula com fórmula sem valor calculado (planilha salva fora do
    Excel, por exemplo pelo ``config upgrade``) é calculada aqui (``FormulaCells``)."""
    it = ws.iter_rows(values_only=True)
    first = [str(c).strip() if c is not None else "" for c in next(it, [])]
    idx = {h: first.index(h) for h in header if h in first}
    lost = [h for h in header if h not in idx and h not in optional]
    if lost:
        raise KeyError(f"Aba '{ws.title}' sem as colunas {lost}")
    calc = FormulaCells(ws, ws_formulas) if ws_formulas is not None else None
    out = []
    for n, row in enumerate(it, start=2):
        row = list(row) + [None] * len(first)
        rec = {h: row[i] for h, i in idx.items()}
        if calc is not None:
            for h, i in idx.items():
                if rec[h] is None:
                    rec[h] = calc.value(n, i + 1)
        if any(v not in (None, "") for v in rec.values()):
            out.append((n, rec))
    return out


class FormulaCells:
    """Valor de células com fórmula sem valor guardado: referências da mesma aba e + - * / ( ).

    O openpyxl grava as fórmulas sem o resultado; até a planilha ser aberta e salva no Excel,
    a leitura com ``data_only`` vê vazio. Antes (0.9.0 e anteriores) essas linhas eram
    ignoradas sem aviso: a aba Mares do GLF27 virou 1 direção e 1 Hs.
    """

    _REF = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")

    def __init__(self, ws_values, ws_formulas):
        self.values, self.formulas = ws_values, ws_formulas
        self.cache: dict[tuple[int, int], object] = {}

    def value(self, row: int, col: int, _stack=()):
        key = (row, col)
        if key in self.cache:
            return self.cache[key]
        cached = self.values.cell(row, col).value
        raw = self.formulas.cell(row, col).value
        if cached is not None or not (isinstance(raw, str) and raw.startswith("=")):
            return cached if cached is not None else raw
        if key in _stack:
            raise ValueError(f"Aba '{self.formulas.title}': referência circular em {raw}")
        expr = raw[1:]

        def ref(m):
            from openpyxl.utils import column_index_from_string

            v = self.value(int(m.group(2)), column_index_from_string(m.group(1)), _stack + (key,))
            if v is None or v == "":
                v = 0
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(f"Aba '{self.formulas.title}', {raw}: {m.group(0)} não é número ({v!r})")
            return repr(float(v))

        try:
            val = _arith(self._REF.sub(ref, expr.upper()))
        except ValueError as exc:
            raise ValueError(f"{exc}. Abra e salve a planilha no Excel para gravar os valores") from None
        self.cache[key] = val
        return val


def _arith(expr: str) -> float:
    import ast
    import operator as op

    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.USub: op.neg,
           ast.UAdd: op.pos}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.operand))
        raise ValueError(f"fórmula não suportada fora do Excel: ={expr}")

    try:
        return ev(ast.parse(expr.replace(",", "."), mode="eval"))
    except SyntaxError:
        raise ValueError(f"fórmula não suportada fora do Excel: ={expr}") from None


# --------------------------------------------------------------------------- atualização

def upgrade_config(path: Path | str, sets: Optional[dict[str, str]] = None) -> list[str]:
    """Acrescenta a uma configuração existente o que versões novas do pacote introduziram.

    Nada que já existe é alterado, exceto os valores pedidos em ``sets`` (aba Defaults).
    Antes de salvar, grava uma cópia ``<nome>.bak_<data>.xlsx`` ao lado.
    """
    import datetime as _dt
    import shutil

    from openpyxl import load_workbook

    path = Path(path)
    sets = dict(sets or {})
    for key in sets:
        P.get(key)  # valida a chave
    wb = load_workbook(path)
    changes: list[str] = []

    ws = wb["Defaults"]
    present = {str(r[0].value).strip(): r for r in ws.iter_rows(min_row=2) if r[0].value}
    for p in P.PARAMS:
        if p.input_column or p.key in present:
            continue
        value = sets.pop(p.key, None)
        if value is None:
            value = ";".join(f"{x:g}" for x in p.default) if isinstance(p.default, list) else p.default
        ws.append([p.key, value, p.unit, p.description])
        changes.append(f"Defaults: + {p.key} = {value if value is not None else '(vazio)'}")
    for key, value in list(sets.items()):
        if key in present:
            old = present[key][1].value
            present[key][1].value = value
            changes.append(f"Defaults: {key}: {old!r} -> {value!r}")
        else:
            ws.append([key, value, P.get(key).unit, P.get(key).description])
            changes.append(f"Defaults: + {key} = {value}")

    ws = wb["Papéis"]
    roles = {str(r[0].value).strip().strip("{}") for r in ws.iter_rows(min_row=2) if r[0].value}
    for role, name, desc in DEFAULT_ROLES:
        if role not in roles:
            ws.append([role, name, desc])
            changes.append(f"Papéis: + {role} = {name}")

    ws = wb["Checklist"]
    rows = {(str(r[0].value), str(r[1].value)) for r in ws.iter_rows(min_row=2) if r[0].value}
    for row in DEFAULT_CHECKLIST:
        if (row[0], row[1]) not in rows:
            ws.append(list(row))
            changes.append(f"Checklist: + {row[0]} / {row[1]}")

    ws = wb["Operações"]
    header = [c.value for c in ws[1]]
    for col in OPTIONAL_COLUMNS["Operações"]:
        if col not in header:
            ws.cell(1, len(header) + 1, col)
            header.append(col)
            changes.append(f"Operações: + coluna '{col}'")
            if col == "Outra extremidade (sugestão)":
                ci = len(header)
                for r in range(2, ws.max_row + 1):
                    op = ws.cell(r, 1).value
                    if op in DEFAULT_COUNTERPARTS:
                        ws.cell(r, ci, DEFAULT_COUNTERPARTS[op])

    if changes:
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.stem}.bak_{stamp}{path.suffix}"))
        wb.save(path)
    return changes
