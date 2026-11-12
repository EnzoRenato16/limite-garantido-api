"""Propriedades do painel de risco sobre carteiras quaisquer."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.dominio.dinheiro import Dinheiro
from app.dominio.politica import TipoAtivo
from app.dominio.stress import (
    ContaNaCarteira,
    GarantiaNaCarteira,
    PremissasDaGarantiaExterna,
    simular_painel,
)
from tests.apoio import politica

POL = politica(teto_cliente="50000.00")
CUSTODIANTES = (None, "custodiante_agil", "custodiante_lento")
DESCONTO = {tipo: Decimal("0.01") for tipo in TipoAtivo}

garantias = st.builds(
    GarantiaNaCarteira,
    tipo_ativo=st.sampled_from(TipoAtivo),
    valor_de_mercado=st.integers(0, 10_000_000).map(Dinheiro),
    custodiante_id=st.sampled_from(CUSTODIANTES),
)
contas = st.builds(
    ContaNaCarteira,
    conta_id=st.uuids().map(str),
    divida=st.integers(0, 10_000_000).map(Dinheiro),
    garantias=st.lists(garantias, max_size=3).map(tuple),
)
carteiras = st.lists(contas, max_size=25)
choque = st.decimals(Decimal(-1), Decimal(1), places=4)
choques = st.dictionaries(st.sampled_from(TipoAtivo), choque)
taxas = st.decimals(Decimal(0), Decimal(1), places=4)
premissas = st.builds(
    PremissasDaGarantiaExterna,
    custodiantes_indisponiveis=st.frozensets(st.sampled_from(CUSTODIANTES[1:])),
    dias_extra_de_execucao=st.integers(0, 30),
    desconto_diario_por_ativo=st.just(DESCONTO),
)


@settings(max_examples=300, deadline=None)
@given(carteira=carteiras, choques=choques, taxa=taxas, premissas=premissas)
def test_perda_nunca_negativa_nem_maior_que_a_exposicao(
    carteira: list[ContaNaCarteira],
    choques: dict[TipoAtivo, Decimal],
    taxa: Decimal,
    premissas: PremissasDaGarantiaExterna,
) -> None:
    perda = simular_painel(carteira, choques, POL, taxa, premissas).perda
    assert Dinheiro.zero() <= perda.perda_com_execucao_imediata
    assert Dinheiro.zero() <= perda.perda_adicional_externa
    assert perda.perda_esperada_total == (
        perda.perda_com_execucao_imediata + perda.perda_adicional_externa
    )
    assert perda.perda_esperada_total <= perda.exposicao


@settings(max_examples=300, deadline=None)
@given(
    carteira=carteiras,
    base=choques,
    piora=st.dictionaries(
        st.sampled_from(TipoAtivo), st.decimals(Decimal(0), Decimal(1), places=4)
    ),
    taxa=taxas,
    premissas=premissas,
)
def test_choque_mais_forte_nunca_diminui_a_perda(
    carteira: list[ContaNaCarteira],
    base: dict[TipoAtivo, Decimal],
    piora: dict[TipoAtivo, Decimal],
    taxa: Decimal,
    premissas: PremissasDaGarantiaExterna,
) -> None:
    pior = {
        tipo: max(Decimal(-1), base.get(tipo, Decimal(0)) - piora.get(tipo, Decimal(0)))
        for tipo in TipoAtivo
    }
    leve = simular_painel(carteira, base, POL, taxa, premissas).perda
    forte = simular_painel(carteira, pior, POL, taxa, premissas).perda
    assert forte.perda_esperada_total >= leve.perda_esperada_total
    assert forte.perda_com_execucao_imediata >= leve.perda_com_execucao_imediata


@settings(max_examples=300, deadline=None)
@given(carteira=carteiras, choques=choques, taxa=taxas, premissas=premissas)
def test_custodiante_fora_do_ar_so_piora_a_parcela_externa(
    carteira: list[ContaNaCarteira],
    choques: dict[TipoAtivo, Decimal],
    taxa: Decimal,
    premissas: PremissasDaGarantiaExterna,
) -> None:
    fora = PremissasDaGarantiaExterna(
        custodiantes_indisponiveis=premissas.custodiantes_indisponiveis | {"custodiante_lento"},
        dias_extra_de_execucao=premissas.dias_extra_de_execucao,
        desconto_diario_por_ativo=premissas.desconto_diario_por_ativo,
    )
    normal = simular_painel(carteira, choques, POL, taxa, premissas).perda
    pior = simular_painel(carteira, choques, POL, taxa, fora).perda
    assert pior.perda_com_execucao_imediata == normal.perda_com_execucao_imediata
    assert pior.perda_adicional_externa >= normal.perda_adicional_externa


@settings(max_examples=200, deadline=None)
@given(carteira=carteiras, premissas=premissas)
def test_choque_zero_nao_poe_ninguem_saudavel_em_chamada(
    carteira: list[ContaNaCarteira], premissas: PremissasDaGarantiaExterna
) -> None:
    resultado = simular_painel(carteira, {}, POL, Decimal(1), premissas)
    for conta in resultado.stress.contas:
        assert conta.em_chamada_depois == conta.em_chamada_antes
    assert resultado.depois.contas_em_chamada == resultado.antes.contas_em_chamada
