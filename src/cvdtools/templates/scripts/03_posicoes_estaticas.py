"""03 - Posições estáticas: puxada zero, horizontal, CB pull-in/pay-out e overpulls.

Saídas em <pasta do caso>/01.Positions: um .sim por posição, positions.csv e statics_log.csv.
Pode levar de minutos a horas. Para interromper: clique no terminal e tecle Ctrl+C.
"""

MODELO = ""    # vazio = modelo montado pelo 02
SOMENTE = []   # vazio = todas; ex.: ["Horizontal", "Max_Fixed_Overpull"]
SALVAR_SIMS = True


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    api.posicoes_estaticas(caso["ID"], modelo=MODELO or None, pasta=caso.get("PASTA") or None,
                           somente=SOMENTE or None, salvar_sims=SALVAR_SIMS)
