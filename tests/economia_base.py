"""Apoio de teste: o esquema das 34 premissas e a coluna `base`.

O esquema vem do TOML (`app/premissas_economia.toml`), única fonte de verdade. Os valores
da coluna `base` ficam aqui por extenso, e um teste confere que batem com o TOML.
"""

from __future__ import annotations

from decimal import Decimal

from app.dominio.economia.premissas import (
    EsquemaDaPremissa,
    FonteDeReceita,
    Premissas,
    montar_premissas,
)
from app.dominio.economia.premissas import NomeDaPremissa as N
from app.infra.economia import carregar_parametros_da_economia

ESQUEMA: dict[N, EsquemaDaPremissa] = dict(carregar_parametros_da_economia().esquema)

VALORES_BASE: dict[N, Decimal] = {
    N.gasto_mensal: Decimal("1500"),
    N.saldo_em_garantia: Decimal("8000"),
    N.ltv_medio: Decimal("0.85"),
    N.fracao_interna: Decimal("0.70"),
    N.fracao_externa: Decimal("0.30"),
    N.garantias_por_cliente: Decimal("1.5"),
    N.gravames_ao_mes: Decimal("0.10"),
    N.dias_financiados: Decimal("10"),
    N.taxa_de_intercambio: Decimal("0.012"),
    N.fracao_do_intercambio_da_plataforma: Decimal("0.50"),
    N.fee_por_garantia_ativa: Decimal("2.00"),
    N.fee_gravame_interna: Decimal("1.00"),
    N.fee_gravame_externa: Decimal("8.00"),
    N.taxa_sobre_investimento_ao_ano: Decimal("0.003"),
    N.fracao_de_clientes_no_rotativo: Decimal("0.05"),
    N.fracao_da_fatura_rolada: Decimal("0.30"),
    N.taxa_do_rotativo_ao_mes: Decimal("0.08"),
    N.meses_no_rotativo: Decimal("2"),
    N.fracao_cedida_ao_fidc: Decimal("0.60"),
    N.taxa_fidc_ao_ano: Decimal("0.005"),
    N.cdi_ao_ano: Decimal("0.1365"),
    N.spread_fidc_ao_ano: Decimal("0.03"),
    N.spread_balanco_ao_ano: Decimal("0.04"),
    N.taxa_base_de_perda_ao_ano: Decimal("0.005"),
    N.frequencia_do_cenario_ao_ano: Decimal("0.05"),
    N.custo_de_processamento_por_cartao: Decimal("3.00"),
    N.custo_do_gravame_externo: Decimal("5.00"),
    N.execucoes_ao_ano: Decimal("0.02"),
    N.custo_de_execucao_interna: Decimal("15.00"),
    N.custo_de_execucao_externa: Decimal("60.00"),
    N.aliquota_sobre_receita: Decimal("0.10"),
    N.custo_fixo_mensal: Decimal("400000"),
    N.parceiros: Decimal("5"),
    N.clientes_por_parceiro: Decimal("20000"),
}


def premissas_base() -> Premissas:
    """Premissas da coluna base, com as seis receitas ligadas."""
    return montar_premissas(VALORES_BASE, set(FonteDeReceita), "custodiante_fora_do_ar", ESQUEMA)
