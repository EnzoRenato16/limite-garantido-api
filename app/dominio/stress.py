"""Simulador de stress: e se os preços caírem amanhã?

Aplica um choque por tipo de ativo sobre a carteira de um parceiro e responde:
quantas contas entram em chamada de margem, de quanto é o déficit e quanto da dívida
ficaria sem cobertura mesmo vendendo toda a garantia ao preço estressado.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.politica import PoliticaRisco, TipoAtivo


@dataclass(frozen=True, slots=True)
class GarantiaNaCarteira:
    tipo_ativo: TipoAtivo
    valor_de_mercado: Dinheiro


@dataclass(frozen=True, slots=True)
class ContaNaCarteira:
    conta_id: str
    divida: Dinheiro
    garantias: tuple[GarantiaNaCarteira, ...]


@dataclass(frozen=True, slots=True)
class ContaEstressada:
    conta_id: str
    divida: Dinheiro
    valor_das_garantias_antes: Dinheiro
    valor_das_garantias_depois: Dinheiro
    limite_garantido_depois: Dinheiro
    # Dívida acima do limite garantido estressado (chamada de margem).
    deficit_de_margem: Dinheiro
    # Dívida acima do valor de mercado estressado: nem vendendo tudo paga.
    exposicao_descoberta: Dinheiro
    em_chamada_antes: bool
    em_chamada_depois: bool


@dataclass(frozen=True, slots=True)
class ResultadoDoStress:
    contas_analisadas: int
    contas_em_chamada_antes: int
    contas_em_chamada_depois: int
    novas_chamadas_de_margem: int
    divida_total: Dinheiro
    valor_das_garantias_antes: Dinheiro
    valor_das_garantias_depois: Dinheiro
    deficit_de_margem_total: Dinheiro
    exposicao_descoberta_total: Dinheiro
    # exposição descoberta x probabilidade de inadimplência, arredondada para cima.
    perda_esperada: Dinheiro
    contas: tuple[ContaEstressada, ...]


def _fator_do_choque(choque: Decimal) -> Decimal:
    """Choque de -0.30 vira fator 0.70. Razão adimensional {1}."""
    if not isinstance(choque, Decimal) or not choque.is_finite() or not -1 <= choque <= 1:
        raise ValorInvalido("Cada choque precisa estar entre -1 (perda total) e 1 (+100%)")
    return Decimal(1) + choque


def simular_stress(
    carteira: Iterable[ContaNaCarteira],
    choques: Mapping[TipoAtivo, Decimal],
    politica: PoliticaRisco,
    probabilidade_de_inadimplencia: Decimal = Decimal(1),
) -> ResultadoDoStress:
    """Aplica os choques e consolida o efeito na carteira.

    A perda esperada parte do pior caso (probabilidade de inadimplência 1): toda conta
    descoberta deixa de pagar. Informe uma probabilidade menor para um cenário realista.
    """
    if not 0 <= probabilidade_de_inadimplencia <= 1:
        raise ValorInvalido("A probabilidade de inadimplência precisa estar entre 0 e 1")
    fatores = {tipo: _fator_do_choque(choque) for tipo, choque in choques.items()}

    contas: list[ContaEstressada] = []
    for conta in carteira:
        valor_antes = Dinheiro.zero()
        valor_depois = Dinheiro.zero()
        limite_antes = Dinheiro.zero()
        limite_depois = Dinheiro.zero()
        for garantia in conta.garantias:
            ltv = politica.ltv_por_ativo.get(garantia.tipo_ativo, Decimal(0))
            # {BRL} * fator {1} -> {BRL}; garantia sempre para baixo.
            estressado = garantia.valor_de_mercado.aplicar_razao(
                fatores.get(garantia.tipo_ativo, Decimal(1))
            )
            valor_antes += garantia.valor_de_mercado
            valor_depois += estressado
            limite_antes += garantia.valor_de_mercado.aplicar_razao(ltv)
            limite_depois += estressado.aplicar_razao(ltv)
        contas.append(
            ContaEstressada(
                conta_id=conta.conta_id,
                divida=conta.divida,
                valor_das_garantias_antes=valor_antes,
                valor_das_garantias_depois=valor_depois,
                limite_garantido_depois=limite_depois,
                deficit_de_margem=(conta.divida - limite_depois).nao_negativo(),
                exposicao_descoberta=(conta.divida - valor_depois).nao_negativo(),
                em_chamada_antes=conta.divida > limite_antes,
                em_chamada_depois=conta.divida > limite_depois,
            )
        )

    descoberta = Dinheiro.somar(c.exposicao_descoberta for c in contas)
    return ResultadoDoStress(
        contas_analisadas=len(contas),
        contas_em_chamada_antes=sum(c.em_chamada_antes for c in contas),
        contas_em_chamada_depois=sum(c.em_chamada_depois for c in contas),
        novas_chamadas_de_margem=sum(
            c.em_chamada_depois and not c.em_chamada_antes for c in contas
        ),
        divida_total=Dinheiro.somar(c.divida for c in contas),
        valor_das_garantias_antes=Dinheiro.somar(c.valor_das_garantias_antes for c in contas),
        valor_das_garantias_depois=Dinheiro.somar(c.valor_das_garantias_depois for c in contas),
        deficit_de_margem_total=Dinheiro.somar(c.deficit_de_margem for c in contas),
        exposicao_descoberta_total=descoberta,
        # Estimativa de perda arredonda para cima: o lado seguro é o maior.
        perda_esperada=descoberta.aplicar_razao(probabilidade_de_inadimplencia, para_cima=True),
        contas=tuple(contas),
    )
