"""Ledger de partidas dobradas.

Toda movimentação vira um `Lancamento` com partidas a débito e a crédito que somam o
mesmo valor. Saldos (dívida, valor das garantias) são sempre a soma dessas partidas.

Plano de contas, por conta de cliente:

| Conta contábil          | Natureza | O que representa                                         |
|-------------------------|----------|----------------------------------------------------------|
| garantias_custodiadas   | devedora | Valor de mercado dos investimentos bloqueados            |
| garantias_a_devolver    | credora  | Contrapartida: a garantia é do cliente e volta para ele  |
| recebiveis_a_faturar    | devedora | Compras aprovadas que ainda não entraram em fatura       |
| recebiveis_faturados    | devedora | Fatura fechada e ainda não paga                          |
| liquidacao_a_pagar      | credora  | O que a plataforma deve à rede pelas compras aprovadas   |
| caixa_recebimentos      | devedora | Dinheiro que entrou: pagamentos e execução de garantia   |

As duas primeiras são contas de compensação: registram a garantia recebida sem misturar
com o dinheiro da operação de crédito.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import LancamentoDesbalanceado


class ContaContabil(StrEnum):
    GARANTIAS_CUSTODIADAS = "garantias_custodiadas"
    GARANTIAS_A_DEVOLVER = "garantias_a_devolver"
    RECEBIVEIS_A_FATURAR = "recebiveis_a_faturar"
    RECEBIVEIS_FATURADOS = "recebiveis_faturados"
    LIQUIDACAO_A_PAGAR = "liquidacao_a_pagar"
    CAIXA_RECEBIMENTOS = "caixa_recebimentos"


CONTAS_DE_NATUREZA_DEVEDORA = frozenset(
    {
        ContaContabil.GARANTIAS_CUSTODIADAS,
        ContaContabil.RECEBIVEIS_A_FATURAR,
        ContaContabil.RECEBIVEIS_FATURADOS,
        ContaContabil.CAIXA_RECEBIMENTOS,
    }
)


class Sentido(StrEnum):
    DEBITO = "D"
    CREDITO = "C"


class TipoLancamento(StrEnum):
    BLOQUEIO_GARANTIA = "bloqueio_garantia"
    AJUSTE_MARCACAO = "ajuste_marcacao"
    LIBERACAO_GARANTIA = "liberacao_garantia"
    COMPRA = "compra"
    FECHAMENTO_FATURA = "fechamento_fatura"
    PAGAMENTO = "pagamento"
    EXECUCAO_GARANTIA = "execucao_garantia"


@dataclass(frozen=True, slots=True)
class Partida:
    conta_contabil: ContaContabil
    sentido: Sentido
    valor: Dinheiro
    garantia_id: str | None = None


def debito(conta: ContaContabil, valor: Dinheiro, garantia_id: str | None = None) -> Partida:
    return Partida(conta, Sentido.DEBITO, valor, garantia_id)


def credito(conta: ContaContabil, valor: Dinheiro, garantia_id: str | None = None) -> Partida:
    return Partida(conta, Sentido.CREDITO, valor, garantia_id)


@dataclass(frozen=True, slots=True)
class Lancamento:
    id: str
    tipo: TipoLancamento
    partidas: tuple[Partida, ...]
    referencia_id: str
    descricao: str
    criado_em: datetime

    def __post_init__(self) -> None:
        if len(self.partidas) < 2:
            raise LancamentoDesbalanceado("Um lançamento precisa de pelo menos duas partidas")
        if any(not partida.valor.positivo for partida in self.partidas):
            raise LancamentoDesbalanceado("Toda partida precisa ter valor positivo")
        debitos, creditos = self.total_de_debitos, self.total_de_creditos
        if debitos != creditos:
            raise LancamentoDesbalanceado(
                f"Débitos ({debitos}) e créditos ({creditos}) não fecham",
                debitos=debitos.texto(),
                creditos=creditos.texto(),
            )

    @property
    def total_de_debitos(self) -> Dinheiro:
        return Dinheiro.somar(p.valor for p in self.partidas if p.sentido is Sentido.DEBITO)

    @property
    def total_de_creditos(self) -> Dinheiro:
        return Dinheiro.somar(p.valor for p in self.partidas if p.sentido is Sentido.CREDITO)


ChaveDeSaldo = tuple[ContaContabil, str | None]


class Saldos:
    """Saldos de uma conta de cliente, derivados das partidas.

    Por dentro guarda `débitos - créditos` de cada (conta contábil, garantia). Por fora
    devolve o saldo no sentido natural da conta: positivo quando ela está "cheia".
    """

    def __init__(self, debitos_menos_creditos: Mapping[ChaveDeSaldo, Dinheiro] | None = None):
        self._liquido: dict[ChaveDeSaldo, Dinheiro] = dict(debitos_menos_creditos or {})

    def aplicar(self, lancamento: Lancamento) -> None:
        for partida in lancamento.partidas:
            chave = (partida.conta_contabil, partida.garantia_id)
            atual = self._liquido.get(chave, Dinheiro.zero())
            if partida.sentido is Sentido.DEBITO:
                self._liquido[chave] = atual + partida.valor
            else:
                self._liquido[chave] = atual - partida.valor

    def saldo(self, conta: ContaContabil, garantia_id: str | None = None) -> Dinheiro:
        return self._no_sentido_natural(
            conta, self._liquido.get((conta, garantia_id), Dinheiro.zero())
        )

    def saldo_total(self, conta: ContaContabil) -> Dinheiro:
        liquido = Dinheiro.somar(
            valor for (conta_contabil, _), valor in self._liquido.items() if conta_contabil is conta
        )
        return self._no_sentido_natural(conta, liquido)

    def debitos_menos_creditos(self) -> Dinheiro:
        """Soma de tudo. Num ledger íntegro é sempre zero."""
        return Dinheiro.somar(self._liquido.values())

    @staticmethod
    def _no_sentido_natural(conta: ContaContabil, liquido: Dinheiro) -> Dinheiro:
        if conta in CONTAS_DE_NATUREZA_DEVEDORA:
            return liquido
        return Dinheiro.zero() - liquido


def ledger_fecha(lancamentos: Iterable[Lancamento]) -> bool:
    """Confere se a soma de todos os débitos é igual à de todos os créditos."""
    debitos = Dinheiro.zero()
    creditos = Dinheiro.zero()
    for lancamento in lancamentos:
        debitos += lancamento.total_de_debitos
        creditos += lancamento.total_de_creditos
    return debitos == creditos
