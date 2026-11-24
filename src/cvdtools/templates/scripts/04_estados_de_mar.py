"""04 - Modelo dinâmico base e estados de mar.

1. Cria <pasta do caso>/<nome>_dyn.dat a partir de uma posição estática (padrão: 01.Horizontal):
   MCV ancorado na posição em que está e cabo do guindaste apagado.
2. Gera os estados de mar (aba Mares do cvd_config.xlsx) em <pasta do caso>/02.Dynamic: um .dat por
   estado de mar, manifest.csv e batch_list.txt.

Para usar um modelo dinâmico feito à mão: CRIAR_MODELO_DINAMICO = False e o arquivo em MODELO_DINAMICO.
"""

CRIAR_MODELO_DINAMICO = True
POSICAO = "01.Horizontal"   # posição de partida (pasta 01.Positions do caso)
MODELO_DINAMICO = ""        # só com CRIAR_MODELO_DINAMICO = False; vazio = <pasta do caso>/<nome>_dyn.dat
PASTA_SAIDA = ""            # só com CRIAR_MODELO_DINAMICO = False; vazio = <pasta do caso>/02.Dynamic
REGULARES = False           # True: ondas regulares em vez de irregulares
RODAR_EM_SEGUIDA = False    # True: já roda as simulações (equivale ao 05)
SIMULTANEAS = None          # simulações ao mesmo tempo (None = núcleos - 1)
SOBRESCREVER = False        # sobrescreve estados de mar já gerados


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    if CRIAR_MODELO_DINAMICO:
        api.modelo_dinamico(caso["ID"], posicao=POSICAO, pasta=caso.get("PASTA") or None, gerar_mares=True,
                            rodar=RODAR_EM_SEGUIDA, regular=REGULARES, sobrescrever=SOBRESCREVER,
                            simultaneas=SIMULTANEAS)
    else:
        api.estados_de_mar(caso["ID"], modelo_dinamico=MODELO_DINAMICO or None, pasta_saida=PASTA_SAIDA or None,
                           pasta=caso.get("PASTA") or None, rodar=RODAR_EM_SEGUIDA, regular=REGULARES,
                           sobrescrever=SOBRESCREVER, simultaneas=SIMULTANEAS)
