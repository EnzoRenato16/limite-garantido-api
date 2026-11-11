from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.dominio.dinheiro import Dinheiro
from app.dominio.politica import TipoAtivo
from app.dominio.stress import ContaNaCarteira, GarantiaNaCarteira, simular_stress
from tests.apoio import politica

POL = politica()

dinheiro = st.integers(0, 10**9).map(Dinheiro)
garantias = st.builds(GarantiaNaCarteira, st.sampled_from(TipoAtivo), dinheiro)
contas = st.builds(
    ContaNaCarteira,
    conta_id=st.uuids().map(str),
    divida=dinheiro,
    garantias=st.lists(garantias, max_size=4).map(tuple),
)
carteiras = st.lists(contas, max_size=8)
quedas = st.decimals(Decimal("-1"), Decimal("0"), places=4)


@given(carteiras, quedas, quedas)
def test_choque_mais_forte_nunca_melhora_a_carteira(
    carteira: list[ContaNaCarteira], a: Decimal, b: Decimal
) -> None:
    leve, forte = max(a, b), min(a, b)
    todos = lambda queda: dict.fromkeys(TipoAtivo, queda)  # noqa: E731
    resultado_leve = simular_stress(carteira, todos(leve), POL)
    resultado_forte = simular_stress(carteira, todos(forte), POL)
    assert resultado_forte.contas_em_chamada_depois >= resultado_leve.contas_em_chamada_depois
    assert resultado_forte.deficit_de_margem_total >= resultado_leve.deficit_de_margem_total
    assert resultado_forte.exposicao_descoberta_total >= resultado_leve.exposicao_descoberta_total
    assert resultado_forte.valor_das_garantias_depois <= resultado_leve.valor_das_garantias_depois


@given(carteiras, quedas, st.decimals(Decimal(0), Decimal(1), places=4))
def test_perda_esperada_fica_entre_zero_e_a_divida(
    carteira: list[ContaNaCarteira], queda: Decimal, pd: Decimal
) -> None:
    resultado = simular_stress(carteira, dict.fromkeys(TipoAtivo, queda), POL, pd)
    assert Dinheiro.zero() <= resultado.perda_esperada <= resultado.exposicao_descoberta_total
    assert resultado.exposicao_descoberta_total <= resultado.deficit_de_margem_total
    assert resultado.deficit_de_margem_total <= resultado.divida_total
    assert resultado.valor_das_garantias_depois <= resultado.valor_das_garantias_antes
    assert resultado.novas_chamadas_de_margem <= resultado.contas_em_chamada_depois
