"""Portas: o que a aplicação precisa do mundo de fora, sem dizer como é feito."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.dominio.calendario import CalendarioDeDiasUteis
from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.garantia import Garantia, TipoSolicitacao
from app.dominio.parceiro import Parceiro
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import CatalogoRegulatorio, Trilho
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
    # Entrada concluída: o ativo chegou (portabilidade, transferência, TD Garantia) ou a
    # operadora enviou as informações (Res. Conjunta 12).
    CONCLUIDA = "concluida"
    # A contraparte recusou (transferência de custódia, TD Garantia).
    RECUSADA = "recusada"
    # Impedimento à portabilidade (Res. CVM 210, art. 19): ativo em garantia, por exemplo.
    IMPEDIMENTO = "impedimento"


@dataclass(frozen=True, slots=True)
class PedidoDeEntrada:
    """O que vai para a contraparte. `solicitacao_id` torna o pedido idempotente: o
    mesmo id enviado duas vezes é o mesmo pedido."""

    solicitacao_id: str
    contraparte_id: str
    trilho: Trilho
    garantia_id: str
    tipo_ativo: TipoAtivo
    # O valor aplicado do ativo {BRL}.
    valor: Dinheiro
    criado_em: datetime


@dataclass(frozen=True, slots=True)
class RespostaDaContraparte:
    solicitacao_id: str
    status: StatusDaResposta
    # Id do evento do lado da contraparte. É a chave de idempotência das respostas:
    # a mesma conclusão, por callback ou por consulta, tem o mesmo id.
    evento_id: str | None = None
    motivo: str | None = None
    # Só a entidade operadora (Res. Conjunta 12, art. 7º): o que ela informa ao pedido.
    valor_disponivel: Dinheiro | None = None
    tributos_informados: Dinheiro | None = None


@dataclass(frozen=True, slots=True)
class InfoDaContraparte:
    id: str
    nome: str
    descricao: str
    # Todas as contrapartes deste projeto são simuladas.
    simulado: bool
    # Os trilhos que ela atende.
    trilhos: tuple[Trilho, ...]


@dataclass(frozen=True, slots=True)
class LocalDaSolicitacao:
    """Onde mora um pedido feito à contraparte (aberto ou já encerrado)."""

    solicitacao_id: str
    parceiro_id: str
    conta_id: str
    garantia_id: str
    contraparte_id: str
    tipo: TipoSolicitacao


class ContraparteDesconhecida(Exception):
    """Pedido para uma contraparte que não está configurada."""


class Contraparte(Protocol):
    """A outra ponta de um trilho de entrada: quem responde que o ativo chegou.

    Todas as implementações são simuladores (corretora de origem, banco de origem, agente
    de custódia do TD, entidade operadora): o projeto não integra com ninguém.
    """

    def contrapartes(self) -> Sequence[InfoDaContraparte]:
        """Contrapartes disponíveis."""
        ...

    def conhece(self, contraparte_id: str) -> bool: ...

    def solicitar_entrada(self, pedido: PedidoDeEntrada) -> RespostaDaContraparte:
        """Envia o pedido. A resposta já pode vir pronta, ou `pendente`."""
        ...

    def consultar(self, pedido: PedidoDeEntrada) -> RespostaDaContraparte:
        """Situação de um pedido já feito. É o caminho de reserva quando o callback
        não chega."""
        ...


class InfraestruturaDeRegistro(Protocol):
    """Onde o gravame é registrado: B3 (balcão ou central depositária), Selic via TD
    Garantia, escriturador, plataforma da Res. Conjunta 12. É só simulação."""

    @property
    def confirma_na_hora(self) -> bool:
        """O registro de uma garantia da casa sai no mesmo ato? Falso no modo legado."""
        ...

    def registrar(self, garantia: Garantia) -> str:
        """Registra o gravame e devolve o protocolo."""
        ...

    def executar(self, garantia: Garantia, valor_de_mercado: Dinheiro) -> Dinheiro:
        """Vende a garantia: devolve o valor bruto da venda {BRL}."""
        ...

    def baixar(self, garantia: Garantia) -> None:
        """Baixa o gravame na infraestrutura."""
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

    def garantias_aguardando_contraparte(
        self, limite: int, parceiro_id: str | None = None
    ) -> list[tuple[str, str, str]]:
        """Trios (parceiro_id, conta_id, garantia_id) com gravame `solicitado`, até `limite`."""
        ...

    def contas_a_conciliar(self, parceiro_id: str | None = None) -> list[tuple[str, str]]:
        """Pares (parceiro_id, conta_id) com gravame ainda vivo ou dívida em aberto."""
        ...

    def contas_com_baixa_pendente(self) -> list[tuple[str, str]]:
        """Pares (parceiro_id, conta_id) com baixa de gravame pedida e ainda não feita."""
        ...

    def contas_com_emprestimo_a_avaliar(
        self, hoje: date, parceiro_id: str | None = None
    ) -> list[tuple[str, str]]:
        """Pares (parceiro_id, conta_id) com empréstimo aberto e parcela vencida em aberto."""
        ...

    def resposta_ja_registrada(self, contraparte_id: str, evento_id: str) -> bool: ...

    def registrar_resposta(
        self,
        *,
        contraparte_id: str,
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
    def contraparte(self) -> Contraparte: ...

    @property
    def infraestrutura(self) -> InfraestruturaDeRegistro: ...

    @property
    def catalogo(self) -> CatalogoRegulatorio: ...

    @property
    def calendario(self) -> CalendarioDeDiasUteis: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


FabricaDeUnidades = Callable[[], AbstractContextManager[UnidadeDeTrabalho]]
