"""Adaptadores de custódia: quem coloca o gravame no investimento.

Na fase 1 o investimento fica dentro do próprio grupo da plataforma, então o bloqueio é
uma operação interna e sai confirmado na hora. Nas fases 2 e 3 (Tesouro, previdência,
outros custodiantes) a confirmação vem de fora e a garantia espera em `pendente`.
"""

from __future__ import annotations

from app.dominio.garantia import Garantia


class CustodiaInterna:
    def solicitar_bloqueio(self, garantia: Garantia) -> bool:
        return True


class CustodiaAssincrona:
    """Simula um custodiante externo: o pedido é aceito e a confirmação chega depois,
    por `POST /v1/admin/garantias/{id}/confirmacao-de-bloqueio`."""

    def solicitar_bloqueio(self, garantia: Garantia) -> bool:
        return False
