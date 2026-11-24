"""Infraestrutura de registro SIMULADA: onde o gravame seria registrado de verdade.

O projeto não integra com a B3, o Selic nem a plataforma da Res. Conjunta 12: este módulo
só devolve um protocolo com cara de protocolo, vende ao valor de mercado e baixa sem erro.
O protocolo é `SIM-<infraestrutura>-<id da garantia>`, estável para a mesma garantia.
"""

from __future__ import annotations

from app.aplicacao.portas import InfraestruturaDeRegistro
from app.config import Configuracao
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import InfraestruturaIndisponivel
from app.dominio.garantia import Garantia


class InfraestruturaSimulada:
    """`confirma_na_hora=False` é o modo legado: a garantia da casa só registra depois de
    uma confirmação administrativa (`CUSTODIA_MODO=assincrona`)."""

    def __init__(self, *, confirma_na_hora: bool = True) -> None:
        self._confirma_na_hora = confirma_na_hora

    @property
    def confirma_na_hora(self) -> bool:
        return self._confirma_na_hora

    def registrar(self, garantia: Garantia) -> str:
        return f"SIM-{garantia.gravame.infraestrutura}-{garantia.id}"

    def executar(self, garantia: Garantia, valor_de_mercado: Dinheiro) -> Dinheiro:
        # A venda simulada rende o valor de mercado: sem deságio, sem atraso.
        return valor_de_mercado

    def baixar(self, garantia: Garantia) -> None:
        # Nada a fazer: a baixa na infraestrutura simulada é sempre aceita.
        return None


def _confere_o_protocolo(infraestrutura: InfraestruturaSimulada) -> InfraestruturaDeRegistro:
    return infraestrutura


class InfraestruturaDesligada:
    """Não há registro real: toda operação recusa com 503, sem fingir protocolo."""

    confirma_na_hora = False

    def _recusar(self) -> InfraestruturaIndisponivel:
        return InfraestruturaIndisponivel(
            "Não há infraestrutura de registro de gravame ligada neste ambiente"
        )

    def registrar(self, garantia: Garantia) -> str:
        raise self._recusar()

    def executar(self, garantia: Garantia, valor_de_mercado: Dinheiro) -> Dinheiro:
        raise self._recusar()

    def baixar(self, garantia: Garantia) -> None:
        raise self._recusar()


def montar_infraestrutura(config: Configuracao) -> InfraestruturaDeRegistro:
    if config.infraestrutura_efetiva == "desligada":
        return InfraestruturaDesligada()
    return InfraestruturaSimulada(confirma_na_hora=config.custodia_modo == "interna")
