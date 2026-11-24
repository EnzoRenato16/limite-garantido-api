"""Carteira sintética do painel: determinística, plausível e rápida."""

from __future__ import annotations

import time
from decimal import Decimal

from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto
from app.dominio.stress import simular_painel
from app.infra.carteira_sintetica import (
    carregar_parametros,
    gerar_carteira,
)
from app.infra.politica_referencia import carregar_politica_de_referencia
from app.infra.regulacao import carregar_catalogo


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
        "cdb",
        "fundo_di",
        "lci",
        "lca",
        "tesouro_selic",
        "tesouro_prefixado",
        "tesouro_ipca",
        "acoes",
        "fii",
        "etf",
        "previdencia",
    }
    assert all(g.valor_de_mercado.positivo for g in garantias)
    # A faixa rara de R$ 100.000 a R$ 400.000 existe, para a concentração aparecer.
    assert any(g.valor_de_mercado.centavos >= 100_000_00 for g in garantias)


def test_cartao_so_tem_cdb_e_fundo_di_e_o_resto_so_no_emprestimo() -> None:
    carteira = gerar_carteira(carregar_parametros(), 1000, 42)
    garantias = [g for conta in carteira for g in conta.garantias]
    no_cartao = {g.tipo_ativo for g in garantias if g.produto is Produto.CARTAO}
    assert no_cartao == {TipoAtivo.CDB, TipoAtivo.FUNDO_DI}
    no_emprestimo = {g.tipo_ativo for g in garantias if g.produto is Produto.EMPRESTIMO}
    assert {
        TipoAtivo.ACOES,
        TipoAtivo.PREVIDENCIA,
        TipoAtivo.TESOURO_SELIC,
        TipoAtivo.LCI,
        TipoAtivo.LCA,
        TipoAtivo.FII,
        TipoAtivo.ETF,
    } <= no_emprestimo
    # Uma conta tem um produto só.
    assert all(len({g.produto for g in conta.garantias}) == 1 for conta in carteira)


def test_onde_cada_ativo_esta() -> None:
    garantias = [g for c in gerar_carteira(carregar_parametros(), 1000, 42) for g in c.garantias]
    tesouro = {TipoAtivo.TESOURO_SELIC, TipoAtivo.TESOURO_PREFIXADO, TipoAtivo.TESOURO_IPCA}
    assert {g.onde_esta for g in garantias if g.tipo_ativo in tesouro} == {
        "agente_td_a",
        "agente_td_b",
    }
    assert {g.onde_esta for g in garantias if g.tipo_ativo is TipoAtivo.PREVIDENCIA} == {
        "entidade_operadora_simulada"
    }
    outros = [g for g in garantias if g.tipo_ativo not in tesouro | {TipoAtivo.PREVIDENCIA}]
    assert all(g.onde_esta is None for g in outros)
    # Emissor só em CDB, LCI e LCA; prazo só em quem vence.
    fgc = {TipoAtivo.CDB, TipoAtivo.LCI, TipoAtivo.LCA}
    assert all((g.emissor is not None) == (g.tipo_ativo in fgc) for g in garantias)
    assert all(g.vencimento_em_anos is not None for g in garantias if g.tipo_ativo in fgc | tesouro)


def test_mil_clientes_em_menos_de_um_segundo() -> None:
    parametros = carregar_parametros()
    politica = carregar_politica_de_referencia()
    cenario = parametros.cenarios["crash_da_bolsa"]
    inicio = time.perf_counter()
    carteira = gerar_carteira(parametros, 1000, 42)
    simular_painel(
        carteira,
        cenario.choques,
        politica,
        cenario.taxa_sem_reforco,
        cenario.premissas,
        carregar_catalogo(),
    )
    assert time.perf_counter() - inicio < 1.0


def test_os_cinco_cenarios_prontos() -> None:
    cenarios = carregar_parametros().cenarios
    assert list(cenarios) == [
        "dia_normal",
        "crash_da_bolsa",
        "alta_de_juros",
        "quebra_de_emissor",
        "agente_perde_participacao",
    ]
    assert "custodiante_fora_do_ar" not in cenarios
    crash = cenarios["crash_da_bolsa"].choques
    assert crash == {
        TipoAtivo.ACOES: Decimal("-0.30"),
        TipoAtivo.FII: Decimal("-0.30"),
        TipoAtivo.ETF: Decimal("-0.30"),
    }
    assert cenarios["alta_de_juros"].choques[TipoAtivo.TESOURO_PREFIXADO] == Decimal("-0.10")
    quebra = cenarios["quebra_de_emissor"].premissas
    assert quebra.emissores_em_quebra == frozenset({"conglomerado_b"})
    agente = cenarios["agente_perde_participacao"].premissas
    assert agente.atraso_por_onde_esta == {"agente_td_b": 14}
    assert agente.custodiantes_indisponiveis == frozenset()


def _painel(cenario_id: str):  # type: ignore[no-untyped-def]
    parametros = carregar_parametros()
    c = parametros.cenarios[cenario_id]
    carteira = gerar_carteira(parametros, 1000, 42)
    return simular_painel(
        carteira,
        c.choques,
        carregar_politica_de_referencia(),
        c.taxa_sem_reforco,
        c.premissas,
        carregar_catalogo(),
    )


def test_quebra_de_emissor_zera_so_cdb_lci_e_lca_do_conglomerado_b() -> None:
    parametros = carregar_parametros()
    premissas = parametros.cenarios["quebra_de_emissor"].premissas
    garantias = [g for c in gerar_carteira(parametros, 1000, 42) for g in c.garantias]
    for g in garantias:
        fator = premissas.fator_de_recuperacao(g)
        quebrou = g.emissor == "conglomerado_b"
        assert fator == (Decimal(0) if quebrou else Decimal(1))
    quebra = _painel("quebra_de_emissor").perda
    normal = _painel("dia_normal").perda
    assert quebra.perda_esperada_total > normal.perda_esperada_total


def test_agente_que_perde_a_participacao_atrasa_14_dias_sem_perda_total() -> None:
    parametros = carregar_parametros()
    premissas = parametros.cenarios["agente_perde_participacao"].premissas
    garantias = [g for c in gerar_carteira(parametros, 1000, 42) for g in c.garantias]
    atrasadas = [g for g in garantias if g.onde_esta == "agente_td_b"]
    assert atrasadas
    for g in atrasadas:
        fator = premissas.fator_de_recuperacao(g)
        desconto = parametros.desconto_diario_por_ativo[g.tipo_ativo] * 14
        assert fator == 1 - desconto
        assert 0 < fator < 1
    outras = [g for g in garantias if g.onde_esta != "agente_td_b"]
    assert all(premissas.fator_de_recuperacao(g) == 1 for g in outras)
