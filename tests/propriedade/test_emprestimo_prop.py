"""Propriedades do empréstimo: sequências sorteadas de pagamentos e viradas de dia.

- o ledger fecha em zero;
- o saldo devedor nunca fica negativo;
- a previdência nunca executa com 90 dias ou menos de atraso, nem antes de 10 dias
  depois do `aviso_10_em`;
- a cronograma Price fecha o principal e o saldo em zero.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.dominio.dinheiro import Dinheiro
from app.dominio.emprestimo import EstadoEmprestimo, cronograma_price
from app.dominio.erros import ValorInvalido
from app.dominio.gravame import EstadoGravame
from app.dominio.politica import TipoAtivo
from tests.apoio import prazo_da_baixa
from tests.unidade.test_emprestimo import (
    CAT,
    POL,
    _ativo,
    _contratar,
    _previdencia,
    nova_conta,
)

OPERACAO = st.one_of(
    st.tuples(st.just("dia"), st.integers(min_value=1, max_value=45)),
    st.tuples(st.just("pagar"), st.integers(min_value=1, max_value=100)),
)


@settings(max_examples=150, deadline=None)
@given(
    operacoes=st.lists(OPERACAO, max_size=25),
    previdencia=st.booleans(),
    valor=st.integers(min_value=1_000, max_value=9_000),
    parcelas=st.integers(min_value=1, max_value=24),
)
def test_emprestimo_sob_pagamentos_e_viradas_de_dia(
    operacoes: list[tuple[str, int]], previdencia: bool, valor: int, parcelas: int
) -> None:
    conta = nova_conta()
    if previdencia:
        garantia = _previdencia(conta, fim=date(2060, 1, 1))
        protocolos = {garantia.id: "SIM-rc12-1"}
    else:
        garantia = _ativo(conta, TipoAtivo.ACOES, "30000.00")
        protocolos = {}
    emprestimo = _contratar(
        conta, [garantia], valor=f"{valor}.00", parcelas=parcelas, protocolos=protocolos
    )
    hoje = emprestimo.parcelas[0].vencimento - timedelta(days=3)
    for tipo, n in operacoes:
        agora = datetime.combine(hoje, time(12), tzinfo=UTC)
        if tipo == "dia":
            hoje += timedelta(days=n)
            agora = datetime.combine(hoje, time(12), tzinfo=UTC)
            dias = emprestimo.dias_em_atraso(hoje)
            antes = garantia.estado
            conta.virar_o_dia(POL, CAT, agora, hoje, prazo_da_baixa(hoje))
            executou = antes is EstadoGravame.REGISTRADO and garantia.estado is (
                EstadoGravame.EXECUTADO
            )
            if executou and previdencia:
                assert dias > 90
                assert emprestimo.aviso_10_em is not None
                assert hoje >= emprestimo.aviso_10_em + timedelta(days=10)
        elif not emprestimo.encerrado:
            saldo = emprestimo.saldo_devedor(hoje)
            parte = Dinheiro(saldo.centavos * n // 100)
            if parte.positivo:
                conta.pagar_parcela(emprestimo.id, parte, agora, hoje, prazo_da_baixa(hoje))
        assert conta.saldos.debitos_menos_creditos().nulo
        assert not emprestimo.saldo_devedor(hoje).negativo
        if emprestimo.estado is EstadoEmprestimo.QUITADO:
            assert emprestimo.saldo_devedor(hoje).nulo


@given(
    centavos=st.integers(min_value=100_00, max_value=5_000_000),
    taxa=st.decimals(min_value=Decimal(0), max_value=Decimal("0.10"), places=4),
    quantidade=st.integers(min_value=1, max_value=60),
)
def test_price_fecha_o_principal_e_o_saldo(centavos: int, taxa: Decimal, quantidade: int) -> None:
    principal = Dinheiro(centavos)
    try:
        parcelas = cronograma_price(principal, taxa, quantidade, date(2026, 4, 30))
    except ValorInvalido:
        # O arredondamento para cima quitaria o saldo antes da última parcela: recusado.
        return
    assert sum(p.amortizacao.centavos for p in parcelas) == centavos
    assert all(p.valor == p.juros + p.amortizacao and p.valor.positivo for p in parcelas)
    saldo = principal
    for p in parcelas:
        assert p.juros == saldo.aplicar_razao(taxa)
        saldo = saldo - p.amortizacao
        assert not saldo.negativo
    assert saldo.nulo
