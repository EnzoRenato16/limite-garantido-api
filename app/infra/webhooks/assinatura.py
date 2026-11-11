"""Assinatura dos webhooks.

Cada entrega leva o cabeçalho

    X-LG-Assinatura: t=<unix>,v1=<hex>

onde `v1` é o HMAC-SHA256, com o segredo do parceiro, de `"<t>.<corpo>"`. O timestamp
entra na assinatura: quem capturar uma entrega não consegue reapresentá-la depois, porque
o receptor recusa `t` fora da tolerância (5 minutos por padrão).

`verificar_assinatura` é o que o parceiro roda do lado dele. Pode copiar: só usa a
biblioteca padrão.
"""

from __future__ import annotations

import hashlib
import hmac
import time

CABECALHO_DE_ASSINATURA = "X-LG-Assinatura"
CABECALHO_DO_EVENTO = "X-LG-Evento-Id"
CABECALHO_DO_TIPO = "X-LG-Evento-Tipo"
CABECALHO_DA_ENTREGA = "X-LG-Entrega-Id"
VERSAO = "v1"
TOLERANCIA_PADRAO_EM_SEGUNDOS = 300


class AssinaturaInvalida(Exception):
    """A entrega não veio da plataforma, foi alterada ou é uma reapresentação."""


def _hmac(segredo: str, timestamp: int, corpo: bytes) -> str:
    mensagem = f"{timestamp}.".encode() + corpo
    return hmac.new(segredo.encode(), mensagem, hashlib.sha256).hexdigest()


def assinar(segredo: str, corpo: bytes, timestamp: int | None = None) -> str:
    """Monta o valor do cabeçalho de assinatura para um corpo."""
    instante = int(time.time()) if timestamp is None else timestamp
    return f"t={instante},{VERSAO}={_hmac(segredo, instante, corpo)}"


def verificar_assinatura(
    segredo: str,
    corpo: bytes,
    cabecalho: str | None,
    *,
    tolerancia_em_segundos: int = TOLERANCIA_PADRAO_EM_SEGUNDOS,
    agora: int | None = None,
) -> None:
    """Levanta `AssinaturaInvalida` se a entrega não for confiável.

    Use o corpo exatamente como chegou (bytes), antes de qualquer parse de JSON.
    """
    if not cabecalho:
        raise AssinaturaInvalida("Cabeçalho de assinatura ausente")
    partes = dict(item.split("=", 1) for item in cabecalho.split(",") if "=" in item)
    try:
        timestamp = int(partes["t"])
        recebida = partes[VERSAO]
    except (KeyError, ValueError) as erro:
        raise AssinaturaInvalida("Cabeçalho de assinatura malformado") from erro
    instante = int(time.time()) if agora is None else agora
    if abs(instante - timestamp) > tolerancia_em_segundos:
        raise AssinaturaInvalida("Timestamp fora da tolerância: possível reapresentação")
    # compare_digest: tempo constante, para não vazar a assinatura certa byte a byte.
    if not hmac.compare_digest(_hmac(segredo, timestamp, corpo), recebida):
        raise AssinaturaInvalida("Assinatura não confere")
