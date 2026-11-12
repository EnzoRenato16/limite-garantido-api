from __future__ import annotations

from decimal import Decimal, localcontext

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.dominio.erros import ValorInvalido
from app.dominio.taxas import (
    DIAS_CORRIDOS_NO_ANO,
    DIAS_UTEIS_NO_ANO,
    MESES_NO_ANO,
    Fracao,
    FrequenciaAoAno,
    TaxaAoAno,
    TaxaAoMes,
    dias_para_meses,
)


def test_constantes() -> None:
    assert (MESES_NO_ANO, DIAS_UTEIS_NO_ANO, DIAS_CORRIDOS_NO_ANO) == (12, 252, 365)


def test_taxa_ao_ano_vira_mensal_composta() -> None:
    mensal = TaxaAoAno(Decimal("0.1365")).ao_mes()
    assert isinstance(mensal, TaxaAoMes)
    with localcontext() as ctx:
        ctx.prec = 34
        de_volta = (1 + mensal.valor) ** 12 - 1
        assert abs(de_volta - Decimal("0.1365")) < Decimal("1e-20")


def test_ao_mes_e_o_mesmo_que_21_dias_uteis() -> None:
    anual = Decimal("0.1365")
    with localcontext() as ctx:
        ctx.prec = 34
        esperado = (1 + anual) ** (Decimal(21) / Decimal(252)) - 1
        assert abs(TaxaAoAno(anual).ao_mes().valor - esperado) < Decimal("1e-25")


def test_composicao_cdi_mais_spread() -> None:
    composta = TaxaAoAno(Decimal("0.10")).composta_com(TaxaAoAno(Decimal("0.03")))
    assert composta == TaxaAoAno(Decimal("0.133"))
    assert composta.valor == Decimal("0.133")


def test_frequencia_divide_por_12() -> None:
    assert FrequenciaAoAno(Decimal("0.6")).ao_mes() == Decimal("0.05")
    assert FrequenciaAoAno(Decimal(0)).ao_mes() == 0


def test_dias_para_meses() -> None:
    with localcontext() as ctx:
        ctx.prec = 34
        dias = Decimal(365) / Decimal(12)
    assert abs(dias_para_meses(dias) - Decimal(1)) < Decimal("1e-25")
    assert dias_para_meses(Decimal(0)) == 0


@pytest.mark.parametrize("tipo", [TaxaAoAno, TaxaAoMes, FrequenciaAoAno, Fracao])
@pytest.mark.parametrize("ruim", [Decimal("-0.01"), Decimal("Infinity"), Decimal("NaN")])
def test_recusa_negativo_e_infinito(tipo: type, ruim: Decimal) -> None:
    with pytest.raises(ValorInvalido):
        tipo(ruim)


def test_fracao_fica_entre_zero_e_um() -> None:
    assert Fracao(Decimal(0)).valor == 0
    assert Fracao(Decimal(1)).valor == 1
    with pytest.raises(ValorInvalido):
        Fracao(Decimal("1.0001"))


def test_recusa_quem_nao_e_decimal() -> None:
    with pytest.raises(ValorInvalido):
        TaxaAoAno(0.1)  # type: ignore[arg-type]


def test_dias_negativos_sao_recusados() -> None:
    with pytest.raises(ValorInvalido):
        dias_para_meses(Decimal(-1))


_taxas = st.decimals(min_value=0, max_value=5, places=6)


@given(_taxas, _taxas)
def test_ao_mes_e_monotona(a: Decimal, b: Decimal) -> None:
    menor, maior = sorted((a, b))
    assert TaxaAoAno(menor).ao_mes().valor <= TaxaAoAno(maior).ao_mes().valor


def test_ao_mes_de_zero_e_zero() -> None:
    assert TaxaAoAno(Decimal(0)).ao_mes().valor == 0
