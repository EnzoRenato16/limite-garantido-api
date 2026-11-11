"""Regras de risco da versão 0.1: LTV por tipo de ativo e prazo de execução."""

from decimal import ROUND_DOWN, Decimal

# LTV (loan-to-value): quanto de limite cada R$ 1 de garantia libera.
LTV_POR_ATIVO: dict[str, Decimal] = {
    "deposito": Decimal("1.00"),
    "fundo_di": Decimal("0.95"),
    "tesouro_selic": Decimal("0.95"),
    "cdb": Decimal("0.85"),
    "tesouro_prefixado": Decimal("0.80"),
    "tesouro_ipca": Decimal("0.80"),
    "acoes": Decimal("0.60"),
}

# Na execução, vendemos primeiro o que é mais líquido.
ORDEM_DE_LIQUIDEZ: list[str] = [
    "deposito",
    "fundo_di",
    "tesouro_selic",
    "cdb",
    "tesouro_prefixado",
    "tesouro_ipca",
    "acoes",
]

# A garantia é executada a partir do 7º dia de atraso da fatura.
DIAS_PARA_EXECUCAO = 7

CENTAVO = Decimal("0.01")


def arredondar_para_baixo(valor: Decimal) -> Decimal:
    """Arredonda para centavos sempre para baixo (a favor da segurança)."""
    return valor.quantize(CENTAVO, rounding=ROUND_DOWN)


def limite_da_garantia(tipo_ativo: str, valor_de_mercado: Decimal) -> Decimal:
    """Limite liberado por uma garantia: valor de mercado x LTV, para baixo."""
    return arredondar_para_baixo(valor_de_mercado * LTV_POR_ATIVO[tipo_ativo])
