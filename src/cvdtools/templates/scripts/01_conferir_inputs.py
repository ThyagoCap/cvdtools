"""01 - Conferir os inputs do caso (planilha geral + cvd_config.xlsx + curvas).

Mostra os ERROS e AVISOS do caso e o valor usado em cada parâmetro, com a origem
(Overrides > Input Sheet > Defaults). Corrija os erros antes de montar o modelo.
"""

MOSTRAR_VALORES = True   # lista o valor de cada parâmetro
CONFERIR_CURVAS = True   # lista as curvas do cvd_curves.xlsx


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    api.conferir_inputs(caso["ID"], mostrar_valores=MOSTRAR_VALORES, curvas=CONFERIR_CURVAS)
