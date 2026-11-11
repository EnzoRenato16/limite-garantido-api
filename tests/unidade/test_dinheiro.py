from decimal import Decimal

import pytest

from app.dominio.dinheiro import Dinheiro, ValorMonetarioInvalido


def test_cria_a_partir_de_decimal_texto_e_inteiro() -> None:
    assert Dinheiro.de_decimal(Decimal("5000.00")).centavos == 500_000
    assert Dinheiro.de_decimal("33.33").centavos == 3333
    assert Dinheiro.de_decimal(7).centavos == 700


def test_recusa_float_para_nao_herdar_erro_binario() -> None:
    with pytest.raises(ValorMonetarioInvalido):
        Dinheiro.de_decimal(0.1)  # type: ignore[arg-type]


def test_recusa_mais_de_duas_casas_em_vez_de_arredondar_calado() -> None:
    with pytest.raises(ValorMonetarioInvalido):
        Dinheiro.de_decimal("10.001")


@pytest.mark.parametrize("entrada", ["abc", "NaN", "Infinity", ""])
def test_recusa_valores_que_nao_sao_numero_finito(entrada: str) -> None:
    with pytest.raises(ValorMonetarioInvalido):
        Dinheiro.de_decimal(entrada)


def test_centavos_precisam_ser_inteiros() -> None:
    with pytest.raises(TypeError):
        Dinheiro(10.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Dinheiro(True)


def test_soma_subtracao_e_comparacao() -> None:
    a, b = Dinheiro.de_decimal("10.50"), Dinheiro.de_decimal("0.75")
    assert (a + b).para_decimal() == Decimal("11.25")
    assert (a - b).para_decimal() == Decimal("9.75")
    assert b < a
    assert max(a, b) == a
    assert Dinheiro.somar([a, b, b]) == Dinheiro(1200)
    assert Dinheiro.somar([]) == Dinheiro.zero()


def test_para_decimal_sempre_tem_duas_casas() -> None:
    assert str(Dinheiro(425_000).para_decimal()) == "4250.00"
    assert str(Dinheiro(5).para_decimal()) == "0.05"
    assert Dinheiro(-150).texto() == "-1.50"


def test_aplicar_razao_arredonda_para_baixo() -> None:
    # 33,33 x 95% = 31,6635 -> 31,66
    assert Dinheiro.de_decimal("33.33").aplicar_razao(Decimal("0.95")) == Dinheiro(3166)
    # 0,01 x 85% = 0,0085 -> 0,00
    assert Dinheiro(1).aplicar_razao(Decimal("0.85")) == Dinheiro.zero()


def test_aplicar_razao_para_cima_quando_o_lado_seguro_e_o_maior() -> None:
    # Estimativa de perda arredonda para cima: 0,01 x 50% = 0,005 -> 0,01
    assert Dinheiro(1).aplicar_razao(Decimal("0.5"), para_cima=True) == Dinheiro(1)


def test_aplicar_razao_recusa_negativos_e_float() -> None:
    with pytest.raises(ValorMonetarioInvalido):
        Dinheiro(100).aplicar_razao(Decimal("-0.1"))
    with pytest.raises(ValorMonetarioInvalido):
        Dinheiro(-100).aplicar_razao(Decimal("0.5"))
    with pytest.raises(ValorMonetarioInvalido):
        Dinheiro(100).aplicar_razao(0.5)  # type: ignore[arg-type]


def test_formatacao_em_reais() -> None:
    assert Dinheiro(425_000).formatado() == "R$ 4.250,00"
    assert Dinheiro(123_456_789).formatado() == "R$ 1.234.567,89"
    assert Dinheiro(-5).formatado() == "-R$ 0,05"
