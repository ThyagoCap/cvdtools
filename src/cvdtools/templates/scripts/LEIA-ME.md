# Scripts do pipeline CVD/DCVD

1. Abra esta pasta de projeto no VS Code (File > Open Folder).
2. Em `00_caso.py`, preencha o `ID` do caso (coluna ID da Input Sheet) e salve.
3. Rode os scripts na ordem: abra o arquivo e clique em ▶ (Run Python File) no canto superior direito.
   - `01` confere os inputs;
   - `02` monta o modelo (e `02b` diagnostica se a estática não converge);
   - `03` calcula as posições estáticas;
   - `04` cria o modelo dinâmico base (a partir da 01.Horizontal: MCV ancorado, sem cabo do guindaste) e gera os estados de mar;
   - `05` roda as simulações; `06` pós-processa e calcula a janela;
   - `07` e `08` são a triagem e a busca adaptativa do Hs limite (opcionais).
4. As opções de cada etapa ficam no topo do script, em MAIÚSCULAS. Não é preciso mexer no resto.

Para interromper uma etapa: clique no terminal do VS Code e tecle Ctrl+C.
Cada etapa grava um log na pasta do caso (`cvd_build.log`, ...).

Atualizar o cvdtools (no terminal do VS Code, nesta pasta de projeto):

    python -m cvdtools update

O `update` instala a versão do GitHub e roda o `setup`, que acrescenta os parâmetros novos ao
`cvd_config.xlsx` e atualiza os scripts. Mesmo sem o `update`, os scripts se atualizam sozinhos na
primeira vez que um deles roda depois de o cvdtools mudar. Scripts que você editou não são
sobrescritos: a versão nova fica ao lado, com `.novo.py` no nome.

Primeira instalação numa máquina:

    {install_command}
