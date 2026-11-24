"""Empréstimo parcelado com garantia de investimentos.

Cronograma pela Tabela Price, parcelas em dias corridos de calendário mensal, saldo
devedor sem juros futuros e a régua de execução: o prazo contratual da política para os
ativos em geral e, na previdência (Res. Conjunta 12), mais de 90 dias de atraso, aviso ao
cliente aos 45 dias de atraso e aviso 10 dias antes da liquidação (art. 6º, §2º, IV;
art. 12).

Unidades: valores em {BRL} (`Dinheiro`), taxa em {1/mês} (razão adimensional por mês),
prazos em {dia_corrido}. A parcela arredonda para cima ao centavo, os juros do mês
arredondam para baixo (o saldo nunca sobe por arredondamento) e a última parcela fecha o
saldo em zero.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_CEILING, Decimal, localcontext
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.garantia import Garantia
from app.dominio.ltv import ltv_efetivo
from app.dominio.politica import PoliticaRisco
from app.dominio.regulacao import CatalogoRegulatorio


class EstadoEmprestimo(StrEnum):
    ATIVO = "ativo"
    EM_ATRASO = "em_atraso"
    QUITADO = "quitado"
    LIQUIDADO_POR_EXECUCAO = "liquidado_por_execucao"


@dataclass(slots=True)
class Parcela:
    numero: int
    vencimento: date
    valor: Dinheiro  # {BRL}: juros + amortização
    juros: Dinheiro  # {BRL}
    amortizacao: Dinheiro  # {BRL}
    paga_em: datetime | None = None
    # Quanto já entrou em cada parte (pagamento parcial, na execução).
    juros_pago: Dinheiro = field(default_factory=Dinheiro.zero)
    amortizacao_paga: Dinheiro = field(default_factory=Dinheiro.zero)
    # Início do período da parcela: o vencimento anterior ou a data da contratação.
    inicio: date | None = None

    @property
    def inicio_do_periodo(self) -> date:
        return self.inicio or _somar_meses(self.vencimento, -1)

    def juros_acumulados(self, hoje: date) -> Dinheiro:
        """Juros do período {BRL} que já valem hoje.

        Parcela exigível: todos. Parcela vincenda: pro rata pelos {dia_corrido} decorridos
        desde o início do período, arredondado para cima (a favor do credor) e nunca acima
        dos juros da parcela.
        """
        if self.exigivel(hoje):
            return self.juros
        periodo = (self.vencimento - self.inicio_do_periodo).days  # {dia_corrido}
        decorridos = min(max((hoje - self.inicio_do_periodo).days, 0), periodo)
        if periodo <= 0:
            return self.juros
        # {centavo} x {dia} / {dia} -> {centavo}, para cima (aritmética inteira exata).
        centavos = -(-self.juros.centavos * decorridos // periodo)
        return min(Dinheiro(centavos), self.juros)

    @property
    def em_aberto(self) -> bool:
        return self.paga_em is None

    def exigivel(self, hoje: date) -> bool:
        """Venceu (ou vence hoje): cobra juros e amortização."""
        return self.vencimento <= hoje

    def em_atraso(self, hoje: date) -> bool:
        return self.em_aberto and self.vencimento < hoje

    def dias_em_atraso(self, hoje: date) -> int:
        """{dia_corrido} depois do vencimento; zero para parcela paga ou ainda no prazo."""
        return max((hoje - self.vencimento).days, 0) if self.em_aberto else 0

    def juros_devido(self, hoje: date) -> Dinheiro:
        """Juros ainda devidos {BRL}: os acumulados até hoje menos os já pagos."""
        if not self.em_aberto:
            return Dinheiro.zero()
        return (self.juros_acumulados(hoje) - self.juros_pago).nao_negativo()

    def amortizacao_devida(self) -> Dinheiro:
        return Dinheiro.zero() if not self.em_aberto else self.amortizacao - self.amortizacao_paga

    def devido(self, hoje: date) -> Dinheiro:
        return self.juros_devido(hoje) + self.amortizacao_devida()

    def aplicar(self, valor: Dinheiro, hoje: date, agora: datetime) -> tuple[Dinheiro, Dinheiro]:
        """Abate até `valor` desta parcela. Devolve (amortização, juros) pagos {BRL}.

        Paga juros primeiro. Parcela vincenda paga os juros pro rata dos dias decorridos
        e, quitada antes do vencimento, é reescrita sem os juros futuros.
        """
        if not self.em_aberto:
            return Dinheiro.zero(), Dinheiro.zero()
        juros = min(valor, self.juros_devido(hoje))
        amortizacao = min(valor - juros, self.amortizacao_devida())
        self.juros_pago += juros
        self.amortizacao_paga += amortizacao
        if self.amortizacao_devida().nulo and self.juros_devido(hoje).nulo:
            self.paga_em = agora
            if not self.exigivel(hoje):
                self.juros = self.juros_pago
                self.valor = self.amortizacao + self.juros
        return amortizacao, juros


@dataclass(slots=True)
class Emprestimo:
    id: str
    conta_id: str
    principal: Dinheiro  # {BRL}
    taxa_ao_mes: Decimal  # {1/mês}
    parcelas: list[Parcela]
    garantia_ids: tuple[str, ...]
    contratado_em: datetime
    estado: EstadoEmprestimo = EstadoEmprestimo.ATIVO
    # Primeiro dia de atraso {dia_corrido} em que a garantia pode ser executada.
    dias_minimos_execucao: int = 30
    # Dia em que os avisos da previdência saíram (Res. Conjunta 12, art. 6º, §2º, IV).
    aviso_45_em: date | None = None
    aviso_10_em: date | None = None

    @property
    def encerrado(self) -> bool:
        return self.estado in (EstadoEmprestimo.QUITADO, EstadoEmprestimo.LIQUIDADO_POR_EXECUCAO)

    @property
    def parcelas_em_aberto(self) -> list[Parcela]:
        return [p for p in self.parcelas if p.em_aberto]

    def saldo_devedor(self, hoje: date) -> Dinheiro:
        """Parcelas exigíveis em aberto mais a amortização das vincendas, com os juros
        pro rata dos dias já decorridos do período (sem juros futuros)."""
        return Dinheiro.somar(p.devido(hoje) for p in self.parcelas)

    def dias_em_atraso(self, hoje: date) -> int:
        """{dia_corrido} da parcela em aberto mais antiga; zero se não há parcela em atraso."""
        return max((p.dias_em_atraso(hoje) for p in self.parcelas), default=0)

    def aplicar_pagamento(
        self, valor: Dinheiro, hoje: date, agora: datetime
    ) -> tuple[Dinheiro, Dinheiro]:
        """Distribui `valor` pelas parcelas em ordem. Devolve (amortização, juros) {BRL}.

        Quem chama garante `valor` <= `saldo_devedor(hoje)`.
        """
        restante = valor
        amortizacao = Dinheiro.zero()
        juros = Dinheiro.zero()
        for parcela in self.parcelas:
            if restante.nulo:
                break
            a, j = parcela.aplicar(restante, hoje, agora)
            amortizacao += a
            juros += j
            restante = restante - a - j
        if restante.positivo:
            raise ValorInvalido("Pagamento maior que o saldo devedor do empréstimo")
        return amortizacao, juros


def _somar_meses(base: date, meses: int) -> date:
    indice = base.year * 12 + (base.month - 1) + meses
    ano, mes = divmod(indice, 12)
    mes += 1
    return date(ano, mes, min(base.day, calendar.monthrange(ano, mes)[1]))


def cronograma_price(
    principal: Dinheiro,
    taxa_ao_mes: Decimal,
    quantidade: int,
    primeiro_vencimento: date,
    contratado_em: date | None = None,
) -> list[Parcela]:
    """Parcelas iguais (Tabela Price), vencendo todo mês no dia do primeiro vencimento.

    A parcela é arredondada para cima ao centavo; os juros de cada mês, para baixo; a
    última parcela fecha o saldo (a soma das amortizações é o principal). `contratado_em`
    abre o primeiro período (sem ele, um mês antes do primeiro vencimento).
    """
    if not principal.positivo:
        raise ValorInvalido("O principal do empréstimo precisa ser positivo")
    if quantidade < 1:
        raise ValorInvalido("O empréstimo precisa de pelo menos uma parcela")
    if not isinstance(taxa_ao_mes, Decimal) or not taxa_ao_mes.is_finite():
        raise ValorInvalido("Taxa do empréstimo inválida")
    if not 0 <= taxa_ao_mes < 1:
        raise ValorInvalido("A taxa ao mês precisa estar em [0, 1)")

    with localcontext() as contexto:
        contexto.prec = 60
        if taxa_ao_mes == 0:
            bruto = Decimal(principal.centavos) / quantidade
        else:
            fator = (1 + taxa_ao_mes) ** -quantidade
            bruto = Decimal(principal.centavos) * taxa_ao_mes / (1 - fator)
        prestacao = Dinheiro(int(bruto.to_integral_value(rounding=ROUND_CEILING)))

    parcelas: list[Parcela] = []
    saldo = principal
    inicio = contratado_em or _somar_meses(primeiro_vencimento, -1)
    for numero in range(1, quantidade + 1):
        juros = saldo.aplicar_razao(taxa_ao_mes)
        amortizacao = saldo if numero == quantidade else min(prestacao - juros, saldo)
        if not amortizacao.positivo:
            raise ValorInvalido(
                "O valor é pequeno demais para este número de parcelas",
                principal=principal.texto(),
                parcelas=quantidade,
            )
        vencimento = _somar_meses(primeiro_vencimento, numero - 1)
        parcelas.append(
            Parcela(
                numero=numero,
                vencimento=vencimento,
                valor=amortizacao + juros,
                juros=juros,
                amortizacao=amortizacao,
                inicio=inicio,
            )
        )
        inicio = vencimento
        saldo = saldo - amortizacao
    return parcelas


def valor_maximo_do_emprestimo(
    garantias: Iterable[Garantia],
    politica: PoliticaRisco,
    catalogo: CatalogoRegulatorio,
    hoje: date,
    valor_de: Callable[[Garantia], Dinheiro] | None = None,
) -> Dinheiro:
    """Quanto as garantias disponíveis sustentam de empréstimo {BRL}.

    Só entra garantia `disponivel_para_emprestimo` (produto empréstimo, gravame vigente e
    sem empréstimo). Cada garantia vale `valor x LTV efetivo`, arredondado para baixo.
    `valor_de` dá o valor de mercado de cada uma (a conta o lê do ledger); sem ele, vale o
    aplicado, ou o líquido informado pela operadora na previdência.
    """

    def padrao(garantia: Garantia) -> Dinheiro:
        informacoes = garantia.informacoes
        return informacoes.valor_liquido if informacoes else garantia.valor_aplicado

    valor_da = valor_de or padrao
    total = Dinheiro.zero()
    for garantia in garantias:
        if garantia.disponivel_para_emprestimo:
            total += valor_da(garantia).aplicar_razao(
                ltv_efetivo(garantia, politica, catalogo, hoje)
            )
    return total
