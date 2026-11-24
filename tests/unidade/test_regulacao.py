"""Catálogo regulatório: que combinação de ativo, trilho e produto é aceita, e com que LTV."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import CombinacaoNaoPermitida, ValorInvalido
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import (
    CarteiraDeProvisao,
    CatalogoRegulatorio,
    Categoria,
    CondicaoNoCartao,
    Instrumento,
    Produto,
    RegraDeRetencao,
    TemaDaAfirmacao,
    Trilho,
)
from app.infra.regulacao import carregar_catalogo

T = TipoAtivo
CARTAO = Produto.CARTAO
EMPRESTIMO = Produto.EMPRESTIMO


@pytest.fixture(scope="module")
def catalogo() -> CatalogoRegulatorio:
    return carregar_catalogo()


def _codigo(
    catalogo: CatalogoRegulatorio,
    tipo: TipoAtivo,
    trilho: Trilho,
    produto: Produto,
    *,
    liquidez_dias: int | None = None,
    plano: str | None = None,
    ja_em_garantia: bool = False,
) -> CombinacaoNaoPermitida:
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        catalogo.exigir_combinacao(
            tipo,
            trilho,
            produto,
            liquidez_dias=liquidez_dias,
            plano=plano,
            ja_em_garantia=ja_em_garantia,
        )
    return erro.value


# ------------------------------------------------------------ combinações recusadas
def test_previdencia_no_cartao(catalogo: CatalogoRegulatorio) -> None:
    erro = _codigo(catalogo, T.PREVIDENCIA, Trilho.RES_CONJUNTA_12, CARTAO, plano="pgbl")
    assert erro.codigo == "previdencia_so_no_emprestimo"
    assert "POST /v1/contas/{conta_id}/garantias/previdencia" in erro.sugestao
    assert "POST /v1/contas/{conta_id}/emprestimos" in erro.sugestao
    assert erro.detalhes["sugestao"] == erro.sugestao


@pytest.mark.parametrize(
    ("tipo", "trilho"),
    [
        (T.ACOES, Trilho.PORTABILIDADE_CVM_210),
        (T.FII, Trilho.PORTABILIDADE_CVM_210),
        (T.ETF, Trilho.CASA),
        (T.LCI, Trilho.CASA),
        (T.LCA, Trilho.TRANSFERENCIA_DE_CUSTODIA),
    ],
)
def test_renda_variavel_lci_e_lca_so_no_emprestimo(
    catalogo: CatalogoRegulatorio, tipo: TipoAtivo, trilho: Trilho
) -> None:
    erro = _codigo(catalogo, tipo, trilho, CARTAO)
    assert erro.codigo == "ativo_so_no_emprestimo"
    assert "POST /v1/contas/{conta_id}/emprestimos" in erro.sugestao
    # Com empréstimo, a mesma combinação passa.
    catalogo.exigir_combinacao(tipo, trilho, EMPRESTIMO, liquidez_dias=None)


def test_cdb_no_cartao_exige_liquidez_diaria(catalogo: CatalogoRegulatorio) -> None:
    erro = _codigo(catalogo, T.CDB, Trilho.CASA, CARTAO, liquidez_dias=30)
    assert erro.codigo == "cartao_exige_liquidez_diaria"
    assert "POST /v1/contas/{conta_id}/emprestimos" in erro.sugestao
    # Liquidez desconhecida também não entra no cartão: o lado seguro é recusar.
    assert _codigo(catalogo, T.CDB, Trilho.CASA, CARTAO).codigo == "cartao_exige_liquidez_diaria"
    catalogo.exigir_combinacao(T.CDB, Trilho.CASA, CARTAO, liquidez_dias=0)
    catalogo.exigir_combinacao(T.CDB, Trilho.CASA, EMPRESTIMO, liquidez_dias=30)


def test_fundo_di_no_cartao_so_ate_d1(catalogo: CatalogoRegulatorio) -> None:
    erro = _codigo(catalogo, T.FUNDO_DI, Trilho.CASA, CARTAO, liquidez_dias=2)
    assert erro.codigo == "fundo_no_cartao_so_ate_d1"
    assert "POST /v1/contas/{conta_id}/emprestimos" in erro.sugestao
    catalogo.exigir_combinacao(T.FUNDO_DI, Trilho.CASA, CARTAO, liquidez_dias=1)
    catalogo.exigir_combinacao(T.FUNDO_DI, Trilho.CASA, CARTAO, liquidez_dias=0)


@pytest.mark.parametrize("tipo", [T.TESOURO_SELIC, T.TESOURO_PREFIXADO, T.TESOURO_IPCA])
def test_tesouro_no_cartao_depende_da_chave_do_agente_do_td(
    catalogo: CatalogoRegulatorio, tipo: TipoAtivo
) -> None:
    assert catalogo.ctvm_do_grupo_e_agente_do_td is False
    erro = _codigo(catalogo, tipo, Trilho.TD_GARANTIA, CARTAO)
    assert erro.codigo == "tesouro_no_cartao_exige_agente_do_td"
    assert "POST /v1/contas/{conta_id}/garantias/tesouro" in erro.sugestao
    assert "POST /v1/contas/{conta_id}/emprestimos" in erro.sugestao
    catalogo.exigir_combinacao(tipo, Trilho.TD_GARANTIA, EMPRESTIMO, liquidez_dias=None)

    com_agente = carregar_catalogo().com_agente_do_td(True)
    com_agente.exigir_combinacao(tipo, Trilho.TD_GARANTIA, CARTAO, liquidez_dias=None)


@pytest.mark.parametrize(
    ("tipo", "rota"),
    [
        (T.CDB, "POST /v1/contas/{conta_id}/transferencias-de-custodia"),
        (T.LCI, "POST /v1/contas/{conta_id}/transferencias-de-custodia"),
        (T.LCA, "POST /v1/contas/{conta_id}/transferencias-de-custodia"),
        (T.TESOURO_SELIC, "POST /v1/contas/{conta_id}/garantias/tesouro"),
        (T.TESOURO_PREFIXADO, "POST /v1/contas/{conta_id}/garantias/tesouro"),
        (T.TESOURO_IPCA, "POST /v1/contas/{conta_id}/garantias/tesouro"),
    ],
)
def test_portabilidade_cvm_210_nao_atende_renda_fixa_nem_tesouro(
    catalogo: CatalogoRegulatorio, tipo: TipoAtivo, rota: str
) -> None:
    erro = _codigo(catalogo, tipo, Trilho.PORTABILIDADE_CVM_210, EMPRESTIMO)
    assert erro.codigo == "trilho_nao_atende_o_ativo"
    assert rota in erro.sugestao


def test_portabilidade_de_ativo_ja_em_garantia(catalogo: CatalogoRegulatorio) -> None:
    erro = _codigo(catalogo, T.ACOES, Trilho.PORTABILIDADE_CVM_210, EMPRESTIMO, ja_em_garantia=True)
    assert erro.codigo == "ativo_ja_em_garantia"
    assert "POST /v1/contas/{conta_id}/portabilidades" in erro.sugestao
    catalogo.exigir_combinacao(
        T.ACOES, Trilho.PORTABILIDADE_CVM_210, EMPRESTIMO, liquidez_dias=None
    )


@pytest.mark.parametrize("trilho", list(Trilho))
@pytest.mark.parametrize("produto", list(Produto))
def test_coe_nao_e_recomendado_em_nenhuma_combinacao(
    catalogo: CatalogoRegulatorio, trilho: Trilho, produto: Produto
) -> None:
    erro = _codigo(catalogo, T.COE, trilho, produto, liquidez_dias=0)
    assert erro.codigo == "coe_nao_recomendado"
    assert "GET /v1/regulacao/matriz" in erro.sugestao


def test_previdencia_fapi_fica_fora(catalogo: CatalogoRegulatorio) -> None:
    erro = _codigo(catalogo, T.PREVIDENCIA, Trilho.RES_CONJUNTA_12, EMPRESTIMO, plano="fapi")
    assert erro.codigo == "plano_fora_da_res_conjunta_12"
    assert "POST /v1/contas/{conta_id}/garantias/previdencia" in erro.sugestao
    for plano in ("pgbl", "vgbl", "capitalizacao"):
        catalogo.exigir_combinacao(
            T.PREVIDENCIA, Trilho.RES_CONJUNTA_12, EMPRESTIMO, liquidez_dias=None, plano=plano
        )


def test_previdencia_so_entra_pela_res_conjunta_12(catalogo: CatalogoRegulatorio) -> None:
    erro = _codigo(catalogo, T.PREVIDENCIA, Trilho.CASA, EMPRESTIMO, plano="vgbl")
    assert erro.codigo == "trilho_nao_atende_o_ativo"
    assert "POST /v1/contas/{conta_id}/garantias/previdencia" in erro.sugestao


def test_erro_traz_codigo_estavel_e_sugestao() -> None:
    erro = CombinacaoNaoPermitida("prazo_alem_da_acumulacao", sugestao="Encurte o prazo")
    assert erro.codigo == "prazo_alem_da_acumulacao"
    assert erro.sugestao == "Encurte o prazo"
    assert erro.mensagem
    assert erro.detalhes == {"sugestao": "Encurte o prazo"}


# ---------------------------------------------------------------------------- LTV
@pytest.mark.parametrize(
    ("anos", "esperado"),
    [
        ("1", "0.85"),
        ("2", "0.85"),
        ("2.01", "0.80"),
        ("3", "0.80"),
        ("5", "0.80"),
        ("10", "0.70"),
        ("0", "0.85"),
    ],
)
def test_ltv_do_prefixado_cai_com_o_prazo(
    catalogo: CatalogoRegulatorio, anos: str, esperado: str
) -> None:
    assert catalogo.ltv_maximo(T.TESOURO_PREFIXADO, Decimal(anos)) == Decimal(esperado)
    assert catalogo.ltv_maximo(T.TESOURO_IPCA, Decimal(anos)) == Decimal(esperado)


def test_ltv_sem_prazo(catalogo: CatalogoRegulatorio) -> None:
    assert catalogo.ltv_maximo(T.CDB, None) == Decimal("0.95")
    assert catalogo.ltv_maximo(T.TESOURO_SELIC, None) == Decimal("0.95")
    assert catalogo.ltv_maximo(T.LCI, None) == Decimal("0.90")
    assert catalogo.ltv_maximo(T.ACOES, None) == Decimal("0.70")
    assert catalogo.ltv_maximo(T.PREVIDENCIA, None) == Decimal("0.80")
    # Prazo desconhecido num ativo com faixas por prazo: vale a faixa mais longa (a menor).
    assert catalogo.ltv_maximo(T.TESOURO_PREFIXADO, None) == Decimal("0.70")


def test_ltv_de_coe_e_prazo_negativo(catalogo: CatalogoRegulatorio) -> None:
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        catalogo.ltv_maximo(T.COE, None)
    assert erro.value.codigo == "coe_nao_recomendado"
    with pytest.raises(ValorInvalido):
        catalogo.ltv_maximo(T.TESOURO_PREFIXADO, Decimal("-1"))


# ------------------------------------------------------------- dados do catálogo
def test_todo_tipo_de_ativo_tem_regra(catalogo: CatalogoRegulatorio) -> None:
    assert set(catalogo.ativos) == set(TipoAtivo)
    assert set(catalogo.trilhos) == set(Trilho)
    assert len(TipoAtivo) == 12
    assert "deposito" not in {t.value for t in TipoAtivo}


def test_valores_do_brief(catalogo: CatalogoRegulatorio) -> None:
    assert catalogo.teto_do_fgc == Dinheiro.de_decimal("250000.00")
    assert catalogo.prazo_da_baixa_dias_uteis == 2
    pisos = catalogo.pisos_de_provisao
    assert pisos[CarteiraDeProvisao.C2].primeiro_mes == Decimal("0.30")
    assert pisos[CarteiraDeProvisao.C3].primeiro_mes == Decimal("0.45")
    assert pisos[CarteiraDeProvisao.C5].primeiro_mes == Decimal("0.50")
    assert pisos[CarteiraDeProvisao.C2].decimo_terceiro_mes == Decimal("0.708")
    assert pisos[CarteiraDeProvisao.C3].decimo_terceiro_mes == Decimal("0.894")
    assert pisos[CarteiraDeProvisao.C5].decimo_terceiro_mes == Decimal("0.908")


def test_carteira_de_provisao_por_instrumento(catalogo: CatalogoRegulatorio) -> None:
    cdb = catalogo.regra(T.CDB)
    assert set(cdb.carteira_de_provisao.values()) == {CarteiraDeProvisao.C2}
    acoes = catalogo.regra(T.ACOES)
    assert acoes.carteira_de_provisao[Instrumento.ALIENACAO_FIDUCIARIA] is CarteiraDeProvisao.C2
    assert acoes.carteira_de_provisao[Instrumento.PENHOR] is CarteiraDeProvisao.C3
    lci = catalogo.regra(T.LCI)
    assert lci.carteira_de_provisao[Instrumento.CESSAO_FIDUCIARIA] is CarteiraDeProvisao.C3
    previdencia = catalogo.regra(T.PREVIDENCIA)
    assert previdencia.instrumentos == (Instrumento.DIREITO_DE_RESGATE,)
    assert previdencia.carteira_de_provisao[Instrumento.DIREITO_DE_RESGATE] is CarteiraDeProvisao.C3


def test_prazos_dos_trilhos(catalogo: CatalogoRegulatorio) -> None:
    portabilidade = catalogo.trilhos[Trilho.PORTABILIDADE_CVM_210]
    assert portabilidade.prazo_e_regulatorio
    assert portabilidade.prazo_para(T.ACOES) == 2
    assert portabilidade.prazo_para(T.FII) == 2
    assert portabilidade.prazo_para(T.ETF) == 2
    assert portabilidade.prazo_para(T.FUNDO_DI) == 9
    transferencia = catalogo.trilhos[Trilho.TRANSFERENCIA_DE_CUSTODIA]
    assert (transferencia.prazo_para(T.CDB), transferencia.prazo_e_regulatorio) == (5, False)
    td = catalogo.trilhos[Trilho.TD_GARANTIA]
    assert (td.prazo_para(T.TESOURO_IPCA), td.prazo_e_regulatorio, td.pode_recusar) == (
        3,
        False,
        True,
    )
    rc12 = catalogo.trilhos[Trilho.RES_CONJUNTA_12]
    assert (rc12.prazo_para(T.PREVIDENCIA), rc12.prazo_e_regulatorio, rc12.pode_recusar) == (
        2,
        True,
        False,
    )
    casa = catalogo.trilhos[Trilho.CASA]
    assert casa.prazo_para(T.CDB) is None  # None só quando o trilho não tem prazo
    assert not casa.tem_prazo
    assert portabilidade.tem_prazo
    assert catalogo.prazo_do_trilho(Trilho.PORTABILIDADE_CVM_210, T.FUNDO_DI) == 9
    assert catalogo.prazo_do_trilho(Trilho.CASA, T.CDB) is None


def test_prazo_de_trilho_que_nao_atende_o_ativo_recusa_com_sugestao(
    catalogo: CatalogoRegulatorio,
) -> None:
    # Prazo por ativo: o CDB não está na portabilidade.
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        catalogo.trilhos[Trilho.PORTABILIDADE_CVM_210].prazo_para(T.CDB)
    assert erro.value.codigo == "trilho_nao_atende_o_ativo"
    assert erro.value.sugestao
    # Prazo único (transferência de custódia): quem confere o ativo é o catálogo.
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        catalogo.prazo_do_trilho(Trilho.TRANSFERENCIA_DE_CUSTODIA, T.PREVIDENCIA)
    assert erro.value.codigo == "trilho_nao_atende_o_ativo"
    assert "garantias/previdencia" in erro.value.sugestao


def test_regras_de_produto_e_execucao(catalogo: CatalogoRegulatorio) -> None:
    assert catalogo.regra(T.CDB).condicao_no_cartao is CondicaoNoCartao.LIQUIDEZ_DIARIA
    assert catalogo.regra(T.FUNDO_DI).condicao_no_cartao is CondicaoNoCartao.RESGATE_ATE_D1
    assert catalogo.regra(T.TESOURO_SELIC).condicao_no_cartao is (
        CondicaoNoCartao.CTVM_AGENTE_DO_TD
    )
    assert catalogo.regra(T.PREVIDENCIA).produtos == (EMPRESTIMO,)
    assert catalogo.regra(T.PREVIDENCIA).dias_minimos_execucao == 90
    assert catalogo.regra(T.PREVIDENCIA).planos_aceitos == ("pgbl", "vgbl", "capitalizacao")
    assert catalogo.regra(T.COE).recomendado is False
    assert all(catalogo.regra(t).recomendado for t in TipoAtivo if t is not T.COE)


def test_retencao_na_execucao_e_dado_do_catalogo(catalogo: CatalogoRegulatorio) -> None:
    def regra(tipo: TipoAtivo) -> RegraDeRetencao:
        retencao = catalogo.regra(tipo).retencao
        assert retencao is not None
        return retencao.regra

    for tipo in (T.CDB, T.FUNDO_DI, T.TESOURO_SELIC, T.TESOURO_PREFIXADO, T.TESOURO_IPCA):
        assert regra(tipo) is RegraDeRetencao.TABELA_REGRESSIVA_RENDA_FIXA
    assert regra(T.LCI) is regra(T.LCA) is RegraDeRetencao.ISENTO
    for tipo in (T.ACOES, T.FII, T.ETF):
        retencao = catalogo.regra(tipo).retencao
        assert retencao is not None
        assert retencao.regra is RegraDeRetencao.SOBRE_A_VENDA
        assert retencao.percentual == Decimal("0.00005")
    previdencia = catalogo.regra(T.PREVIDENCIA).retencao
    assert previdencia is not None
    assert previdencia.regra is RegraDeRetencao.JA_LIQUIDO
    assert previdencia.percentual is None
    primeira = previdencia.afirmacoes[0]
    assert (primeira.categoria, primeira.artigo) == (Categoria.INTERPRETACAO, "art. 13")
    assert [
        (faixa.ate_dias_corridos, faixa.aliquota) for faixa in catalogo.tabela_regressiva_renda_fixa
    ] == [
        (180, Decimal("0.225")),
        (360, Decimal("0.20")),
        (720, Decimal("0.175")),
        (None, Decimal("0.15")),
    ]
    # FII e ETF não foram reconferidos na fonte.
    fii = catalogo.regra(T.FII).retencao
    assert fii is not None
    assert {a.categoria for a in fii.afirmacoes} == {Categoria.NAO_VERIFICADO}


def test_cada_ativo_tem_as_afirmacoes_da_matriz(catalogo: CatalogoRegulatorio) -> None:
    temas = {
        TemaDaAfirmacao.O_QUE_FUNCIONA,
        TemaDaAfirmacao.TRILHO,
        TemaDaAfirmacao.INSTRUMENTO,
        TemaDaAfirmacao.INFRAESTRUTURA,
        TemaDaAfirmacao.PRODUTO,
        TemaDaAfirmacao.PRAZO,
        TemaDaAfirmacao.RISCO_DE_EXECUCAO,
    }
    for tipo, regra in catalogo.ativos.items():
        assert temas <= {a.tema for a in regra.afirmacoes}, tipo
        for afirmacao in regra.afirmacoes:
            if afirmacao.categoria is Categoria.NORMA:
                assert afirmacao.norma
                assert afirmacao.artigo


def test_politica_de_parceiro_valida_contra_o_catalogo(catalogo: CatalogoRegulatorio) -> None:
    catalogo.validar_ltv_da_politica({T.CDB: Decimal("0.95"), T.ACOES: Decimal("0.70")})
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        catalogo.validar_ltv_da_politica({T.COE: Decimal("0.10")})
    assert erro.value.codigo == "politica_ativo_nao_recomendado"
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        catalogo.validar_ltv_da_politica({T.ACOES: Decimal("0.71")})
    assert erro.value.codigo == "politica_ltv_acima_da_faixa"
    assert erro.value.detalhes["ltv_maximo"] == "0.70"


def test_toda_carteira_de_provisao_em_uso_tem_piso(catalogo: CatalogoRegulatorio) -> None:
    from dataclasses import replace

    from app.dominio.regulacao import CarteiraDeProvisao

    for carteira in (CarteiraDeProvisao.C2, CarteiraDeProvisao.C3, CarteiraDeProvisao.C5):
        sem_piso = {c: p for c, p in catalogo.pisos_de_provisao.items() if c is not carteira}
        with pytest.raises(ValorInvalido, match="sem piso"):
            replace(catalogo, pisos_de_provisao=sem_piso)
