"""07 - Janela prevista pelo domínio da frequência do OrcaFlex (minutos, linearizada). Opcional.

Serve de ponto de partida da busca adaptativa (08) e, comparada com uma campanha irregular
completa, para medir o erro da previsão.
Saída: <pasta do caso>/05.Screening/janela_fd.xlsx.
"""

MODELO_DINAMICO = ""   # vazio = <pasta do caso>/<nome>_dyn.dat
COMPARAR_COM = ""      # planilha do 06 (resultados das irregulares) para comparar; vazio = não compara
# Linha com curva de histerese: o OrcaFlex não roda domínio da frequência com ela. Use
# RIGIDEZ = "secant" e uma curvatura de referência (1/m), por exemplo 1/MBR.
RIGIDEZ = "model"      # "model" ou "secant"
CURVATURA_SECANTE = None
TITULO = ""


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    out = api.triagem_fd(caso["ID"], modelo_dinamico=MODELO_DINAMICO or None, comparar_com=COMPARAR_COM or None,
                         pasta=caso.get("PASTA") or None, rigidez=RIGIDEZ, curvatura_secante=CURVATURA_SECANTE,
                         titulo=TITULO)
    print(f"\nPrevisão: {out}")
