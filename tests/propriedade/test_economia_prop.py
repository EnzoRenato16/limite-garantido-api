"""Propriedades do cálculo econômico, sobre premissas válidas sorteadas.

- A soma das linhas bate com cada visão, centavo por centavo.
- Plataforma + parceiro = arranjo.
- Mais perda, mais CDI ou mais spread nunca aumentam a margem da plataforma.
- Mais intercâmbio nunca diminui a receita da plataforma nem a do parceiro.
- O ponto de equilíbrio N paga o custo fixo e N − 1 não paga.
- Cada barra do tornado contém a base e usa extremos válidos e coerentes.
- O empate da interna x externa confere quando recalculado.

Nas propriedades de monotonia o aumento é estritamente positivo e a alavanca mexe de
verdade na conta: a fonte fica ligada e o saldo financiado é positivo.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.dominio.economia.analises import (
    analisar_tornado,
    comparar_interna_externa,
    ponto_de_equilibrio,
)
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.calculo import (
    Linha,
    Parte,
    ResultadoDoMes,
    TaxasDePerda,
    calcular_mes,
)
from app.dominio.economia.premissas import FonteDeReceita, Premissas, montar_premissas
from app.dominio.economia.premissas import NomeDaPremissa as N
from app.dominio.taxas import Fracao, FrequenciaAoAno, TaxaAoAno
from tests.economia_base import ESQUEMA
from tests.propriedade.estrategias_economia import (
    CASAS,
    premissas_validas,
    taxas_de_perda,
)

PROPRIEDADE = settings(max_examples=200, deadline=None)

aumentos = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("0.5"),
    places=CASAS,
    allow_nan=False,
    allow_infinity=False,
)
DO_ARRANJO = {Parte.plataforma, Parte.parceiro}


def _tem_saldo_financiado(p: Premissas, perdas: TaxasDePerda) -> bool:
    return calcular_mes(p, perdas).perda.saldo_financiado.positivo


def _somar(r: ResultadoDoMes, condicao: Callable[[Linha], bool]) -> Dinheiro:
    return Dinheiro.somar(linha.valor for linha in r.linhas if condicao(linha))


@PROPRIEDADE
@given(premissas_validas(), taxas_de_perda())
def test_soma_das_linhas_bate_com_as_tres_visoes(p: Premissas, perdas: TaxasDePerda) -> None:
    r = calcular_mes(p, perdas)
    for visao, parte in ((r.plataforma, Parte.plataforma), (r.parceiro, Parte.parceiro)):
        assert visao.receita == Dinheiro.somar(
            linha.valor for linha in r.linhas if linha.destino == parte
        )
        assert visao.custo == Dinheiro.somar(
            linha.valor for linha in r.linhas if linha.origem == parte
        )
        assert visao.margem == visao.receita - visao.custo

    assert r.arranjo.receita == _somar(
        r, lambda linha: linha.destino in DO_ARRANJO and linha.origem not in DO_ARRANJO
    )
    assert r.arranjo.custo == _somar(
        r, lambda linha: linha.origem in DO_ARRANJO and linha.destino not in DO_ARRANJO
    )
    assert r.arranjo.margem == r.arranjo.receita - r.arranjo.custo
    assert all(linha.valor.centavos >= 0 for linha in r.linhas)


@PROPRIEDADE
@given(premissas_validas(), taxas_de_perda())
def test_plataforma_mais_parceiro_e_o_arranjo(p: Premissas, perdas: TaxasDePerda) -> None:
    r = calcular_mes(p, perdas)
    assert r.plataforma.margem + r.parceiro.margem == r.arranjo.margem


@PROPRIEDADE
@given(
    premissas_validas(),
    taxas_de_perda(),
    st.sampled_from(["taxa_base", "frequencia", "condicional_interna", "condicional_externa"]),
    aumentos,
)
def test_mais_perda_nunca_aumenta_a_margem_da_plataforma(
    p: Premissas, perdas: TaxasDePerda, alavanca: str, aumento: Decimal
) -> None:
    assume(_tem_saldo_financiado(p, perdas))
    mais_p, mais_perdas = p, perdas
    match alavanca:
        case "taxa_base":
            mais_p = p.com(
                taxa_base_de_perda_ao_ano=TaxaAoAno(p.taxa_base_de_perda_ao_ano.valor + aumento)
            )
        case "frequencia":
            mais_p = p.com(
                frequencia_do_cenario_ao_ano=FrequenciaAoAno(
                    p.frequencia_do_cenario_ao_ano.valor + aumento
                )
            )
        case "condicional_interna":
            atual = perdas.condicional_interna.valor
            assume(atual < 1 and p.fracao_interna.valor > 0)
            assume(p.frequencia_do_cenario_ao_ano.valor > 0)
            maior = min(atual + aumento, Decimal(1))
            mais_perdas = TaxasDePerda(
                perdas.cenario_id, perdas.nome, Fracao(maior), perdas.condicional_externa
            )
        case _:
            atual = perdas.condicional_externa.valor
            assume(atual < 1 and p.fracao_externa.valor > 0)
            assume(p.frequencia_do_cenario_ao_ano.valor > 0)
            maior = min(atual + aumento, Decimal(1))
            mais_perdas = TaxasDePerda(
                perdas.cenario_id, perdas.nome, perdas.condicional_interna, Fracao(maior)
            )

    antes = calcular_mes(p, perdas)
    depois = calcular_mes(mais_p, mais_perdas)
    assert depois.plataforma.margem <= antes.plataforma.margem
    assert depois.perda.taxa_ao_ano.valor >= antes.perda.taxa_ao_ano.valor


@PROPRIEDADE
@given(
    premissas_validas(),
    taxas_de_perda(),
    st.sampled_from(["cdi_ao_ano", "spread_fidc_ao_ano", "spread_balanco_ao_ano"]),
    aumentos,
)
def test_mais_cdi_ou_spread_nunca_aumenta_a_margem_da_plataforma(
    p: Premissas, perdas: TaxasDePerda, campo: str, aumento: Decimal
) -> None:
    assume(_tem_saldo_financiado(p, perdas))
    atual: TaxaAoAno = getattr(p, campo)
    mais = p.com(**{campo: TaxaAoAno(atual.valor + aumento)})
    assert calcular_mes(mais, perdas).plataforma.margem <= calcular_mes(p, perdas).plataforma.margem


@PROPRIEDADE
@given(premissas_validas(), taxas_de_perda(), aumentos)
def test_mais_intercambio_nunca_diminui_a_receita_da_plataforma_nem_a_do_parceiro(
    p: Premissas, perdas: TaxasDePerda, aumento: Decimal
) -> None:
    p = p.com(receitas_ligadas=p.receitas_ligadas | {FonteDeReceita.intercambio})
    maior = min(p.taxa_de_intercambio.valor + aumento, Decimal(1))
    assert maior > p.taxa_de_intercambio.valor
    antes = calcular_mes(p, perdas)
    depois = calcular_mes(p.com(taxa_de_intercambio=Fracao(maior)), perdas)
    assert depois.plataforma.receita >= antes.plataforma.receita
    assert depois.parceiro.receita >= antes.parceiro.receita


# ------------------------------------------------------------------- análises


@PROPRIEDADE
@given(
    st.integers(min_value=-(10**6), max_value=10**6),
    st.integers(min_value=0, max_value=10**10),
)
def test_equilibrio_n_paga_e_n_menos_1_nao(margem: int, custo_fixo: int) -> None:
    equilibrio = ponto_de_equilibrio(Dinheiro(margem), Dinheiro(custo_fixo))
    if margem <= 0:
        assert equilibrio.clientes is None
        assert equilibrio.motivo
        return
    n = equilibrio.clientes
    assert n is not None
    assert equilibrio.motivo is None
    assert n * margem - custo_fixo >= 0
    if custo_fixo == 0:
        assert n == 0
    else:
        assert (n - 1) * margem - custo_fixo < 0


ANALISE = settings(max_examples=40, deadline=None)


@ANALISE
@given(premissas_validas(), taxas_de_perda())
def test_tornado_contem_a_base(p: Premissas, perdas: TaxasDePerda) -> None:
    base = calcular_mes(p, perdas).plataforma.margem
    barras = analisar_tornado(p, perdas, ESQUEMA)
    assert len(barras) == 33
    for barra in barras:
        assert barra.minimo <= base <= barra.maximo
        assert barra.minimo <= barra.margem_no_baixo <= barra.maximo
        assert barra.minimo <= barra.margem_no_alto <= barra.maximo
        assert barra.amplitude == barra.maximo - barra.minimo
        # Os extremos usados respeitam a validação e a coerência das premissas.
        for valor in (barra.valor_baixo, barra.valor_alto):
            valores = p.valores()
            valores[barra.premissa] = valor
            if barra.premissa is N.fracao_externa:
                valores[N.fracao_interna] = 1 - valor
            montar_premissas(valores, p.receitas_ligadas, p.cenario_de_risco, ESQUEMA)
    amplitudes = [barra.amplitude for barra in barras]
    assert amplitudes == sorted(amplitudes, reverse=True)


@ANALISE
@given(premissas_validas(), taxas_de_perda())
def test_empate_da_interna_x_externa_confere(p: Premissas, perdas: TaxasDePerda) -> None:
    c = comparar_interna_externa(p, perdas, ESQUEMA)
    assert c.custo_a_mais_da_externa == c.margem_interna - c.margem_externa
    externa = p.com(fracao_interna=Fracao(Decimal(0)), fracao_externa=Fracao(Decimal(1)))

    def margem(premissas: Premissas) -> Dinheiro:
        return calcular_mes(premissas, perdas).plataforma.margem

    if not c.custo_a_mais_da_externa.positivo:
        assert c.fee_de_gravame_externo_para_empatar is None
        assert c.gasto_para_empatar is None
    fee = c.fee_de_gravame_externo_para_empatar
    if fee is not None:
        assert margem(externa.com(fee_gravame_externa=fee)) >= c.margem_interna
        if fee.positivo:
            antes = externa.com(fee_gravame_externa=fee - Dinheiro(1))
            assert margem(antes) < c.margem_interna
    gasto = c.gasto_para_empatar
    assert (gasto is None) == (c.gasto_cabe_no_limite is None)
    if gasto is not None:
        assert margem(externa.com(gasto_mensal=gasto)) >= c.margem_interna
        if gasto.positivo:
            assert margem(externa.com(gasto_mensal=gasto - Dinheiro(1))) < c.margem_interna
        limite = p.saldo_em_garantia.aplicar_razao(p.ltv_medio.valor)
        assert c.gasto_cabe_no_limite == (gasto <= limite)
