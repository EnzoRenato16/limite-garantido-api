"""Estratégias do Hypothesis para o modelo econômico.

Cada premissa é sorteada dentro da faixa aceita do esquema (dinheiro em centavos,
inteiro sem casas). Depois a coerência é forçada: a interna é 1 − a externa, o gasto
não passa do limite que a garantia gera e a carteira não passa de 100 milhões de
clientes. Tudo passa por `montar_premissas`, então as premissas saem sempre válidas.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import strategies as st

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.calculo import TaxasDePerda
from app.dominio.economia.premissas import (
    LIMITE_DE_CLIENTES_NA_CARTEIRA,
    EsquemaDaPremissa,
    FonteDeReceita,
    Premissas,
    montar_premissas,
)
from app.dominio.economia.premissas import NomeDaPremissa as N
from app.dominio.economia.premissas import TipoDaPremissa as T
from app.dominio.taxas import Fracao
from tests.economia_base import ESQUEMA

CASAS = 6  # casas decimais das taxas, frações e quantidades sorteadas


def _valor(esquema: EsquemaDaPremissa) -> st.SearchStrategy[Decimal]:
    match esquema.tipo:
        case T.dinheiro:
            return st.integers(
                min_value=int(esquema.minimo * 100), max_value=int(esquema.maximo * 100)
            ).map(lambda centavos: Decimal(centavos).scaleb(-2))
        case T.inteiro:
            return st.integers(min_value=int(esquema.minimo), max_value=int(esquema.maximo)).map(
                Decimal
            )
        case _:
            return st.decimals(
                min_value=esquema.minimo,
                max_value=esquema.maximo,
                places=CASAS,
                allow_nan=False,
                allow_infinity=False,
            )


@st.composite
def premissas_validas(draw: st.DrawFn) -> Premissas:
    valores = {nome: draw(_valor(ESQUEMA[nome])) for nome in N}

    valores[N.fracao_interna] = 1 - valores[N.fracao_externa]
    limite = Dinheiro.de_decimal(valores[N.saldo_em_garantia]).aplicar_razao(valores[N.ltv_medio])
    valores[N.gasto_mensal] = min(valores[N.gasto_mensal], limite.para_decimal())
    teto_por_parceiro = LIMITE_DE_CLIENTES_NA_CARTEIRA // int(valores[N.parceiros])
    valores[N.clientes_por_parceiro] = min(
        valores[N.clientes_por_parceiro], Decimal(teto_por_parceiro)
    )

    receitas = draw(st.frozensets(st.sampled_from(list(FonteDeReceita))))
    return montar_premissas(valores, receitas, "custodiante_fora_do_ar", ESQUEMA)


fracoes = st.decimals(
    min_value=Decimal(0), max_value=Decimal(1), places=CASAS, allow_nan=False, allow_infinity=False
)


@st.composite
def taxas_de_perda(draw: st.DrawFn) -> TaxasDePerda:
    return TaxasDePerda(
        cenario_id="sorteado",
        nome="Cenário sorteado",
        condicional_interna=Fracao(draw(fracoes)),
        condicional_externa=Fracao(draw(fracoes)),
    )
