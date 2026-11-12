"""Regras de crédito da garantia em custodiante externo."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.dominio.conta import Conta, EstadoConta, EstadoFatura
from app.dominio.erros import (
    GarantiaCobrindoDivida,
    SolicitacaoDivergente,
    SolicitacaoNoPrazo,
    TransicaoInvalida,
    ValorInvalido,
)
from app.dominio.eventos import TipoEvento
from app.dominio.garantia import EstadoGarantia, Garantia, OrigemGarantia, TipoSolicitacao
from app.dominio.ledger import ContaContabil, TipoLancamento
from app.dominio.politica import TipoAtivo
from tests.apoio import (
    AGORA,
    CUSTODIANTE,
    HOJE,
    PRAZO,
    bloquear,
    bloquear_externa,
    nova_conta,
    politica,
    reais,
    registrar_externa,
)

POL = politica()
E = EstadoGarantia
T = TipoEvento
ZERO = reais("0.00")


def _tipos(conta: Conta) -> list[TipoEvento]:
    tipos = [evento.tipo for evento in conta.pendencias.eventos]
    conta.pendencias.limpar()
    return tipos


def _sol(garantia: Garantia) -> str:
    assert garantia.solicitacao is not None
    return garantia.solicitacao.id


def _ledger_fecha(conta: Conta) -> None:
    assert conta.saldos.debitos_menos_creditos() == ZERO
    custodiado = conta.saldos.saldo_total(ContaContabil.GARANTIAS_CUSTODIADAS)
    assert custodiado == conta.saldos.saldo_total(ContaContabil.GARANTIAS_A_DEVOLVER)


# ======================================================================= gravame
def test_pedido_de_gravame_nasce_solicitado_e_sem_limite() -> None:
    conta = nova_conta()
    garantia = registrar_externa(conta, TipoAtivo.TESOURO_SELIC, "10000.00")

    assert garantia.estado is E.SOLICITADA
    assert garantia.origem is OrigemGarantia.EXTERNA
    assert garantia.custodiante_id == CUSTODIANTE
    assert garantia.solicitacao is not None
    assert garantia.solicitacao.tipo is TipoSolicitacao.GRAVAME
    assert garantia.solicitacao.prazo_ate == AGORA + PRAZO
    posicao = conta.posicao(POL)
    assert posicao.limite_total == ZERO
    assert posicao.valor_das_garantias == ZERO
    assert conta.pendencias.lancamentos == []
    evento = conta.pendencias.eventos[0]
    assert evento.tipo is T.GRAVAME_SOLICITADO
    assert evento.dados["custodiante_id"] == CUSTODIANTE
    assert evento.dados["solicitacao_id"] == garantia.solicitacao.id


def test_pedido_externo_exige_prazo() -> None:
    with pytest.raises(ValorInvalido):
        nova_conta().registrar_garantia(
            TipoAtivo.CDB, reais("10.00"), POL, AGORA, HOJE, custodiante_id=CUSTODIANTE
        )


def test_aceite_leva_a_pendente_sem_limite_e_e_idempotente() -> None:
    conta = nova_conta()
    garantia = registrar_externa(conta, TipoAtivo.CDB, "1000.00")
    conta.aceitar_solicitacao(garantia.id, _sol(garantia), AGORA)
    conta.aceitar_solicitacao(garantia.id, _sol(garantia), AGORA + timedelta(minutes=1))

    assert garantia.estado is E.GRAVAME_PENDENTE
    assert garantia.solicitacao is not None
    assert garantia.solicitacao.enviada_em == AGORA
    assert conta.posicao(POL).limite_total == ZERO


def test_confirmacao_bloqueia_com_o_mesmo_lancamento_da_interna() -> None:
    interna = nova_conta()
    bloquear(interna, TipoAtivo.CDB, "1000.00")
    externa = nova_conta()
    garantia = registrar_externa(externa, TipoAtivo.CDB, "1000.00")
    _tipos(externa)
    externa.aceitar_solicitacao(garantia.id, _sol(garantia), AGORA)
    externa.confirmar_gravame(garantia.id, _sol(garantia), POL, AGORA, HOJE)

    assert garantia.estado is E.BLOQUEADA
    assert garantia.solicitacao is None
    assert externa.posicao(POL) == interna.posicao(POL)
    [lancamento] = externa.pendencias.lancamentos
    [referencia] = interna.pendencias.lancamentos
    assert lancamento.tipo is TipoLancamento.BLOQUEIO_GARANTIA
    assert [(p.conta_contabil, p.sentido, p.valor) for p in lancamento.partidas] == [
        (p.conta_contabil, p.sentido, p.valor) for p in referencia.partidas
    ]
    assert _tipos(externa) == [T.GRAVAME_CONFIRMADO, T.GARANTIA_BLOQUEADA, T.LIMITE_ATUALIZADO]
    _ledger_fecha(externa)


def test_confirmacao_sem_aceite_previo_tambem_vale() -> None:
    conta = nova_conta()
    garantia = registrar_externa(conta, TipoAtivo.CDB, "1000.00")
    conta.confirmar_gravame(garantia.id, _sol(garantia), POL, AGORA, HOJE)
    assert garantia.estado is E.BLOQUEADA


def test_confirmacao_de_outra_solicitacao_nao_mexe_em_nada() -> None:
    conta = nova_conta()
    garantia = registrar_externa(conta, TipoAtivo.CDB, "1000.00")
    with pytest.raises(SolicitacaoDivergente):
        conta.confirmar_gravame(garantia.id, "sol_outra", POL, AGORA, HOJE)
    assert garantia.estado is E.SOLICITADA
    assert conta.posicao(POL).limite_total == ZERO


def test_recusa_encerra_sem_lancamento_e_sem_limite() -> None:
    conta = nova_conta()
    garantia = registrar_externa(conta, TipoAtivo.CDB, "1000.00")
    _tipos(conta)
    solicitacao_id = _sol(garantia)
    conta.recusar_solicitacao(garantia.id, solicitacao_id, "ativo_ja_onerado", POL, AGORA, HOJE)

    assert garantia.estado is E.GRAVAME_RECUSADO
    assert garantia.motivo == "ativo_ja_onerado"
    assert conta.pendencias.lancamentos == []
    assert conta.posicao(POL).limite_total == ZERO
    evento = conta.pendencias.eventos[0]
    assert evento.tipo is T.GRAVAME_RECUSADO
    assert evento.dados["motivo"] == "ativo_ja_onerado"
    # Uma confirmação que chegue depois da recusa não ressuscita a garantia.
    with pytest.raises(SolicitacaoDivergente):
        conta.confirmar_gravame(garantia.id, solicitacao_id, POL, AGORA, HOJE)


def test_expiracao_so_depois_do_prazo_e_sem_lancamento() -> None:
    conta = nova_conta()
    garantia = registrar_externa(conta, TipoAtivo.CDB, "1000.00")
    conta.aceitar_solicitacao(garantia.id, _sol(garantia), AGORA)
    _tipos(conta)
    with pytest.raises(SolicitacaoNoPrazo):
        conta.expirar_solicitacao(garantia.id, POL, AGORA + PRAZO, HOJE)

    conta.expirar_solicitacao(garantia.id, POL, AGORA + PRAZO + timedelta(seconds=1), HOJE)
    assert garantia.estado is E.GRAVAME_EXPIRADO
    assert conta.pendencias.lancamentos == []
    assert _tipos(conta) == [T.GRAVAME_EXPIRADO]
    assert conta.posicao(POL).limite_total == ZERO


def test_pendente_interno_legado_tambem_expira() -> None:
    conta = nova_conta()
    garantia = conta.registrar_garantia(TipoAtivo.CDB, reais("1000.00"), POL, AGORA, HOJE)
    conta.expirar_solicitacao(garantia.id, POL, AGORA + PRAZO, HOJE)
    assert garantia.estado is E.GRAVAME_EXPIRADO
    assert [e.tipo for e in conta.pendencias.eventos] == [T.GRAVAME_EXPIRADO]


# ===================================================================== liberação
def test_liberacao_tira_o_limite_no_pedido_e_lanca_so_na_confirmacao() -> None:
    conta = nova_conta()
    garantia = bloquear_externa(conta, TipoAtivo.CDB, "1000.00")
    _tipos(conta)
    conta.pendencias.limpar()

    conta.solicitar_liberacao(garantia.id, POL, AGORA, HOJE, prazo_ate=AGORA + PRAZO)
    assert garantia.estado is E.LIBERACAO_PENDENTE
    assert conta.posicao(POL).limite_total == ZERO
    assert conta.posicao(POL).valor_das_garantias == ZERO
    assert conta.pendencias.lancamentos == []
    assert _tipos(conta) == [T.LIBERACAO_SOLICITADA, T.LIMITE_ATUALIZADO]
    # O dinheiro continua no custodiante até ele confirmar.
    assert conta.valor_de_mercado(garantia.id) == reais("1000.00")

    conta.confirmar_liberacao(garantia.id, _sol(garantia), POL, AGORA, HOJE)
    assert garantia.estado is E.LIBERADA
    [lancamento] = conta.pendencias.lancamentos
    assert lancamento.tipo is TipoLancamento.LIBERACAO_GARANTIA
    assert conta.valor_de_mercado(garantia.id) == ZERO
    assert _tipos(conta) == [T.LIBERACAO_CONFIRMADA, T.GARANTIA_LIBERADA]
    _ledger_fecha(conta)


def test_liberacao_externa_respeita_a_divida() -> None:
    conta = nova_conta()
    garantia = bloquear_externa(conta, TipoAtivo.CDB, "1000.00")
    conta.autorizar_compra(reais("500.00"), "loja", POL, AGORA, HOJE)
    with pytest.raises(GarantiaCobrindoDivida):
        conta.solicitar_liberacao(garantia.id, POL, AGORA, HOJE, prazo_ate=AGORA + PRAZO)
    assert garantia.estado is E.BLOQUEADA


def test_compra_durante_a_liberacao_nao_usa_o_limite_que_esta_saindo() -> None:
    conta = nova_conta()
    garantia = bloquear_externa(conta, TipoAtivo.CDB, "1000.00")
    conta.solicitar_liberacao(garantia.id, POL, AGORA, HOJE, prazo_ate=AGORA + PRAZO)
    compra = conta.autorizar_compra(reais("1.00"), "loja", POL, AGORA, HOJE)
    assert not compra.aprovada


@pytest.mark.parametrize("como", ["recusa", "sem_resposta"])
def test_liberacao_que_falha_devolve_o_limite_sem_lancamento(como: str) -> None:
    conta = nova_conta()
    garantia = bloquear_externa(conta, TipoAtivo.CDB, "1000.00")
    limite = conta.posicao(POL).limite_total
    conta.solicitar_liberacao(garantia.id, POL, AGORA, HOJE, prazo_ate=AGORA + PRAZO)
    conta.pendencias.limpar()
    if como == "recusa":
        conta.recusar_solicitacao(
            garantia.id, _sol(garantia), "bloqueio_judicial", POL, AGORA, HOJE
        )
    else:
        conta.expirar_solicitacao(garantia.id, POL, AGORA + PRAZO + timedelta(seconds=1), HOJE)

    assert garantia.estado is E.BLOQUEADA
    assert garantia.solicitacao is None
    assert conta.posicao(POL).limite_total == limite
    assert conta.pendencias.lancamentos == []
    assert _tipos(conta) == [T.LIBERACAO_FALHOU, T.LIMITE_ATUALIZADO]


def test_liberacao_direta_nao_vale_para_garantia_externa() -> None:
    conta = nova_conta()
    garantia = bloquear_externa(conta, TipoAtivo.CDB, "1000.00")
    with pytest.raises(TransicaoInvalida):
        conta.liberar(garantia.id, POL, AGORA, HOJE)
    assert garantia.estado is E.BLOQUEADA


# ====================================================================== execução
def _conta_com_fatura_vencida(
    valor_garantia: str = "10000.00", compra: str = "5000.00"
) -> tuple[Conta, Garantia]:
    conta = nova_conta()
    garantia = bloquear_externa(conta, TipoAtivo.TESOURO_SELIC, valor_garantia)
    conta.autorizar_compra(reais(compra), "loja", POL, AGORA, HOJE)
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    conta.pendencias.limpar()
    return conta, garantia


def _virar(conta: Conta, dias: int) -> None:
    conta.virar_o_dia(POL, AGORA + timedelta(days=dias), HOJE + timedelta(days=dias))


def test_execucao_externa_pede_so_o_devido_e_nao_abate_a_divida_no_pedido() -> None:
    conta, garantia = _conta_com_fatura_vencida()
    _virar(conta, 7)

    assert garantia.estado is E.EXECUCAO_SOLICITADA
    assert garantia.valor_em_execucao == reais("5000.00")
    assert garantia.solicitacao is not None
    assert garantia.solicitacao.tipo is TipoSolicitacao.EXECUCAO
    assert conta.divida_faturada == reais("5000.00")
    assert not any(
        lancamento.tipo is TipoLancamento.EXECUCAO_GARANTIA
        for lancamento in conta.pendencias.lancamentos
    )
    tipos = _tipos(conta)
    assert T.EXECUCAO_SOLICITADA in tipos
    assert T.GARANTIA_EXECUTADA not in tipos
    # Em execução não gera limite, mas também não abre chamada de margem por si só.
    assert T.CHAMADA_DE_MARGEM not in tipos
    assert conta.posicao(POL).limite_total == ZERO
    assert conta.posicao(POL).deficit == ZERO


def test_a_rotina_nao_pede_de_novo_o_que_ja_esta_em_execucao() -> None:
    conta, garantia = _conta_com_fatura_vencida()
    _virar(conta, 7)
    conta.pendencias.limpar()
    _virar(conta, 8)
    _virar(conta, 9)
    assert T.EXECUCAO_SOLICITADA not in _tipos(conta)
    assert garantia.valor_em_execucao == reais("5000.00")


def test_confirmacao_abate_o_valor_recebido_e_deixa_a_sobra_bloqueada() -> None:
    conta, garantia = _conta_com_fatura_vencida()
    _virar(conta, 7)
    conta.pendencias.limpar()
    conta.confirmar_execucao(
        garantia.id, _sol(garantia), reais("4900.00"), POL, AGORA, HOJE + timedelta(days=7)
    )

    assert garantia.estado is E.PARCIALMENTE_EXECUTADA
    assert garantia.valor_em_execucao is None
    assert conta.valor_de_mercado(garantia.id) == reais("5000.00")
    assert conta.divida_faturada == reais("100.00")
    assert conta.saldos.saldo_total(ContaContabil.CAIXA_RECEBIMENTOS) == reais("4900.00")
    tipos = _tipos(conta)
    assert tipos[:2] == [T.EXECUCAO_CONFIRMADA, T.GARANTIA_EXECUTADA]
    _ledger_fecha(conta)

    # A venda rendeu menos: no dia seguinte a rotina pede só a diferença.
    _virar(conta, 8)
    assert garantia.estado is E.EXECUCAO_SOLICITADA
    assert garantia.valor_em_execucao == reais("100.00")


def test_confirmacao_que_cobre_a_fatura_liquida_e_destrava() -> None:
    conta, garantia = _conta_com_fatura_vencida()
    _virar(conta, 7)
    conta.confirmar_execucao(
        garantia.id, _sol(garantia), reais("5000.00"), POL, AGORA, HOJE + timedelta(days=7)
    )
    assert conta.divida_total == ZERO
    assert conta.faturas[0].estado is EstadoFatura.LIQUIDADA_POR_EXECUCAO
    assert conta.estado is EstadoConta.ATIVA
    _ledger_fecha(conta)


def test_venda_acima_da_divida_nao_credita_alem_dela() -> None:
    conta, garantia = _conta_com_fatura_vencida()
    _virar(conta, 7)
    conta.pendencias.limpar()
    conta.confirmar_execucao(
        garantia.id, _sol(garantia), reais("5200.00"), POL, AGORA, HOJE + timedelta(days=7)
    )
    assert conta.saldos.saldo_total(ContaContabil.CAIXA_RECEBIMENTOS) == reais("5000.00")
    assert conta.divida_total == ZERO
    evento = next(e for e in conta.pendencias.eventos if e.tipo is T.EXECUCAO_CONFIRMADA)
    assert evento.dados["valor_obtido"] == "5200.00"
    assert evento.dados["valor_aplicado_na_divida"] == "5000.00"
    assert evento.dados["valor_devolvido_ao_cliente"] == "200.00"
    _ledger_fecha(conta)


def test_valor_obtido_negativo_e_recusado() -> None:
    conta, garantia = _conta_com_fatura_vencida()
    _virar(conta, 7)
    with pytest.raises(ValorInvalido):
        conta.confirmar_execucao(garantia.id, _sol(garantia), reais("-1.00"), POL, AGORA, HOJE)
    assert garantia.estado is E.EXECUCAO_SOLICITADA


@pytest.mark.parametrize("como", ["recusa", "sem_resposta"])
def test_execucao_que_falha_volta_ao_bloqueio_e_e_pedida_de_novo(como: str) -> None:
    conta, garantia = _conta_com_fatura_vencida()
    _virar(conta, 7)
    conta.pendencias.limpar()
    if como == "recusa":
        conta.recusar_solicitacao(garantia.id, _sol(garantia), "mercado_fechado", POL, AGORA, HOJE)
    else:
        assert garantia.solicitacao is not None
        depois_do_prazo = garantia.solicitacao.prazo_ate + timedelta(seconds=1)
        conta.expirar_solicitacao(garantia.id, POL, depois_do_prazo, HOJE)

    assert garantia.estado is E.BLOQUEADA
    assert garantia.valor_em_execucao is None
    assert conta.pendencias.lancamentos == []
    assert conta.divida_faturada == reais("5000.00")
    assert T.EXECUCAO_FALHOU in _tipos(conta)

    _virar(conta, 8)
    assert garantia.estado is E.EXECUCAO_SOLICITADA


def test_interna_e_externa_juntas_nunca_passam_da_fatura() -> None:
    conta = nova_conta()
    deposito = bloquear(conta, TipoAtivo.DEPOSITO, "1000.00")
    externa = bloquear_externa(conta, TipoAtivo.TESOURO_SELIC, "10000.00")
    conta.autorizar_compra(reais("5000.00"), "loja", POL, AGORA, HOJE)
    conta.fechar_fatura(HOJE, AGORA, HOJE)
    _virar(conta, 7)

    # O depósito (interno, mais líquido) é vendido na hora; o resto vai ao custodiante.
    assert deposito.estado is E.EXECUTADA
    assert conta.divida_faturada == reais("4000.00")
    assert externa.valor_em_execucao == reais("4000.00")
    _virar(conta, 8)
    assert externa.valor_em_execucao == reais("4000.00")
    _ledger_fecha(conta)


def test_rotina_mantem_quem_espera_o_custodiante_sem_marcar_a_mercado() -> None:
    conta, garantia = _conta_com_fatura_vencida()
    _virar(conta, 7)
    with pytest.raises(TransicaoInvalida):
        conta.remarcar(garantia.id, reais("1.00"), POL, AGORA, HOJE)


def test_pedido_de_liberacao_nao_vale_para_garantia_interna() -> None:
    conta = nova_conta()
    garantia = bloquear(conta, TipoAtivo.CDB, "1000.00")
    with pytest.raises(TransicaoInvalida):
        conta.solicitar_liberacao(garantia.id, POL, AGORA, HOJE, prazo_ate=AGORA + PRAZO)
    assert garantia.estado is E.BLOQUEADA
