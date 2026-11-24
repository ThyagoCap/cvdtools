"""05 - Rodar as simulações de uma pasta em paralelo (alternativa ao batch do OrcaFlex).

Pula os .dat que já têm .sim (a não ser com SOBRESCREVER). Ctrl+C no terminal interrompe.
"""

PASTA_SIMS = ""     # vazio = <pasta do caso>/02.Dynamic
SIMULTANEAS = None  # simulações ao mesmo tempo (None = núcleos - 1)
SOBRESCREVER = False


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    paths = api.caso(caso["ID"], caso.get("PASTA") or None)
    api.rodar_simulacoes(PASTA_SIMS or paths.dinamica, simultaneas=SIMULTANEAS, sobrescrever=SOBRESCREVER)
