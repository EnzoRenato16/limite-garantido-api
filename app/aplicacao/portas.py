"""Portas: o que a aplicação precisa do mundo de fora, sem dizer como é feito."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.garantia import Garantia, TipoSolicitacao
from app.dominio.parceiro import Parceiro
from app.dominio.politica import TipoAtivo
from app.dominio.stress import ContaNaCarteira


class Relogio(Protocol):
    def agora(self) -> datetime:
        """Instante atual, em UTC."""
        ...

    def hoje(self) -> date:
        """Data de negócio (fuso de Brasília): é ela que conta atraso e prazo."""
        ...


class StatusDaResposta(StrEnum):
    # Pedido aceito, resposta ainda por vir.
    PENDENTE = "pendente"
    CONFIRMADA = "confirmada"
    RECUSADA = "recusada"


@dataclass(frozen=True, slots=True)
class PedidoAoCustodiante:
    """O que vai para o custodiante. `solicitacao_id` torna o pedido idempotente: o
    mesmo id enviado duas vezes é o mesmo pedido."""

    solicitacao_id: str
    custodiante_id: str
    tipo: TipoSolicitacao
    garantia_id: str
    tipo_ativo: TipoAtivo
    # Gravame e liberação: o valor da garantia. Execução: quanto vender.
    valor: Dinheiro
    criado_em: datetime


@dataclass(frozen=True, slots=True)
class RespostaDoCustodiante:
    solicitacao_id: str
    status: StatusDaResposta
    # Id do evento do lado do custodiante. É a chave de idempotência das respostas:
    # a mesma confirmação, por callback ou por consulta, tem o mesmo id.
    evento_id: str | None = None
    # Execução confirmada: quanto a venda rendeu.
    valor_obtido: Dinheiro | None = None
    motivo: str | None = None


@dataclass(frozen=True, slots=True)
class InfoDoCustodiante:
    id: str
    nome: str
    descricao: str
    # Todos os custodiantes externos deste projeto são simulados.
    simulado: bool
    # Quanto ele tem para responder a um pedido antes de o pedido expirar.
    prazo_de_resposta: timedelta


@dataclass(frozen=True, slots=True)
class LocalDaSolicitacao:
    """Onde mora um pedido feito ao custodiante (aberto ou já encerrado)."""

    solicitacao_id: str
    parceiro_id: str
    conta_id: str
    garantia_id: str
    custodiante_id: str
    tipo: TipoSolicitacao


class CustodianteDesconhecido(Exception):
    """Pedido para um custodiante que não está configurado."""


class Custodiante(Protocol):
    """Quem coloca e tira o gravame do investimento.

    `solicitar_bloqueio` é a fase 1 (investimento dentro de casa). O resto é a fase 2:
    custodiante externo, que responde depois e pode recusar ou não responder.
    """

    def solicitar_bloqueio(self, garantia: Garantia) -> bool:
        """Pede o gravame do ativo. `True` se já saiu confirmado; `False` se fica pendente."""
        ...

    def custodiantes(self) -> Sequence[InfoDoCustodiante]:
        """Custodiantes externos disponíveis."""
        ...

    def conhece(self, custodiante_id: str) -> bool: ...

    def solicitar_gravame(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante: ...

    def solicitar_liberacao(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante: ...

    def solicitar_execucao(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante: ...

    def consultar(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        """Situação de um pedido já feito. É o caminho de reserva quando o callback
        não chega."""
        ...


class ProvedorDePrecos(Protocol):
    def preco(self, tipo_ativo: TipoAtivo, dia: date) -> Decimal:
        """Preço de referência do ativo no dia: {BRL} por unidade."""
        ...


class RepositorioDeContas(Protocol):
    def adicionar(self, conta: Conta) -> None: ...

    def obter(self, parceiro_id: str, conta_id: str) -> Conta | None: ...

    def obter_para_atualizar(self, parceiro_id: str, conta_id: str) -> Conta | None:
        """Carrega a conta com trava de linha: quem vier depois espera."""
        ...

    def obter_pela_garantia_para_atualizar(
        self, garantia_id: str, parceiro_id: str | None = None
    ) -> Conta | None: ...

    def salvar(self, conta: Conta, parceiro: Parceiro) -> None:
        """Grava estado, lançamentos e eventos (outbox). Levanta conflito se a versão mudou."""
        ...

    def carteira_do_parceiro(self, parceiro_id: str) -> Dinheiro:
        """Soma dos saldos devedores de todas as contas do parceiro."""
        ...

    def carteira_para_stress(self, parceiro_id: str) -> list[ContaNaCarteira]: ...

    def contas_com_garantia_a_marcar(
        self, hoje: date, parceiro_id: str | None = None
    ) -> Sequence[tuple[str, str]]:
        """Pares (parceiro_id, conta_id) com garantia ativa ainda não marcada hoje."""
        ...

    def contas_com_prazo_vencido(
        self, hoje: date, parceiro_id: str | None = None
    ) -> Sequence[tuple[str, str]]:
        """Pares (parceiro_id, conta_id) com fatura vencida ou chamada de margem vencida."""
        ...

    def localizar_solicitacao(self, solicitacao_id: str) -> LocalDaSolicitacao | None: ...

    def garantias_aguardando_custodiante(
        self, parceiro_id: str | None = None
    ) -> Sequence[tuple[str, str, str]]:
        """Trios (parceiro_id, conta_id, garantia_id) que esperam resposta de custódia."""
        ...

    def resposta_ja_registrada(self, custodiante_id: str, evento_id: str) -> bool: ...

    def registrar_resposta(
        self,
        *,
        custodiante_id: str,
        evento_id: str,
        solicitacao_id: str,
        garantia_id: str,
        parceiro_id: str,
        status: StatusDaResposta,
        canal: str,
        resultado: str,
        recebida_em: datetime,
        valor_obtido: Dinheiro | None = None,
        motivo: str | None = None,
    ) -> None:
        """Grava a resposta. Levanta `RespostaDuplicada` se o evento já foi registrado."""
        ...

    def antecipar_prazo(self, solicitacao_id: str, prazo_ate: datetime) -> None:
        """Só o sandbox usa: faz o prazo de um pedido vencer agora."""
        ...


class RepositorioDeParceiros(Protocol):
    def adicionar(self, parceiro: Parceiro, chave_prefixo: str, chave_hash: str) -> None: ...

    def obter(self, parceiro_id: str) -> Parceiro | None: ...

    def salvar(self, parceiro: Parceiro) -> None: ...

    def trocar_chave(self, parceiro_id: str, chave_prefixo: str, chave_hash: str) -> None: ...

    def travar(self, parceiro_id: str) -> None:
        """Trava a linha do parceiro até o fim da transação (serializa o teto da carteira)."""
        ...


class UnidadeDeTrabalho(Protocol):
    """Uma transação: repositórios que enxergam a mesma sessão, mais commit e rollback."""

    @property
    def contas(self) -> RepositorioDeContas: ...

    @property
    def parceiros(self) -> RepositorioDeParceiros: ...

    @property
    def relogio(self) -> Relogio: ...

    @property
    def custodiante(self) -> Custodiante: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


FabricaDeUnidades = Callable[[], AbstractContextManager[UnidadeDeTrabalho]]
