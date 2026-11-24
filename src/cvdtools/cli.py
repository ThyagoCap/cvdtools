"""Linha de comando ``cvd``.

Configuração do projeto (uma vez):
    cvd project init --code 1327 --workbook ..\\1327.planilha_geral_offline.xlsx --config cvd_config.xlsx ...
    cvd config init --out cvd_config.xlsx [--from-json ..\\__INPUTS.json]

Fase 1 (inputs):
    cvd inputs list
    cvd inputs validate [--id ID] [--out relatorio.xlsx]
    cvd inputs show ID
    cvd inputs diff-legacy ID --json ..\\__INPUTS.json
    cvd snapshot ID [--out pasta] [--legacy-json]

Fase 2 (núcleo):
    cvd build ID [--out pasta] [--catenary fixed|legacy] [--overwrite] [--no-checklist] [--legacy-json]
    cvd build-legacy --json ..\\__INPUTS.json --base ..\\00.Base\\base_input.dat --out modelo.dat
    cvd checklist ID --model caso.dat [--base base.dat] [--out Checklist.xlsx]
    cvd seastates ID --base-dyn modelo_dyn.dat --out pasta [--regular] [--overwrite]
    cvd post --sims pasta --out resultados.xlsx (--id ID | --json __INPUTS.json) [--workers N]

Fase 0 (regressão):
    cvd baseline capture | verify ;  cvd compare-models A B
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


# --------------------------------------------------------------------------- auxiliares

def _project(args):
    from .project import load_project

    return load_project(Path(args.project) if args.project else None, args.workbook, args.config)


def _print_table(rows: list[dict], columns: list[str]) -> None:
    widths = {c: max([len(c)] + [len(str(r.get(c, ""))) for r in rows]) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def _counts(results) -> dict:
    out: dict[str, int] = {}
    for r in results:
        out[r.status] = out.get(r.status, 0) + 1
    return out


# --------------------------------------------------------------------------- projeto / config

def _cmd_setup(args) -> int:
    from .setup_project import run_setup

    return run_setup(args.folder, args.workbook_path, args.code, vscode=not args.no_vscode,
                     config_from=args.config_from)


def _cmd_update(args) -> int:
    from .setup_project import update

    return update(args.folder, args.ref, setup=not args.no_setup)


def _cmd_project_init(args) -> int:
    from .project import write_project_file

    p = write_project_file(Path.cwd(), args.code, args.workbook_path, args.config_path,
                           args.analysis_root, args.base_dir, args.curves)
    print(f"Criado {p}")
    return 0


def _cmd_config_init(args) -> int:
    from .io.config import default_config, write_config

    legacy = json.loads(Path(args.from_json).read_text(encoding="utf-8")) if args.from_json else None
    p = write_config(default_config(legacy), Path(args.out))
    extra = f" (valores, PostProc e Critérios copiados de {args.from_json})" if legacy else ""
    print(f"Configuração criada em {p}{extra}")
    return 0


def _cmd_config_upgrade(args) -> int:
    from .io.config import upgrade_config

    sets = {}
    for item in args.set or []:
        if "=" not in item:
            raise ValueError(f"--set espera chave=valor (recebido {item!r})")
        k, v = item.split("=", 1)
        sets[k.strip()] = v.strip()
    path = Path(args.config) if args.config else _project(args).config
    changes = upgrade_config(path, sets)
    if not changes:
        print("Configuração já está atualizada.")
    for c in changes:
        print(c)
    return 0


# --------------------------------------------------------------------------- curvas

def _cmd_curves_init(args) -> int:
    from .curves import curve_from_sheet, write_curve_template

    curves = []
    if args.from_sheet:
        src = Path(args.source_workbook) if args.source_workbook else _project(args).workbook
        c = curve_from_sheet(src, args.from_sheet, args.id, x_col=args.x_col, y_col=args.y_col,
                             x_unit=args.x_unit, y_unit=args.y_unit, kind=args.kind, first_row=args.first_row,
                             source=f"{src.name} / aba '{args.from_sheet}'", load_case=args.load_case or "")
        curves.append(c)
    p = write_curve_template(Path(args.out), curves)
    print(f"Planilha de curvas criada em {p} ({len(curves)} curva(s)).")
    if curves:
        print("Confira Load case, Fonte e preencha Aliases (textos da Input Sheet que apontam para a curva).")
    return 0


def _cmd_curves_check(args) -> int:
    proj = _project(args)
    lib = proj.curve_library()
    if lib is None:
        print("O projeto não declara planilhas de curvas (campo 'curves' do cvd_project.json).")
        return 1
    rows = [{"ID": c.id, "Tipo": c.kind, "Load case": c.load_case, "Pontos": len(c.x),
             "Unid.": f"{c.x_unit} / {c.y_unit}", "Histerese": "S" if c.hysteretic else "N",
             "No OrcaFlex": c.name, "Aliases": "; ".join(c.aliases), "Arquivo": c.file}
            for c in lib.curves.values()]
    _print_table(rows, ["ID", "Tipo", "Load case", "Pontos", "Unid.", "Histerese", "No OrcaFlex", "Aliases", "Arquivo"])
    for i in lib.issues:
        print(f"{i.level}  {i.curve_id}: {i.message}")
    return 1 if any(i.level == "ERRO" for i in lib.issues) else 0


# --------------------------------------------------------------------------- fase 1

def _cmd_inputs_list(args) -> int:
    from .io.workbook import read_input_sheet

    sheet = read_input_sheet(_project(args).workbook)
    _print_table([{"Linha": r.excel_row, "ID": r.id, "Operation Type": r.operation_type,
                   "Poço": r.meta.get("well"), "Pasta": r.meta.get("folder")} for r in sheet.rows],
                 ["Linha", "ID", "Operation Type", "Poço", "Pasta"])
    return 0


def _cmd_inputs_validate(args) -> int:
    from .io.config import load_config
    from .io.workbook import read_input_sheet
    from .validation import ERROR, summarize, validate_case, validate_sheet, write_report

    proj = _project(args)
    sheet, cfg, curves = read_input_sheet(proj.workbook), load_config(proj.config), proj.curve_library()
    issues = validate_case(sheet, cfg, args.id, curves) if args.id else validate_sheet(sheet, cfg, curves)
    shown = [i for i in issues if args.all or i.level != "INFO"]
    _print_table([{"Nível": i.level, "ID": i.case_id, "Linha": i.excel_row, "Campo": i.field,
                   "Mensagem": i.message} for i in shown], ["Nível", "ID", "Linha", "Campo", "Mensagem"])
    print("\n" + summarize(issues))
    if args.out:
        print(f"Relatório: {write_report(issues, Path(args.out))}")
    return 1 if any(i.level == ERROR for i in issues) else 0


def _cmd_inputs_show(args) -> int:
    from . import params as P
    from .pipeline import load_case
    from .values import to_jsonable

    case = load_case(_project(args), args.id)
    print(f"{case.case_id}  |  {case.operation.operation_type}  |  template '{case.operation.template}'  |  "
          f"operation_name {case.operation_name}\n")
    print("Premissas de modelagem:")
    _print_table([{"Premissa": n, "Valor": v, "Fonte": src} for n, v, src in case.premises()],
                 ["Premissa", "Valor", "Fonte"])
    print()
    _print_table([{"Parâmetro": k, "Valor": to_jsonable(rv.value), "Unid.": P.get(k).unit, "Fonte": rv.source,
                   "Input Sheet": to_jsonable(rv.raw_input) if rv.raw_input is not None else ""}
                  for k, rv in case.values.items()], ["Parâmetro", "Valor", "Unid.", "Fonte", "Input Sheet"])
    print("\nCritérios:")
    _print_table([{"Critério": c.name, "Limite": to_jsonable(c.limit), "Origem": c.limit_origin,
                   "Objeto": c.obj, "Variável": c.variable} for c in case.criteria],
                 ["Critério", "Limite", "Origem", "Objeto", "Variável"])
    return 0


def _cmd_inputs_diff(args) -> int:
    from .io.legacy import diff_case_vs_legacy
    from .pipeline import load_case

    case = load_case(_project(args), args.id)
    rows = diff_case_vs_legacy(case, json.loads(Path(args.json).read_text(encoding="utf-8")))
    if not rows:
        print("Sem diferenças nos parâmetros de dados.")
        return 0
    _print_table(rows, ["parametro", "json_antigo", "planilha", "fonte", "unidade"])
    print(f"\n{len(rows)} parâmetro(s) diferente(s).")
    return 0


def _cmd_snapshot(args) -> int:
    from .pipeline import snapshot_case

    for k, v in snapshot_case(_project(args), args.id, args.out, args.legacy_json).items():
        print(f"{k}: {v}")
    return 0


# --------------------------------------------------------------------------- fase 2

def _geometry_line(cat: dict) -> str:
    head = f"Geometria ({cat.get('mode')}): declinação End A {cat.get('end_a_declination', float('nan')):.1f}°"
    if cat.get("two_ends"):
        return (f"{head}, duas extremidades: distância entre os hubs {cat.get('end_distance', float('nan')):.3f} m "
                f"({cat.get('end_distance_source', '?')}; estimativa {cat.get('end_distance_estimate', float('nan')):.3f} m)")
    return (f"{head}, trecho suspenso {cat.get('suspended_length', float('nan')):.3f} m, "
            f"End B X = {cat.get('end_b_x', float('nan')):.3f} m")


def _cmd_build(args) -> int:
    from .pipeline import build_case

    out = build_case(_project(args), args.id, args.out, args.catenary, args.overwrite,
                     not args.no_checklist, args.legacy_json)
    print(f"Modelo: {out['model']}")
    print(f"Snapshot: {out['case_json']}")
    print(_geometry_line(out["catenary"]))
    for n in out["notes"]:
        print(f"Nota: {n}")
    if "checklist" in out:
        print(f"Checklist: {out['checklist']}  {out['checklist_status']}")
    return 0


def _cmd_diagnose(args) -> int:
    from .log import log_to_file
    from .model.diagnose import diagnose
    from .pipeline import case_output_dir, load_case

    proj = _project(args)
    case = load_case(proj, args.id)
    model = Path(args.model) if args.model else case_output_dir(proj, case, None) / f"{case.operation_name}.dat"
    out = Path(args.out) if args.out else model.parent
    with log_to_file(out / "cvd_diagnose.log"):
        res = diagnose(case, model, out, [s.strip() for s in args.only.split(",")] if args.only else None,
                       args.max_iterations)
    _print_table([{"Variante": r.name, "Status": r.status, "Tempo (s)": f"{r.seconds:.1f}",
                   "Mudança": r.extra.get("change", ""), "Mensagem": r.message[:90]} for r in res["results"]],
                 ["Variante", "Status", "Tempo (s)", "Mudança", "Mensagem"])
    print()
    for line in res["verdict"]:
        print(f">> {line}")
    print(f"\nDetalhes em {res['csv']}")
    return 0


def _cmd_build_legacy(args) -> int:
    from .io.legacy import case_from_legacy_json
    from .model.builder import build_model

    case = case_from_legacy_json(json.loads(Path(args.json).read_text(encoding="utf-8")))
    res = build_model(case, Path(args.base), Path(args.out), catenary=args.catenary)
    print(f"Modelo: {res.model_path}  (catenária {args.catenary}, End B X = {res.catenary['end_b_x']:.4f} m)")
    return 0


def _cmd_checklist(args) -> int:
    from .inputs import slug
    from .model.checklist import build_checklist
    from .pipeline import load_case

    proj = _project(args)
    case = load_case(proj, args.id)
    base = Path(args.base) if args.base else proj.base_model(case.operation.base_model)
    model = Path(args.model)
    out = Path(args.out) if args.out else model.parent / f"Checklist_{slug(args.id)}.xlsx"
    path, results = build_checklist(proj.workbook, proj.config, args.id, model, base, out, proj.project_code,
                                    proj.curves)
    print(f"Checklist: {path}  {_counts(results)}")
    return 0


def _cmd_seastates(args) -> int:
    from .dynamics.seastates import generate_irregular, generate_regular
    from .io.config import load_config
    from .pipeline import load_case

    from .log import log_to_file

    proj = _project(args)
    with log_to_file(Path(args.out) / "cvd_seastates.log"):
        case = load_case(proj, args.id)
        grid = load_config(proj.config).sea_grid
        if args.regular:
            files = generate_regular(case, Path(args.base_dyn), grid, Path(args.out), overwrite=args.overwrite)
        else:
            files = generate_irregular(case, Path(args.base_dyn), grid, Path(args.out), overwrite=args.overwrite,
                                       workers=args.workers)
        print(f"{len(files)} arquivo(s) em {args.out} (manifest.csv e batch_list.txt atualizados)")
        if args.run:
            from .dynamics.run import run_simulations

            res = run_simulations([Path(args.out) / f.file for f in files], args.workers, args.threads,
                                  args.overwrite, Path(args.out) / "run_log.csv")
            print(f"Simulações: {sum(r.status == 'OK' for r in res)} OK, {sum(r.status == 'FALHOU' for r in res)} "
                  f"falharam (run_log.csv)")
    return 0


def _cmd_dynbase(args) -> int:
    from .dynamics.dynbase import make_dynamic_base
    from .log import log_to_file
    from .pipeline import case_output_dir, load_case

    proj = _project(args)
    case = load_case(proj, args.id)
    case_dir = case_output_dir(proj, case, args.out)
    with log_to_file(case_dir / "cvd_dynbase.log"):
        dyn = make_dynamic_base(case, case_dir, case.operation_name, Path(args.source) if args.source else None,
                                args.position, check_statics=not args.no_check)
    print(f"Modelo dinâmico base: {dyn}")
    if not args.seastates:
        return 0
    args.base_dyn, args.out = str(dyn), str(case_dir / "02.Dynamic")
    return _cmd_seastates(args)


def _cmd_run(args) -> int:
    from .dynamics.run import collect, run_simulations
    from .log import log_to_file

    out = Path(args.dir) if args.dir else Path(args.list).parent
    with log_to_file(out / "cvd_run.log"):
        files = collect(Path(args.dir) if args.dir else None, args.pattern, Path(args.list) if args.list else None)
        res = run_simulations(files, args.workers, args.threads, args.overwrite, out / "run_log.csv")
    _print_table([{"Arquivo": r.file, "Status": r.status, "Tempo (s)": f"{r.seconds:.0f}", "Obs.": r.message}
                  for r in res if r.status != "OK"], ["Arquivo", "Status", "Tempo (s)", "Obs."])
    print(f"\n{sum(r.status == 'OK' for r in res)} OK, {sum(r.status == 'FALHOU' for r in res)} falharam, "
          f"{sum(r.status == 'PULADO' for r in res)} puladas. Detalhes em {out / 'run_log.csv'}")
    return 0 if all(r.status != "FALHOU" for r in res) else 1


def _hs_range(args, case) -> tuple[float, float, float]:
    """Faixa de Hs das sugestões: argumentos da linha de comando, senão simulation.hs_* do caso."""
    out = []
    for arg, key, default in (("hs_min", "simulation.hs_min", 0.25), ("hs_max", "simulation.hs_max", 2.5),
                              ("hs_step", "simulation.hs_step", 0.25)):
        v = getattr(args, arg, None)
        if v is None:
            try:
                v = case.num(key)
            except Exception:
                v = default
        out.append(float(v))
    return out[0], out[1], out[2]


def _suggest(args, case, st, pairs=None):
    """Próximos Hs: busca adaptativa (padrão) ou passos fixos (como o _13). Devolve (sugestões, tabela da busca)."""
    from .post import window as W

    method = getattr(args, "method", None)
    if not method:
        try:
            method = str(case.get("window.suggest_method")).strip().lower()
        except Exception:
            method = "adaptive"
        method = method if method in ("adaptive", "steps") else "adaptive"
    hs_min, hs_max, hs_step = _hs_range(args, case)
    if method == "steps":
        return W.suggest(st, hs_min, hs_max, hs_step, args.steps) if st is not None else [], None
    from .post import adaptive as A

    per_pair = getattr(args, "per_pair", None)
    if per_pair is None:
        try:
            per_pair = int(case.num("window.per_pair"))
        except Exception:
            per_pair = 2
    seeds = None
    if getattr(args, "seed", None):
        from .screening.compare import read_prediction

        seeds = read_prediction(Path(args.seed))
    uf_limit = float(st.attrs["uf_limit"]) if st is not None else float(
        args.uf_limit if getattr(args, "uf_limit", None) is not None else case.num("statics.uf_limit"))
    searches = A.plan(st, hs_min, hs_max, hs_step, uf_limit, seeds=seeds, pairs=pairs, per_pair=per_pair)
    return A.suggestions(searches), A.search_table(searches)


def _mares_pairs(args):
    if not getattr(args, "mares", False):
        return None
    from .io.config import load_config

    return [(float(tp), float(d)) for d, tp, _ in load_config(_project(args).config).sea_grid]


def _print_search(search) -> None:
    if search is None or "Situação" not in search.columns:
        return
    counts = search["Situação"].value_counts().to_dict()
    print("Busca adaptativa: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + " (aba Busca)")


def _search_args(p) -> None:
    p.add_argument("--method", choices=["adaptive", "steps"],
                   help="Sugestão dos próximos Hs (padrão: window.suggest_method do caso = adaptive)")
    p.add_argument("--per-pair", type=int, help="Hs por (Tp, direção) e rodada na busca adaptativa (padrão: window.per_pair = 2)")
    p.add_argument("--seed", metavar="PREVISAO_XLSX",
                   help="Previsão (aba Previsão de 'screen fd' ou 'screen predict') para os pares ainda sem UF")
    p.add_argument("--mares", action="store_true", help="Inclui todos os (Tp, direção) da aba Mares, mesmo sem simulação")


def _cmd_window(args) -> int:
    from .pipeline import load_case
    from .post import window as W

    proj = _project(args)
    case = load_case(proj, args.id)
    if not args.results:
        if not (args.seed or args.mares):
            print("Informe --results (planilha do 'cvd post') ou, para começar a busca sem simulações, --seed e/ou --mares.")
            return 1
        sugg, search = _suggest(args, case, None, _mares_pairs(args))
        out = Path(args.out) if args.out else (Path(args.seed).with_name(Path(args.seed).stem + "_busca.xlsx")
                                               if args.seed else Path("busca_inicial.xlsx"))
        import pandas as pd

        with pd.ExcelWriter(out, engine="openpyxl") as w:
            pd.DataFrame([{"Tp (s)": t, "Hs (m)": h, "Dir (°)": d} for t, h, d in sugg]
                         or [{"Tp (s)": None}]).to_excel(w, sheet_name="Sugestão", index=False)
            if search is not None:
                search.to_excel(w, sheet_name="Busca", index=False)
        print(f"{len(sugg)} estado(s) de mar para a 1ª rodada (abas Sugestão e Busca em {out})")
        _generate_suggested(args, case, sugg, out.parent)
        return 0
    df = W.read_results(Path(args.results), args.sheet)
    st = W.evaluate(df, case, args.uf_limit)
    for m in st.attrs.get("missing_criteria", []):
        print(f"AVISO critério fora do Status: {m}")
    limits = W.window_limits(st)
    sugg, search = _suggest(args, case, st, _mares_pairs(args))
    out = Path(args.out) if args.out else Path(args.results).with_suffix(".xlsx")
    W.write_window(out, st, limits, sugg, case, args.title, search)
    _print_search(search)
    n_ok = int((st["Status"] == W.APPROVED).sum())
    print(f"{len(st)} simulação(ões): {n_ok} aprovada(s), {len(st) - n_ok} reprovada(s) (UF limite {st.attrs['uf_limit']:g})")
    print(W.window_grid(limits).fillna("").to_string())
    for lim in limits:
        if lim.note:
            print(f"AVISO Tp {lim.tp:g} s, dir {lim.direction:g}°: {lim.note}")
    print(f"\n{len(sugg)} estado(s) de mar sugerido(s) (aba Sugestão). Janela gravada em {out}")
    _generate_suggested(args, case, sugg, Path(args.results).parent)
    if args.clean:
        names = W.cleanup_candidates(st)
        dest = Path(args.unused) if args.unused else Path(args.clean).parent / "Unused_Sims"
        moved = W.move_unused(names, Path(args.clean), dest, args.apply)
        verb = "movido(s)" if args.apply else "seriam movido(s) (use --apply)"
        print(f"Limpeza: {len(names)} simulação(ões) não definem o limite; {len(moved)} arquivo(s) {verb} para {dest}")
    return 0


def _generate_suggested(args, case, sugg, default_parent: Path) -> None:
    if not args.generate:
        return
    if not sugg:
        print("Nada a gerar: nenhum estado de mar sugerido.")
        return
    from .dynamics.seastates import generate_irregular, irregular_name
    from .post import window as W

    gen_out = Path(args.gen_out or default_parent / "Iterative_Irreg")
    exists = [(t, h, d) for t, h, d in sugg if (gen_out / f"{irregular_name(case.operation_name, t, h, d)}.dat").exists()]
    if exists:
        print(f"AVISO {len(exists)} sugerido(s) já têm .dat em {gen_out} (não rodou ou falhou?): não regerados. "
              + ", ".join(f"Tp {t:g}/Hs {h:g}/dir {d:g}" for t, h, d in exists))
    sugg = [x for x in sugg if x not in exists]
    if not sugg:
        return
    files = generate_irregular(case, Path(args.generate), W.suggestion_grid(sugg), gen_out)
    print(f"{len(files)} .dat gerado(s) em {gen_out} (batch_list.txt atualizado)")


def _cmd_screen_generate(args) -> int:
    from .pipeline import load_case
    from .screening.semidynamic import generate

    case = load_case(_project(args), args.id)
    files = generate(case, Path(args.base_dyn), Path(args.out), overwrite=args.overwrite)
    print(f"{len(files)} arquivo(s) semi-dinâmico(s) em {args.out}. Rode no batch e depois 'cvd post' + "
          f"'cvd screen critical'.")
    return 0


def _cmd_screen_critical(args) -> int:
    from .pipeline import load_case
    from .post.window import read_results
    from .screening.semidynamic import critical_amplitudes, evaluate, write_critical

    case = load_case(_project(args), args.id)
    st = evaluate(read_results(Path(args.results), args.sheet), case, args.uf_limit)
    for m in st.attrs.get("missing_criteria", []):
        print(f"AVISO critério fora do UF: {m}")
    crits = critical_amplitudes(st, st.attrs["uf_limit"])
    path = write_critical(Path(args.out), crits)
    _print_table([{"Tp (s)": f"{c.tp:g}", "A* (m)": "-" if c.amplitude is None else f"{c.amplitude:.3f}",
                   "Status": c.status, "Governa": c.governing,
                   "A* 2º grau": "-" if c.poly2_amplitude is None else f"{c.poly2_amplitude:.3f}",
                   "R²": "-" if c.poly2_r2 is None else f"{c.poly2_r2:.3f}"} for c in crits],
                 ["Tp (s)", "A* (m)", "Status", "Governa", "A* 2º grau", "R²"])
    print(f"\nAmplitudes críticas em {path}")
    return 0


def _cmd_screen_response(args) -> int:
    from .pipeline import load_case
    from .screening.vessel import run

    case = load_case(_project(args), args.id)
    print(f"Resposta da embarcação em {run(case, Path(args.base_dyn), Path(args.out))}")
    return 0


def _cmd_screen_predict(args) -> int:
    from .pipeline import load_case
    from .screening import predict as P
    from .screening.semidynamic import read_critical
    from .screening.vessel import read_max_heave, response_per_hs

    case = load_case(_project(args), args.id)
    crits = read_critical(Path(args.critical))
    resp = response_per_hs(read_max_heave(Path(args.response)))
    step, hs_min, hs_max = case.num("simulation.hs_step"), case.num("simulation.hs_min"), case.num("simulation.hs_max")
    pred = P.predict(crits, resp, step, hs_max)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    P.write_prediction(out, pred, crits, args.title or case.case_id, case.operation.operation_type)
    grid = P.seed_grid(pred, step, hs_min, hs_max, args.around)
    n = sum(len(h) for *_, h in grid)
    print(f"Janela prevista em {out}. Grade sugerida para as irregulares: {n} estado(s) de mar "
          f"(Hs prevista ± {args.around} passo(s)).")
    if args.generate:
        from .dynamics.seastates import generate_irregular
        files = generate_irregular(case, Path(args.generate), grid, Path(args.gen_out or out.parent / "Irregular"))
        print(f"{len(files)} .dat gerado(s)")
    return 0


def _parse_floats(text: str) -> list[float]:
    return [float(x) for x in str(text).replace(",", ";").split(";") if x.strip()]


def _cmd_screen_fd(args) -> int:
    from .io.config import load_config
    from .log import log_to_file
    from .pipeline import load_case
    from .screening.frequency import run_fd_screening, write_fd

    proj = _project(args)
    case = load_case(proj, args.id)
    out = Path(args.out)
    grid = load_config(proj.config).sea_grid           # mesma grade (aba Mares) das irregulares
    if args.tps:
        keep = set(_parse_floats(args.tps))
        grid = [g for g in grid if float(g[1]) in keep]
    if args.dirs:
        keep = set(_parse_floats(args.dirs))
        grid = [g for g in grid if float(g[0]) in keep]
    if not grid:
        print("Nenhum (direção, Tp) na aba Mares com os filtros dados.")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    from .screening.frequency import HystereticFDError

    n_hs = sum(len(h) for *_, h in grid)
    print(f"Grade da aba Mares: {len({g[0] for g in grid})} direção(ões), {len({g[1] for g in grid})} Tp, "
          f"{len(grid)} par(es), {n_hs} Hs (+ Hs de referência {args.hs_ref:g} m)")
    with log_to_file(out.with_suffix(".log")):
        try:
            res = run_fd_screening(case, Path(args.base_dyn), grid, args.hs_ref, args.storm_hours,
                                   args.workers or _default_workers(), args.uf_limit, bend=args.bend_stiffness,
                                   secant_curvature=args.secant_curvature)
        except (HystereticFDError, ValueError) as exc:
            print(f"ERRO {exc}")
            return 2
        write_fd(out, res, case.num("simulation.hs_step"), args.title or case.case_id, case.operation.operation_type)
    from .screening.frequency import fd_notes

    for n in fd_notes(res):
        print(f"AVISO {n}")
    bad = [r for r in res if r["hs_limit"] is None]
    print(f"{len(res)} par(es) direção × Tp; {len(bad)} sem Hs limite (ver coluna Obs.). Janela prevista em {out}")
    if args.compare:
        return _compare(out, Path(args.compare), args.sheet, case, args.uf_limit)
    return 0


def _default_workers() -> int:
    import os

    return max(1, (os.cpu_count() or 2) - 1)


def _compare(pred_path: Path, results: Path, sheet: str, case, uf_limit) -> int:
    import pandas as pd

    from .post import window as W
    from .screening import compare as C

    xls = pd.ExcelFile(results)
    if "Status" in xls.sheet_names:
        st = pd.read_excel(results, sheet_name="Status")
    else:
        st = W.evaluate(W.read_results(results, sheet), case, uf_limit)
    limits = C.compare_limits(C.read_prediction(pred_path), C.td_brackets(st))
    uf_rows = []
    if "UF FD" in pd.ExcelFile(pred_path).sheet_names:
        uf_rows = C.compare_uf(pd.read_excel(pred_path, sheet_name="UF FD"), st)
    summ = C.summary(limits, uf_rows)
    C.write_comparison(pred_path, limits, uf_rows, summ)
    for r in summ:
        v = r["Valor"]
        print(f"  {r['Indicador']}: {v:.3f}" if isinstance(v, float) else f"  {r['Indicador']}: {v}")
    print(f"Comparação gravada em {pred_path} (abas Comparação resumo/limites/UF)")
    return 0


def _cmd_screen_compare(args) -> int:
    from .pipeline import load_case

    case = load_case(_project(args), args.id)
    return _compare(Path(args.predicted), Path(args.results), args.sheet, case, args.uf_limit)


def _cmd_post(args) -> int:
    from .inputs import slug
    from .post.extract import (default_workers, extract_many, find_sims, item_notes, missing_sims, write_results,
                               write_sheet)

    if args.json:
        from .io.legacy import case_from_legacy_json
        case = case_from_legacy_json(json.loads(Path(args.json).read_text(encoding="utf-8")))
    else:
        from .pipeline import load_case
        case = load_case(_project(args), args.id)
    items = list(case.postproc)
    if not args.no_criteria:
        from .post.window import criteria_postitems
        crit = criteria_postitems(case)
        items += [i for i in crit if i.label not in {x.label for x in items}]
    if not items:
        print("Nenhum item de pós-processamento definido (aba PostProc / template da operação).")
        return 1
    sims_dir = Path(args.sims)
    out = Path(args.out) if args.out else sims_dir / f"{slug(case.case_id)}_resultados.xlsx"
    sims = find_sims(sims_dir, args.pattern)
    missing = missing_sims(sims_dir, args.pattern)
    if not sims:
        print(f"Nenhum .sim em {sims_dir} (filtro {args.pattern}).")
        return 1
    print(f"{len(sims)} .sim em {sims_dir}; {len(missing)} .dat sem .sim. Extraindo com "
          f"{args.workers or default_workers()} processo(s)...")
    df = extract_many(sims, items, workers=args.workers or default_workers())
    path = write_results(df, out)
    failed = df.attrs.get("failed", [])
    item_errors = int(df["_errors"].notna().sum()) if "_errors" in df.columns else 0

    import pandas as pd
    notes = [{"Tipo": "Interpretação", "Arquivo/Item": "", "Detalhe": n} for n in item_notes(items)]
    notes += [{"Tipo": "Sem .sim (não rodou ou falhou)", "Arquivo/Item": m, "Detalhe": ""} for m in missing]
    notes += [{"Tipo": "Falha ao abrir o .sim", "Arquivo/Item": Path(f).name,
               "Detalhe": (tb.strip().splitlines() or [""])[-1]} for f, tb in failed]
    write_sheet(path, "Notas", pd.DataFrame(notes or [{"Tipo": "", "Arquivo/Item": "", "Detalhe": "Sem observações"}]))

    print(f"{len(df)} simulação(ões) -> {path} (aba Logs). Falhas ao abrir: {len(failed)}. "
          f"Linhas com item em erro: {item_errors}. Sem .sim: {len(missing)} (aba Notas).")
    for n in item_notes(items):
        print(f"AVISO {n}")
    if args.window:
        from .post import window as W

        st = W.evaluate(df, case, args.uf_limit)
        for m in st.attrs.get("missing_criteria", []):
            print(f"AVISO critério fora do Status: {m}")
        limits = W.window_limits(st)
        sugg, search = _suggest(args, case, st, _mares_pairs(args))
        W.write_window(path, st, limits, sugg, case, args.title, search)
        _print_search(search)
        n_ok = int((st["Status"] == W.APPROVED).sum())
        print(f"Status: {n_ok} aprovada(s), {len(st) - n_ok} reprovada(s) (UF limite {st.attrs['uf_limit']:g}). "
              f"Abas Janela, UF, Limites, Status e Sugestão gravadas.")
        print(W.window_grid(limits).fillna("").to_string())
    return 0 if not failed else 1


# --------------------------------------------------------------------------- fase 3

def _cmd_statics(args) -> int:
    from .pipeline import load_case
    from .statics.positions import run_positions

    from .log import log_to_file

    if not args.out:
        args.out = str(Path(args.model).resolve().parent / "01.Positions")
    with log_to_file(Path(args.out) / "cvd_statics.log"):
        case = load_case(_project(args), args.id)
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        results, eng = run_positions(case, Path(args.model), Path(args.out), only, save_sims=not args.no_sims)
    print(f"Referência: cabo do guincho {eng.L_ref:.3f} m (modelo montado: {eng.L_built:.3f} m; "
          f"puxada zero = lingas começam a tracionar)")
    if eng.skipped:
        print(f"Critérios sem limite numérico (fora do UF): {', '.join(eng.skipped)}")
    _print_table([{"Posição": r.name, "MCV": r.state, "Puxada (m)": f"{r.pull:.3f}", "Ângulo": f"{r.angle:.2f}",
                   "UF máx": f"{r.uf_max:.3f}", "Governa": r.governing, "Status": r.status, "Obs.": r.message}
                  for r in results], ["Posição", "MCV", "Puxada (m)", "Ângulo", "UF máx", "Governa", "Status", "Obs."])
    print(f"\n{len(eng.log)} cálculos de estática. Resultados em {Path(args.out) / 'positions.csv'} e statics_log.csv")
    return 0 if all(r.status != "FALHOU" for r in results) else 1


def _cmd_statics_sweep(args) -> int:
    from .pipeline import load_case
    from .statics.engine import StaticsEngine

    from .log import log_to_file

    out = Path(args.out)
    with log_to_file(out.with_suffix(".log")):
        case = load_case(_project(args), args.id)
        eng = StaticsEngine(case, Path(args.model))
        eng.find_reference()
        n = int(round((args.to - args.start) / args.step)) + 1
        pulls = [round(args.start + i * args.step, 6) for i in range(n)]
        evs = eng.sweep(args.state, pulls)
    out.parent.mkdir(parents=True, exist_ok=True)
    eng.write_log(out)
    _print_table([{"Puxada (m)": f"{e.pull:.3f}", "Convergiu": "S" if e.converged else "N", "Ângulo": f"{e.angle:.2f}",
                   "UF máx": f"{e.uf_max:.3f}", "Governa": e.governing, "Tração lingas": f"{e.link_tension:.2f}"}
                  for e in evs], ["Puxada (m)", "Convergiu", "Ângulo", "UF máx", "Governa", "Tração lingas"])
    print(f"\nVarredura gravada em {out}")
    return 0


# --------------------------------------------------------------------------- fase 0

def _cmd_capture(args) -> int:
    from .regression.capture import capture_baseline

    out = capture_baseline(
        code_base=Path(args.code_base), out_dir=Path(args.out), with_adjust=not args.no_adjust,
        postproc_table=Path(args.postproc_table) if args.postproc_table else None,
        postproc_sheet=args.postproc_sheet, sims_dir=Path(args.sims_dir) if args.sims_dir else None,
        keep_workdir=args.keep_workdir)
    print(f"Baseline gravado em {out}")
    return 0


def _cmd_verify(args) -> int:
    from .regression.capture import verify_baseline

    problems = verify_baseline(Path(args.dir), Path(args.code_base) if args.code_base else None)
    if problems:
        print("Problemas encontrados:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Baseline íntegro." + (" Pipeline antiga sem alterações desde a captura." if args.code_base else ""))
    return 0


def _cmd_compare_models(args) -> int:
    from .regression.compare import Tolerance, compare, format_report
    from .regression.fingerprint import model_to_dict

    diffs = compare(model_to_dict(args.expected), model_to_dict(args.actual),
                    Tolerance(rel_tol=args.rel_tol, abs_tol=args.abs_tol), ignore=args.ignore)
    print(format_report(diffs, limit=args.limit))
    return 1 if diffs else 0


# --------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="Arquivo cvd_project.json (padrão: procura a partir da pasta atual)")
    common.add_argument("--workbook", help="Planilha geral (sobrescreve o projeto)")
    common.add_argument("--config", help="Planilha de configuração (sobrescreve o projeto)")
    common.add_argument("-v", "--verbose", action="count", default=0,
                        help="Mais detalhes (cada etapa e cada linha do checklist)")
    common.add_argument("-q", "--quiet", action="store_true", help="Só avisos e erros")

    parser = argparse.ArgumentParser(prog="cvd", description="Pipeline CVD/DCVD (NSG)")
    parser.add_argument("--version", action="version", version=f"cvdtools {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("setup", aliases=["configurar"],
                       help="Prepara ou atualiza a pasta de projeto (cvd_project.json, configuração, curvas, scripts)")
    p.add_argument("--folder", help="Pasta do projeto (padrão: a pasta atual)")
    p.add_argument("--workbook", dest="workbook_path", help="Planilha geral (padrão: procura a aba 'Input Sheet')")
    p.add_argument("--code", help="Código do projeto (padrão: o número no início do nome da planilha)")
    p.add_argument("--no-vscode", action="store_true", help="Não cria/altera .vscode/settings.json")
    p.add_argument("--config-from", help="Pasta de projeto de referência: copia cvd_config.xlsx e cvd_curves.xlsx "
                                         "que faltarem")
    p.set_defaults(func=_cmd_setup)

    p = sub.add_parser("update", aliases=["atualizar"],
                       help="Atualiza o cvdtools pelo GitHub e roda o setup na pasta de projeto")
    p.add_argument("--folder", help="Pasta do projeto (padrão: a pasta atual)")
    p.add_argument("--ref", default="", help="Tag ou branch (padrão: main), ex.: v0.12.1")
    p.add_argument("--no-setup", action="store_true")
    p.set_defaults(func=_cmd_update)

    proj = sub.add_parser("project", help="Arquivo de projeto").add_subparsers(dest="sub")
    p = proj.add_parser("init", help="Cria cvd_project.json na pasta atual")
    p.add_argument("--code", default="", help="Código do projeto (prefixo dos nomes, ex.: 1327)")
    p.add_argument("--workbook", dest="workbook_path", required=True)
    p.add_argument("--config", dest="config_path", default="cvd_config.xlsx")
    p.add_argument("--analysis-root", default="", help="Pasta com as pastas dos poços (ex.: ../..)")
    p.add_argument("--base-dir", default="", help="Base dos caminhos 'Modelo base' da aba Operações")
    p.add_argument("--curves", nargs="*", default=[], help="Planilhas de curvas (ex.: cvd_curves.xlsx)")
    p.set_defaults(func=_cmd_project_init)

    cfg = sub.add_parser("config", help="Planilha de configuração").add_subparsers(dest="sub")
    p = cfg.add_parser("init", help="Gera a planilha de configuração")
    p.add_argument("--out", default="cvd_config.xlsx")
    p.add_argument("--from-json", help="__INPUTS.json antigo para copiar valores, PostProc e Critérios")
    p.set_defaults(func=_cmd_config_init)
    cur = sub.add_parser("curves", help="Biblioteca de curvas externas").add_subparsers(dest="sub")
    p = cur.add_parser("init", parents=[common], help="Cria a planilha de curvas (opcionalmente importando uma aba)")
    p.add_argument("--out", default="cvd_curves.xlsx")
    p.add_argument("--from-sheet", help="Aba com a curva tabelada (ex.: 'Winch Extension Stiffness')")
    p.add_argument("--source-workbook", help="Planilha de origem (padrão: planilha geral do projeto)")
    p.add_argument("--id", default="CURVA_1")
    p.add_argument("--kind", choices=["bending", "axial"], default="axial")
    p.add_argument("--x-col", type=int, default=1)
    p.add_argument("--y-col", type=int, default=2)
    p.add_argument("--x-unit", default="%")
    p.add_argument("--y-unit", default="kN")
    p.add_argument("--first-row", type=int, default=2)
    p.add_argument("--load-case")
    p.set_defaults(func=_cmd_curves_init)
    p = cur.add_parser("check", parents=[common], help="Lista e valida as curvas do projeto")
    p.set_defaults(func=_cmd_curves_check)

    p = cfg.add_parser("upgrade", parents=[common], help="Acrescenta parâmetros/papéis/linhas novos a uma configuração existente")
    p.add_argument("--set", action="append", help="chave=valor na aba Defaults (pode repetir)")
    p.set_defaults(func=_cmd_config_upgrade)

    inp = sub.add_parser("inputs", help="Inputs da planilha geral").add_subparsers(dest="sub")
    p = inp.add_parser("list", parents=[common])
    p.set_defaults(func=_cmd_inputs_list)
    p = inp.add_parser("validate", parents=[common])
    p.add_argument("--id")
    p.add_argument("--out", help="Relatório .xlsx ou .csv")
    p.add_argument("--all", action="store_true", help="Mostrar também mensagens INFO")
    p.set_defaults(func=_cmd_inputs_validate)
    p = inp.add_parser("show", parents=[common])
    p.add_argument("id")
    p.set_defaults(func=_cmd_inputs_show)
    p = inp.add_parser("diff-legacy", parents=[common])
    p.add_argument("id")
    p.add_argument("--json", required=True)
    p.set_defaults(func=_cmd_inputs_diff)

    p = sub.add_parser("snapshot", parents=[common], help="Grava case.json do caso")
    p.add_argument("id")
    p.add_argument("--out")
    p.add_argument("--legacy-json", action="store_true", help="Também grava __INPUTS.json no formato antigo")
    p.set_defaults(func=_cmd_snapshot)

    p = sub.add_parser("build", parents=[common], help="Monta o modelo do caso + snapshot + checklist")
    p.add_argument("id")
    p.add_argument("--out")
    p.add_argument("--catenary", choices=["fixed", "legacy"], default="fixed")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-checklist", action="store_true")
    p.add_argument("--legacy-json", action="store_true")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("diagnose", parents=[common],
                       help="Estática que não converge: roda variantes do modelo e aponta a causa")
    p.add_argument("id")
    p.add_argument("--model", help="Modelo a diagnosticar (padrão: o .dat do caso em casos/)")
    p.add_argument("--out")
    p.add_argument("--only", help="Variantes separadas por vírgula (ex.: como_gerado,sem_corcova)")
    p.add_argument("--max-iterations", type=int, help="Limite de iterações por variante")
    p.set_defaults(func=_cmd_diagnose)

    p = sub.add_parser("build-legacy", help="Monta um modelo a partir do __INPUTS.json antigo (regressão)")
    p.add_argument("--json", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--catenary", choices=["fixed", "legacy"], default="legacy")
    p.set_defaults(func=_cmd_build_legacy)

    p = sub.add_parser("checklist", parents=[common], help="Gera o checklist de um modelo já montado")
    p.add_argument("id")
    p.add_argument("--model", required=True)
    p.add_argument("--base")
    p.add_argument("--out")
    p.set_defaults(func=_cmd_checklist)

    p = sub.add_parser("seastates", parents=[common], help="Gera os arquivos de estados de mar (aba Mares)")
    p.add_argument("id")
    p.add_argument("--base-dyn", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--regular", action="store_true")
    p.add_argument("--workers", type=int, help="Processos em paralelo (padrão: núcleos - 1)")
    p.add_argument("--run", action="store_true", help="Roda as simulações logo depois de gerar (em paralelo)")
    p.add_argument("--threads", type=int, default=1, help="Threads do OrcaFlex por simulação (com --run)")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=_cmd_seastates)

    p = sub.add_parser("dynbase", parents=[common],
                       help="Modelo dinâmico base (<nome>_dyn.dat) a partir de uma posição estática: MCV ancorado, "
                            "sem cabo do guindaste; com --seastates, já gera os estados de mar")
    p.add_argument("id")
    p.add_argument("--position", default="01.Horizontal", help="Posição de partida (padrão: 01.Horizontal)")
    p.add_argument("--source", help="Arquivo de partida (.sim ou .dat) em vez da posição")
    p.add_argument("--out", help="Pasta do caso (padrão: a do build)")
    p.add_argument("--no-check", action="store_true", help="Não confere a estática do modelo dinâmico")
    p.add_argument("--seastates", action="store_true", help="Gera os estados de mar em <caso>/02.Dynamic")
    p.add_argument("--regular", action="store_true")
    p.add_argument("--run", action="store_true", help="Com --seastates: roda as simulações em seguida")
    p.add_argument("--workers", type=int)
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--overwrite", action="store_true", help="Sobrescreve os estados de mar já gerados")
    p.set_defaults(func=_cmd_dynbase)

    p = sub.add_parser("run", parents=[common],
                       help="Roda simulações dinâmicas em paralelo (um processo por simulação)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dir", help="Pasta com os .dat")
    g.add_argument("--list", help="batch_list.txt (um caminho por linha)")
    p.add_argument("--pattern", default="*.dat")
    p.add_argument("--workers", type=int, help="Simulações ao mesmo tempo (padrão: núcleos - 1)")
    p.add_argument("--threads", type=int, default=1, help="Threads do OrcaFlex por simulação (padrão 1)")
    p.add_argument("--overwrite", action="store_true", help="Roda de novo mesmo se o .sim já existe")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("post", parents=[common], help="Extrai resultados de .sim (e, com --window, a janela)")
    p.add_argument("--sims", required=True, help="Pasta com os .sim")
    p.add_argument("--out", help="Planilha de saída (padrão: <pasta dos .sim>/<caso>_resultados.xlsx)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id")
    g.add_argument("--json", help="Usar o postProcess de um __INPUTS.json antigo")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--pattern", default="*.sim")
    p.add_argument("--no-criteria", action="store_true",
                   help="Não acrescentar as colunas 'Critério: ...' (máximo dinâmico de cada critério)")
    p.add_argument("--window", action="store_true",
                   help="Depois da extração: Status (UF), Janela, mapa de UF, Limites e Sugestão na mesma planilha")
    p.add_argument("--uf-limit", type=float)
    p.add_argument("--title", default="")
    p.add_argument("--hs-min", type=float, help="Padrão: simulation.hs_min do caso")
    p.add_argument("--hs-max", type=float, help="Padrão: simulation.hs_max do caso")
    p.add_argument("--hs-step", type=float, help="Padrão: simulation.hs_step do caso")
    p.add_argument("--steps", type=int, default=2, help="Com --method steps: quantos Hs acima/abaixo")
    _search_args(p)
    p.set_defaults(func=_cmd_post)

    p = sub.add_parser("window", parents=[common],
                       help="Status (UF) dos estados de mar, janela no formato 'Janelas', sugestão e limpeza")
    p.add_argument("id")
    p.add_argument("--results", help="Planilha (aba Logs) ou CSV gerado pelo 'cvd post' (sem ela: 1ª rodada, "
                                     "com --seed e/ou --mares)")
    p.add_argument("--sheet", default="Logs")
    p.add_argument("--out", help="Planilha de saída (padrão: a própria planilha de resultados)")
    p.add_argument("--uf-limit", type=float)
    p.add_argument("--title", default="")
    p.add_argument("--hs-min", type=float, help="Padrão: simulation.hs_min do caso")
    p.add_argument("--hs-max", type=float, help="Padrão: simulation.hs_max do caso")
    p.add_argument("--hs-step", type=float, help="Padrão: simulation.hs_step do caso")
    p.add_argument("--steps", type=int, default=2, help="Com --method steps: quantos Hs acima/abaixo por (Tp, direção)")
    _search_args(p)
    p.add_argument("--generate", metavar="BASE_DYN", help="Gera os .dat sugeridos a partir deste modelo dinâmico")
    p.add_argument("--gen-out", help="Pasta dos .dat sugeridos (com --generate)")
    p.add_argument("--clean", metavar="SIM_DIR", help="Lista (ou move, com --apply) as simulações que não definem o limite")
    p.add_argument("--unused", help="Destino da limpeza (padrão: SIM_DIR/../Unused_Sims)")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=_cmd_window)

    sc = sub.add_parser("screen", help="Triagem: semi-dinâmico, resposta da embarcação e janela prevista")
    sc_sub = sc.add_subparsers(dest="sub")
    p = sc_sub.add_parser("generate", parents=[common], help="Gera os .dat semi-dinâmicos (pay-out harmônico)")
    p.add_argument("id")
    p.add_argument("--base-dyn", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=_cmd_screen_generate)
    p = sc_sub.add_parser("critical", parents=[common], help="Amplitude crítica por Tp a partir do 'cvd post'")
    p.add_argument("id")
    p.add_argument("--results", required=True)
    p.add_argument("--sheet", default="Logs")
    p.add_argument("--uf-limit", type=float)
    p.add_argument("--out", required=True, help="CSV das amplitudes críticas")
    p.set_defaults(func=_cmd_screen_critical)
    p = sc_sub.add_parser("response", parents=[common], help="Resposta vertical da embarcação (NsgOrcFx)")
    p.add_argument("id")
    p.add_argument("--base-dyn", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_screen_response)
    p = sc_sub.add_parser("predict", parents=[common], help="Janela prevista = A* ÷ resposta")
    p.add_argument("id")
    p.add_argument("--critical", required=True)
    p.add_argument("--response", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--around", type=int, default=1, help="Passos de Hs em torno da previsão na grade sugerida")
    p.add_argument("--generate", metavar="BASE_DYN")
    p.add_argument("--gen-out")
    p.set_defaults(func=_cmd_screen_predict)
    p = sc_sub.add_parser("fd", parents=[common],
                          help="Janela prevista pela dinâmica no domínio da frequência do OrcaFlex (rápida, linearizada)")
    p.add_argument("id")
    p.add_argument("--base-dyn", required=True, help="Modelo dinâmico base (o mesmo das irregulares)")
    p.add_argument("--out", required=True, help="Planilha de saída (.xlsx)")
    p.add_argument("--tps", help="Só estes Tp (lista ;). Padrão: todos os da aba Mares")
    p.add_argument("--dirs", help="Só estas direções relativas à proa (lista ;). Padrão: todas as da aba Mares")
    p.add_argument("--hs-ref", type=float, default=2.0, help="Hs da 1ª análise de cada par (padrão 2 m)")
    p.add_argument("--storm-hours", type=float, default=3.0, help="Duração da tempestade do MPM (padrão 3 h)")
    p.add_argument("--uf-limit", type=float)
    p.add_argument("--workers", type=int, help="Análises ao mesmo tempo (padrão: núcleos - 1)")
    p.add_argument("--title", default="")
    p.add_argument("--compare", metavar="RESULTADOS_XLSX", help="Já compara com a planilha do 'cvd post --window'")
    p.add_argument("--sheet", default="Logs")
    p.add_argument("--bend-stiffness", choices=["model", "secant"], default="model",
                   help="model: rigidez do modelo (o OrcaFlex não roda FD com curva histerética: o comando para "
                        "antes); secant: troca a curva de EIx da linha por EI = M(κ)/κ em --secant-curvature")
    p.add_argument("--secant-curvature", type=float, help="Curvatura de referência da rigidez secante (1/m)")
    p.set_defaults(func=_cmd_screen_fd)
    p = sc_sub.add_parser("compare", parents=[common],
                          help="Compara uma janela prevista (fd ou predict) com a janela das irregulares")
    p.add_argument("id")
    p.add_argument("--predicted", required=True, help="Planilha da previsão (aba Previsão)")
    p.add_argument("--results", required=True, help="Planilha do 'cvd post --window' (aba Status)")
    p.add_argument("--sheet", default="Logs")
    p.add_argument("--uf-limit", type=float)
    p.set_defaults(func=_cmd_screen_compare)

    st = sub.add_parser("statics", help="Estática: referência de puxada, posições e varredura")
    st_sub = st.add_subparsers(dest="sub")
    p = st_sub.add_parser("positions", parents=[common], help="Calcula as posições relevantes e salva os .sim")
    p.add_argument("id")
    p.add_argument("--model", required=True, help=".dat do caso (saída do cvd build)")
    p.add_argument("--out", help="Pasta de saída (padrão: 01.Positions ao lado do modelo)")
    p.add_argument("--only", help="Lista separada por vírgula (ex.: Horizontal,Max_Fixed_Overpull)")
    p.add_argument("--no-sims", action="store_true")
    p.set_defaults(func=_cmd_statics)
    p = st_sub.add_parser("sweep", parents=[common], help="Varre a puxada e registra ângulo, UF e trações")
    p.add_argument("id")
    p.add_argument("--model", required=True)
    p.add_argument("--state", choices=["free", "fixed"], default="free")
    p.add_argument("--from", dest="start", type=float, default=0.0)
    p.add_argument("--to", type=float, required=True)
    p.add_argument("--step", type=float, default=0.2)
    p.add_argument("--out", required=True, help="CSV de saída")
    p.set_defaults(func=_cmd_statics_sweep)

    base = sub.add_parser("baseline", help="Captura e verificação do caso de referência").add_subparsers(dest="sub")
    p = base.add_parser("capture")
    p.add_argument("--code-base", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--no-adjust", action="store_true")
    p.add_argument("--postproc-table")
    p.add_argument("--postproc-sheet", default="Logs")
    p.add_argument("--sims-dir")
    p.add_argument("--keep-workdir", action="store_true")
    p.set_defaults(func=_cmd_capture)
    p = base.add_parser("verify")
    p.add_argument("--dir", required=True)
    p.add_argument("--code-base")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("compare-models", help="Compara dois modelos OrcaFlex propriedade a propriedade")
    p.add_argument("expected")
    p.add_argument("actual")
    p.add_argument("--rel-tol", type=float, default=1e-9)
    p.add_argument("--abs-tol", type=float, default=1e-9)
    p.add_argument("--ignore", nargs="*", default=[])
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=_cmd_compare_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    for name in ("project", "workbook", "config"):
        if not hasattr(args, name):
            setattr(args, name, None)
    from .log import setup_logging

    setup_logging(getattr(args, "verbose", 0), getattr(args, "quiet", False))
    try:
        return args.func(args)
    except (FileNotFoundError, FileExistsError, KeyError, ValueError, NotImplementedError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrompido pelo usuário.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
