"""Identificadores públicos: prefixo + ULID (`gar_01JCD...`).

O ULID começa com o instante em milissegundos, então ordenar por id é ordenar por
criação. Isso deixa a paginação por cursor barata: o cursor é o último id da página.

Dois ids gerados no mesmo milissegundo, no mesmo processo, também saem em ordem: o
segundo é o primeiro mais um (ULID monotônico). Entre processos diferentes a ordem
dentro do mesmo milissegundo não é garantida, e nada no sistema depende dela.
"""

from __future__ import annotations

import secrets
import threading
import time

_ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # base32 de Crockford
_BITS_ALEATORIOS = 80
_MAIOR_ALEATORIO = (1 << _BITS_ALEATORIOS) - 1

_trava = threading.Lock()
_ultimo_instante = 0
_ultimo_aleatorio = 0


def _base32(numero: int, tamanho: int) -> str:
    caracteres = []
    for _ in range(tamanho):
        numero, resto = divmod(numero, 32)
        caracteres.append(_ALFABETO[resto])
    return "".join(reversed(caracteres))


def novo_id(prefixo: str) -> str:
    global _ultimo_instante, _ultimo_aleatorio
    with _trava:
        # max(): se o relógio do sistema voltar, o gerador não volta junto.
        instante = max(time.time_ns() // 1_000_000, _ultimo_instante)
        if instante == _ultimo_instante and _ultimo_aleatorio < _MAIOR_ALEATORIO:
            aleatorio = _ultimo_aleatorio + 1
        else:
            if instante == _ultimo_instante:
                instante += 1  # esgotou o milissegundo (na prática, nunca)
            aleatorio = secrets.randbits(_BITS_ALEATORIOS)
        _ultimo_instante, _ultimo_aleatorio = instante, aleatorio
    return f"{prefixo}_{_base32(instante, 10)}{_base32(aleatorio, 16)}"
