"""Identificadores públicos: prefixo + ULID (`gar_01JCD...`).

O ULID começa com o instante em milissegundos, então ordenar por id é ordenar por
criação. Isso deixa a paginação por cursor barata: o cursor é o último id da página.
"""

from __future__ import annotations

import secrets
import time

_ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # base32 de Crockford
_BITS_ALEATORIOS = 80


def _base32(numero: int, tamanho: int) -> str:
    caracteres = []
    for _ in range(tamanho):
        numero, resto = divmod(numero, 32)
        caracteres.append(_ALFABETO[resto])
    return "".join(reversed(caracteres))


def novo_id(prefixo: str) -> str:
    milissegundos = time.time_ns() // 1_000_000
    aleatorio = secrets.randbits(_BITS_ALEATORIOS)
    return f"{prefixo}_{_base32(milissegundos, 10)}{_base32(aleatorio, 16)}"
