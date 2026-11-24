"""06 - Pós-processar os .sim e calcular a janela operacional.

Saída: <pasta dos .sim>/<ID>_resultados.xlsx com as abas Logs, Status, Janela, UF, Limites e Sugestão.
A aba Janela está no formato das abas "Janelas" da planilha geral (copie e cole).
"""

PASTA_SIMS = ""   # vazio = <pasta do caso>/02.Dynamic
JANELA = True     # False: só extrai os resultados (aba Logs)
TITULO = ""       # título da janela, ex.: "GLF-27 CVD 2ª MOBO New Out"


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    out = api.pos_processar(caso["ID"], pasta_sims=PASTA_SIMS or None, pasta=caso.get("PASTA") or None,
                            janela=JANELA, titulo=TITULO)
    print(f"\nResultados: {out}")
