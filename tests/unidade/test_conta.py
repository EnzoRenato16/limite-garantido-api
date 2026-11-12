"""Regras de crédito da conta, sem banco e sem HTTP."""

from datetime import timedelta
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
from app.dominio.garantia import EstadoGarantia
from app.dominio.ledger import ContaContabil, TipoLancamento, ledger_fecha
from app.dominio.politica import TipoAtivo
from tests.apoio import AGORA, HOJE, bloquear, nova_conta, politica, reais

POL = politica()


def tipos(conta: Conta) -> list[str]:
    return [str(evento.tipo) for evento in conta.pendencias.eventos]


def comprar(conta: Conta, valor: str, hoje=HOJE, pol=POL, carteira=None):  # type: ignore[no-untyped-def]
    return conta.autorizar_compra(reais(valor), "Loja Exemplo", pol, AGORA, hoje, carteira)


def conta_com_fatura(valor_cdb: str = "5000.00", compra: str = "4000.00") -> Conta:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, valor_cdb)
    comprar(conta, compra)
    conta.fechar_fatura(HOJE + timedelta(days=10), AGORA, HOJE)
    return conta


# ---------------------------------------------------------------- garantias
def test_garantia_nasce_pendente_e_so_gera_limite_depois_do_bloqueio() -> None:
    conta = nova_conta()
    garantia = conta.registrar_garantia(TipoAtivo.CDB, reais("5000.00"), POL, AGORA, HOJE)
    assert garantia.estado is EstadoGarantia.PENDENTE
    assert conta.posicao(POL).limite_total == reais("0.00")
    assert conta.pendencias.lancamentos == []

    conta.confirmar_bloqueio(garantia.id, POL, AGORA, HOJE)
    assert garantia.estado is EstadoGarantia.BLOQUEADA
    posicao = conta.posicao(POL)
    assert posicao.valor_das_garantias == reais("5000.00")
    assert posicao.limite_total == reais("4250.00")  # 5.000 x 85%
    assert posicao.limite_disponivel == reais("4250.00")
    assert tipos(conta) == ["garantia.bloqueada", "limite.atualizado"]
    assert [x.tipo for x in conta.pendencias.lancamentos] == [TipoLancamento.BLOQUEIO_GARANTIA]


def test_nao_confirma_o_mesmo_bloqueio_duas_vezes() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    with pytest.raises(TransicaoInvalida):
        conta.confirmar_bloqueio(garantia.id, POL, AGORA, HOJE)
    assert conta.posicao(POL).limite_total == reais("4250.00")


def test_garantia_exige_valor_positivo_e_ativo_aceito() -> None:
    conta = nova_conta()
    with pytest.raises(ValorInvalido):
        conta.registrar_garantia(TipoAtivo.CDB, reais("0.00"), POL, AGORA, HOJE)
    so_cdb = politica(ltv={TipoAtivo.CDB: Decimal("0.85")})
    with pytest.raises(AtivoNaoAceito):
        conta.registrar_garantia(TipoAtivo.ACOES, reais("100.00"), so_cdb, AGORA, HOJE)
    with pytest.raises(GarantiaNaoEncontrada):
        conta.garantia("gar_inexistente")


def test_limite_soma_garantias_arredondando_cada_parcela_para_baixo() -> None:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.FUNDO_DI, "33.33")  # 31,6635 -> 31,66
    bloquear(conta, TipoAtivo.FUNDO_DI, "33.33")  # 31,6635 -> 31,66
    assert conta.posicao(POL).limite_garantido == reais("63.32")


def test_teto_por_cliente_limita_o_limite_mas_nao_a_garantia() -> None:
    pol = politica(teto_cliente="3000.00")
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00", pol)
    posicao = conta.posicao(pol)
    assert posicao.limite_garantido == reais("4250.00")
    assert posicao.limite_total == reais("3000.00")
    assert comprar(conta, "3000.01", pol=pol).motivo_negacao is MotivoNegacao.LIMITE_INSUFICIENTE
    assert comprar(conta, "3000.00", pol=pol).aprovada


def test_garantia_de_ativo_que_saiu_da_politica_para_de_gerar_limite() -> None:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.ACOES, "1000.00")
    so_cdb = politica(ltv={TipoAtivo.CDB: Decimal("0.85")})
    posicao = conta.posicao(so_cdb)
    assert posicao.valor_das_garantias == reais("1000.00")
    assert posicao.limite_garantido == reais("0.00")


# ------------------------------------------------------------------ compras
def test_compra_aprovada_consome_limite_e_lanca_no_ledger() -> None:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00")
    conta.pendencias.limpar()
    compra = comprar(conta, "4000.00")
    assert compra.aprovada
    assert compra.motivo_negacao is None
    posicao = conta.posicao(POL)
    assert posicao.divida_total == reais("4000.00")
    assert posicao.limite_disponivel == reais("250.00")
    assert tipos(conta) == ["compra.aprovada", "limite.atualizado"]
    assert conta.saldos.saldo(ContaContabil.LIQUIDACAO_A_PAGAR) == reais("4000.00")
    assert conta.pendencias.compras == [compra]


def test_compra_acima_do_limite_e_negada_sem_mexer_no_ledger() -> None:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    conta.pendencias.limpar()
    negada = comprar(conta, "500.00")
    assert not negada.aprovada
    assert negada.motivo_negacao is MotivoNegacao.LIMITE_INSUFICIENTE
    assert conta.posicao(POL).divida_total == reais("4000.00")
    assert conta.pendencias.lancamentos == []
    assert tipos(conta) == ["compra.negada"]
    assert conta.pendencias.eventos[0].dados["limite_disponivel"] == "250.00"


def test_compra_no_centavo_exato_do_limite_e_aprovada() -> None:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00")
    assert comprar(conta, "4250.00").aprovada
    assert conta.posicao(POL).limite_disponivel == reais("0.00")
    assert not comprar(conta, "0.01").aprovada


def test_compra_exige_valor_positivo() -> None:
    conta = nova_conta()
    with pytest.raises(ValorInvalido):
        comprar(conta, "0.00")


def test_teto_do_parceiro_nega_compra_que_estoura_a_carteira() -> None:
    pol = politica(teto_parceiro="10000.00")
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00", pol)
    negada = comprar(conta, "1000.00", pol=pol, carteira=reais("9500.00"))
    assert negada.motivo_negacao is MotivoNegacao.TETO_DO_PARCEIRO_EXCEDIDO
    assert comprar(conta, "500.00", pol=pol, carteira=reais("9500.00")).aprovada


def test_teto_do_parceiro_sem_carteira_informada_falha_fechado() -> None:
    pol = politica(teto_parceiro="10000.00")
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00", pol)
    with pytest.raises(ValueError, match="carteira"):
        comprar(conta, "100.00", pol=pol, carteira=None)


# -------------------------------------------------------------- remarcação
def test_queda_do_ativo_reduz_limite_e_abre_chamada_de_margem_com_prazo() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    conta.pendencias.limpar()

    conta.remarcar(garantia.id, reais("4500.00"), POL, AGORA, HOJE)

    posicao = conta.posicao(POL)
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
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "1000.00")
    conta.remarcar(garantia.id, reais("4500.00"), POL, AGORA, HOJE)
    assert conta.chamada_aberta is None
    conta.remarcar(garantia.id, reais("5200.00"), POL, AGORA, HOJE)
    assert conta.valor_de_mercado(garantia.id) == reais("5200.00")
    assert ledger_fecha(conta.pendencias.lancamentos)


def test_remarcar_para_o_mesmo_valor_nao_gera_lancamento_nem_evento() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    conta.pendencias.limpar()
    conta.remarcar(garantia.id, reais("5000.00"), POL, AGORA, HOJE)
    assert conta.pendencias.lancamentos == []
    assert conta.pendencias.eventos == []


def test_remarcar_exige_garantia_ativa_e_valor_nao_negativo() -> None:
    conta = nova_conta()
    pendente = conta.registrar_garantia(TipoAtivo.CDB, reais("100.00"), POL, AGORA, HOJE)
    with pytest.raises(TransicaoInvalida):
        conta.remarcar(pendente.id, reais("90.00"), POL, AGORA, HOJE)
    garantia = bloquear(conta, TipoAtivo.CDB, "100.00")
    with pytest.raises(ValorInvalido):
        conta.remarcar(garantia.id, reais("-1.00"), POL, AGORA, HOJE)


def test_chamada_e_atendida_com_reforco_de_garantia() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    conta.remarcar(garantia.id, reais("4500.00"), POL, AGORA, HOJE)
    chamada = conta.chamada_aberta
    assert chamada is not None
    conta.pendencias.limpar()

    bloquear(conta, TipoAtivo.DEPOSITO, "175.00")

    assert chamada.estado is EstadoChamada.ATENDIDA
    assert conta.chamada_aberta is None
    assert "chamada_de_margem" in tipos(conta)
    atendida = next(e for e in conta.pendencias.eventos if e.tipo is TipoEvento.CHAMADA_DE_MARGEM)
    assert atendida.dados["situacao"] == "atendida"


def test_chamada_e_atendida_com_pagamento() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    conta.remarcar(garantia.id, reais("4500.00"), POL, AGORA, HOJE)
    conta.pagar(reais("174.99"), POL, AGORA, HOJE)
    assert conta.chamada_aberta is not None  # falta 1 centavo
    conta.pagar(reais("0.01"), POL, AGORA, HOJE)
    assert conta.chamada_aberta is None


def test_chamada_vencida_bloqueia_a_conta_e_regularizar_destrava() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    conta.remarcar(garantia.id, reais("4500.00"), POL, AGORA, HOJE)
    chamada = conta.chamada_aberta
    assert chamada is not None

    # No último dia do prazo nada acontece.
    conta.virar_o_dia(POL, AGORA, chamada.prazo_ate)
    assert conta.estado is EstadoConta.ATIVA

    conta.pendencias.limpar()
    depois_do_prazo = chamada.prazo_ate + timedelta(days=1)
    conta.virar_o_dia(POL, AGORA, depois_do_prazo)
    assert chamada.estado is EstadoChamada.VENCIDA
    assert conta.estado is EstadoConta.BLOQUEADA
    assert conta.motivo_bloqueio is MotivoBloqueio.CHAMADA_DE_MARGEM_VENCIDA
    assert conta.pendencias.eventos[0].dados["situacao"] == "vencida"
    negada = comprar(conta, "1.00", hoje=depois_do_prazo)
    assert negada.motivo_negacao is MotivoNegacao.CONTA_BLOQUEADA
    # Rodar a rotina de novo no mesmo dia não repete o efeito.
    conta.pendencias.limpar()
    conta.virar_o_dia(POL, AGORA, depois_do_prazo)
    assert conta.pendencias.eventos == []
    assert conta.chamada_aberta is None  # conta bloqueada não abre chamada nova

    conta.pagar(reais("175.00"), POL, AGORA, depois_do_prazo)
    assert conta.estado is EstadoConta.ATIVA
    assert conta.motivo_bloqueio is None


# ---------------------------------------------------------------- liberação
def test_liberacao_e_recusada_enquanto_a_garantia_cobre_divida() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "1000.00")
    with pytest.raises(GarantiaCobrindoDivida) as erro:
        conta.liberar(garantia.id, POL, AGORA, HOJE)
    assert erro.value.detalhes["divida_total"] == "1000.00"
    assert garantia.estado is EstadoGarantia.BLOQUEADA

    conta.pagar(reais("1000.00"), POL, AGORA, HOJE)
    conta.pendencias.limpar()
    conta.liberar(garantia.id, POL, AGORA, HOJE)
    assert garantia.estado is EstadoGarantia.LIBERADA
    assert conta.posicao(POL).limite_total == reais("0.00")
    assert conta.saldos.saldo_total(ContaContabil.GARANTIAS_A_DEVOLVER) == reais("0.00")
    assert tipos(conta) == ["garantia.liberada", "limite.atualizado"]


def test_libera_uma_garantia_se_a_outra_ainda_cobre_a_divida() -> None:
    conta = nova_conta()
    cdb = bloquear(conta, TipoAtivo.CDB, "5000.00")
    deposito = bloquear(conta, TipoAtivo.DEPOSITO, "1000.00")
    comprar(conta, "1000.00")
    conta.liberar(cdb.id, POL, AGORA, HOJE)
    assert conta.posicao(POL).limite_disponivel == reais("0.00")
    with pytest.raises(GarantiaCobrindoDivida):
        conta.liberar(deposito.id, POL, AGORA, HOJE)


def test_garantia_liberada_nao_volta_a_ser_liberada_nem_remarcada() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "5000.00")
    conta.liberar(garantia.id, POL, AGORA, HOJE)
    with pytest.raises(TransicaoInvalida):
        conta.liberar(garantia.id, POL, AGORA, HOJE)
    with pytest.raises(TransicaoInvalida):
        conta.remarcar(garantia.id, reais("1.00"), POL, AGORA, HOJE)


# ----------------------------------------------------- faturas e pagamentos
def test_fechar_fatura_move_a_divida_para_faturada() -> None:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "4000.00")
    fatura = conta.fechar_fatura(HOJE + timedelta(days=10), AGORA, HOJE)
    assert fatura.estado is EstadoFatura.FECHADA
    assert fatura.valor_fechado == reais("4000.00")
    assert conta.divida_faturada == reais("4000.00")
    assert conta.divida_a_faturar == reais("0.00")
    assert conta.divida_total == reais("4000.00")


def test_fechar_fatura_tem_pre_condicoes() -> None:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00")
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
    comprar(conta, "100.00")  # compra nova, ainda a faturar
    conta.pendencias.limpar()

    pagamento = conta.pagar(reais("4050.00"), POL, AGORA, HOJE)

    assert pagamento.valor_na_fatura == reais("4000.00")
    assert pagamento.valor_antecipado == reais("50.00")
    assert conta.divida_faturada == reais("0.00")
    assert conta.divida_a_faturar == reais("50.00")
    assert conta.fatura_em_aberto is None
    assert conta.faturas[0].estado is EstadoFatura.PAGA
    assert tipos(conta) == ["fatura.paga", "limite.atualizado"]


def test_pagamento_parcial_mantem_a_fatura_em_aberto() -> None:
    conta = conta_com_fatura()
    conta.pagar(reais("1000.00"), POL, AGORA, HOJE)
    assert conta.fatura_em_aberto is not None
    assert conta.divida_faturada == reais("3000.00")


def test_pagamento_sem_fatura_antecipa_compras() -> None:
    conta = nova_conta()
    bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "300.00")
    pagamento = conta.pagar(reais("300.00"), POL, AGORA, HOJE)
    assert pagamento.fatura_id is None
    assert pagamento.valor_antecipado == reais("300.00")
    assert conta.divida_total == reais("0.00")


def test_pagamento_invalido_e_recusado() -> None:
    conta = conta_com_fatura()
    with pytest.raises(ValorInvalido):
        conta.pagar(reais("0.00"), POL, AGORA, HOJE)
    with pytest.raises(PagamentoExcedeDivida):
        conta.pagar(reais("4000.01"), POL, AGORA, HOJE)


# ------------------------------------------------------- atraso e execução
def test_fatura_entra_em_atraso_no_dia_seguinte_ao_vencimento() -> None:
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    conta.pendencias.limpar()

    conta.virar_o_dia(POL, AGORA, fatura.vencimento)
    assert fatura.estado is EstadoFatura.FECHADA
    assert conta.pendencias.eventos == []

    dia_1 = fatura.vencimento + timedelta(days=1)
    conta.virar_o_dia(POL, AGORA, dia_1)
    assert fatura.estado is EstadoFatura.EM_ATRASO
    assert tipos(conta) == ["fatura.em_atraso"]
    assert conta.pendencias.eventos[0].dados["dias_em_atraso"] == 1
    assert comprar(conta, "1.00", hoje=dia_1).motivo_negacao is MotivoNegacao.FATURA_EM_ATRASO

    # O evento de atraso sai uma vez só.
    conta.pendencias.limpar()
    conta.virar_o_dia(POL, AGORA, fatura.vencimento + timedelta(days=2))
    assert conta.pendencias.eventos == []


def test_ate_o_sexto_dia_nada_e_executado_e_no_setimo_a_garantia_e_executada() -> None:
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    garantia = conta.garantias[0]

    conta.virar_o_dia(POL, AGORA, fatura.vencimento + timedelta(days=6))
    assert garantia.estado is EstadoGarantia.BLOQUEADA
    assert conta.divida_total == reais("4000.00")

    conta.pendencias.limpar()
    conta.virar_o_dia(POL, AGORA, fatura.vencimento + timedelta(days=7))

    # Vende só os R$ 4.000 devidos. Os R$ 1.000 que sobram continuam bloqueados.
    assert garantia.estado is EstadoGarantia.PARCIALMENTE_EXECUTADA
    assert conta.valor_de_mercado(garantia.id) == reais("1000.00")
    assert conta.divida_total == reais("0.00")
    assert fatura.estado is EstadoFatura.LIQUIDADA_POR_EXECUCAO
    assert conta.posicao(POL).limite_total == reais("850.00")
    assert conta.estado is EstadoConta.ATIVA
    assert tipos(conta) == ["garantia.executada", "limite.atualizado"]
    executada = conta.pendencias.eventos[0].dados
    assert executada["valor_executado"] == "4000.00"
    assert executada["valor_remanescente"] == "1000.00"
    assert executada["dias_em_atraso"] == 7
    assert conta.saldos.saldo(ContaContabil.CAIXA_RECEBIMENTOS) == reais("4000.00")
    assert ledger_fecha(conta.pendencias.lancamentos)


def test_execucao_comeca_pelo_ativo_mais_liquido() -> None:
    conta = nova_conta()
    acoes = bloquear(conta, TipoAtivo.ACOES, "10000.00")
    cdb = bloquear(conta, TipoAtivo.CDB, "5000.00")
    comprar(conta, "6000.00")
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    conta.pendencias.limpar()

    conta.virar_o_dia(POL, AGORA, HOJE + timedelta(days=7))

    executadas = [
        e.dados for e in conta.pendencias.eventos if e.tipo is TipoEvento.GARANTIA_EXECUTADA
    ]
    # CDB (mais líquido) vai inteiro; ações cobrem só os R$ 1.000 que faltam.
    assert [e["garantia_id"] for e in executadas] == [cdb.id, acoes.id]
    assert [e["valor_executado"] for e in executadas] == ["5000.00", "1000.00"]
    assert cdb.estado is EstadoGarantia.EXECUTADA
    assert acoes.estado is EstadoGarantia.PARCIALMENTE_EXECUTADA
    assert conta.valor_de_mercado(acoes.id) == reais("9000.00")
    assert conta.divida_total == reais("0.00")


def test_execucao_so_cobre_a_fatura_vencida_nao_as_compras_a_faturar() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.DEPOSITO, "1000.00")
    comprar(conta, "400.00")
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    comprar(conta, "100.00")  # depois do fechamento: ainda não venceu
    conta.virar_o_dia(POL, AGORA, HOJE + timedelta(days=7))
    assert conta.valor_de_mercado(garantia.id) == reais("600.00")
    assert conta.divida_faturada == reais("0.00")
    assert conta.divida_a_faturar == reais("100.00")


def test_garantia_insuficiente_e_executada_inteira_e_a_conta_fica_bloqueada() -> None:
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    garantia = conta.garantias[0]
    conta.remarcar(garantia.id, reais("3000.00"), POL, AGORA, HOJE)  # ativo despencou
    dia_7 = fatura.vencimento + timedelta(days=7)

    conta.virar_o_dia(POL, AGORA, dia_7)

    assert garantia.estado is EstadoGarantia.EXECUTADA
    assert conta.divida_total == reais("1000.00")  # ficou sem cobertura
    assert fatura.estado is EstadoFatura.EM_ATRASO
    assert conta.estado is EstadoConta.BLOQUEADA
    assert conta.posicao(POL).limite_disponivel == reais("0.00")

    # No dia seguinte não há mais o que executar: nenhum evento repetido.
    conta.pendencias.limpar()
    conta.virar_o_dia(POL, AGORA, dia_7 + timedelta(days=1))
    assert TipoEvento.GARANTIA_EXECUTADA not in [e.tipo for e in conta.pendencias.eventos]

    # Pagou o que faltava: fatura paga e conta destravada.
    conta.pagar(reais("1000.00"), POL, AGORA, dia_7 + timedelta(days=1))
    assert fatura.estado is EstadoFatura.PAGA
    assert conta.estado is EstadoConta.ATIVA


def test_garantia_parcialmente_executada_pode_ser_executada_de_novo_e_liberada() -> None:
    conta = conta_com_fatura("5000.00", "2000.00")
    garantia = conta.garantias[0]
    conta.virar_o_dia(POL, AGORA, HOJE + timedelta(days=17))
    assert garantia.estado is EstadoGarantia.PARCIALMENTE_EXECUTADA
    assert conta.valor_de_mercado(garantia.id) == reais("3000.00")

    depois = HOJE + timedelta(days=30)
    comprar(conta, "1000.00", hoje=depois)
    conta.fechar_fatura(depois, AGORA, depois)
    conta.virar_o_dia(POL, AGORA, depois + timedelta(days=7))
    assert garantia.estado is EstadoGarantia.PARCIALMENTE_EXECUTADA
    assert conta.valor_de_mercado(garantia.id) == reais("2000.00")

    conta.liberar(garantia.id, POL, AGORA, depois + timedelta(days=7))
    assert garantia.estado is EstadoGarantia.LIBERADA


def test_pagar_antes_do_setimo_dia_evita_a_execucao() -> None:
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    conta.virar_o_dia(POL, AGORA, fatura.vencimento + timedelta(days=3))
    conta.pagar(reais("4000.00"), POL, AGORA, fatura.vencimento + timedelta(days=3))
    conta.virar_o_dia(POL, AGORA, fatura.vencimento + timedelta(days=7))
    assert conta.garantias[0].estado is EstadoGarantia.BLOQUEADA
    assert fatura.estado is EstadoFatura.PAGA


def test_dias_para_execucao_vem_da_politica() -> None:
    pol = politica(dias_para_execucao=3)
    conta = conta_com_fatura()
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    conta.virar_o_dia(pol, AGORA, fatura.vencimento + timedelta(days=3))
    assert conta.garantias[0].estado is EstadoGarantia.PARCIALMENTE_EXECUTADA


def test_ledger_fecha_depois_de_uma_historia_completa() -> None:
    conta = conta_com_fatura()
    garantia = conta.garantias[0]
    conta.remarcar(garantia.id, reais("4500.00"), POL, AGORA, HOJE)
    conta.pagar(reais("500.00"), POL, AGORA, HOJE)
    conta.virar_o_dia(POL, AGORA, HOJE + timedelta(days=17))
    conta.liberar(garantia.id, POL, AGORA, HOJE + timedelta(days=17))
    assert ledger_fecha(conta.pendencias.lancamentos)
    assert conta.saldos.debitos_menos_creditos() == reais("0.00")
    assert conta.saldos.saldo_total(ContaContabil.GARANTIAS_CUSTODIADAS) == reais("0.00")


def test_fatura_em_atraso_e_avisada_uma_vez_so() -> None:
    """O evento sai na transição para em_atraso, não a cada dia de atraso."""
    pol = politica(dias_para_execucao=30)
    conta = nova_conta()
    bloquear(conta, TipoAtivo.DEPOSITO, "1000.00", pol)
    conta.autorizar_compra(reais("500.00"), "loja", pol, AGORA, HOJE)
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    conta.pendencias.limpar()
    for dia in range(1, 11):
        conta.virar_o_dia(pol, AGORA + timedelta(days=dia), HOJE + timedelta(days=dia))
    avisos = [e for e in conta.pendencias.eventos if e.tipo is TipoEvento.FATURA_EM_ATRASO]
    assert len(avisos) == 1
    assert avisos[0].dados["dias_em_atraso"] == 1
    # Os dias de atraso seguem disponíveis na conta (a API mostra em fatura_em_aberto).
    fatura = conta.fatura_em_aberto
    assert fatura is not None
    assert fatura.dias_em_atraso(HOJE + timedelta(days=10)) == 10
