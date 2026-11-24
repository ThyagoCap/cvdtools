"""04 - Gerar os estados de mar (aba Mares do cvd_config.xlsx) a partir do modelo dinâmico base.

ANTES: monte à mão o modelo dinâmico base, <nome>_dyn.dat, na pasta do caso (a partir da
posição estática escolhida). Rode o 00_caso.py para ver o nome esperado.
Saídas em <pasta do caso>/02.Dynamic: um .dat por estado de mar, manifest.csv e batch_list.txt.
"""

MODELO_DINAMICO = ""   # vazio = <pasta do caso>/<nome>_dyn.dat
PASTA_SAIDA = ""       # vazio = <pasta do caso>/02.Dynamic
REGULARES = False      # True: ondas regulares em vez de irregulares
RODAR_EM_SEGUIDA = False   # True: já roda as simulações (equivale ao 05)
SIMULTANEAS = None     # simulações ao mesmo tempo (None = núcleos - 1)
SOBRESCREVER = False


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    api.estados_de_mar(caso["ID"], modelo_dinamico=MODELO_DINAMICO or None, pasta_saida=PASTA_SAIDA or None,
                       pasta=caso.get("PASTA") or None, rodar=RODAR_EM_SEGUIDA, regular=REGULARES,
                       sobrescrever=SOBRESCREVER, simultaneas=SIMULTANEAS)
