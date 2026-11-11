"""Propriedades do tipo Dinheiro: valem para qualquer valor, não só para exemplos."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.dominio.dinheiro import Dinheiro

centavos = st.integers(min_value=-(10**13), max_value=10**13)
nao_negativos = st.integers(min_value=0, max_value=10**13).map(Dinheiro)
razoes = st.decimals(min_value=Decimal(0), max_value=Decimal(1), places=6)


@given(centavos)
def test_ida_e_volta_por_decimal_e_por_texto(valor: int) -> None:
    dinheiro = Dinheiro(valor)
    assert Dinheiro.de_decimal(dinheiro.para_decimal()) == dinheiro
    assert Dinheiro.de_decimal(dinheiro.texto()) == dinheiro


@given(centavos)
def test_texto_sempre_tem_exatamente_duas_casas(valor: int) -> None:
    inteiro, _, fracao = Dinheiro(valor).texto().partition(".")
    assert len(fracao) == 2
    assert inteiro.lstrip("-").isdigit()


@given(centavos, centavos)
def test_soma_e_subtracao_sao_inversas(a: int, b: int) -> None:
    assert (Dinheiro(a) + Dinheiro(b)) - Dinheiro(b) == Dinheiro(a)
    assert Dinheiro.somar([Dinheiro(a), Dinheiro(b)]) == Dinheiro(a) + Dinheiro(b)


@given(nao_negativos, razoes)
def test_arredondar_para_baixo_nunca_passa_do_valor_exato(valor: Dinheiro, razao: Decimal) -> None:
    exato = valor.centavos * razao
    resultado = valor.aplicar_razao(razao)
    assert resultado.centavos <= exato < resultado.centavos + 1


@given(nao_negativos, razoes)
def test_arredondar_para_cima_nunca_fica_abaixo_do_valor_exato(
    valor: Dinheiro, razao: Decimal
) -> None:
    exato = valor.centavos * razao
    resultado = valor.aplicar_razao(razao, para_cima=True)
    assert resultado.centavos - 1 < exato <= resultado.centavos


@given(nao_negativos, razoes)
def test_ltv_de_ate_cem_por_cento_nunca_gera_limite_maior_que_a_garantia(
    valor: Dinheiro, ltv: Decimal
) -> None:
    assert valor.aplicar_razao(ltv) <= valor


@given(nao_negativos, nao_negativos, razoes)
def test_mais_garantia_nunca_da_menos_limite(a: Dinheiro, b: Dinheiro, ltv: Decimal) -> None:
    menor, maior = sorted([a, b])
    assert menor.aplicar_razao(ltv) <= maior.aplicar_razao(ltv)
