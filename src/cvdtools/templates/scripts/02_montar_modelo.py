"""02 - Montar o modelo OrcaFlex do caso (+ case.json e checklist de verificação).

Saídas na pasta do caso: <nome>.dat, case.json, Checklist_<ID>.xlsx e cvd_build.log.
Se a estática não convergir, rode o 02b_diagnosticar.py.
"""

SOBRESCREVER = True   # False: para se o modelo já existir
CHECKLIST = True      # gera o Checklist_<ID>.xlsx


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    paths = api.montar_modelo(caso["ID"], sobrescrever=SOBRESCREVER, pasta=caso.get("PASTA") or None,
                              checklist=CHECKLIST)
    print()
    print(paths)
