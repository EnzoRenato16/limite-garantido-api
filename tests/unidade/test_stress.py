from decimal import Decimal

import pytest

from app.dominio.erros import ValorInvalido
from app.dominio.politica import TipoAtivo
from app.dominio.stress import ContaNaCarteira, GarantiaNaCarteira, simular_stress
from tests.apoio import politica, reais

POL = politica()


def _conta(conta_id: str, divida: str, *garantias: tuple[TipoAtivo, str]) -> ContaNaCarteira:
    return ContaNaCarteira(
        conta_id=conta_id,
        divida=reais(divida),
        garantias=tuple(GarantiaNaCarteira(tipo, reais(valor)) for tipo, valor in garantias),
    )


CARTEIRA = [
    _conta("cta_acoes", "5500.00", (TipoAtivo.ACOES, "10000.00")),  # limite 6.000
    _conta("cta_pre", "7500.00", (TipoAtivo.TESOURO_PREFIXADO, "10000.00")),  # limite 8.000
    _conta("cta_cdb", "1000.00", (TipoAtivo.CDB, "5000.00")),  # folgada
]
CHOQUE = {TipoAtivo.ACOES: Decimal("-0.30"), TipoAtivo.TESOURO_PREFIXADO: Decimal("-0.10")}


def test_choque_conta_as_novas_chamadas_e_o_deficit() -> None:
    resultado = simular_stress(CARTEIRA, CHOQUE, POL)
    assert resultado.contas_analisadas == 3
    assert resultado.contas_em_chamada_antes == 0
    assert resultado.contas_em_chamada_depois == 2
    assert resultado.novas_chamadas_de_margem == 2
    assert resultado.valor_das_garantias_antes == reais("25000.00")
    assert resultado.valor_das_garantias_depois == reais("21000.00")  # 7.000 + 9.000 + 5.000
    # ações: 5.500 - 7.000 x 60% = 1.300 | prefixado: 7.500 - 9.000 x 80% = 300
    assert resultado.deficit_de_margem_total == reais("1600.00")
    # Vendendo tudo ao preço estressado ainda cobre: 7.000 > 5.500 e 9.000 > 7.500.
    assert resultado.exposicao_descoberta_total == reais("0.00")
    assert resultado.perda_esperada == reais("0.00")


def test_exposicao_descoberta_e_perda_esperada() -> None:
    carteira = [_conta("cta_1", "5500.00", (TipoAtivo.ACOES, "10000.00"))]
    choque = {TipoAtivo.ACOES: Decimal("-0.60")}  # garantia vai a 4.000
    pior_caso = simular_stress(carteira, choque, POL)
    assert pior_caso.exposicao_descoberta_total == reais("1500.00")
    assert pior_caso.perda_esperada == reais("1500.00")
    com_pd = simular_stress(carteira, choque, POL, Decimal("0.333"))
    assert com_pd.perda_esperada == reais("499.50")
    assert simular_stress(carteira, choque, POL, Decimal(0)).perda_esperada == reais("0.00")


def test_conta_que_ja_estava_em_chamada_nao_conta_como_nova() -> None:
    carteira = [_conta("cta_1", "7000.00", (TipoAtivo.ACOES, "10000.00"))]
    resultado = simular_stress(carteira, CHOQUE, POL)
    assert resultado.contas_em_chamada_antes == 1
    assert resultado.novas_chamadas_de_margem == 0


def test_sem_choque_nada_muda_e_carteira_vazia_da_zeros() -> None:
    sem_choque = simular_stress(CARTEIRA, {}, POL)
    assert sem_choque.valor_das_garantias_depois == sem_choque.valor_das_garantias_antes
    assert sem_choque.contas_em_chamada_depois == 0
    vazio = simular_stress([], CHOQUE, POL)
    assert vazio.contas_analisadas == 0
    assert vazio.perda_esperada == reais("0.00")


def test_valor_estressado_arredonda_para_baixo() -> None:
    carteira = [_conta("cta_1", "0.00", (TipoAtivo.ACOES, "0.03"))]
    resultado = simular_stress(carteira, {TipoAtivo.ACOES: Decimal("-0.50")}, POL)
    assert resultado.valor_das_garantias_depois == reais("0.01")  # 0,015 -> 0,01


@pytest.mark.parametrize("choque", ["-1.01", "1.5", "NaN"])
def test_choque_fora_do_intervalo_e_recusado(choque: str) -> None:
    with pytest.raises(ValorInvalido):
        simular_stress(CARTEIRA, {TipoAtivo.ACOES: Decimal(choque)}, POL)


def test_probabilidade_fora_do_intervalo_e_recusada() -> None:
    with pytest.raises(ValorInvalido):
        simular_stress(CARTEIRA, CHOQUE, POL, Decimal("1.1"))
