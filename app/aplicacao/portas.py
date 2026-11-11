"""Portas: o que a aplicação precisa do mundo de fora, sem dizer como é feito."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.garantia import Garantia
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


class Custodiante(Protocol):
    def solicitar_bloqueio(self, garantia: Garantia) -> bool:
        """Pede o gravame do ativo. `True` se já saiu confirmado; `False` se fica pendente."""
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
