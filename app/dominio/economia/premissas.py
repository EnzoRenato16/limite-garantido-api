"""As premissas do modelo econômico: nomes, unidades, faixas e coerência.

Cada premissa tem tipo e unidade. Taxa é fração, não percentual: 0,1365 quer dizer 13,65%
ao ano, e `13.65` é recusado com a faixa aceita na mensagem. Dinheiro é em reais com até
duas casas. A validação junta todos os erros antes de levantar `PremissasInvalidas`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.taxas import Fracao, FrequenciaAoAno, TaxaAoAno, TaxaAoMes

LIMITE_DE_CLIENTES_NA_CARTEIRA = 100_000_000  # {cliente}


class NomeDaPremissa(StrEnum):
    gasto_mensal = "gasto_mensal"
    saldo_em_garantia = "saldo_em_garantia"
    ltv_medio = "ltv_medio"
    fracao_interna = "fracao_interna"
    fracao_externa = "fracao_externa"
    garantias_por_cliente = "garantias_por_cliente"
    gravames_ao_mes = "gravames_ao_mes"
    dias_financiados = "dias_financiados"
    taxa_de_intercambio = "taxa_de_intercambio"
    fracao_do_intercambio_da_plataforma = "fracao_do_intercambio_da_plataforma"
    fee_por_garantia_ativa = "fee_por_garantia_ativa"
    fee_gravame_interna = "fee_gravame_interna"
    fee_gravame_externa = "fee_gravame_externa"
    taxa_sobre_investimento_ao_ano = "taxa_sobre_investimento_ao_ano"
    fracao_de_clientes_no_rotativo = "fracao_de_clientes_no_rotativo"
    fracao_da_fatura_rolada = "fracao_da_fatura_rolada"
    taxa_do_rotativo_ao_mes = "taxa_do_rotativo_ao_mes"
    meses_no_rotativo = "meses_no_rotativo"
    fracao_cedida_ao_fidc = "fracao_cedida_ao_fidc"
    taxa_fidc_ao_ano = "taxa_fidc_ao_ano"
    cdi_ao_ano = "cdi_ao_ano"
    spread_fidc_ao_ano = "spread_fidc_ao_ano"
    spread_balanco_ao_ano = "spread_balanco_ao_ano"
    taxa_base_de_perda_ao_ano = "taxa_base_de_perda_ao_ano"
    frequencia_do_cenario_ao_ano = "frequencia_do_cenario_ao_ano"
    custo_de_processamento_por_cartao = "custo_de_processamento_por_cartao"
    custo_do_gravame_externo = "custo_do_gravame_externo"
    execucoes_ao_ano = "execucoes_ao_ano"
    custo_de_execucao_interna = "custo_de_execucao_interna"
    custo_de_execucao_externa = "custo_de_execucao_externa"
    aliquota_sobre_receita = "aliquota_sobre_receita"
    custo_fixo_mensal = "custo_fixo_mensal"
    parceiros = "parceiros"
    clientes_por_parceiro = "clientes_por_parceiro"


class TipoDaPremissa(StrEnum):
    dinheiro = "dinheiro"
    fracao = "fracao"
    taxa_ao_ano = "taxa_ao_ano"
    taxa_ao_mes = "taxa_ao_mes"
    frequencia_ao_ano = "frequencia_ao_ano"
    quantidade = "quantidade"
    inteiro = "inteiro"


class FonteDeReceita(StrEnum):
    intercambio = "intercambio"
    fee_garantia_ativa = "fee_garantia_ativa"
    fee_gravame = "fee_gravame"
    receita_sobre_investimento = "receita_sobre_investimento"
    juros_rotativo = "juros_rotativo"
    taxa_fidc = "taxa_fidc"


@dataclass(frozen=True, slots=True)
class EsquemaDaPremissa:
    """Tudo o que se sabe sobre uma premissa, menos o valor."""

    nome: NomeDaPremissa
    nome_exibicao: str
    tipo: TipoDaPremissa
    unidade: str
    # Faixa aceita: a validação recusa o que sai dela (as duas pontas valem).
    minimo: Decimal
    maximo: Decimal
    # Faixa razoável: os extremos do tornado. None fica fora do tornado.
    razoavel_minimo: Decimal | None
    razoavel_maximo: Decimal | None
    origem: str
    fonte: str | None = None


class PremissasInvalidas(ValorInvalido):
    """Uma ou mais premissas fora da faixa ou incoerentes.

    `detalhes["erros"]` é uma lista de `{"campo": nome, "mensagem": texto}`.
    """

    codigo: ClassVar[str] = "premissas_invalidas"


@dataclass(frozen=True, slots=True)
class Premissas:
    """Um valor tipado por premissa, mais as receitas ligadas e o cenário de risco."""

    gasto_mensal: Dinheiro
    saldo_em_garantia: Dinheiro
    ltv_medio: Fracao
    fracao_interna: Fracao
    fracao_externa: Fracao
    garantias_por_cliente: Decimal
    gravames_ao_mes: Decimal
    dias_financiados: Decimal
    taxa_de_intercambio: Fracao
    fracao_do_intercambio_da_plataforma: Fracao
    fee_por_garantia_ativa: Dinheiro
    fee_gravame_interna: Dinheiro
    fee_gravame_externa: Dinheiro
    taxa_sobre_investimento_ao_ano: TaxaAoAno
    fracao_de_clientes_no_rotativo: Fracao
    fracao_da_fatura_rolada: Fracao
    taxa_do_rotativo_ao_mes: TaxaAoMes
    meses_no_rotativo: int
    fracao_cedida_ao_fidc: Fracao
    taxa_fidc_ao_ano: TaxaAoAno
    cdi_ao_ano: TaxaAoAno
    spread_fidc_ao_ano: TaxaAoAno
    spread_balanco_ao_ano: TaxaAoAno
    taxa_base_de_perda_ao_ano: TaxaAoAno
    frequencia_do_cenario_ao_ano: FrequenciaAoAno
    custo_de_processamento_por_cartao: Dinheiro
    custo_do_gravame_externo: Dinheiro
    execucoes_ao_ano: Decimal
    custo_de_execucao_interna: Dinheiro
    custo_de_execucao_externa: Dinheiro
    aliquota_sobre_receita: Fracao
    custo_fixo_mensal: Dinheiro
    parceiros: int
    clientes_por_parceiro: int
    receitas_ligadas: frozenset[FonteDeReceita]
    cenario_de_risco: str

    def com(self, **mudancas: Any) -> Premissas:
        """Cópia com campos trocados. Não revalida: use `montar_premissas` para isso."""
        return replace(self, **mudancas)

    def valores(self) -> dict[NomeDaPremissa, Decimal]:
        """O valor cru de cada premissa: dinheiro em reais, inteiro como Decimal."""
        return {nome: _para_decimal(getattr(self, nome.value)) for nome in NomeDaPremissa}


type _Valor = Dinheiro | Fracao | TaxaAoAno | TaxaAoMes | FrequenciaAoAno | Decimal | int


def _para_decimal(valor: _Valor) -> Decimal:
    if isinstance(valor, Dinheiro):
        return valor.para_decimal()
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, int):
        return Decimal(valor)
    return valor.valor


# --------------------------------------------------------------------- texto
def _numero(valor: Decimal) -> str:
    """Decimal em português: milhar com ponto, decimais com vírgula."""
    texto = f"{valor:,f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def _percentual(valor: Decimal) -> str:
    return _numero((valor * 100).normalize() if valor != 0 else Decimal(0))


def _descricao_da_unidade(esquema: EsquemaDaPremissa) -> str:
    match esquema.tipo:
        case TipoDaPremissa.taxa_ao_ano:
            return "ao ano, fração: 0,1365 = 13,65%"
        case TipoDaPremissa.taxa_ao_mes:
            return "ao mês, fração: 0,08 = 8%"
        case TipoDaPremissa.fracao:
            return "fração: 0,30 = 30%"
        case _:
            return esquema.unidade


def _mensagem_de_faixa(esquema: EsquemaDaPremissa) -> str:
    return (
        f"{esquema.nome} precisa estar entre {_numero(esquema.minimo)} e "
        f"{_numero(esquema.maximo)} ({_descricao_da_unidade(esquema)})"
    )


# ---------------------------------------------------------------- validação
def _erro_do_valor(valor: Decimal, esquema: EsquemaDaPremissa) -> str | None:
    """Primeiro problema do valor sozinho, ou None se ele serve."""
    if not isinstance(valor, Decimal) or not valor.is_finite():
        return f"{esquema.nome} precisa ser um número finito ({_descricao_da_unidade(esquema)})"
    if not esquema.minimo <= valor <= esquema.maximo:
        return _mensagem_de_faixa(esquema)
    if esquema.tipo is TipoDaPremissa.dinheiro and valor != valor.quantize(Decimal("0.01")):
        return f"{esquema.nome} tem no máximo duas casas decimais, os centavos ({esquema.unidade})"
    if esquema.tipo is TipoDaPremissa.inteiro and valor != valor.to_integral_value():
        return f"{esquema.nome} precisa ser um número inteiro ({esquema.unidade})"
    return None


def _converter(valor: Decimal, tipo: TipoDaPremissa) -> object:
    match tipo:
        case TipoDaPremissa.dinheiro:
            return Dinheiro.de_decimal(valor)
        case TipoDaPremissa.fracao:
            return Fracao(valor)
        case TipoDaPremissa.taxa_ao_ano:
            return TaxaAoAno(valor)
        case TipoDaPremissa.taxa_ao_mes:
            return TaxaAoMes(valor)
        case TipoDaPremissa.frequencia_ao_ano:
            return FrequenciaAoAno(valor)
        case TipoDaPremissa.quantidade:
            return valor
        case TipoDaPremissa.inteiro:
            return int(valor)


def montar_premissas(
    valores: Mapping[NomeDaPremissa, Decimal],
    receitas_ligadas: Iterable[FonteDeReceita],
    cenario_de_risco: str,
    esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa],
) -> Premissas:
    """Valida tudo e monta as premissas. Levanta `PremissasInvalidas` com todos os erros."""
    erros: list[dict[str, str]] = []

    def falha(campo: NomeDaPremissa, mensagem: str) -> None:
        erros.append({"campo": campo.value, "mensagem": mensagem})

    validos: dict[NomeDaPremissa, Decimal] = {}
    for nome in NomeDaPremissa:
        if nome not in esquema:
            falha(nome, f"{nome} não tem esquema (faixa e unidade)")
        elif nome not in valores:
            falha(nome, f"{nome} falta ({esquema[nome].unidade})")
        else:
            problema = _erro_do_valor(valores[nome], esquema[nome])
            if problema is None:
                validos[nome] = valores[nome]
            else:
                falha(nome, problema)

    _checar_coerencia(validos, falha)

    if erros:
        raise PremissasInvalidas(
            f"{len(erros)} premissa(s) inválida(s): " + "; ".join(e["mensagem"] for e in erros),
            erros=erros,
        )

    campos = {nome.value: _converter(validos[nome], esquema[nome].tipo) for nome in NomeDaPremissa}
    return Premissas(
        **campos,  # type: ignore[arg-type]
        receitas_ligadas=frozenset(receitas_ligadas),
        cenario_de_risco=cenario_de_risco,
    )


def _checar_coerencia(
    v: Mapping[NomeDaPremissa, Decimal],
    falha: Callable[[NomeDaPremissa, str], None],
) -> None:
    if NomeDaPremissa.fracao_interna in v and NomeDaPremissa.fracao_externa in v:
        soma = v[NomeDaPremissa.fracao_interna] + v[NomeDaPremissa.fracao_externa]
        if soma != 1:
            falha(
                NomeDaPremissa.fracao_interna,
                "fracao_interna + fracao_externa precisam somar 100% (fração, soma 1), "
                f"mas somam {_percentual(soma)}%",
            )
    if (
        NomeDaPremissa.gasto_mensal in v
        and NomeDaPremissa.saldo_em_garantia in v
        and NomeDaPremissa.ltv_medio in v
    ):
        saldo = Dinheiro.de_decimal(v[NomeDaPremissa.saldo_em_garantia])
        limite = saldo.aplicar_razao(v[NomeDaPremissa.ltv_medio])  # {BRL} * {1} -> {BRL}
        if Dinheiro.de_decimal(v[NomeDaPremissa.gasto_mensal]) > limite:
            falha(
                NomeDaPremissa.gasto_mensal,
                f"gasto_mensal não pode passar do limite que a garantia gera, "
                f"saldo_em_garantia x ltv_medio = {limite.formatado()} (R$/mês)",
            )
    if NomeDaPremissa.parceiros in v and NomeDaPremissa.clientes_por_parceiro in v:
        total = (
            v[NomeDaPremissa.parceiros] * v[NomeDaPremissa.clientes_por_parceiro]
        )  # {parceiro} * {cliente/parceiro}
        if total > LIMITE_DE_CLIENTES_NA_CARTEIRA:
            falha(
                NomeDaPremissa.parceiros,
                "parceiros x clientes_por_parceiro não pode passar de "
                f"{_numero(Decimal(LIMITE_DE_CLIENTES_NA_CARTEIRA))} clientes, mas dá "
                f"{_numero(total)} (cliente)",
            )
