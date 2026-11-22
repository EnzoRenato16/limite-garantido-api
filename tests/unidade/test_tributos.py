"""Retenção na execução, lida do catálogo, e o demonstrativo."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.garantia import Garantia
from app.dominio.gravame import Gravame
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Infraestrutura, Instrumento, Produto, Trilho
from app.dominio.tributos import ResultadoDaExecucao, retencao_na_execucao
from tests.apoio import AGORA, CATALOGO, HOJE, reais


def _garantia(tipo: TipoAtivo, aplicado: str, dias_atras: int = 0) -> Garantia:
    return Garantia(
        id="gar_teste",
        conta_id="cta_teste",
        tipo_ativo=tipo,
        valor_aplicado=reais(aplicado),
        criada_em=AGORA - timedelta(days=dias_atras),
        marcada_em=HOJE,
        produto=Produto.EMPRESTIMO,
        gravame=Gravame.solicitar(
            infraestrutura=Infraestrutura.B3_BALCAO,
            instrumento=Instrumento.CESSAO_FIDUCIARIA,
            trilho=Trilho.CASA,
            agora=AGORA,
        ),
    )


def _retencao(tipo: TipoAtivo, aplicado: str, valor: str, dias: int = 0) -> Dinheiro:
    return retencao_na_execucao(_garantia(tipo, aplicado, dias), reais(valor), HOJE, CATALOGO)


@pytest.mark.parametrize(
    ("dias", "esperado"),
    [(0, "45.00"), (180, "45.00"), (181, "40.00"), (360, "40.00"), (400, "35.00"), (721, "30.00")],
)
def test_renda_fixa_segue_a_tabela_regressiva_sobre_o_rendimento(dias: int, esperado: str) -> None:
    # Rendimento de R$ 200,00: 22,5%, 20%, 17,5% e 15% conforme o prazo.
    assert _retencao(TipoAtivo.CDB, "5000.00", "5200.00", dias) == reais(esperado)


def test_sem_rendimento_nao_ha_retencao_na_renda_fixa() -> None:
    assert _retencao(TipoAtivo.TESOURO_SELIC, "5000.00", "4800.00", 10) == reais("0.00")


def test_retencao_arredonda_para_cima() -> None:
    # 22,5% de R$ 0,01 = 0,00225 -> R$ 0,01.
    assert _retencao(TipoAtivo.FUNDO_DI, "100.00", "100.01") == reais("0.01")


@pytest.mark.parametrize("tipo", [TipoAtivo.LCI, TipoAtivo.LCA])
def test_lci_e_lca_sao_isentas(tipo: TipoAtivo) -> None:
    assert _retencao(tipo, "1000.00", "1500.00", 10) == reais("0.00")


def test_acoes_retem_0005_por_cento_da_venda_para_cima() -> None:
    # 0,005% de R$ 10.000,01 = 0,5000005 -> R$ 0,51.
    assert _retencao(TipoAtivo.ACOES, "1.00", "10000.01") == reais("0.51")
    assert _retencao(TipoAtivo.ETF, "1.00", "10000.00") == reais("0.50")


def test_previdencia_usa_o_percentual_ilustrativo() -> None:
    assert _retencao(TipoAtivo.PREVIDENCIA, "1.00", "10000.00") == reais("1000.00")


def test_ativo_sem_regra_de_retencao_e_recusado() -> None:
    with pytest.raises(ValorInvalido):
        _retencao(TipoAtivo.COE, "1.00", "1.00")


def test_demonstrativo_fecha_as_contas() -> None:
    r = ResultadoDaExecucao.demonstrar(reais("5200.00"), reais("35.00"), reais("4000.00"))
    assert r.liquido == reais("5165.00")
    assert r.divida_abatida == reais("4000.00")
    assert r.excedente_devolvido == reais("1165.00")
    menor = ResultadoDaExecucao.demonstrar(reais("100.00"), reais("0.00"), reais("400.00"))
    assert menor.divida_abatida == reais("100.00")
    assert menor.excedente_devolvido == reais("0.00")
    with pytest.raises(ValorInvalido):
        ResultadoDaExecucao(reais("1.00"), reais("0.00"), reais("1.00"), reais("2.00"), reais("0"))
