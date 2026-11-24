"""Retenção na execução e demonstrativo da venda da garantia.

Quem paga o rendimento retém o tributo (anexo D1 da fonte); a SCD recebe o líquido. As
regras e os números vêm do catálogo regulatório (`RetencaoNaExecucao` de cada ativo e a
tabela regressiva da renda fixa): este módulo só sabe aplicá-las.

Unidades: valores em {BRL} (`Dinheiro`), alíquotas e percentuais em {1}, prazo desde a
aplicação em {dia_corrido}. A retenção arredonda para cima (o lado seguro para quem
recebe o líquido).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.garantia import Garantia
from app.dominio.regulacao import CatalogoRegulatorio, RegraDeRetencao


def retencao_na_execucao(
    garantia: Garantia, valor: Dinheiro, hoje: date, catalogo: CatalogoRegulatorio
) -> Dinheiro:
    """Quanto quem paga retém ao vender ou resgatar `valor` {BRL} da garantia hoje.

    - tabela regressiva: alíquota pelos {dia_corrido} desde a aplicação, sobre o
      rendimento (`valor - valor_aplicado`, nunca negativo);
    - isento: zero;
    - já líquido (previdência): zero, porque o valor entrou líquido dos tributos
      informados pela operadora (Res. Conj. 12, art. 7º);
    - sobre a venda e percentual ilustrativo: percentual sobre `valor`.

    A data da aplicação é `aplicado_em` da garantia; sem ela, a da constituição
    (`criada_em`): se o ativo foi aplicado antes, o prazo real é maior e a alíquota real é
    menor ou igual, então a retenção estimada nunca fica abaixo da verdadeira.
    """
    if valor.negativo:
        raise ValorInvalido("Valor da venda negativo")
    retencao = catalogo.regra(garantia.tipo_ativo).retencao
    if retencao is None:
        raise ValorInvalido(
            f"{garantia.tipo_ativo} não tem regra de retenção no catálogo",
            tipo_ativo=str(garantia.tipo_ativo),
        )
    if retencao.regra in (RegraDeRetencao.ISENTO, RegraDeRetencao.JA_LIQUIDO):
        return Dinheiro.zero()
    if retencao.regra is RegraDeRetencao.TABELA_REGRESSIVA_RENDA_FIXA:
        dias = max((hoje - garantia.data_da_aplicacao).days, 0)  # {dia_corrido}
        rendimento = (valor - garantia.valor_aplicado).nao_negativo()  # {BRL}
        # {BRL} x {1} -> {BRL}, para cima.
        return rendimento.aplicar_razao(_aliquota_regressiva(catalogo, dias), para_cima=True)
    # SOBRE_A_VENDA e PERCENTUAL_ILUSTRATIVO: percentual {1} sobre o valor {BRL}.
    assert retencao.percentual is not None  # noqa: S101 - validado no catálogo
    return valor.aplicar_razao(retencao.percentual, para_cima=True)


def _aliquota_regressiva(catalogo: CatalogoRegulatorio, dias_corridos: int) -> Decimal:
    """Alíquota {1} da faixa que contém os {dia_corrido} desde a aplicação."""
    for faixa in catalogo.tabela_regressiva_renda_fixa:
        if faixa.ate_dias_corridos is None or dias_corridos <= faixa.ate_dias_corridos:
            return faixa.aliquota
    raise ValorInvalido("Tabela regressiva sem faixa aberta")  # o catálogo já impede


@dataclass(frozen=True, slots=True)
class ResultadoDaExecucao:
    """Demonstrativo da execução de uma garantia, tudo em {BRL}.

    valor_de_mercado = retencao + liquido; liquido = divida_abatida + excedente_devolvido.
    """

    valor_de_mercado: Dinheiro
    retencao: Dinheiro
    liquido: Dinheiro
    divida_abatida: Dinheiro
    excedente_devolvido: Dinheiro

    def __post_init__(self) -> None:
        campos = (
            self.valor_de_mercado,
            self.retencao,
            self.liquido,
            self.divida_abatida,
            self.excedente_devolvido,
        )
        if any(valor.negativo for valor in campos):
            raise ValorInvalido("Demonstrativo da execução com valor negativo")
        if self.retencao + self.liquido != self.valor_de_mercado:
            raise ValorInvalido("Retenção mais líquido precisa dar o valor de mercado")
        if self.divida_abatida + self.excedente_devolvido != self.liquido:
            raise ValorInvalido("Dívida abatida mais excedente precisa dar o líquido")

    @classmethod
    def demonstrar(
        cls, valor_de_mercado: Dinheiro, retencao: Dinheiro, devido: Dinheiro
    ) -> ResultadoDaExecucao:
        """O líquido abate até `devido`; o que sobra volta ao cliente."""
        retida = min(retencao, valor_de_mercado)
        liquido = valor_de_mercado - retida
        abatida = min(liquido, devido.nao_negativo())
        return cls(
            valor_de_mercado=valor_de_mercado,
            retencao=retida,
            liquido=liquido,
            divida_abatida=abatida,
            excedente_devolvido=liquido - abatida,
        )

    def para_dados(self) -> dict[str, str]:
        return {
            "valor_de_mercado": self.valor_de_mercado.texto(),
            "retencao": self.retencao.texto(),
            "liquido": self.liquido.texto(),
            "divida_abatida": self.divida_abatida.texto(),
            "excedente_devolvido": self.excedente_devolvido.texto(),
        }
