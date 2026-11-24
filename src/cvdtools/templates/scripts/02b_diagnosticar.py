"""02b - Estática do modelo montado não converge: roda variantes do modelo e aponta a causa.

Saída: cvd_diagnose.log na pasta do caso.
"""

MODELO = ""   # vazio = modelo montado pelo 02; ou o caminho de outro .dat


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    api.diagnosticar(caso["ID"], modelo=MODELO or None, pasta=caso.get("PASTA") or None)
