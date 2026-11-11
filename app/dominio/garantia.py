"""Garantia: o investimento bloqueado, com máquina de estados explícita.

    pendente ──► bloqueada ──► liberada
                    │  ▲
                    │  └── (a sobra continua bloqueada)
                    ├──► parcialmente_executada ──► liberada | executada
                    └──► executada

`pendente` existe porque, fora de casa (Tesouro, previdência, outro custodiante), o
gravame é confirmado por um terceiro e pode demorar. Dentro de casa a confirmação é
imediata.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import TransicaoInvalida
from app.dominio.politica import TipoAtivo


class EstadoGarantia(StrEnum):
    PENDENTE = "pendente"
    BLOQUEADA = "bloqueada"
    PARCIALMENTE_EXECUTADA = "parcialmente_executada"
    LIBERADA = "liberada"
    EXECUTADA = "executada"


TRANSICOES_PERMITIDAS: Mapping[EstadoGarantia, frozenset[EstadoGarantia]] = {
    EstadoGarantia.PENDENTE: frozenset({EstadoGarantia.BLOQUEADA}),
    EstadoGarantia.BLOQUEADA: frozenset(
        {
            EstadoGarantia.LIBERADA,
            EstadoGarantia.EXECUTADA,
            EstadoGarantia.PARCIALMENTE_EXECUTADA,
        }
    ),
    EstadoGarantia.PARCIALMENTE_EXECUTADA: frozenset(
        {
            EstadoGarantia.LIBERADA,
            EstadoGarantia.EXECUTADA,
            # Uma segunda execução parcial mantém o estado.
            EstadoGarantia.PARCIALMENTE_EXECUTADA,
        }
    ),
    EstadoGarantia.LIBERADA: frozenset(),
    EstadoGarantia.EXECUTADA: frozenset(),
}

# Estados em que a garantia está de fato bloqueada e conta para o limite.
ESTADOS_ATIVOS = frozenset({EstadoGarantia.BLOQUEADA, EstadoGarantia.PARCIALMENTE_EXECUTADA})


@dataclass(slots=True)
class Garantia:
    id: str
    conta_id: str
    tipo_ativo: TipoAtivo
    # Quanto o cliente aplicou. É histórico: o valor de hoje vem do ledger.
    valor_aplicado: Dinheiro
    estado: EstadoGarantia
    criada_em: datetime
    # Data da última marcação a mercado.
    marcada_em: date

    @property
    def ativa(self) -> bool:
        return self.estado in ESTADOS_ATIVOS

    def transicionar(self, novo_estado: EstadoGarantia) -> None:
        if novo_estado not in TRANSICOES_PERMITIDAS[self.estado]:
            raise TransicaoInvalida(
                f"Garantia {self.id} está {self.estado} e não pode ir para {novo_estado}",
                garantia_id=self.id,
                estado_atual=str(self.estado),
                estado_pedido=str(novo_estado),
            )
        self.estado = novo_estado

    def exigir_ativa(self, operacao: str) -> None:
        if not self.ativa:
            raise TransicaoInvalida(
                f"Não dá para {operacao} uma garantia {self.estado}",
                garantia_id=self.id,
                estado_atual=str(self.estado),
            )
