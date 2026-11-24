# Scripts do pipeline CVD/DCVD

1. Abra esta pasta de projeto no VS Code (File > Open Folder).
2. Em `00_caso.py`, preencha o `ID` do caso (coluna ID da Input Sheet) e salve.
3. Rode os scripts na ordem: abra o arquivo e clique em ▶ (Run Python File) no canto superior direito.
   - `01` confere os inputs;
   - `02` monta o modelo (e `02b` diagnostica se a estática não converge);
   - `03` calcula as posições estáticas;
   - `04` gera os estados de mar (antes, monte à mão o modelo dinâmico base);
   - `05` roda as simulações; `06` pós-processa e calcula a janela;
   - `07` e `08` são a triagem e a busca adaptativa do Hs limite (opcionais).
4. As opções de cada etapa ficam no topo do script, em MAIÚSCULAS. Não é preciso mexer no resto.

Para interromper uma etapa: clique no terminal do VS Code e tecle Ctrl+C.
Cada etapa grava um log na pasta do caso (`cvd_build.log`, ...).

Atualizar o cvdtools (no terminal do VS Code, nesta pasta):

    {install_command}
    python -m cvdtools setup

O `setup` atualiza a configuração e os scripts. Scripts que você editou não são sobrescritos: a
versão nova fica ao lado, com `.novo.py` no nome.
