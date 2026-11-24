"""08 - Busca adaptativa do Hs limite (poucas irregulares por par Tp x direção).

Cada rodada:
  1. rode este script -> gera os .dat da rodada em <pasta do caso>/02.Dynamic/<RODADA>;
  2. rode as simulações (05, com PASTA_SIMS = essa pasta);
  3. pós-processe (06, com PASTA_SIMS = essa pasta);
  4. troque RODADA (busca_2, busca_3...), preencha RESULTADOS_ANTERIORES e repita até não haver sugestão.
"""

RODADA = "busca_1"
PREVISAO = ""                # 1ª rodada: planilha do 07 (ex.: "casos/<ID>/05.Screening/janela_fd.xlsx")
RESULTADOS_ANTERIORES = ""   # rodadas seguintes: planilha do 06 da rodada anterior
TITULO = ""


if __name__ == "__main__":
    from cvdtools import api

    caso = api.ler_caso()
    out = api.janela_adaptativa(caso["ID"], RODADA, previsao=PREVISAO or None,
                                resultados=RESULTADOS_ANTERIORES or None, pasta=caso.get("PASTA") or None,
                                titulo=TITULO)
    print(f"\n.dat da rodada em: {out}")
