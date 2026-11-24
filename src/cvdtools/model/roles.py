"""Papéis lógicos -> nomes de objetos no modelo OrcaFlex.

Em vez de nomes fixos ("Line", "MCV MOBO", "Island Performer"), o código usa
papéis como ``{LINE}`` e a aba ``Papéis`` da configuração diz o nome real.
"""

from __future__ import annotations

import re

_ROLE = re.compile(r"\{([A-Z0-9_]+)\}")


def resolve_name(text: str, roles: dict[str, str]) -> str:
    """Troca ``{ROLE}`` pelo nome do objeto. Texto sem chaves é devolvido como está."""

    def sub(m):
        role = m.group(1)
        if role not in roles or not roles[role]:
            raise KeyError(f"Papel {{{role}}} não definido na aba 'Papéis'")
        return roles[role]

    return _ROLE.sub(sub, str(text))


def has_role(text: str) -> bool:
    return bool(_ROLE.search(str(text)))
