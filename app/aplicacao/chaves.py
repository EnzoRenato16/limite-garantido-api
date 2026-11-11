"""API keys e segredos de webhook.

A chave tem o formato `lgk_<identificador>_<segredo>`. Guardamos o prefixo
(`lgk_<identificador>`, para achar o parceiro) e o SHA-256 da chave inteira. O segredo
tem 256 bits aleatórios, então um hash rápido basta: não há dicionário a atacar.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

_MARCA = "lgk"
_PARTES = 3


@dataclass(frozen=True, slots=True)
class ChaveGerada:
    # Mostrada uma única vez, na criação. Depois disso só existe o hash.
    chave: str
    prefixo: str
    hash: str


def gerar_api_key() -> ChaveGerada:
    chave = f"{_MARCA}_{secrets.token_hex(6)}_{secrets.token_urlsafe(32)}"
    return descrever_api_key(chave)


def descrever_api_key(chave: str) -> ChaveGerada:
    prefixo = prefixo_da_chave(chave)
    if prefixo is None:
        raise ValueError("API key fora do formato lgk_<identificador>_<segredo>")
    return ChaveGerada(chave=chave, prefixo=prefixo, hash=hash_da_chave(chave))


def prefixo_da_chave(chave: str) -> str | None:
    partes = chave.split("_", _PARTES - 1)
    if len(partes) != _PARTES or partes[0] != _MARCA or not partes[1] or not partes[2]:
        return None
    return f"{partes[0]}_{partes[1]}"


def hash_da_chave(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()


def chave_confere(chave: str, hash_esperado: str) -> bool:
    """Comparação em tempo constante, para não vazar o hash pelo tempo de resposta."""
    return hmac.compare_digest(hash_da_chave(chave), hash_esperado)


def gerar_segredo_de_webhook() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"
