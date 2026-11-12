"""Carteira sintética do painel: determinística, plausível e rápida."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.dominio.erros import ValorInvalido
from app.dominio.stress import simular_painel
from app.infra.carteira_sintetica import (
    TAMANHO_MAXIMO,
    carregar_parametros,
    gerar_carteira,
)
from app.infra.politica_referencia import carregar_politica_de_referencia


def test_mesma_semente_mesma_carteira_e_semente_diferente_outra() -> None:
    parametros = carregar_parametros()
    assert gerar_carteira(parametros, 200, 42) == gerar_carteira(parametros, 200, 42)
    assert gerar_carteira(parametros, 200, 42) != gerar_carteira(parametros, 200, 43)


def test_carteira_tem_a_mistura_pedida() -> None:
    carteira = gerar_carteira(carregar_parametros(), 1000, 42)
    assert len(carteira) == 1000
    garantias = [g for conta in carteira for g in conta.garantias]
    tipos = {g.tipo_ativo.value for g in garantias}
    assert tipos == {
        "deposito",
        "tesouro_selic",
        "tesouro_prefixado",
        "tesouro_ipca",
        "cdb",
        "acoes",
    }
    custodiantes = {g.custodiante_id for g in garantias}
    assert custodiantes == {None, "custodiante_agil", "custodiante_lento"}
    externas = sum(g.custodiante_id is not None for g in garantias)
    assert 0.15 < externas / len(garantias) < 0.45
    assert all(g.valor_de_mercado.positivo for g in garantias)
    # Depósito fica sempre dentro de casa.
    assert all(g.custodiante_id is None for g in garantias if g.tipo_ativo.value == "deposito")


def test_mil_clientes_em_menos_de_um_segundo() -> None:
    parametros = carregar_parametros()
    politica = carregar_politica_de_referencia()
    cenario = parametros.cenarios["crash_da_bolsa"]
    inicio = time.perf_counter()
    carteira = gerar_carteira(parametros, 1000, 42)
    simular_painel(carteira, cenario.choques, politica, cenario.taxa_sem_reforco, cenario.premissas)
    assert time.perf_counter() - inicio < 1.0


def test_os_quatro_cenarios_prontos() -> None:
    cenarios = carregar_parametros().cenarios
    assert list(cenarios) == [
        "dia_normal",
        "crash_da_bolsa",
        "alta_de_juros",
        "custodiante_fora_do_ar",
    ]
    assert cenarios["crash_da_bolsa"].choques["acoes"] == Decimal("-0.30")
    assert cenarios["alta_de_juros"].choques["tesouro_prefixado"] == Decimal("-0.10")
    fora = cenarios["custodiante_fora_do_ar"]
    assert fora.premissas.custodiantes_indisponiveis == frozenset({"custodiante_lento"})
    assert fora.choques == cenarios["dia_normal"].choques


def test_cenario_fora_do_ar_mantem_a_perda_imediata_e_piora_a_externa() -> None:
    parametros = carregar_parametros()
    politica = carregar_politica_de_referencia()
    carteira = gerar_carteira(parametros, 1000, 42)
    normal, fora = (
        simular_painel(carteira, c.choques, politica, c.taxa_sem_reforco, c.premissas)
        for c in (parametros.cenarios["dia_normal"], parametros.cenarios["custodiante_fora_do_ar"])
    )
    assert fora.perda.perda_adicional_externa > normal.perda.perda_adicional_externa


@pytest.mark.parametrize("tamanho", [0, TAMANHO_MAXIMO + 1])
def test_tamanho_fora_do_limite(tamanho: int) -> None:
    with pytest.raises(ValorInvalido):
        gerar_carteira(carregar_parametros(), tamanho, 42)
