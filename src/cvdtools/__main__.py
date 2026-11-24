"""Permite rodar ``python -m cvdtools ...`` (equivale ao comando ``cvd``).

A proteção ``if __name__ == "__main__"`` é obrigatória no Windows: os processos paralelos
(geração de estados de mar, simulações, pós-processamento) importam este módulo de novo
como ``__mp_main__``; sem ela, cada processo filho rodaria o comando inteiro outra vez.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
