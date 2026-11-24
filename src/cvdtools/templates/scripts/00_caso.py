"""CASO EM ANÁLISE: todos os scripts desta pasta usam o ID abaixo.

1. Troque o ID pelo da coluna "ID" da Input Sheet da planilha geral (copie e cole).
2. Salve (Ctrl+S).
3. Rode os scripts na ordem (01, 02, 03...): abra o arquivo e clique em ▶ (Run Python File),
   no canto superior direito do VS Code.

Rodar este arquivo só mostra as pastas e os arquivos do caso.
"""

ID = ""   # ex.: "GLF27 2CVD MOBO New Out"

# Pasta do caso. Vazio = padrão (casos/<ID> nesta pasta de projeto, ou a pasta do poço se o
# cvd_project.json define analysis_root). Use outra pasta para testar uma variante do caso.
PASTA = ""


if __name__ == "__main__":
    from cvdtools import api

    if not ID.strip():
        raise SystemExit("Preencha o ID do caso neste arquivo e salve.")
    print(api.caso(ID, PASTA or None))
