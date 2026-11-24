"""Orquestração das etapas por caso (usada pela linha de comando)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .inputs import resolve_case, slug
from .io.config import load_config
from .io.legacy import case_to_legacy_json
from .io.snapshot import write_snapshot
from .io.workbook import read_input_sheet
from .project import Project
from .log import log, log_to_file, step
from .schema import CaseInputs


def load_case(project: Project, case_id: str) -> CaseInputs:
    with step(f"Lendo planilha geral ({project.workbook.name})"):
        sheet = read_input_sheet(project.workbook)
    with step(f"Lendo configuração ({project.config.name})"):
        cfg = load_config(project.config)
    with step("Lendo biblioteca de curvas"):
        curves = project.curve_library()
        if curves is not None:
            log.info("  %d curva(s) em %s", len(curves.curves), ", ".join(p.name for p in curves.files))
            for i in curves.issues:
                log.warning("  curva %s: %s", i.curve_id, i.message)
    with step(f"Resolvendo o caso '{case_id}'"):
        case = resolve_case(sheet, cfg, case_id, project.project_code, curves)
    log.info("  Operação: %s | template '%s' | nome %s", case.operation.operation_type, case.operation.template,
             case.operation_name)
    for name, value, source in case.premises():
        log.info("  Premissa: %s = %s [%s]", name, value, source)
    for key, msg in case.meta.get("curve_errors", {}).items():
        log.warning("  %s: %s", key, msg)
    return case


def case_output_dir(project: Project, case: CaseInputs, out_dir: Optional[str]) -> Path:
    if out_dir:
        return Path(out_dir).resolve()
    found = project.case_dir(case.meta.get("well", ""), case.meta.get("folder", ""))
    if found is not None:
        return found
    if project.analysis_root:
        raise FileNotFoundError(
            f"Não achei a pasta do caso (poço {case.meta.get('well')!r}, pasta {case.meta.get('folder')!r}) "
            f"em analysis_root={project.analysis_root}. Use --out.")
    # sem analysis_root no cvd_project.json: pasta de trabalho <projeto>/casos/<ID>
    from .inputs import slug

    root = project.path.parent if project.path else Path.cwd()
    target = (root / "casos" / slug(case.case_id)).resolve()
    log.info("analysis_root não definido no cvd_project.json: usando %s", target)
    return target


def default_case_dir(project: Project, case_id: str) -> Path:
    """Pasta padrão do caso sem carregar a planilha (só quando não há analysis_root)."""
    from .inputs import slug

    root = project.path.parent if project.path else Path.cwd()
    return (root / "casos" / slug(case_id)).resolve()


def snapshot_case(project: Project, case_id: str, out_dir: Optional[str] = None,
                  legacy_json: bool = False) -> dict:
    case = load_case(project, case_id)
    target = case_output_dir(project, case, out_dir)
    paths = {"case_json": write_snapshot(case, target, project.workbook, project.config)}
    if legacy_json:
        p = target / "__INPUTS.json"
        if p.exists():
            raise FileExistsError(f"{p} já existe; não sobrescrevo o JSON antigo.")
        p.write_text(json.dumps(case_to_legacy_json(case), indent=4, ensure_ascii=False), encoding="utf-8")
        paths["legacy_json"] = p
    return paths


def build_case(project: Project, case_id: str, out_dir: Optional[str] = None, catenary: str = "fixed",
               overwrite: bool = False, checklist: bool = True, legacy_json: bool = False) -> dict:
    target = Path(out_dir).resolve() if out_dir else None
    if target is None and not project.analysis_root:
        target = default_case_dir(project, case_id)   # casos/<ID>, com log em arquivo também
        out_dir = str(target)
    if target is None:
        return _build_case(project, case_id, out_dir, catenary, overwrite, checklist, legacy_json)
    with log_to_file(target / "cvd_build.log"):
        return _build_case(project, case_id, out_dir, catenary, overwrite, checklist, legacy_json)


def _build_case(project: Project, case_id: str, out_dir, catenary, overwrite, checklist, legacy_json) -> dict:
    from .model.builder import build_model

    case = load_case(project, case_id)
    two_ends = case.modelled_ends() == 2
    base_name = (case.operation.base_model_two_ends or case.operation.base_model) if two_ends \
        else case.operation.base_model
    if not base_name:
        raise ValueError(f"Sem 'Modelo base' para '{case.operation.operation_type}' na aba Operações.")
    base = project.base_model(base_name)
    target = case_output_dir(project, case, out_dir)
    model_path = target / f"{case.operation_name}.dat"
    if model_path.exists() and not overwrite:
        raise FileExistsError(f"{model_path} já existe (use --overwrite)")
    log.info("Modelo base: %s", base)
    log.info("Saída: %s", model_path)

    result = build_model(case, base, model_path, catenary=catenary)
    if two_ends:
        from .values import is_number

        if is_number(case.get("model.end_distance")):
            result.notes.append(f"Distância entre os hubs fixada em model.end_distance = "
                                f"{case.num('model.end_distance'):g} m (sem ajuste pela estática)")
        elif case.flag("model.tune_end_b") is not False:
            from .model.twoends import tune_end_distance

            with step("Ajustando a distância entre os hubs pela estática"):
                tuned = tune_end_distance(case, model_path)
            result.catenary.update({f"tuned_{k}": v for k, v in tuned.items()})
            if tuned["status"] == "OK":
                result.catenary["end_distance"] = tuned["end_distance"]
                result.catenary["end_distance_source"] = "ajustada pela estática"
                how = " (MCV B livre em X, 1 estática)" if tuned.get("x_free") else ""
                result.notes.append(
                    f"Distância entre os hubs ajustada pela estática{how}: {tuned['end_distance_start']:.3f} -> "
                    f"{tuned['end_distance']:.3f} m (menor tração entre os BRs {tuned['end_tension']:.3f} kN, alvo "
                    f"{tuned['end_tension_target']:g} kN)")
            else:
                result.notes.append(f"AVISO: ajuste da distância entre os hubs falhou ({tuned.get('message')}); "
                                    f"mantida a estimativa {tuned['end_distance_start']:.3f} m")
    elif catenary == "fixed" and case.flag("model.tune_end_b") is not False:
        from .model.endb import tune_end_b

        with step("Ajustando End B X pela estática"):
            tuned = tune_end_b(case, model_path)
        result.catenary.update({f"tuned_{k}": v for k, v in tuned.items()})
        if tuned["status"] == "OK":
            result.catenary["end_b_x"] = tuned["end_b_x"]
            result.notes.append(f"End B X ajustado pela estática: {tuned['end_b_x_start']:.3f} -> {tuned['end_b_x']:.3f} m "
                                f"(tração no End B {tuned['end_b_tension']:.3f} kN, alvo {tuned['end_b_tension_target']:g} kN)")
        else:
            result.notes.append(f"AVISO: ajuste do End B pela estática falhou ({tuned.get('message')}); mantida a "
                                f"estimativa geométrica {tuned['end_b_x_start']:.3f} m")
    if case.operation.camelback and case.flag("model.prepare_camelback") is not False \
            and case.flag("model.final_statics") is not False:
        from .model.camelback import prepare_camelback

        with step("Preparando a corcova (masterlink e cabo do guincho)"):
            prep = prepare_camelback(case, model_path)
        result.catenary["camelback"] = prep
        if prep["status"] in ("OK", "PARCIAL"):
            result.notes.append(
                f"Corcova preparada: masterlink em ({prep['masterlink'][0]:.2f}, {prep['masterlink'][2]:.2f}) "
                f"[{prep.get('masterlink_mode', 'apice')}]; cabo do "
                f"guincho {prep['winch_main_length_before']:.2f} -> {prep['winch_main_length']:.2f} m; na posição "
                f"antiga as lingas estariam esticadas {prep['sling_stretch_before'][0]:.2f} m / "
                f"{prep['sling_stretch_before'][1]:.2f} m")
            if prep["status"] == "PARCIAL":
                result.notes.append(f"AVISO: preparo da corcova parcial ({prep.get('message')})")
        else:
            result.notes.append(f"AVISO: preparo da corcova não feito ({prep.get('message')})")
    if case.flag("model.final_statics") is not False:
        from .model.endb import finalize_statics

        with step("Estática final do modelo completo"):
            fin = finalize_statics(case, model_path)
        result.catenary["final_statics"] = fin["status"]
        result.catenary["final_statics_stages"] = fin.get("stages", [])
        if fin["status"] == "OK":
            result.notes.append("Estática final convergiu; posições calculadas gravadas como forma inicial das linhas")
        elif fin["status"] == "PARCIAL":
            result.notes.append(f"AVISO: estática final convergiu só com o masterlink fixo ({fin['message']}); "
                                f"posições dessa etapa gravadas. Rode 'cvd diagnose' para isolar a causa")
        else:
            result.notes.append(f"AVISO: estática final do modelo completo não convergiu ({fin['message']}); o modelo "
                                f"foi salvo sem posições calculadas e pode não convergir ao abrir. Rode 'cvd diagnose'")
    out = {"model": result.model_path, "catenary": result.catenary, "notes": result.notes}
    out["case_json"] = write_snapshot(case, target, project.workbook, project.config,
                                      extra={"build": {**result.catenary, "base_model": str(base),
                                                       "notes": result.notes}})
    if legacy_json:
        p = target / "__INPUTS.json"
        if not p.exists():
            p.write_text(json.dumps(case_to_legacy_json(case), indent=4, ensure_ascii=False), encoding="utf-8")
            out["legacy_json"] = p
    if checklist:
        from .model.checklist import build_checklist

        with step("Gerando checklist"):
            path, results = build_checklist(project.workbook, project.config, case_id, model_path, base,
                                            target / f"Checklist_{slug(case_id)}.xlsx", project.project_code,
                                            project.curves)
        out["checklist"] = path
        out["checklist_status"] = _count(results)
    return out


def _count(results) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
