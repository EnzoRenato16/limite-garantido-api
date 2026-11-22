"""Regras de crédito do cartão sobre o gravame, sem banco e sem HTTP."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.dominio.conta import (
    Conta,
    EstadoChamada,
    EstadoConta,
    EstadoFatura,
    MotivoBloqueio,
    MotivoNegacao,
)
from app.dominio.erros import (
    AtivoNaoAceito,
    CombinacaoNaoPermitida,
    FaturaEmAberto,
    GarantiaCobrindoDivida,
    GarantiaNaoEncontrada,
    PagamentoExcedeDivida,
    SemSaldoParaFaturar,
    TransicaoInvalida,
    ValorInvalido,
    VencimentoInvalido,
)
from app.dominio.eventos import TipoEvento
from app.dominio.gravame import EstadoGravame
from app.dominio.ledger import ContaContabil, TipoLancamento, ledger_fecha
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Instrumento, Produto, Trilho
from tests.apoio import (
    AGORA,
    CATALOGO,
    HOJE,
    custodiar,
    nova_conta,
    politica,
    prazo_da_baixa,
    reais,
)

POL = politica()
CAT = CATALOGO
E = EstadoGravame


def tipos(conta: Conta) -> list[str]:
    return [str(evento.tipo) for evento in conta.pendencias.eventos]


def pos(conta: Conta, pol=POL, hoje: date = HOJE):  # type: ignore[no-untyped-def]
    return conta.posicao(pol, CAT, hoje)


def comprar(conta: Conta, valor: str, hoje=HOJE, pol=POL, carteira=None):  # type: ignore[no-untyped-def]
    return conta.autorizar_compra(reais(valor), "Loja Exemplo", pol, CAT, AGORA, hoje, carteira)


def pagar(conta: Conta, valor: str, hoje: date = HOJE, pol=POL):  # type: ignore[no-untyped-def]
    return conta.pagar(reais(valor), pol, CAT, AGORA, hoje)


def virar(conta: Conta, hoje: date, pol=POL) -> None:  # type: ignore[no-untyped-def]
    conta.virar_o_dia(pol, CAT, AGORA, hoje, prazo_da_baixa(hoje))


def liberar(conta: Conta, garantia_id: str, hoje: date = HOJE, pol=POL):  # type: ignore[no-untyped-def]
    return conta.liberar(garantia_id, pol, CAT, AGORA, hoje, prazo_da_baixa(hoje))


def remarcar(conta: Conta, garantia_id: str, valor: str, hoje: date = HOJE):  # type: ignore[no-untyped-def]
    return conta.remarcar(garantia_id, reais(valor), POL, CAT, AGORA, hoje)


def conta_com_fatura(valor_cdb: str = "5000.00", compra: str = "4000.00") -> Conta:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, valor_cdb)
    comprar(conta, compra)
    conta.fechar_fatura(HOJE + timedelta(days=10), AGORA, HOJE)
    return conta


def tesouro(  # type: ignore[no-untyped-def]
    conta: Conta, tipo: TipoAtivo, valor: str, pol=POL, vencimento: date | None = None
):
    """Tesouro entra pelo TD Garantia, que no catálogo é o único trilho dele."""
    catalogo = CAT.com_agente_do_td(True)
    garantia = conta.receber_de_fora(
        trilho=Trilho.TD_GARANTIA,
        tipo=tipo,
        valor=reais(valor),
        produto=Produto.CARTAO,
        contraparte_id="agente_td_simulado",
        prazo_ate=AGORA + timedelta(days=3),
        politica=pol,
        catalogo=catalogo,
        agora=AGORA,
        hoje=HOJE,
        vencimento=vencimento,
    )
    return conta.concluir_entrada(garantia.id, "SIM-selic-1", pol, catalogo, AGORA, HOJE)


def executadas(conta: Conta) -> list[dict[str, object]]:
    return [
        dict(e.dados) for e in conta.pendencias.eventos if e.tipo is TipoEvento.GARANTIA_EXECUTADA
    ]


# ------------------------------------------------------- entrada na casa
def test_cdb_com_liquidez_diaria_e_protocolo_gera_limite_na_hora() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    assert garantia.estado is E.REGISTRADO
    assert garantia.gravame.vigente
    assert garantia.gravame.protocolo == "SIM-b3_balcao-1"
    assert garantia.produto is Produto.CARTAO
    posicao = pos(conta)
    assert posicao.valor_das_garantias == reais("5000.00")
    assert posicao.limite_total == reais("4250.00")  # 5.000 x 0,85
    assert posicao.limite_disponivel == reais("4250.00")
    assert tipos(conta) == ["gravame.registrado", "limite.atualizado"]
    registrado = conta.pendencias.eventos[0].dados
    assert registrado["ltv"] == "0.85"
    assert registrado["limite_gerado"] == "4250.00"
    assert registrado["infraestrutura"] == "b3_balcao"
    assert registrado["instrumento"] == "cessao_fiduciaria"
    assert [x.tipo for x in conta.pendencias.lancamentos] == [TipoLancamento.BLOQUEIO_GARANTIA]


def test_sem_protocolo_fica_solicitado_e_sem_limite_ate_concluir() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00", protocolo=None)
    assert garantia.estado is E.SOLICITADO
    assert pos(conta).limite_total == reais("0.00")
    assert pos(conta).valor_das_garantias == reais("0.00")
    assert conta.pendencias.lancamentos == []
    assert tipos(conta) == ["gravame.solicitado"]

    conta.concluir_entrada(garantia.id, "SIM-b3_balcao-2", POL, CAT, AGORA, HOJE)
    assert garantia.estado is E.REGISTRADO
    assert pos(conta).limite_total == reais("4250.00")
    with pytest.raises(TransicaoInvalida):
        conta.concluir_entrada(garantia.id, "SIM-b3_balcao-3", POL, CAT, AGORA, HOJE)


@pytest.mark.parametrize(
    ("tipo", "codigo"),
    [
        (TipoAtivo.PREVIDENCIA, "trilho_nao_atende_o_ativo"),
        (TipoAtivo.LCI, "ativo_so_no_emprestimo"),
        (TipoAtivo.LCA, "ativo_so_no_emprestimo"),
        (TipoAtivo.ACOES, "ativo_so_no_emprestimo"),
        (TipoAtivo.TESOURO_SELIC, "trilho_nao_atende_o_ativo"),
        (TipoAtivo.COE, "coe_nao_recomendado"),
    ],
)
def test_catalogo_barra_o_que_nao_entra_no_cartao_pela_casa(tipo: TipoAtivo, codigo: str) -> None:
    conta = nova_conta()
    ltv = {t: Decimal("0.50") for t in TipoAtivo}
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        custodiar(conta, tipo, "1000.00", politica(ltv=ltv))
    assert erro.value.codigo == codigo
    assert conta.garantias == []


def test_cdb_sem_liquidez_diaria_nao_entra_no_cartao() -> None:
    conta = nova_conta()
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        conta.custodiar_na_casa(
            tipo=TipoAtivo.CDB,
            valor=reais("1000.00"),
            produto=Produto.CARTAO,
            politica=POL,
            catalogo=CAT,
            agora=AGORA,
            hoje=HOJE,
            liquidez_dias=30,
            protocolo="SIM-1",
        )
    assert erro.value.codigo == "cartao_exige_liquidez_diaria"


def test_garantia_exige_valor_positivo_ativo_aceito_e_instrumento_do_catalogo() -> None:
    conta = nova_conta()
    with pytest.raises(ValorInvalido):
        custodiar(conta, TipoAtivo.CDB, "0.00")
    so_cdb = politica(ltv={TipoAtivo.CDB: Decimal("0.85")})
    with pytest.raises(AtivoNaoAceito):
        custodiar(conta, TipoAtivo.FUNDO_DI, "100.00", so_cdb)
    with pytest.raises(ValorInvalido):
        conta.custodiar_na_casa(
            tipo=TipoAtivo.CDB,
            valor=reais("100.00"),
            produto=Produto.CARTAO,
            politica=POL,
            catalogo=CAT,
            agora=AGORA,
            hoje=HOJE,
            liquidez_dias=0,
            instrumento=Instrumento.DIREITO_DE_RESGATE,
            protocolo="SIM-1",
        )
    with pytest.raises(GarantiaNaoEncontrada):
        conta.garantia("gar_inexistente")


def test_garantia_de_emprestimo_nao_gera_limite_de_cartao() -> None:
    conta = nova_conta()
    lci = conta.custodiar_na_casa(
        tipo=TipoAtivo.LCI,
        valor=reais("1000.00"),
        produto=Produto.EMPRESTIMO,
        politica=politica(ltv={TipoAtivo.LCI: Decimal("0.80")}),
        catalogo=CAT,
        agora=AGORA,
        hoje=HOJE,
        protocolo="SIM-1",
    )
    assert lci.gravame.vigente
    assert lci.disponivel_para_emprestimo
    assert not lci.gera_limite
    assert pos(conta).limite_total == reais("0.00")


def test_ltv_e_o_menor_entre_politica_e_teto_do_catalogo_pelo_prazo() -> None:
    catalogo = CAT.com_agente_do_td(True)
    pol = politica(ltv={TipoAtivo.TESOURO_PREFIXADO: Decimal("0.80")})
    conta = nova_conta()
    # Vence em 6 anos: o teto do catálogo é 0,70, abaixo dos 0,80 da política.
    tesouro(conta, TipoAtivo.TESOURO_PREFIXADO, "1000.00", pol, vencimento=date(2032, 3, 2))
    assert conta.posicao(pol, catalogo, HOJE).limite_total == reais("700.00")
    # Faltando menos de 2 anos, o teto sobe para 0,85 e vale o 0,80 da política.
    assert conta.posicao(pol, catalogo, date(2030, 6, 1)).limite_total == reais("800.00")


def test_limite_soma_garantias_arredondando_cada_parcela_para_baixo() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.FUNDO_DI, "33.33")  # 31,6635 -> 31,66
    custodiar(conta, TipoAtivo.FUNDO_DI, "33.33")  # 31,6635 -> 31,66
    assert pos(conta).limite_garantido == reais("63.32")


def test_teto_por_cliente_limita_o_limite_mas_nao_a_garantia() -> None:
    pol = politica(teto_cliente="3000.00")
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00", pol)
    posicao = pos(conta, pol)
    assert posicao.limite_garantido == reais("4250.00")
    assert posicao.limite_total == reais("3000.00")
    assert comprar(conta, "3000.01", pol=pol).motivo_negacao is MotivoNegacao.LIMITE_INSUFICIENTE
    assert comprar(conta, "3000.00", pol=pol).aprovada


def test_garantia_de_ativo_que_saiu_da_politica_para_de_gerar_limite() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.FUNDO_DI, "1000.00")
    so_cdb = politica(ltv={TipoAtivo.CDB: Decimal("0.85")})
    posicao = pos(conta, so_cdb)
    assert posicao.valor_das_garantias == reais("1000.00")
    assert posicao.limite_garantido == reais("0.00")


def test_liberado_para_excussao_nao_gera_limite_novo_mas_cobre_a_divida() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "1000.00")
    garantia.gravame.liberar_para_excussao(AGORA)
    posicao = pos(conta)
    assert posicao.limite_garantido == reais("0.00")
    assert posicao.limite_disponivel == reais("0.00")
    assert posicao.valor_das_garantias == reais("5000.00")
    assert posicao.deficit == reais("0.00")
    assert not comprar(conta, "0.01").aprovada


# ------------------------------------------------------------------ compras
def test_compra_aprovada_consome_limite_e_lanca_no_ledger() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00")
    conta.pendencias.limpar()
    compra = comprar(conta, "4000.00")
    assert compra.aprovada
    assert compra.motivo_negacao is None
    posicao = pos(conta)
    assert posicao.divida_total == reais("4000.00")
    assert posicao.limite_disponivel == reais("250.00")
    assert tipos(conta) == ["compra.aprovada", "limite.atualizado"]
    assert conta.saldos.saldo(ContaContabil.LIQUIDACAO_A_PAGAR) == reais("4000.00")
    assert conta.pendencias.compras == [compra]


def test_compra_acima_do_limite_e_negada_sem_mexer_no_ledger() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    conta.pendencias.limpar()
    negada = comprar(conta, "500.00")
    assert not negada.aprovada
    assert negada.motivo_negacao is MotivoNegacao.LIMITE_INSUFICIENTE
    assert pos(conta).divida_total == reais("4000.00")
    assert conta.pendencias.lancamentos == []
    assert tipos(conta) == ["compra.negada"]
    assert conta.pendencias.eventos[0].dados["limite_disponivel"] == "250.00"


def test_compra_no_centavo_exato_do_limite_e_aprovada() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00")
    assert comprar(conta, "4250.00").aprovada
    assert pos(conta).limite_disponivel == reais("0.00")
    assert not comprar(conta, "0.01").aprovada


def test_compra_exige_valor_positivo() -> None:
    conta = nova_conta()
    with pytest.raises(ValorInvalido):
        comprar(conta, "0.00")


def test_teto_do_parceiro_nega_compra_que_estoura_a_carteira() -> None:
    pol = politica(teto_parceiro="10000.00")
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00", pol)
    negada = comprar(conta, "1000.00", pol=pol, carteira=reais("9500.00"))
    assert negada.motivo_negacao is MotivoNegacao.TETO_DO_PARCEIRO_EXCEDIDO
    assert comprar(conta, "500.00", pol=pol, carteira=reais("9500.00")).aprovada


def test_teto_do_parceiro_sem_carteira_informada_falha_fechado() -> None:
    pol = politica(teto_parceiro="10000.00")
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00", pol)
    with pytest.raises(ValueError, match="carteira"):
        comprar(conta, "100.00", pol=pol, carteira=None)


# -------------------------------------------------------------- remarcação
def test_queda_do_ativo_reduz_limite_e_abre_chamada_de_margem_com_prazo() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    conta.pendencias.limpar()

    remarcar(conta, garantia.id, "4500.00")

    posicao = pos(conta)
    assert posicao.limite_total == reais("3825.00")  # 4.500 x 85%
    assert posicao.limite_disponivel == reais("0.00")
    assert posicao.deficit == reais("175.00")
    chamada = conta.chamada_aberta
    assert chamada is not None
    assert chamada.deficit_inicial == reais("175.00")
    assert chamada.prazo_ate == HOJE + timedelta(days=5)
    assert tipos(conta) == ["chamada_de_margem", "limite.atualizado"]
    evento = conta.pendencias.eventos[0]
    assert evento.dados["situacao"] == "aberta"
    assert evento.dados["deficit"] == "175.00"


def test_queda_sem_deficit_nao_abre_chamada_e_alta_gera_lancamento_inverso() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "1000.00")
    remarcar(conta, garantia.id, "4500.00")
    assert conta.chamada_aberta is None
    remarcar(conta, garantia.id, "5200.00")
    assert conta.valor_de_mercado(garantia.id) == reais("5200.00")
    assert ledger_fecha(conta.pendencias.lancamentos)


def test_remarcar_para_o_mesmo_valor_nao_gera_lancamento_nem_evento() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    conta.pendencias.limpar()
    remarcar(conta, garantia.id, "5000.00")
    assert conta.pendencias.lancamentos == []
    assert conta.pendencias.eventos == []


def test_remarcar_exige_gravame_vigente_e_valor_nao_negativo() -> None:
    conta = nova_conta()
    solicitada = custodiar(conta, TipoAtivo.CDB, "100.00", protocolo=None)
    with pytest.raises(TransicaoInvalida):
        remarcar(conta, solicitada.id, "90.00")
    garantia = custodiar(conta, TipoAtivo.CDB, "100.00")
    with pytest.raises(ValorInvalido):
        remarcar(conta, garantia.id, "-1.00")


def test_chamada_e_atendida_com_reforco_de_garantia() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    remarcar(conta, garantia.id, "4500.00")
    chamada = conta.chamada_aberta
    assert chamada is not None
    conta.pendencias.limpar()

    # 205,89 x 0,85 = 175,0065: limite de 175,00, exatamente o déficit.
    custodiar(conta, TipoAtivo.CDB, "205.89")

    assert chamada.estado is EstadoChamada.ATENDIDA
    assert conta.chamada_aberta is None
    atendida = next(e for e in conta.pendencias.eventos if e.tipo is TipoEvento.CHAMADA_DE_MARGEM)
    assert atendida.dados["situacao"] == "atendida"


def test_chamada_e_atendida_com_pagamento() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    remarcar(conta, garantia.id, "4500.00")
    pagar(conta, "174.99")
    assert conta.chamada_aberta is not None  # falta 1 centavo
    pagar(conta, "0.01")
    assert conta.chamada_aberta is None


def test_chamada_vencida_bloqueia_a_conta_e_regularizar_destrava() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    remarcar(conta, garantia.id, "4500.00")
    chamada = conta.chamada_aberta
    assert chamada is not None

    virar(conta, chamada.prazo_ate)
    assert conta.estado is EstadoConta.ATIVA

    conta.pendencias.limpar()
    depois_do_prazo = chamada.prazo_ate + timedelta(days=1)
    virar(conta, depois_do_prazo)
    assert chamada.estado is EstadoChamada.VENCIDA
    assert conta.estado is EstadoConta.BLOQUEADA
    assert conta.motivo_bloqueio is MotivoBloqueio.CHAMADA_DE_MARGEM_VENCIDA
    assert conta.pendencias.eventos[0].dados["situacao"] == "vencida"
    negada = comprar(conta, "1.00", hoje=depois_do_prazo)
    assert negada.motivo_negacao is MotivoNegacao.CONTA_BLOQUEADA
    conta.pendencias.limpar()
    virar(conta, depois_do_prazo)
    assert conta.pendencias.eventos == []
    assert conta.chamada_aberta is None

    pagar(conta, "175.00", depois_do_prazo)
    assert conta.estado is EstadoConta.ATIVA
    assert conta.motivo_bloqueio is None


# ---------------------------------------------------------------- liberação
def test_liberar_derruba_o_limite_na_hora_e_pede_a_baixa_com_o_prazo() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "1000.00")
    with pytest.raises(GarantiaCobrindoDivida) as erro:
        liberar(conta, garantia.id)
    assert erro.value.detalhes["divida_total"] == "1000.00"
    assert garantia.gravame.vigente

    pagar(conta, "1000.00")
    conta.pendencias.limpar()
    liberar(conta, garantia.id)
    assert garantia.estado is E.REGISTRADO
    assert not garantia.gravame.vigente
    assert garantia.gravame.baixa_prazo == prazo_da_baixa(HOJE)
    assert conta.gravames_com_baixa_pendente() == [garantia]
    assert pos(conta).limite_total == reais("0.00")
    assert conta.saldos.saldo_total(ContaContabil.GARANTIAS_A_DEVOLVER) == reais("0.00")
    assert tipos(conta) == ["garantia.liberada", "gravame.baixa_pedida", "limite.atualizado"]

    conta.pendencias.limpar()
    conta.confirmar_baixa(garantia.id, AGORA)
    assert garantia.estado is E.BAIXADO
    assert conta.gravames_com_baixa_pendente() == []
    assert tipos(conta) == ["gravame.baixado"]


def test_libera_uma_garantia_se_a_outra_ainda_cobre_a_divida() -> None:
    conta = nova_conta()
    cdb = custodiar(conta, TipoAtivo.CDB, "5000.00")
    menor = custodiar(conta, TipoAtivo.CDB, "1000.00")  # limite de 850,00
    comprar(conta, "850.00")
    liberar(conta, cdb.id)
    assert pos(conta).limite_disponivel == reais("0.00")
    with pytest.raises(GarantiaCobrindoDivida):
        liberar(conta, menor.id)


def test_garantia_liberada_nao_volta_a_ser_liberada_nem_remarcada() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00")
    liberar(conta, garantia.id)
    with pytest.raises(TransicaoInvalida):
        liberar(conta, garantia.id)
    with pytest.raises(TransicaoInvalida):
        remarcar(conta, garantia.id, "1.00")


# ----------------------------------------------------- faturas e pagamentos
def test_fechar_fatura_move_a_divida_para_faturada() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    fatura = conta.fechar_fatura(HOJE + timedelta(days=10), AGORA, HOJE)
    assert fatura.estado is EstadoFatura.FECHADA
    assert fatura.valor_fechado == reais("4000.00")
    assert conta.divida_faturada == reais("4000.00")
    assert conta.divida_a_faturar == reais("0.00")
    assert conta.divida_total == reais("4000.00")


def test_fechar_fatura_tem_pre_condicoes() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00")
    with pytest.raises(SemSaldoParaFaturar):
        conta.fechar_fatura(HOJE, AGORA, HOJE)
    comprar(conta, "100.00")
    with pytest.raises(VencimentoInvalido):
        conta.fechar_fatura(HOJE - timedelta(days=1), AGORA, HOJE)
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    comprar(conta, "50.00")
    with pytest.raises(FaturaEmAberto):
        conta.fechar_fatura(HOJE, AGORA, HOJE)


def test_pagamento_abate_a_fatura_primeiro_e_antecipa_o_resto() -> None:
    conta = conta_com_fatura()
    comprar(conta, "100.00")
    conta.pendencias.limpar()

    pagamento = pagar(conta, "4050.00")

    assert pagamento.valor_na_fatura == reais("4000.00")
    assert pagamento.valor_antecipado == reais("50.00")
    assert conta.divida_faturada == reais("0.00")
    assert conta.divida_a_faturar == reais("50.00")
    assert conta.fatura_em_aberto is None
    assert conta.faturas[0].estado is EstadoFatura.PAGA
    assert tipos(conta) == ["fatura.paga", "limite.atualizado"]


def test_pagamento_parcial_mantem_a_fatura_em_aberto() -> None:
    conta = conta_com_fatura()
    pagar(conta, "1000.00")
    assert conta.fatura_em_aberto is not None
    assert conta.divida_faturada == reais("3000.00")


def test_pagamento_sem_fatura_antecipa_compras() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "300.00")
    pagamento = pagar(conta, "300.00")
    assert pagamento.fatura_id is None
    assert pagamento.valor_antecipado == reais("300.00")
    assert conta.divida_total == reais("0.00")


def test_pagamento_invalido_e_recusado() -> None:
    conta = conta_com_fatura()
    with pytest.raises(ValorInvalido):
        pagar(conta, "0.00")
    with pytest.raises(PagamentoExcedeDivida):
        pagar(conta, "4000.01")


# ------------------------------------------------------- atraso e execução
def test_fatura_entra_em_atraso_no_dia_seguinte_ao_vencimento() -> None:
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    conta.pendencias.limpar()

    virar(conta, fatura.vencimento)
    assert fatura.estado is EstadoFatura.FECHADA
    assert conta.pendencias.eventos == []

    dia_1 = fatura.vencimento + timedelta(days=1)
    virar(conta, dia_1)
    assert fatura.estado is EstadoFatura.EM_ATRASO
    assert tipos(conta) == ["fatura.em_atraso"]
    assert conta.pendencias.eventos[0].dados["dias_em_atraso"] == 1
    assert comprar(conta, "1.00", hoje=dia_1).motivo_negacao is MotivoNegacao.FATURA_EM_ATRASO

    conta.pendencias.limpar()
    virar(conta, fatura.vencimento + timedelta(days=2))
    assert conta.pendencias.eventos == []


def test_no_setimo_dia_o_cdb_e_vendido_por_inteiro_com_retencao_e_excedente() -> None:
    """CDB de R$ 5.000 aplicado há 400 dias, a mercado por R$ 5.200; fatura de R$ 4.000."""
    conta = nova_conta()
    aplicado_em = HOJE - timedelta(days=400)
    garantia = custodiar(
        conta,
        TipoAtivo.CDB,
        "5000.00",
        hoje=aplicado_em,
        agora=AGORA - timedelta(days=400),
    )
    remarcar(conta, garantia.id, "5200.00")
    comprar(conta, "4000.00")
    fatura = conta.fechar_fatura(HOJE, AGORA, HOJE)
    virar(conta, HOJE + timedelta(days=6))
    assert garantia.gravame.vigente
    conta.pendencias.limpar()

    dia_7 = HOJE + timedelta(days=7)
    virar(conta, dia_7)

    [executada] = executadas(conta)
    # 17,5% (de 361 a 720 dias) sobre o rendimento de R$ 200,00.
    assert executada["demonstrativo"] == {
        "valor_de_mercado": "5200.00",
        "retencao": "35.00",
        "liquido": "5165.00",
        "divida_abatida": "4000.00",
        "excedente_devolvido": "1165.00",
    }
    assert executada["regra_de_retencao"] == "tabela_regressiva_renda_fixa"
    assert executada["dias_em_atraso"] == 7
    assert executada["fatura_id"] == fatura.id
    assert garantia.estado is E.EXECUTADO
    assert garantia.gravame.baixa_prazo == prazo_da_baixa(dia_7)
    assert conta.gravames_com_baixa_pendente() == [garantia]
    assert tipos(conta) == [
        "gravame.liberado_para_excussao",
        "garantia.executada",
        "gravame.baixa_pedida",
        "limite.atualizado",
    ]
    assert conta.divida_total == reais("0.00")
    assert fatura.estado is EstadoFatura.LIQUIDADA_POR_EXECUCAO
    assert conta.valor_de_mercado(garantia.id) == reais("0.00")
    saldos = conta.saldos
    assert saldos.saldo(ContaContabil.CAIXA_RECEBIMENTOS) == reais("4000.00")
    assert saldos.saldo(ContaContabil.EXCEDENTE_A_DEVOLVER) == reais("0.00")
    assert saldos.debitos_menos_creditos() == reais("0.00")
    assert ledger_fecha(conta.pendencias.lancamentos)
    assert [x.tipo for x in conta.pendencias.lancamentos] == [
        TipoLancamento.EXECUCAO_GARANTIA,
        TipoLancamento.DEVOLUCAO_DE_EXCEDENTE,
    ]
    devolucao = conta.pendencias.lancamentos[1]
    assert devolucao.total_de_debitos == reais("1165.00")


def test_execucao_vende_so_as_garantias_necessarias_na_ordem_da_politica() -> None:
    conta = nova_conta()
    fundo = custodiar(conta, TipoAtivo.FUNDO_DI, "10000.00")
    cdb = custodiar(conta, TipoAtivo.CDB, "5000.00")
    outro_fundo = custodiar(conta, TipoAtivo.FUNDO_DI, "3000.00")
    comprar(conta, "6000.00")
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    conta.pendencias.limpar()

    virar(conta, HOJE + timedelta(days=7))

    vendidas = executadas(conta)
    # CDB primeiro (ordem da política); depois o fundo mais antigo, inteiro.
    assert [e["garantia_id"] for e in vendidas] == [cdb.id, fundo.id]
    assert [e["demonstrativo"]["divida_abatida"] for e in vendidas] == [  # type: ignore[index]
        "5000.00",
        "1000.00",
    ]
    assert vendidas[1]["demonstrativo"]["excedente_devolvido"] == "9000.00"  # type: ignore[index]
    assert outro_fundo.gravame.vigente
    assert conta.divida_total == reais("0.00")
    assert pos(conta, hoje=HOJE + timedelta(days=7)).limite_total == reais("2850.00")


def test_execucao_so_abate_a_fatura_vencida_e_devolve_o_resto() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "1000.00")
    comprar(conta, "400.00")
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    comprar(conta, "100.00")  # depois do fechamento: ainda não venceu
    conta.pendencias.limpar()
    virar(conta, HOJE + timedelta(days=7))
    [executada] = executadas(conta)
    assert executada["demonstrativo"]["excedente_devolvido"] == "600.00"  # type: ignore[index]
    assert garantia.estado is E.EXECUTADO
    assert conta.divida_faturada == reais("0.00")
    assert conta.divida_a_faturar == reais("100.00")
    # Sem garantia, a compra a faturar fica descoberta: chamada de margem.
    assert conta.chamada_aberta is not None


def test_garantia_insuficiente_e_executada_inteira_e_a_conta_fica_bloqueada() -> None:
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    garantia = conta.garantias[0]
    remarcar(conta, garantia.id, "3000.00")
    dia_7 = fatura.vencimento + timedelta(days=7)

    virar(conta, dia_7)

    assert garantia.estado is E.EXECUTADO
    assert conta.divida_total == reais("1000.00")
    assert fatura.estado is EstadoFatura.EM_ATRASO
    assert conta.estado is EstadoConta.BLOQUEADA
    assert pos(conta, hoje=dia_7).limite_disponivel == reais("0.00")

    conta.pendencias.limpar()
    virar(conta, dia_7 + timedelta(days=1))
    assert TipoEvento.GARANTIA_EXECUTADA not in [e.tipo for e in conta.pendencias.eventos]

    pagar(conta, "1000.00", dia_7 + timedelta(days=1))
    assert fatura.estado is EstadoFatura.PAGA
    assert conta.estado is EstadoConta.ATIVA


def test_pagar_antes_do_setimo_dia_evita_a_execucao() -> None:
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    virar(conta, fatura.vencimento + timedelta(days=3))
    pagar(conta, "4000.00", fatura.vencimento + timedelta(days=3))
    virar(conta, fatura.vencimento + timedelta(days=7))
    assert conta.garantias[0].gravame.vigente
    assert fatura.estado is EstadoFatura.PAGA


def test_dias_para_execucao_vem_da_politica() -> None:
    pol = politica(dias_para_execucao=3)
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    virar(conta, fatura.vencimento + timedelta(days=3), pol)
    assert conta.garantias[0].estado is E.EXECUTADO


def test_ledger_fecha_depois_de_uma_historia_completa() -> None:
    conta = conta_com_fatura()
    garantia = conta.garantias[0]
    remarcar(conta, garantia.id, "4500.00")
    pagar(conta, "500.00")
    virar(conta, HOJE + timedelta(days=17))
    conta.confirmar_baixa(garantia.id, AGORA)
    assert ledger_fecha(conta.pendencias.lancamentos)
    assert conta.saldos.debitos_menos_creditos() == reais("0.00")
    assert conta.saldos.saldo_total(ContaContabil.GARANTIAS_CUSTODIADAS) == reais("0.00")
    assert conta.saldos.saldo_total(ContaContabil.EXCEDENTE_A_DEVOLVER) == reais("0.00")


def test_fatura_em_atraso_e_avisada_uma_vez_so() -> None:
    pol = politica(dias_para_execucao=30)
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "1000.00", pol)
    conta.autorizar_compra(reais("500.00"), "loja", pol, CAT, AGORA, HOJE)
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    conta.pendencias.limpar()
    for dia in range(1, 11):
        virar(conta, HOJE + timedelta(days=dia), pol)
    avisos = [e for e in conta.pendencias.eventos if e.tipo is TipoEvento.FATURA_EM_ATRASO]
    assert len(avisos) == 1
    assert avisos[0].dados["dias_em_atraso"] == 1
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    assert fatura.dias_em_atraso(HOJE + timedelta(days=10)) == 10


# --------------------------------------------------- cupom do Tesouro gravado
def test_cupom_fica_vinculado_e_a_execucao_vende_titulo_e_cupom() -> None:
    catalogo = CAT.com_agente_do_td(True)
    conta = nova_conta()
    titulo = tesouro(conta, TipoAtivo.TESOURO_SELIC, "10000.00")
    limite_antes = conta.posicao(POL, catalogo, HOJE).limite_total
    assert limite_antes == reais("9500.00")

    conta.receber_evento_de_custodia(titulo.id, reais("300.00"), AGORA)

    assert conta.valor_do_titulo(titulo.id) == reais("9700.00")
    assert conta.cupons_vinculados(titulo.id) == reais("300.00")
    assert conta.valor_de_mercado(titulo.id) == reais("10000.00")
    assert conta.posicao(POL, catalogo, HOJE).limite_total == limite_antes
    assert conta.pendencias.lancamentos[-1].tipo is TipoLancamento.VINCULACAO_DE_CUPOM

    conta.autorizar_compra(reais("2000.00"), "loja", POL, catalogo, AGORA, HOJE)
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    conta.pendencias.limpar()
    dia_7 = HOJE + timedelta(days=7)
    conta.virar_o_dia(POL, catalogo, AGORA, dia_7, prazo_da_baixa(dia_7))
    [executada] = executadas(conta)
    assert executada["demonstrativo"]["valor_de_mercado"] == "10000.00"  # type: ignore[index]
    assert conta.cupons_vinculados(titulo.id) == reais("0.00")
    assert conta.valor_do_titulo(titulo.id) == reais("0.00")
    assert conta.saldos.debitos_menos_creditos() == reais("0.00")


def test_cupom_so_em_tesouro_e_ate_o_valor_do_titulo() -> None:
    conta = nova_conta()
    cdb = custodiar(conta, TipoAtivo.CDB, "1000.00")
    with pytest.raises(ValorInvalido):
        conta.receber_evento_de_custodia(cdb.id, reais("10.00"), AGORA)
    titulo = tesouro(conta, TipoAtivo.TESOURO_SELIC, "100.00")
    with pytest.raises(ValorInvalido):
        conta.receber_evento_de_custodia(titulo.id, reais("100.01"), AGORA)
    with pytest.raises(ValorInvalido):
        conta.receber_evento_de_custodia(titulo.id, reais("0.00"), AGORA)
