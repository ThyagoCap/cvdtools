"""Registro único de parâmetros da pipeline.

Cada parâmetro tem uma chave canônica (``"line.od"``) e diz de onde pode vir:

- coluna da aba ``Input Sheet`` da planilha geral;
- campo do ``__INPUTS.json`` antigo (para migração e regressão);
- valor padrão de projeto (aba ``Defaults`` da planilha de configuração).

A ordem de precedência na resolução de um caso é:
``Overrides (por ID) > Input Sheet > Defaults``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

NUMBER = "number"
CURVE_OR_NUMBER = "curve_or_number"  # aceita número ou nome de curva do OrcaFlex
LIST = "list"
TEXT = "text"
BOOL = "bool"

# Grupos de exigência: o builder com corcova precisa de todos os "build" e "camelback".
BUILD = "build"
CAMELBACK = "camelback"
LIMITS = "limits"
WINCH_EXT = "winch_extension"   # premissa: cabo do guincho em duas seções
TWO_ENDS = "two_ends"           # premissa: as duas extremidades da linha modeladas com MCV


@dataclass(frozen=True)
class Param:
    key: str
    unit: str
    description: str
    kind: str = NUMBER
    input_column: Optional[str] = None
    legacy: Optional[tuple[str, str]] = None
    default: Any = None
    required: frozenset = field(default_factory=frozenset)


def _p(key, unit, desc, kind=NUMBER, col=None, legacy=None, default=None, req=()):
    return Param(key, unit, desc, kind, col, legacy, default, frozenset(req))


PARAMS: list[Param] = [
    # ------------------------------------------------------------------ ambiente
    _p("environment.lda", "m", "Lâmina d'água", legacy=("EnvironmentDims", "LDA"), req=[BUILD]),
    _p("environment.seabed_normal_stiffness", "kN/m/m²", "Rigidez normal do solo",
       legacy=("EnvironmentDims", "SeabedNormalStiffness"), default=100.0, req=[BUILD]),
    _p("environment.seabed_shear_stiffness", "kN/m/m²", "Rigidez de cisalhamento do solo",
       legacy=("EnvironmentDims", "SeabedShearStiffness"), default=100.0, req=[BUILD]),
    _p("environment.friction_lateral", "-", "Coeficiente de atrito lateral",
       legacy=("EnvironmentDims", "FrictionCoefLateral"), req=[BUILD]),
    _p("environment.friction_axial", "-", "Coeficiente de atrito axial",
       legacy=("EnvironmentDims", "FrictionCoefAxial"), req=[BUILD]),
    # ------------------------------------------------------------------ MCV
    _p("mcv.angle", "°", "Ângulo do gooseneck", col="MCV Angle (°)", legacy=("MCVDims", "Angle"), req=[BUILD]),
    _p("mcv.a", "m", "Cota A", col="MCV A (m)", legacy=("MCVDims", "A"), req=[BUILD]),
    _p("mcv.a5", "m", "Cota A a 5 m", legacy=("MCVDims", "A5")),
    _p("mcv.a10", "m", "Cota A a 10 m", legacy=("MCVDims", "A10")),
    _p("mcv.b", "m", "Cota B", col="MCV B (m)", legacy=("MCVDims", "B"), req=[BUILD]),
    _p("mcv.c", "m", "Cota C", col="MCV C (m)", legacy=("MCVDims", "C"), req=[BUILD]),
    _p("mcv.d", "m", "Cota D", col="MCV D (m)", legacy=("MCVDims", "D")),
    _p("mcv.e", "m", "Cota E", col="MCV E (m)", legacy=("MCVDims", "E")),
    _p("mcv.f", "m", "Cota F", col="MCV F (m)", legacy=("MCVDims", "F"), req=[BUILD]),
    _p("mcv.g", "m", "Cota G", col="MCV G (m)", legacy=("MCVDims", "G"), req=[BUILD]),
    _p("mcv.hub_height", "m", "Altura do hub (A - F) informada", col="Hub Height A-F (m)"),
    _p("mcv.mass_air", "t", "Massa do MCV no ar", col="MCV Mass Air (t)", legacy=("MCVDims", "MassInAir"), req=[BUILD]),
    _p("mcv.mass_sub", "t", "Massa submersa do MCV", col="MCV Sub Mass (t)", legacy=("MCVDims", "MassSubmerged")),
    _p("mcv.volume", "m³", "Volume do MCV", col="MCV Volume (m³)", legacy=("MCVDims", "Volume"), req=[BUILD]),
    _p("mcv.base_size", "m", "Lado da base desenhada do MCV", legacy=("MCVDims", "MCVBaseSize"), default=0.6, req=[BUILD]),
    _p("mcv.axial_tension_limit", "kN", "Tração admissível no flange", col="MCV Axial Tension (kN)", req=[LIMITS]),
    _p("mcv.shear_force_limit", "kN", "Cortante admissível no flange", col="MCV Shear Force (kN)", req=[LIMITS]),
    _p("mcv.bend_moment_limit", "kN.m", "Momento admissível no flange", col="MCV Bend Moment (kN.m)", req=[LIMITS]),
    _p("mcv.tolerance_angle", "°", "Tolerância de ângulo para conexão", col="MCV Tolerance Angle (°)"),
    # ------------------------------------------------------------------ linha
    _p("line.total_length", "m", "Comprimento total da linha", col="Length (m)"),
    _p("line.od", "m", "Diâmetro externo", col="Line OD (m)", legacy=("LineTypeDims", "OD"), req=[BUILD]),
    _p("line.id", "m", "Diâmetro interno", col="Line ID (m)", legacy=("LineTypeDims", "ID"), req=[BUILD]),
    _p("line.mass", "t/m", "Massa linear no ar", col="Line Mass in Air (t/m)", legacy=("LineTypeDims", "MassPerUnitLength"), req=[BUILD]),
    _p("line.mbr", "m", "Raio mínimo de curvatura", col="Line MBR (m)", legacy=("LineTypeDims", "xMinRadius"), req=[BUILD]),
    _p("line.ea", "kN", "Rigidez axial", col="Line EA (kN)", legacy=("LineTypeDims", "EA"), req=[BUILD]),
    _p("line.eix", "kN.m²", "Rigidez à flexão (número ou nome da curva no modelo)", kind=CURVE_OR_NUMBER,
       col="Line EIx (kN.m²)", legacy=("LineTypeDims", "EIx"), req=[BUILD]),
    _p("line.contents_density", "t/m³",
       "Densidade do conteúdo da linha flexível. 0 = a massa da Input Sheet já é a da linha cheia (não contar o "
       "conteúdo duas vezes)", default=0.0),
    # ------------------------------------------------------------------ restritor de curvatura
    _p("br.length", "m", "Comprimento do BR", col="BR Length (m)", legacy=("BendRestrictorDims", "Length"), req=[BUILD]),
    _p("br.od", "m", "Diâmetro externo do BR", col="BR OD (m)", legacy=("BendRestrictorDims", "OD"), req=[BUILD]),
    _p("br.id", "m", "Diâmetro interno do BR", col="BR ID (m)", legacy=("BendRestrictorDims", "ID"), req=[BUILD]),
    _p("br.mass", "t/m", "Massa linear do BR", col="BR Mass in Air (t/m)", legacy=("BendRestrictorDims", "MassPerUnitLength"), req=[BUILD]),
    _p("br.locking_radius", "m", "Raio de travamento do BR (MBR do tipo e da curva de rigidez)",
       col="BR Locking Radius (m)", legacy=("BendRestrictorDims", "xMinRadius"), req=[BUILD]),
    _p("br.ea", "kN", "Rigidez axial do BR", legacy=("BendRestrictorDims", "EA"), default=200.0, req=[BUILD]),
    _p("br.eix", "kN.m²", "EIx do BR (nome da curva de rigidez)", kind=CURVE_OR_NUMBER,
       legacy=("BendRestrictorDims", "EIx"), default="Bend Restrictor Stiffness Curve", req=[BUILD]),
    _p("br.bend_moment_limit", "kN.m", "Momento admissível do BR", col="BR Bend Moment (kN.m)", req=[LIMITS]),
    _p("br.shear_force_limit", "kN", "Cortante admissível do BR", col="BR Shear Force (kN)", req=[LIMITS]),
    # ------------------------------------------------------------------ end fitting
    _p("ef.length", "m", "Comprimento do end fitting", col="EF Length (m)", legacy=("EndFittingDims", "Length"), req=[BUILD]),
    _p("ef.od", "m", "Diâmetro externo do EF", col="EF OD (m)", legacy=("EndFittingDims", "OD"), req=[BUILD]),
    _p("ef.id", "m", "Diâmetro interno do EF", col="EF ID (m)", legacy=("EndFittingDims", "ID"), req=[BUILD]),
    _p("ef.mass", "t/m", "Massa linear do EF", col="EF Mass in Air (t/m)", legacy=("EndFittingDims", "MassPerUnitLength"), req=[BUILD]),
    _p("ef.mbr", "m", "MBR do EF", legacy=("EndFittingDims", "xMinRadius"), default=10.0, req=[BUILD]),
    _p("ef.ea", "kN", "EA do EF (sobrescrito pelo cálculo em aço)", legacy=("EndFittingDims", "EA"), default=1e7, req=[BUILD]),
    _p("ef.eix", "kN.m²", "EIx do EF (sobrescrito pelo cálculo em aço)", legacy=("EndFittingDims", "EIx"), default=1e5, req=[BUILD]),
    # ------------------------------------------------------------------ cabos
    _p("crane_wire.od", "m", "OD do cabo do guindaste", col="Crane Wire OD (m)", legacy=("CraneWireDims", "OD"), req=[BUILD]),
    _p("crane_wire.id", "m", "ID do cabo do guindaste", legacy=("CraneWireDims", "ID"), default=0.0, req=[BUILD]),
    _p("crane_wire.mass", "t/m", "Massa do cabo do guindaste", col="Crane Wire Mass in Air (t/m)",
       legacy=("CraneWireDims", "MassPerUnitLength"), req=[BUILD]),
    _p("crane_wire.mbr", "m", "MBR do cabo do guindaste", legacy=("CraneWireDims", "xMinRadius"), default=2.0, req=[BUILD]),
    _p("crane_wire.ea", "kN", "EA do cabo do guindaste (número ou curva tração × deformação)", kind=CURVE_OR_NUMBER,
       col="Crane Wire EA (kN)", legacy=("CraneWireDims", "EA"), req=[BUILD]),
    _p("crane_wire.eix", "kN.m²", "EIx do cabo do guindaste", legacy=("CraneWireDims", "EIx"), req=[BUILD]),
    _p("winch_wire.od", "m", "OD do cabo do guincho", col="Winch Wire OD (m)", legacy=("WinchWireDims", "OD"), req=[BUILD]),
    _p("winch_wire.id", "m", "ID do cabo do guincho", legacy=("WinchWireDims", "ID"), default=0.0, req=[BUILD]),
    _p("winch_wire.mass", "t/m", "Massa do cabo do guincho", col="Winch Wire Mass in Air (t/m)",
       legacy=("WinchWireDims", "MassPerUnitLength"), req=[BUILD]),
    _p("winch_wire.mbr", "m", "MBR do cabo do guincho", legacy=("WinchWireDims", "xMinRadius"), default=2.0, req=[BUILD]),
    _p("winch_wire.ea", "kN", "EA do cabo do guincho (número ou curva tração × deformação)", kind=CURVE_OR_NUMBER,
       col="Winch Wire EA (kN)", legacy=("WinchWireDims", "EA"), req=[BUILD]),
    _p("winch_wire.eix", "kN.m²", "EIx do cabo do guincho", legacy=("WinchWireDims", "EIx"), req=[BUILD]),
    _p("winch_ext.length", "m", "Comprimento da extensão do cabo do guincho", col="Extension Winch Wire Length (m)",
       req=[WINCH_EXT]),
    _p("winch_ext.od", "m", "OD da extensão", col="Extension Winch Wire OD (m)", req=[WINCH_EXT]),
    _p("winch_ext.id", "m", "ID da extensão", default=0.0),
    _p("winch_ext.mass", "t/m", "Massa da extensão", col="Extension Winch Wire Mass in Air (t/m)", req=[WINCH_EXT]),
    _p("winch_ext.ea", "kN", "EA da extensão (número ou nome da curva no modelo)", kind=CURVE_OR_NUMBER,
       col="Extension Winch Wire EA (kN)", req=[WINCH_EXT]),
    _p("winch_ext.mbr", "m", "MBR da extensão (vazio = mantém o do modelo base)"),
    _p("winch_ext.eix", "kN.m²", "EIx da extensão (vazio = mantém o do modelo base)", kind=CURVE_OR_NUMBER),
    _p("winch_ext.segments", "-", "Nº de segmentos da seção de extensão", default=50.0),
    # ------------------------------------------------------------------ corcova e flutuadores
    _p("camelback.sling_length", "m", "Comprimento das lingas da corcova", legacy=("CamelBackDims", "SlingLength"), req=[CAMELBACK]),
    _p("camelback.leg1_kp", "m", "KP da perna 1", legacy=("CamelBackDims", "Leg1KP"), req=[CAMELBACK]),
    _p("camelback.leg2_kp", "m", "KP da perna 2", legacy=("CamelBackDims", "Leg2KP"), req=[CAMELBACK]),
    _p("camelback.stiffness", "kN", "Rigidez das lingas", legacy=("CamelBackDims", "Stiffness"), req=[CAMELBACK]),
    _p("floaters.buoyancy", "t", "Empuxo líquido de cada flutuador (lista ;)", kind=LIST,
       legacy=("FloaterDims", "FloaterBuoyancy"), default=[]),
    _p("floaters.kps", "m", "KP de cada flutuador (lista ;)", kind=LIST, legacy=("FloaterDims", "FloaterKPs"), default=[]),
    _p("floaters.sling_length", "m", "Comprimento da linga de cada flutuador (lista ;)", kind=LIST,
       legacy=("FloaterDims", "FloaterSlingLength"), default=[]),
    _p("floaters.sling_stiffness", "kN", "Rigidez da linga de cada flutuador (lista ;)", kind=LIST,
       legacy=("FloaterDims", "FloaterSlingStiffness"), default=[]),
    # ------------------------------------------------------------------ premissas de modelagem
    _p("model.winch_extension", "S/N",
       "Cabo do guincho em duas seções (guincho + extensão). Vazio = premissa não definida: "
       "o builder mantém as seções do modelo base sem configurar a extensão", kind=BOOL),
    _p("model.modelled_ends", "-", "Extremidades da linha modeladas com MCV (1 = outra ponta livre/ancorada; 2 = as duas)",
       default=1.0),
    _p("model.two_ends_max_length", "m",
       "Linhas mais curtas que isto geram aviso para confirmar se as duas extremidades devem ser modeladas",
       default=500.0),
    _p("model.end_b_case_id", "-", "ID da linha da Input Sheet com o MCV da outra extremidade (2 extremidades)", kind=TEXT),
    _p("model.end_distance", "m",
       "Distância horizontal entre os hubs das duas extremidades (2 extremidades). Vazio = ajustada pela estática "
       "até a menor tração efetiva do trecho livre da linha ser model.end_b_tension_target"),
    _p("ef_b.length", "m", "Comprimento do end fitting da outra extremidade (vem da linha 'model.end_b_case_id'; "
       "vazio = o mesmo da extremidade A)"),
    _p("br_b.length", "m", "Comprimento do BR da outra extremidade (vem da linha 'model.end_b_case_id'; "
       "vazio = o mesmo da extremidade A)"),
    _p("model.end_b_x_free", "-",
       "2 extremidades: MCV B num constraint livre só em X, puxado pela tração alvo (model.end_b_tension_target). "
       "montagem = livre só no ajuste da distância (sem a corcova) e fixo nessa posição no modelo; sempre = livre "
       "também na estática final, nas posições e na dinâmica; nao = ancorado, distância por busca", kind=TEXT, default="montagem"),
    _p("model.end_b_tune_without_friction", "S/N",
       "Ajuste da distância entre os hubs sem atrito com o fundo (só na cópia do ajuste; o modelo final mantém)",
       kind=BOOL, default=1.0),
    _p("model.end_b_pull_length", "m",
       "2 extremidades: distância do MCV B à âncora do tirante que aplica a tração alvo (tirante macio)",
       default=200.0),
    _p("model.two_ends_fine_length", "m",
       "2 extremidades: trecho com discretização fina depois do BR de cada ponta", default=50.0),
    _p("model.two_ends_fine_segment", "m", "2 extremidades: comprimento dos segmentos no trecho fino", default=0.5),
    _p("model.two_ends_coarse_segment", "m", "2 extremidades: comprimento dos segmentos no trecho central",
       default=2.0),
    *[_p(f"mcv_b.{k}", u, f"{d} (MCV da outra extremidade; vem da linha 'model.end_b_case_id')", req=[TWO_ENDS])
      for k, u, d in (("angle", "°", "Ângulo do gooseneck"), ("a", "m", "Cota A"), ("b", "m", "Cota B"),
                      ("c", "m", "Cota C"), ("f", "m", "Cota F"), ("g", "m", "Cota G"),
                      ("mass_air", "t", "Massa no ar"), ("volume", "m³", "Volume"))],
    _p("mcv_b.base_size", "m", "Lado da base desenhada do MCV da outra extremidade", default=0.6, req=[TWO_ENDS]),
    *[_p(f"mcv_b.{k}", u, f"{d} (MCV da outra extremidade; vem da linha 'model.end_b_case_id')")
      for k, u, d in (("d", "m", "Cota D (CoG: z = F − D)"), ("e", "m", "Cota E (CoG: x = G − E)"),
                      ("mass_sub", "t", "Massa submersa"))],
    # ------------------------------------------------------------------ modelo
    _p("model.line_tail_lengths", "m", "Comprimentos das seções após EF+BR+complemento (lista ;)", kind=LIST,
       default=[50.0, 300.0], req=[BUILD]),
    _p("model.line_segments", "-", "Nº de segmentos por seção: EF;BR;compl;seções finais (lista ;)", kind=LIST,
       default=[1.0, 20.0, 1.0, 100.0, 50.0], req=[BUILD]),
    _p("model.tune_end_b", "S/N", "Ajustar o End B X pela estática até a tração no End B atingir o alvo",
       kind=BOOL, default=1.0),
    _p("model.end_b_tension_target", "kN",
       "Tração efetiva alvo no ajuste: no End B (1 extremidade, linha assentada) ou a menor do trecho livre entre "
       "os BRs (2 extremidades, distância entre os hubs)", default=0.5),
    _p("model.final_statics", "S/N",
       "Depois de montar: estática do modelo completo e gravação das posições calculadas como forma inicial",
       kind=BOOL, default=1.0),
    _p("model.prepare_camelback", "S/N",
       "Antes da estática final: masterlink sobre os pontos das lingas e cabo do guincho ajustado (o antigo _02)",
       kind=BOOL, default=1.0),
    _p("model.line_statics_step1", "-", "Método da etapa 1 da estática da linha flexível (Quick, Catenary, ...)",
       kind=TEXT, default="Quick"),
    _p("model.masterlink_position", "-",
       "Masterlink na 1ª estática (fixo): baixo = model.masterlink_height acima do fundo, com X na média dos pontos "
       "das lingas CB1/CB2 na linha + model.masterlink_x_offset; apice = acima dos dois pontos com as lingas no comprimento (até a 0.9.3)",
       kind=TEXT, default="baixo"),
    _p("model.masterlink_height", "m", "Altura do masterlink acima do fundo na 1ª estática (posição 'baixo')",
       default=2.0),
    _p("model.masterlink_x_offset", "m",
       "Posição 'baixo': X do masterlink = média dos pontos das lingas CB1/CB2 na linha + este valor (+X = para "
       "longe do MCV)", default=1.0),
    _p("model.hub_shape_type", "-", "Tipo de shape dos hubs (Drawing = só desenho, sem contato)", kind=TEXT,
       default="Drawing"),
    _p("model.jacobian_perturbation_factor", "-", "General > Jacobian perturbation factor gravado no modelo",
       default=0.0),
    _p("model.camelback_slack", "m",
       "Folga deixada no cabo do guincho no preparo da corcova (lingas levemente frouxas = antes da puxada zero)",
       default=0.1),
    _p("model.end_b_statics_max_iterations", "-", "Limite de iterações da estática durante o ajuste do End B",
       default=2000.0),
    _p("model.catenary_horizontal_tension", "kN", "Só no modo legacy: tração horizontal arbitrária do _01",
       default=0.5, req=[BUILD]),
    # ------------------------------------------------------------------ estática (fase 3)
    _p("statics.mcv_fixed_state", "-", "MCV conectado ao hub: Propriedade=Valor; ... aplicados ao objeto do MCV",
       kind=TEXT, default="Connection=Anchored"),
    _p("statics.mcv_free_state", "-", "MCV livre (pendurado no guindaste): Propriedade=Valor; ...",
       kind=TEXT, default="Connection=Free"),
    _p("statics.length_control", "-",
       "Sem corcova (fase 5): dado que controla o comprimento suspenso. 'auto' = última seção da linha se o "
       "End B estiver na embarcação, senão 1ª seção do cabo do guincho; ou '{PAPEL}.Propriedade[índice]'",
       kind=TEXT, default="auto"),
    _p("statics.angle_variable", "-", "Resultado do MCV usado como ângulo", kind=TEXT, default="Rotation 2"),
    _p("statics.cb_angle", "°", "Ângulo do MCV nas posições CB pull-in (+) e pay-out (−)", default=3.0),
    _p("statics.uf_limit", "-", "UF máximo nas posições de overpull", default=1.0),
    _p("statics.pull_tension_threshold", "kN", "OBSOLETO (v0.6.3): substituído por statics.pull_tension_rise",
       default=0.01),
    _p("statics.height_tolerance", "m", "Horizontal: tolerância da altura do MCV em relação ao hub", default=0.01),
    _p("statics.max_height_iterations", "-", "Horizontal: máximo de ajustes do cabo do guindaste", default=8),
    _p("statics.free_overpull_angle_factor", "-",
       "Free overpull: para também quando o ângulo varia este fator × statics.cb_angle a partir da horizontal",
       default=3.0),
    _p("statics.max_iterations", "-",
       "Limite de iterações da estática em cada avaliação da puxada (as que convergem usam poucas centenas)",
       default=2000),
    _p("statics.robust", "-",
       "Estáticas de convergência difícil: auto (direto com 2 extremidades; com 1, só se falhar), sempre, "
       "repetir (só se falhar) ou nunca. Usa statics.robust_* e volta aos valores do modelo depois",
       kind=TEXT, default="auto"),
    _p("statics.masterlink_fixed_first", "S/N",
       "Com corcova: cada estática das posições roda antes com o masterlink fixo na posição atual, grava as "
       "posições e depois libera o masterlink", kind=BOOL, default=1.0),
    _p("statics.robust_max_iterations", "-", "Estática robusta: máximo de iterações", default=50000.0),
    _p("statics.robust_min_damping", "-", "Estática robusta: amortecimento mínimo (General > Statics)",
       default=1000.0),
    _p("statics.robust_max_damping", "-", "Estática robusta: amortecimento máximo (General > Statics)",
       default=10000.0),
    _p("statics.robust_min_damping_low", "-", "Escada de damping: menor amortecimento mínimo permitido",
       default=10.0),
    _p("statics.robust_min_damping_high", "-", "Escada de damping: maior amortecimento mínimo permitido",
       default=1e4),
    _p("statics.robust_max_damping_low", "-", "Escada de damping: menor amortecimento máximo permitido",
       default=100.0),
    _p("statics.robust_max_damping_high", "-", "Escada de damping: maior amortecimento máximo permitido",
       default=1e4),
    _p("statics.robust_factor", "-",
       "Escada de damping: fator entre níveis (÷ depois de convergir, × quando não converge)", default=10.0),
    _p("statics.robust_step_down", "-",
       "OBSOLETO (0.10.4): depois de convergir, o damping desce ÷ statics.robust_factor até o piso", default=1),
    _p("statics.robust_escalations", "-",
       "OBSOLETO (0.10.4): sem convergência, o damping sobe × statics.robust_factor até o teto", default=1),
    _p("statics.robust_jacobian", "-",
       "Escada de damping: Jacobian perturbation factor usado ao subir o damping (volta ao do modelo no fim)",
       default=1e-3),
    _p("statics.stall_seconds", "s",
       "Estática robusta: cancela e conta como não convergida se o erro não cair à metade neste tempo "
       "(0 = sem limite)", default=120.0),
    _p("statics.pull_tension_rise", "kN",
       "Puxada zero: aumento da tração nas lingas acima da base (cabo frouxo, masterlink pendurado nas lingas)",
       default=0.5),
    _p("statics.reference_slack", "m", "Cabo solto além do modelo montado para medir a tração-base nas lingas",
       default=2.0),
    _p("statics.pull_step", "m", "Passo inicial da busca de puxada (dobra até enquadrar)", default=0.5),
    _p("statics.pull_max", "m", "Maior puxada pesquisada", default=30.0),
    _p("statics.pull_tolerance", "m", "Tolerância da puxada nas buscas", default=0.01),
    _p("statics.max_evaluations", "-", "Máximo de cálculos de estática por busca", default=40.0),
    # ------------------------------------------------------------------ simulação
    _p("simulation.hs_step", "m", "Passo de Hs nas sugestões", legacy=("SimulationData", "HsSensibility"), default=0.25),
    _p("simulation.hs_min", "m", "Hs mínima", legacy=("SimulationData", "MinHs"), default=0.5),
    _p("simulation.hs_max", "m", "Hs máxima", legacy=("SimulationData", "MaxHs"), default=3.5),
    _p("simulation.variable_time_step", "-", "Usar passo variável (S/N)", kind=BOOL,
       legacy=("SimulationData", "useVariableTimeStep"), default=False),
    _p("simulation.time_step", "s", "Passo de tempo (ou máximo, se variável)", legacy=("SimulationData", "TimeStep"), default=0.05),
    _p("simulation.log_interval", "s", "Intervalo de gravação", default=0.05),
    _p("simulation.build_up_periods", "-", "Build-up em múltiplos de Tp", default=3.0),
    _p("simulation.time_before_wave", "s", "Tempo antes da maior onda (simulação = 2x)", default=300.0),
    _p("simulation.wave_search_duration", "s", "Duração da busca da maior onda", default=10800.0),
    _p("simulation.wave_preview_x", "m", "X do ponto de busca da onda", default=38.18589630127008),
    _p("simulation.wave_preview_y", "m", "Y do ponto de busca da onda", default=-54.064498985086054),
    _p("simulation.regular_height_factor", "-", "Hmax/Hs nas ondas regulares", default=1.9),
    _p("window.suggest_method", "-", "Sugestão dos próximos Hs: adaptive (busca do limite pelo UF) ou steps "
       "(passos a partir do último aprovado, como o _13)", kind=TEXT, default="adaptive"),
    _p("window.per_pair", "-", "Hs sugeridos por (Tp, direção) em cada rodada da busca adaptativa", default=2.0),
    # ------------------------------------------------------------------ triagem / janela prevista (fase 6)
    _p("screening.tps", "s", "Tps da triagem semi-dinâmica e da resposta da embarcação (lista ;)", kind=LIST,
       default=[5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0]),
    _p("screening.amplitudes", "m", "Amplitudes de pay-out da triagem semi-dinâmica (lista ;)", kind=LIST,
       default=[round(0.2 + 0.1 * i, 2) for i in range(14)]),
    _p("screening.time_step", "s", "Passo de tempo da triagem semi-dinâmica", default=0.01),
    _p("screening.log_interval", "s", "Intervalo de gravação da triagem semi-dinâmica", default=0.01),
    _p("screening.response_point", "m", "Ponto da embarcação (x;y;z) para a resposta vertical (guincho/guindaste)",
       kind=LIST, default=[56.0, 0.0, 13.0]),
    _p("screening.directions", "°", "Direções relativas da resposta da embarcação (lista ;)", kind=LIST,
       default=[0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0, 120.0, 135.0, 150.0, 165.0, 180.0]),
    _p("screening.response_hs", "m", "Hs usadas no cálculo da resposta da embarcação (lista ;)", kind=LIST,
       default=[1.0, 2.0, 3.0]),
]

BY_KEY: dict[str, Param] = {p.key: p for p in PARAMS}
BY_COLUMN: dict[str, Param] = {p.input_column: p for p in PARAMS if p.input_column}
BY_LEGACY: dict[tuple[str, str], Param] = {p.legacy: p for p in PARAMS if p.legacy}

# Colunas de identificação da Input Sheet (não são parâmetros físicos)
META_COLUMNS = {
    "Item": "item",
    "Well": "well",
    "Line Description": "line_description",
    "Inputs sheet": "inputs_sheet",
    "Operation Type": "operation_type",
    "Location": "location",
    "ID": "id",
    "Folder": "folder",
    "Line Structure": "line_structure",  # só informativo (referência da estrutura da linha)
}


def get(key: str) -> Param:
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(f"Parâmetro desconhecido: {key!r}") from None


def required_keys(groups: set[str]) -> list[str]:
    return [p.key for p in PARAMS if p.required & groups]
