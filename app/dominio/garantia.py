"""Garantia: o investimento bloqueado, com máquina de estados explícita.

Há duas origens, cada uma com a sua tabela de transições.

Interna (fase 1, o investimento fica dentro do grupo da plataforma)::

    pendente ──► bloqueada ──► liberada
       │            ├──► parcialmente_executada ──► liberada | executada
       │            └──► executada
       └──► gravame_expirado   (só no modo legado de custódia assíncrona)

Externa (fase 2, o investimento está em outro custodiante, que confirma depois)::

    solicitada ──► gravame_pendente ──► gravame_confirmado ──► bloqueada
        │                 │
        └─────────────────┴──► gravame_recusado | gravame_expirado

    bloqueada ──► liberacao_pendente ──► liberada        (ou volta: bloqueada)
    bloqueada ──► execucao_solicitada ──► executada | parcialmente_executada
                                         (ou volta: bloqueada)

O limite só nasce em `bloqueada`. Na externa, isso exige passar por
`gravame_confirmado`: não há outro caminho na tabela.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import TransicaoInvalida, ValorInvalido
from app.dominio.politica import TipoAtivo


class OrigemGarantia(StrEnum):
    INTERNA = "interna"
    EXTERNA = "externa"


class EstadoGarantia(StrEnum):
    PENDENTE = "pendente"
    BLOQUEADA = "bloqueada"
    PARCIALMENTE_EXECUTADA = "parcialmente_executada"
    LIBERADA = "liberada"
    EXECUTADA = "executada"
    # Só da garantia externa (e gravame_expirado também para a interna legada).
    SOLICITADA = "solicitada"
    GRAVAME_PENDENTE = "gravame_pendente"
    GRAVAME_CONFIRMADO = "gravame_confirmado"
    GRAVAME_RECUSADO = "gravame_recusado"
    GRAVAME_EXPIRADO = "gravame_expirado"
    LIBERACAO_PENDENTE = "liberacao_pendente"
    EXECUCAO_SOLICITADA = "execucao_solicitada"


class TipoSolicitacao(StrEnum):
    GRAVAME = "gravame"
    LIBERACAO = "liberacao"
    EXECUCAO = "execucao"


def _tabela(
    transicoes: Mapping[EstadoGarantia, frozenset[EstadoGarantia]],
) -> Mapping[EstadoGarantia, frozenset[EstadoGarantia]]:
    """Completa a tabela com todos os estados: o que não aparece não vai a lugar nenhum."""
    return {estado: transicoes.get(estado, frozenset()) for estado in EstadoGarantia}


_E = EstadoGarantia

TRANSICOES_INTERNAS: Mapping[EstadoGarantia, frozenset[EstadoGarantia]] = _tabela(
    {
        _E.PENDENTE: frozenset({_E.BLOQUEADA, _E.GRAVAME_EXPIRADO}),
        _E.BLOQUEADA: frozenset({_E.LIBERADA, _E.EXECUTADA, _E.PARCIALMENTE_EXECUTADA}),
        # Uma segunda execução parcial mantém o estado.
        _E.PARCIALMENTE_EXECUTADA: frozenset(
            {_E.LIBERADA, _E.EXECUTADA, _E.PARCIALMENTE_EXECUTADA}
        ),
    }
)
# Nome da v1.0, mantido para quem já importava.
TRANSICOES_PERMITIDAS = TRANSICOES_INTERNAS

TRANSICOES_EXTERNAS: Mapping[EstadoGarantia, frozenset[EstadoGarantia]] = _tabela(
    {
        _E.SOLICITADA: frozenset({_E.GRAVAME_PENDENTE, _E.GRAVAME_RECUSADO, _E.GRAVAME_EXPIRADO}),
        _E.GRAVAME_PENDENTE: frozenset(
            {_E.GRAVAME_CONFIRMADO, _E.GRAVAME_RECUSADO, _E.GRAVAME_EXPIRADO}
        ),
        _E.GRAVAME_CONFIRMADO: frozenset({_E.BLOQUEADA}),
        _E.BLOQUEADA: frozenset({_E.LIBERACAO_PENDENTE, _E.EXECUCAO_SOLICITADA}),
        _E.PARCIALMENTE_EXECUTADA: frozenset({_E.LIBERACAO_PENDENTE, _E.EXECUCAO_SOLICITADA}),
        # Recusa ou falta de resposta devolvem a garantia ao bloqueio.
        _E.LIBERACAO_PENDENTE: frozenset({_E.LIBERADA, _E.BLOQUEADA}),
        _E.EXECUCAO_SOLICITADA: frozenset({_E.EXECUTADA, _E.PARCIALMENTE_EXECUTADA, _E.BLOQUEADA}),
    }
)

# Estados em que a garantia está de fato bloqueada e gera limite.
ESTADOS_ATIVOS = frozenset({_E.BLOQUEADA, _E.PARCIALMENTE_EXECUTADA})
# Além dos ativos, a garantia em execução ainda está penhorada e cobre a dívida.
ESTADOS_QUE_COBREM_DIVIDA = ESTADOS_ATIVOS | {_E.EXECUCAO_SOLICITADA}
# Estados em que a garantia espera uma resposta do custodiante.
ESTADOS_AGUARDANDO_CUSTODIANTE = frozenset(
    {_E.SOLICITADA, _E.GRAVAME_PENDENTE, _E.LIBERACAO_PENDENTE, _E.EXECUCAO_SOLICITADA}
)
# Estados de onde não se sai mais.
ESTADOS_FINAIS = frozenset({_E.LIBERADA, _E.EXECUTADA, _E.GRAVAME_RECUSADO, _E.GRAVAME_EXPIRADO})


@dataclass(slots=True)
class Solicitacao:
    """Um pedido feito ao custodiante e ainda sem resposta.

    O `id` vai junto no pedido: é ele que torna o pedido idempotente do lado de lá.
    """

    id: str
    tipo: TipoSolicitacao
    criada_em: datetime
    # Sem resposta até aqui, o pedido expira.
    prazo_ate: datetime
    # Quando o custodiante aceitou o pedido. Nulo enquanto não conseguimos enviar.
    enviada_em: datetime | None = None

    def vencida(self, agora: datetime) -> bool:
        return agora > self.prazo_ate


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
    origem: OrigemGarantia = OrigemGarantia.INTERNA
    custodiante_id: str | None = None
    # O pedido em aberto no custodiante (gravame, liberação ou execução).
    solicitacao: Solicitacao | None = None
    # Valor de garantia que foi mandado vender e ainda não teve a venda confirmada.
    valor_em_execucao: Dinheiro | None = None
    # Motivo da última recusa ou falha informada pelo custodiante.
    motivo: str | None = None

    def __post_init__(self) -> None:
        externa = self.origem is OrigemGarantia.EXTERNA
        if externa != (self.custodiante_id is not None):
            raise ValorInvalido(
                "Garantia externa exige custodiante_id, e só ela",
                garantia_id=self.id,
                origem=str(self.origem),
            )

    @property
    def externa(self) -> bool:
        return self.origem is OrigemGarantia.EXTERNA

    @property
    def gera_limite(self) -> bool:
        return self.estado in ESTADOS_ATIVOS

    @property
    def ativa(self) -> bool:
        """Bloqueada e gerando limite (nome da v1.0)."""
        return self.gera_limite

    @property
    def cobre_divida(self) -> bool:
        return self.estado in ESTADOS_QUE_COBREM_DIVIDA

    @property
    def aguardando_custodiante(self) -> bool:
        return self.estado in ESTADOS_AGUARDANDO_CUSTODIANTE

    def transicionar(self, novo_estado: EstadoGarantia) -> None:
        tabela = TRANSICOES_EXTERNAS if self.externa else TRANSICOES_INTERNAS
        if novo_estado not in tabela[self.estado]:
            # A origem só aparece no erro da externa: o da interna fica como na v1.0.
            extra = {"origem": str(self.origem)} if self.externa else {}
            raise TransicaoInvalida(
                f"Garantia {self.id} está {self.estado} e não pode ir para {novo_estado}",
                garantia_id=self.id,
                estado_atual=str(self.estado),
                estado_pedido=str(novo_estado),
                **extra,
            )
        self.estado = novo_estado

    def exigir_ativa(self, operacao: str) -> None:
        if not self.ativa:
            raise TransicaoInvalida(
                f"Não dá para {operacao} uma garantia {self.estado}",
                garantia_id=self.id,
                estado_atual=str(self.estado),
            )
