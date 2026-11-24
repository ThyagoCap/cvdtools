"""99 - Acrescentar ao cvd_config.xlsx os parâmetros novos de uma versão do cvdtools e/ou mudar padrões.

Feche o cvd_config.xlsx no Excel antes. Uma cópia .bak é feita antes de gravar.
O "python -m cvdtools setup" já faz isso depois de cada atualização; use este script para mudar padrões.
"""

# Padrões a gravar na aba Defaults (vazio = só acrescenta o que falta). Exemplos:
#   VALORES = {"model.masterlink_height": 2, "statics.robust_min_damping": 100}
VALORES = {}


if __name__ == "__main__":
    from cvdtools import api

    api.atualizar_configuracao(VALORES)
