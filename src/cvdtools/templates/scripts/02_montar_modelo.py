"""02 - Montar o modelo OrcaFlex do caso (+ case.json e checklist de verificação).

Saídas na pasta do caso: <nome>.dat, case.json, Checklist_<ID>.xlsx e cvd_build.log.
Se a estática não convergir, rode o 02b_diagnosticar.py.

Se faltarem inputs obrigatórios, o script para e grava missing_data.json na pasta do caso: preencha "valor"
de cada item (feche o cvd_config.xlsx no Excel) e rode este script de novo. Os valores vão para o
cvd_config.xlsx (Defaults = projeto; Overrides = só este caso, como o ID do MCV da outra extremidade).
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
