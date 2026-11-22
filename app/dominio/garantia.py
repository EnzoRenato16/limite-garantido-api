"""Garantia: a relação de crédito sobre um ativo custodiado.

A garantia diz qual ativo, quanto foi aplicado, para qual produto (cartão ou empréstimo)
e por qual trilho entrou. O registro na infraestrutura é o `gravame`, com máquina de
estados própria (`app.dominio.gravame`). O estado da garantia é o estado do gravame.

Regra central: a garantia só gera limite (cartão) ou valor de empréstimo com gravame
`registrado` e sem baixa pedida (`gravame.vigente`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.gravame import EstadoGravame, Gravame
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto, Trilho


class TipoSolicitacao(StrEnum):
    # Pedido de entrada a uma contraparte de fora (portabilidade, transferência de
    # custódia, TD Garantia ou informações da previdência).
    ENTRADA = "entrada"


@dataclass(slots=True)
class Solicitacao:
    """Um pedido feito à contraparte de fora e ainda sem resposta.

    O `id` vai junto no pedido: é ele que torna o pedido idempotente do lado de lá.
    """

    id: str
    tipo: TipoSolicitacao
    criada_em: datetime
    # Prazo do trilho, já calculado fora do domínio (dias úteis do catálogo).
    prazo_ate: datetime
    # Quando a contraparte aceitou o pedido. Nulo enquanto não conseguimos enviar.
    enviada_em: datetime | None = None

    def vencida(self, agora: datetime) -> bool:
        return agora > self.prazo_ate


@dataclass(frozen=True, slots=True)
class InformacoesDaOperadora:
    """O que a entidade operadora informa pela plataforma da Res. Conjunta 12 (art. 7º)."""

    valor_disponivel: Dinheiro  # {BRL}
    tributos_informados: Dinheiro  # {BRL}
    recebidas_em: datetime

    @property
    def valor_liquido(self) -> Dinheiro:
        """O que vale como garantia: o disponível menos os tributos informados {BRL}."""
        return self.valor_disponivel - self.tributos_informados

    def __post_init__(self) -> None:
        if self.valor_disponivel.negativo or self.tributos_informados.negativo:
            raise ValorInvalido("Informações da operadora com valor negativo")
        if self.tributos_informados > self.valor_disponivel:
            raise ValorInvalido("Tributos informados acima do valor disponível")


@dataclass(slots=True)
class Garantia:
    id: str
    conta_id: str
    tipo_ativo: TipoAtivo
    # Quanto o cliente aplicou {BRL}: custo de aquisição. O valor de hoje vem do ledger.
    valor_aplicado: Dinheiro
    criada_em: datetime
    # Data da última marcação a mercado.
    marcada_em: date
    produto: Produto
    gravame: Gravame
    # Emissor (conglomerado) do título, para a concentração contra o teto do FGC.
    emissor: str | None = None
    vencimento: date | None = None
    # Prazo de resgate em {dia_util} (0 = D+0); None = desconhecido.
    liquidez_dias: int | None = None
    # Contraparte de fora que responde pela entrada; None na custódia da casa.
    contraparte_id: str | None = None
    # O pedido de entrada em aberto na contraparte.
    solicitacao: Solicitacao | None = None
    # Empréstimo que a garantia cobre (Tarefa 5).
    emprestimo_id: str | None = None
    # Só previdência: tipo do plano e fim da acumulação (Res. Conj. 12, art. 6º, §3º).
    plano: str | None = None
    fim_da_acumulacao: date | None = None
    informacoes: InformacoesDaOperadora | None = None
    # Data da aplicação, para a alíquota do IR na execução; None = data de criação.
    aplicado_em: date | None = None

    @property
    def data_da_aplicacao(self) -> date:
        return self.aplicado_em or self.criada_em.date()

    def __post_init__(self) -> None:
        de_fora = self.gravame.trilho is not Trilho.CASA
        if de_fora != (self.contraparte_id is not None):
            raise ValorInvalido(
                "Garantia de fora exige contraparte_id, e só ela",
                garantia_id=self.id,
                trilho=str(self.gravame.trilho),
            )
        if self.liquidez_dias is not None and self.liquidez_dias < 0:
            raise ValorInvalido("Liquidez em dias úteis não pode ser negativa")

    @property
    def estado(self) -> EstadoGravame:
        return self.gravame.estado

    @property
    def trilho(self) -> Trilho:
        return self.gravame.trilho

    @property
    def gera_limite(self) -> bool:
        """Gera limite de cartão: produto cartão e gravame vigente."""
        return self.produto is Produto.CARTAO and self.gravame.vigente

    @property
    def cobre_divida(self) -> bool:
        """Ainda onerada a favor da SCD: vigente ou já liberada para excussão."""
        return self.gravame.vigente or self.estado is EstadoGravame.LIBERADO_PARA_EXCUSSAO

    @property
    def disponivel_para_emprestimo(self) -> bool:
        return (
            self.produto is Produto.EMPRESTIMO
            and self.gravame.vigente
            and self.emprestimo_id is None
        )
