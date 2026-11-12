"""Propriedades do cálculo econômico, sobre premissas válidas sorteadas.

- A soma das linhas bate com cada visão, centavo por centavo.
- Plataforma + parceiro = arranjo.
- Mais perda, mais CDI ou mais spread nunca aumentam a margem da plataforma.
- Mais intercâmbio nunca diminui a receita da plataforma nem a do parceiro.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.calculo import (
    Linha,
    Parte,
    ResultadoDoMes,
    TaxasDePerda,
    calcular_mes,
)
from app.dominio.economia.premissas import Premissas
from app.dominio.taxas import Fracao, FrequenciaAoAno, TaxaAoAno
from tests.propriedade.estrategias_economia import (
    CASAS,
    fracoes,
    premissas_validas,
    taxas_de_perda,
)

PROPRIEDADE = settings(max_examples=200, deadline=None)

aumentos = st.decimals(
    min_value=Decimal(0),
    max_value=Decimal("0.5"),
    places=CASAS,
    allow_nan=False,
    allow_infinity=False,
)
DO_ARRANJO = {Parte.plataforma, Parte.parceiro}


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
    fracoes,
)
def test_mais_perda_nunca_aumenta_a_margem_da_plataforma(
    p: Premissas, perdas: TaxasDePerda, alavanca: str, aumento: Decimal, alvo: Decimal
) -> None:
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
            maior = max(perdas.condicional_interna.valor, alvo)
            mais_perdas = TaxasDePerda(
                perdas.cenario_id, perdas.nome, Fracao(maior), perdas.condicional_externa
            )
        case _:
            maior = max(perdas.condicional_externa.valor, alvo)
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
    atual: TaxaAoAno = getattr(p, campo)
    mais = p.com(**{campo: TaxaAoAno(atual.valor + aumento)})
    assert calcular_mes(mais, perdas).plataforma.margem <= calcular_mes(p, perdas).plataforma.margem


@PROPRIEDADE
@given(premissas_validas(), taxas_de_perda(), aumentos)
def test_mais_intercambio_nunca_diminui_a_receita_da_plataforma_nem_a_do_parceiro(
    p: Premissas, perdas: TaxasDePerda, aumento: Decimal
) -> None:
    maior = min(p.taxa_de_intercambio.valor + aumento, Decimal(1))
    antes = calcular_mes(p, perdas)
    depois = calcular_mes(p.com(taxa_de_intercambio=Fracao(maior)), perdas)
    assert depois.plataforma.receita >= antes.plataforma.receita
    assert depois.parceiro.receita >= antes.parceiro.receita
