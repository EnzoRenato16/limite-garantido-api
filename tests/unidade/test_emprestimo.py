"""Empréstimo parcelado com garantia: cronograma, limites, régua de execução e ledger."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal

import pytest

from app.dominio.conta import Conta, EstadoConta, Fatura
from app.dominio.dinheiro import Dinheiro
from app.dominio.emprestimo import (
    EstadoEmprestimo,
    cronograma_price,
    valor_maximo_do_emprestimo,
)
from app.dominio.erros import (
    CombinacaoNaoPermitida,
    EmprestimoNegado,
    ErroDominio,
    PagamentoExcedeDivida,
    TransicaoInvalida,
    ValorInvalido,
)
from app.dominio.eventos import TipoEvento
from app.dominio.garantia import Garantia, InformacoesDaOperadora
from app.dominio.gravame import EstadoGravame
from app.dominio.ledger import ContaContabil
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto, Trilho
from tests.apoio import AGORA, CATALOGO, HOJE, nova_conta, politica, prazo_da_baixa, reais

POL = politica(ltv={t: Decimal("0.50") for t in TipoAtivo if t is not TipoAtivo.COE})
CAT = CATALOGO
T = TipoEvento
TAXA = Decimal("0.0199")
PRIMEIRO = HOJE + timedelta(days=30)


def _tipos(conta: Conta) -> list[TipoEvento]:
    tipos = [evento.tipo for evento in conta.pendencias.eventos]
    conta.pendencias.limpar()
    return tipos


def _ativo(
    conta: Conta, tipo: TipoAtivo = TipoAtivo.CDB, valor: str = "20000.00", **extras: object
) -> Garantia:
    return conta.custodiar_na_casa(
        tipo=tipo,
        valor=reais(valor),
        produto=Produto.EMPRESTIMO,
        politica=POL,
        catalogo=CAT,
        agora=AGORA,
        hoje=HOJE,
        protocolo="SIM-b3_balcao-1",
        **extras,  # type: ignore[arg-type]
    )


def _previdencia(
    conta: Conta, fim: date = date(2040, 1, 1), disponivel: str = "20000.00"
) -> Garantia:
    garantia = conta.receber_de_fora(
        trilho=Trilho.RES_CONJUNTA_12,
        tipo=TipoAtivo.PREVIDENCIA,
        valor=reais(disponivel),
        produto=Produto.EMPRESTIMO,
        contraparte_id="entidade_operadora_simulada",
        prazo_ate=AGORA + timedelta(days=7),
        politica=POL,
        catalogo=CAT,
        agora=AGORA,
        hoje=HOJE,
        plano="vgbl",
        fim_da_acumulacao=fim,
    )
    conta.registrar_informacoes_da_operadora(
        garantia.id,
        InformacoesDaOperadora(reais(disponivel), reais("1000.00"), AGORA),
        AGORA,
    )
    return garantia


def _contratar(
    conta: Conta,
    garantias: list[Garantia],
    valor: str = "5000.00",
    parcelas: int = 12,
    protocolos: dict[str, str] | None = None,
    primeiro: date = PRIMEIRO,
):  # type: ignore[no-untyped-def]
    return conta.contratar_emprestimo(
        valor=reais(valor),
        parcelas=parcelas,
        garantia_ids=[g.id for g in garantias],
        politica=POL,
        catalogo=CAT,
        primeiro_vencimento=primeiro,
        agora=AGORA,
        hoje=HOJE,
        protocolos_da_previdencia=protocolos or {},
    )


def _agora(dia: date) -> datetime:
    return datetime.combine(dia, time(12), tzinfo=UTC)


def _virar(conta: Conta, dia: date) -> list[TipoEvento]:
    conta.pendencias.limpar()
    conta.virar_o_dia(POL, CAT, _agora(dia), dia, prazo_da_baixa(dia))
    return _tipos(conta)


def _fecha(conta: Conta) -> None:
    assert conta.saldos.debitos_menos_creditos().nulo


# ------------------------------------------------------------------ cronograma
def test_cronograma_price_de_10_mil_em_12_parcelas() -> None:
    parcelas = cronograma_price(reais("10000.00"), TAXA, 12, date(2026, 4, 1))
    assert len(parcelas) == 12
    assert sum(p.amortizacao.centavos for p in parcelas) == 1_000_000
    # Parcela = P x i / (1 - (1+i)^-n), para cima ao centavo (conta independente).
    exata = Decimal(1_000_000) * TAXA / (1 - (1 + TAXA) ** -12)
    prestacao = int(exata.to_integral_value(rounding=ROUND_CEILING))
    assert all(p.valor.centavos == prestacao for p in parcelas[:-1])
    # Juros do mês para baixo: 1,99% de R$ 10.000,00 = R$ 199,00.
    assert parcelas[0].juros == reais("199.00")
    saldo = 1_000_000
    for p in parcelas:
        assert p.juros.centavos == (saldo * 199) // 10_000  # floor
        assert p.valor == p.juros + p.amortizacao
        saldo -= p.amortizacao.centavos
    assert saldo == 0
    assert parcelas[-1].valor.centavos <= prestacao
    assert [p.vencimento for p in parcelas][:3] == [
        date(2026, 4, 1),
        date(2026, 5, 1),
        date(2026, 6, 1),
    ]


def test_vencimento_no_fim_do_mes_nao_pula_mes() -> None:
    parcelas = cronograma_price(reais("1000.00"), TAXA, 4, date(2026, 1, 31))
    assert [p.vencimento for p in parcelas] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]


def test_cronograma_sem_juros_e_entradas_invalidas() -> None:
    parcelas = cronograma_price(reais("100.00"), Decimal(0), 3, PRIMEIRO)
    assert sum(p.amortizacao.centavos for p in parcelas) == 10_000
    assert all(p.juros.nulo for p in parcelas)
    with pytest.raises(ValorInvalido):
        cronograma_price(reais("0.00"), TAXA, 3, PRIMEIRO)
    with pytest.raises(ValorInvalido):
        cronograma_price(reais("100.00"), TAXA, 0, PRIMEIRO)
    with pytest.raises(ValorInvalido):
        cronograma_price(reais("0.01"), TAXA, 12, PRIMEIRO)


# ------------------------------------------------------------------- contratação
@pytest.mark.parametrize("dias", [-1, 0, 61, 5 * 365])
def test_primeiro_vencimento_fora_de_amanha_a_60_dias_e_recusado(dias: int) -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    with pytest.raises(ErroDominio) as erro:
        _contratar(conta, [cdb], primeiro=HOJE + timedelta(days=dias))
    assert erro.value.codigo == "primeiro_vencimento_fora_do_prazo"


@pytest.mark.parametrize("dias", [1, 60])
def test_primeiro_vencimento_nas_bordas_da_janela_e_aceito(dias: int) -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    emprestimo = _contratar(conta, [cdb], primeiro=HOJE + timedelta(days=dias))
    assert emprestimo.parcelas[0].vencimento == HOJE + timedelta(days=dias)


def test_contratar_desembolsa_e_amarra_as_garantias() -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    conta.pendencias.limpar()
    emprestimo = _contratar(conta, [cdb])
    assert emprestimo.estado is EstadoEmprestimo.ATIVO
    assert emprestimo.principal == reais("5000.00")
    assert cdb.emprestimo_id == emprestimo.id
    assert emprestimo.dias_minimos_execucao == 30
    assert conta.saldos.saldo_total(ContaContabil.EMPRESTIMOS_A_RECEBER) == reais("5000.00")
    assert _tipos(conta) == [T.EMPRESTIMO_CONTRATADO]
    _fecha(conta)
    # A garantia amarrada não é liberada nem conta de novo para outro empréstimo.
    assert not cdb.disponivel_para_emprestimo
    assert conta.valor_maximo_do_emprestimo(POL, CAT, HOJE) == reais("0.00")


def test_valor_maximo_usa_o_ltv_efetivo_de_cada_garantia() -> None:
    conta = nova_conta()
    cdb = _ativo(conta, valor="20000.00")
    fundo = _ativo(conta, TipoAtivo.FUNDO_DI, "10000.00")
    assert valor_maximo_do_emprestimo([cdb, fundo], POL, CAT, HOJE) == reais("15000.00")
    assert conta.valor_maximo_do_emprestimo(POL, CAT, HOJE) == reais("15000.00")
    with pytest.raises(ValorInvalido):
        _contratar(conta, [cdb, fundo], valor="15000.01")
    assert conta.emprestimos == []
    assert _contratar(conta, [cdb, fundo], valor="15000.00").estado is EstadoEmprestimo.ATIVO
    assert conta.valor_maximo_do_emprestimo(POL, CAT, HOJE) == reais("0.00")


def test_garantia_solicitada_ou_do_cartao_nao_entra() -> None:
    conta = nova_conta()
    pendente = conta.custodiar_na_casa(
        tipo=TipoAtivo.CDB,
        valor=reais("20000.00"),
        produto=Produto.EMPRESTIMO,
        politica=POL,
        catalogo=CAT,
        agora=AGORA,
        hoje=HOJE,
    )
    assert pendente.estado is EstadoGravame.SOLICITADO
    with pytest.raises(ValorInvalido):
        _contratar(conta, [pendente])
    cartao = conta.custodiar_na_casa(
        tipo=TipoAtivo.CDB,
        valor=reais("20000.00"),
        produto=Produto.CARTAO,
        politica=POL,
        catalogo=CAT,
        agora=AGORA,
        hoje=HOJE,
        liquidez_dias=0,
        protocolo="SIM-b3_balcao-2",
    )
    with pytest.raises(ValorInvalido):
        _contratar(conta, [cartao])
    assert conta.emprestimos == []
    assert pendente.emprestimo_id is None
    assert cartao.emprestimo_id is None


def test_previdencia_nao_passa_do_fim_da_acumulacao() -> None:
    conta = nova_conta()
    # A 12ª parcela vence em PRIMEIRO + 11 meses.
    ultima = cronograma_price(reais("5000.00"), TAXA, 12, PRIMEIRO)[-1].vencimento
    prev = _previdencia(conta, fim=ultima - timedelta(days=1))
    conta.pendencias.limpar()
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        _contratar(conta, [prev], protocolos={prev.id: "SIM-rc12-1"})
    assert erro.value.codigo == "prazo_alem_da_acumulacao"
    # Nada mudou: o gravame segue solicitado e nada foi emitido.
    assert prev.estado is EstadoGravame.SOLICITADO
    assert conta.pendencias.eventos == []
    assert conta.emprestimos == []
    # No limite exato, vale.
    prev.fim_da_acumulacao = ultima
    assert _contratar(conta, [prev], protocolos={prev.id: "SIM-rc12-1"}).garantia_ids == (prev.id,)


def test_lci_nao_pode_vencer_antes_da_ultima_parcela() -> None:
    conta = nova_conta()
    lci = _ativo(conta, TipoAtivo.LCI, vencimento=HOJE + timedelta(days=200))
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        _contratar(conta, [lci])
    assert erro.value.codigo == "vencimento_nao_casado"
    assert conta.emprestimos == []
    assert lci.emprestimo_id is None
    # Com menos parcelas, a última cabe antes do vencimento do título.
    assert _contratar(conta, [lci], parcelas=6).estado is EstadoEmprestimo.ATIVO


def test_formalizacao_da_previdencia_registra_o_gravame_no_mesmo_passo() -> None:
    conta = nova_conta()
    prev = _previdencia(conta)
    with pytest.raises(ValorInvalido):  # sem protocolo
        _contratar(conta, [prev])
    assert prev.estado is EstadoGravame.SOLICITADO
    conta.pendencias.limpar()
    emprestimo = _contratar(conta, [prev], valor="9500.00", protocolos={prev.id: "SIM-rc12-1"})
    assert prev.estado is EstadoGravame.REGISTRADO
    assert prev.gravame.protocolo == "SIM-rc12-1"
    # LTV 0,5 sobre o valor líquido (20.000,00 - 1.000,00).
    assert emprestimo.principal == reais("9500.00")
    assert emprestimo.dias_minimos_execucao == 91
    tipos = _tipos(conta)
    assert tipos.index(T.GRAVAME_REGISTRADO) < tipos.index(T.EMPRESTIMO_CONTRATADO)
    _fecha(conta)


def test_previdencia_sem_informacoes_nao_formaliza() -> None:
    conta = nova_conta()
    prev = conta.receber_de_fora(
        trilho=Trilho.RES_CONJUNTA_12,
        tipo=TipoAtivo.PREVIDENCIA,
        valor=reais("20000.00"),
        produto=Produto.EMPRESTIMO,
        contraparte_id="entidade_operadora_simulada",
        prazo_ate=AGORA + timedelta(days=7),
        politica=POL,
        catalogo=CAT,
        agora=AGORA,
        hoje=HOJE,
        plano="vgbl",
        fim_da_acumulacao=date(2040, 1, 1),
    )
    with pytest.raises(TransicaoInvalida):
        _contratar(conta, [prev], protocolos={prev.id: "SIM-rc12-1"})


# ------------------------------------------------------------------- pagamentos
def test_pagar_parcela_lanca_amortizacao_e_juros() -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    emprestimo = _contratar(conta, [cdb], valor="10000.00")
    primeira = emprestimo.parcelas[0]
    conta.pendencias.limpar()
    dia = primeira.vencimento
    conta.pagar_parcela(emprestimo.id, primeira.valor, _agora(dia), dia, prazo_da_baixa(dia))
    assert primeira.paga_em is not None
    assert emprestimo.estado is EstadoEmprestimo.ATIVO
    assert conta.saldos.saldo_total(ContaContabil.RECEITA_DE_JUROS) == primeira.juros
    assert conta.saldos.saldo_total(ContaContabil.EMPRESTIMOS_A_RECEBER) == (
        reais("10000.00") - primeira.amortizacao
    )
    assert _tipos(conta) == [T.EMPRESTIMO_PARCELA_PAGA]
    _fecha(conta)
    with pytest.raises(ValorInvalido):
        conta.pagar_parcela(emprestimo.id, reais("0.00"), _agora(dia), dia, prazo_da_baixa(dia))


def test_saldo_devedor_nao_cobra_juros_futuros_e_pagamento_nao_passa_dele() -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    emprestimo = _contratar(conta, [cdb], valor="10000.00")
    # Antes do 1º vencimento, só a amortização (o principal).
    assert emprestimo.saldo_devedor(HOJE) == reais("10000.00")
    dia = PRIMEIRO
    assert emprestimo.saldo_devedor(dia) == (reais("10000.00") + emprestimo.parcelas[0].juros)
    with pytest.raises(PagamentoExcedeDivida):
        conta.pagar_parcela(
            emprestimo.id, reais("10000.01"), _agora(HOJE), HOJE, prazo_da_baixa(HOJE)
        )


def test_quitar_pede_a_baixa_de_todas_as_garantias_e_zera_o_valor_disponivel() -> None:
    conta = nova_conta()
    cdb = _ativo(conta, valor="20000.00")
    fundo = _ativo(conta, TipoAtivo.FUNDO_DI, "10000.00")
    emprestimo = _contratar(conta, [cdb, fundo], valor="8000.00")
    conta.pendencias.limpar()
    saldo = emprestimo.saldo_devedor(HOJE)
    conta.pagar_parcela(emprestimo.id, saldo, AGORA, HOJE, prazo_da_baixa(HOJE))
    assert emprestimo.estado is EstadoEmprestimo.QUITADO
    assert emprestimo.saldo_devedor(HOJE).nulo
    for garantia in (cdb, fundo):
        assert garantia.gravame.baixa_pendente
        assert garantia.gravame.baixa_prazo == prazo_da_baixa(HOJE)
        assert conta.valor_de_mercado(garantia.id).nulo
    assert conta.valor_maximo_do_emprestimo(POL, CAT, HOJE) == reais("0.00")
    tipos = _tipos(conta)
    assert tipos.count(T.GRAVAME_BAIXA_PEDIDA) == 2
    assert T.EMPRESTIMO_QUITADO in tipos
    _fecha(conta)
    with pytest.raises(TransicaoInvalida):
        conta.pagar_parcela(emprestimo.id, reais("1.00"), AGORA, HOJE, prazo_da_baixa(HOJE))


def test_pagar_a_parcela_atrasada_tira_do_atraso_e_zera_os_avisos() -> None:
    conta = nova_conta()
    prev = _previdencia(conta)
    emprestimo = _contratar(conta, [prev], valor="5000.00", protocolos={prev.id: "SIM-rc12-1"})
    primeira = emprestimo.parcelas[0]
    _virar(conta, primeira.vencimento + timedelta(days=50))
    assert emprestimo.estado is EstadoEmprestimo.EM_ATRASO
    assert emprestimo.aviso_45_em is not None
    dia = primeira.vencimento + timedelta(days=50)
    devido = Dinheiro.somar(p.devido(dia) for p in emprestimo.parcelas if p.vencimento <= dia)
    conta.pagar_parcela(emprestimo.id, devido, _agora(dia), dia, prazo_da_baixa(dia))
    assert emprestimo.estado is EstadoEmprestimo.ATIVO
    assert emprestimo.aviso_45_em is None
    assert emprestimo.aviso_10_em is None
    _fecha(conta)


# ------------------------------------------------------------- régua da previdência
def _emprestimo_de_previdencia(conta: Conta):  # type: ignore[no-untyped-def]
    prev = _previdencia(conta)
    emprestimo = _contratar(conta, [prev], valor="5000.00", protocolos={prev.id: "SIM-rc12-1"})
    return prev, emprestimo


def test_linha_do_tempo_da_previdencia() -> None:
    conta = nova_conta()
    prev, emprestimo = _emprestimo_de_previdencia(conta)
    vencimento = emprestimo.parcelas[0].vencimento
    por_dia: dict[int, list[TipoEvento]] = {}
    for dias in range(0, 92):
        por_dia[dias] = _virar(conta, vencimento + timedelta(days=dias))
        if dias == 90:
            # Dia 90: ainda sem execução.
            assert prev.gravame.vigente
            assert emprestimo.estado is EstadoEmprestimo.EM_ATRASO
    de_previdencia = {
        dias: [t for t in tipos if str(t).startswith("previdencia.")]
        for dias, tipos in por_dia.items()
    }
    assert all(tipos == [] for dias, tipos in de_previdencia.items() if dias not in (45, 81))
    assert de_previdencia[44] == []
    assert de_previdencia[45] == [T.PREVIDENCIA_AVISO_45_DIAS]
    assert de_previdencia[81] == [T.PREVIDENCIA_AVISO_10_DIAS]
    assert por_dia[1][:1] == [T.EMPRESTIMO_EM_ATRASO]
    assert T.GARANTIA_EXECUTADA not in [t for dias in range(91) for t in por_dia[dias]]
    # Dia 91: execução.
    assert T.GARANTIA_EXECUTADA in por_dia[91]
    assert emprestimo.estado is EstadoEmprestimo.LIQUIDADO_POR_EXECUCAO
    assert prev.estado is EstadoGravame.EXECUTADO
    assert prev.gravame.baixa_pedida_em is not None
    dia = vencimento + timedelta(days=91)
    assert prev.gravame.baixa_prazo == prazo_da_baixa(dia)  # 2 dias úteis, calculados fora
    _fecha(conta)


def test_execucao_da_previdencia_abate_o_saldo_e_devolve_o_excedente() -> None:
    conta = nova_conta()
    _, emprestimo = _emprestimo_de_previdencia(conta)
    vencimento = emprestimo.parcelas[0].vencimento
    for dias in range(0, 91):
        _virar(conta, vencimento + timedelta(days=dias))
    dia = vencimento + timedelta(days=91)
    saldo_antes = emprestimo.saldo_devedor(dia)
    conta.pendencias.limpar()
    conta.virar_o_dia(POL, CAT, _agora(dia), dia, prazo_da_baixa(dia))
    evento = next(e for e in conta.pendencias.eventos if e.tipo is T.GARANTIA_EXECUTADA)
    demonstrativo = evento.dados["demonstrativo"]
    assert isinstance(demonstrativo, dict)
    assert demonstrativo["divida_abatida"] == saldo_antes.texto()
    liquido = reais(demonstrativo["liquido"])
    assert reais(demonstrativo["excedente_devolvido"]) == liquido - saldo_antes
    assert emprestimo.saldo_devedor(dia).nulo
    assert conta.saldos.saldo_total(ContaContabil.EMPRESTIMOS_A_RECEBER).nulo
    assert conta.saldos.saldo_total(ContaContabil.EXCEDENTE_A_DEVOLVER).nulo
    _fecha(conta)


def test_saltar_91_dias_emite_os_dois_avisos_e_executa_so_10_dias_depois() -> None:
    conta = nova_conta()
    prev, emprestimo = _emprestimo_de_previdencia(conta)
    vencimento = emprestimo.parcelas[0].vencimento
    dia = vencimento + timedelta(days=91)
    tipos = _virar(conta, dia)
    assert T.PREVIDENCIA_AVISO_45_DIAS in tipos
    assert T.PREVIDENCIA_AVISO_10_DIAS in tipos
    assert T.GARANTIA_EXECUTADA not in tipos
    assert emprestimo.aviso_10_em == dia
    assert prev.gravame.vigente
    assert T.GARANTIA_EXECUTADA not in _virar(conta, dia + timedelta(days=9))
    assert prev.gravame.vigente
    assert T.GARANTIA_EXECUTADA in _virar(conta, dia + timedelta(days=10))
    assert emprestimo.estado is EstadoEmprestimo.LIQUIDADO_POR_EXECUCAO
    _fecha(conta)


def test_virar_o_dia_de_novo_no_mesmo_dia_nao_repete_nada() -> None:
    conta = nova_conta()
    _, emprestimo = _emprestimo_de_previdencia(conta)
    dia = emprestimo.parcelas[0].vencimento + timedelta(days=50)
    assert T.PREVIDENCIA_AVISO_45_DIAS in _virar(conta, dia)
    assert _virar(conta, dia) == []


# --------------------------------------------------------------- demais ativos
def test_emprestimo_garantido_por_acoes_executa_no_30o_dia() -> None:
    conta = nova_conta()
    acoes = _ativo(conta, TipoAtivo.ACOES, "20000.00")
    emprestimo = _contratar(conta, [acoes], valor="5000.00")
    vencimento = emprestimo.parcelas[0].vencimento
    assert emprestimo.dias_minimos_execucao == 30
    assert T.GARANTIA_EXECUTADA not in _virar(conta, vencimento + timedelta(days=29))
    assert acoes.gravame.vigente
    tipos = _virar(conta, vencimento + timedelta(days=30))
    assert T.GARANTIA_EXECUTADA in tipos
    assert not [t for t in tipos if str(t).startswith("previdencia.")]
    assert emprestimo.estado is EstadoEmprestimo.LIQUIDADO_POR_EXECUCAO
    assert acoes.gravame.baixa_pendente
    _fecha(conta)


def test_execucao_vende_so_o_necessario_e_libera_a_garantia_que_sobrou() -> None:
    conta = nova_conta()
    cdb = _ativo(conta, TipoAtivo.CDB, "10000.00")  # primeiro na ordem de execução
    fundo = _ativo(conta, TipoAtivo.FUNDO_DI, "10000.00")
    emprestimo = _contratar(conta, [cdb, fundo], valor="5000.00")
    vencimento = emprestimo.parcelas[0].vencimento
    _virar(conta, vencimento + timedelta(days=29))
    _virar(conta, vencimento + timedelta(days=30))
    assert cdb.estado is EstadoGravame.EXECUTADO
    # O fundo não foi vendido: só liberado, com a baixa pedida.
    assert fundo.estado is EstadoGravame.REGISTRADO
    assert fundo.gravame.baixa_pendente
    assert conta.valor_de_mercado(fundo.id).nulo
    _fecha(conta)


def test_garantia_insuficiente_deixa_saldo_e_nao_devolve_excedente() -> None:
    conta = nova_conta()
    cdb = _ativo(conta, TipoAtivo.CDB, "10000.00")
    emprestimo = _contratar(conta, [cdb], valor="5000.00")
    cdb_valor = conta.valor_de_mercado(cdb.id)
    conta.remarcar(cdb.id, reais("1000.00"), POL, CAT, AGORA, HOJE)  # queda forte
    assert conta.valor_de_mercado(cdb.id) < cdb_valor
    vencimento = emprestimo.parcelas[0].vencimento
    _virar(conta, vencimento + timedelta(days=30))
    assert cdb.estado is EstadoGravame.EXECUTADO
    assert emprestimo.estado is EstadoEmprestimo.EM_ATRASO
    saldo = emprestimo.saldo_devedor(vencimento + timedelta(days=30))
    assert saldo.positivo
    assert emprestimo.saldo_devedor(vencimento + timedelta(days=30)) < reais("5300.00")
    _fecha(conta)
    # Sem garantia vigente, a rotina não tenta de novo nem quebra.
    _virar(conta, vencimento + timedelta(days=31))
    _fecha(conta)


# ------------------------------------------------ portões da conta na contratação
def _contratar_com(conta: Conta, garantia: Garantia, pol=POL, carteira=None):  # type: ignore[no-untyped-def]
    return conta.contratar_emprestimo(
        valor=reais("1000.00"),
        parcelas=6,
        garantia_ids=[garantia.id],
        politica=pol,
        catalogo=CAT,
        primeiro_vencimento=PRIMEIRO,
        agora=AGORA,
        hoje=HOJE,
        protocolos_da_previdencia={},
        carteira_do_parceiro=carteira,
    )


def _recusado(conta: Conta, garantia: Garantia, codigo: str, **kw) -> None:  # type: ignore[no-untyped-def]
    conta.pendencias.limpar()
    existentes = len(conta.emprestimos)
    with pytest.raises(ErroDominio) as erro:
        _contratar_com(conta, garantia, **kw)
    assert erro.value.codigo == codigo
    assert len(conta.emprestimos) == existentes
    assert garantia.emprestimo_id is None
    assert conta.pendencias.eventos == []
    assert conta.pendencias.lancamentos == []


def test_conta_bloqueada_nao_contrata_emprestimo() -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    conta.estado = EstadoConta.BLOQUEADA
    _recusado(conta, cdb, "emprestimo_conta_bloqueada")


def test_fatura_do_cartao_em_atraso_barra_o_emprestimo() -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    conta.faturas.append(
        Fatura(
            id="fat_x",
            conta_id=conta.id,
            valor_fechado=reais("10.00"),
            fechada_em=AGORA - timedelta(days=10),
            vencimento=HOJE - timedelta(days=1),
        )
    )
    _recusado(conta, cdb, "emprestimo_fatura_em_atraso")


def test_emprestimo_em_atraso_barra_outro_emprestimo() -> None:
    conta = nova_conta()
    cdb = _ativo(conta, valor="20000.00")
    fundo = _ativo(conta, TipoAtivo.FUNDO_DI, "20000.00")
    primeiro = _contratar_com(conta, cdb)
    primeiro.estado = EstadoEmprestimo.EM_ATRASO
    _recusado(conta, fundo, "emprestimo_em_atraso_na_conta")
    conta.pendencias.limpar()
    primeiro.estado = EstadoEmprestimo.ATIVO
    assert _contratar_com(conta, fundo).estado is EstadoEmprestimo.ATIVO


def test_teto_do_parceiro_vale_para_o_emprestimo_e_falha_fechado() -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    pol = politica(
        ltv={t: Decimal("0.50") for t in TipoAtivo if t is not TipoAtivo.COE},
        teto_parceiro="5000.00",
    )
    with pytest.raises(EmprestimoNegado) as erro:  # sem carteira: recusa, com erro de domínio
        _contratar_com(conta, cdb, pol=pol)
    assert erro.value.codigo == "emprestimo_carteira_nao_informada"
    assert conta.emprestimos == []
    _recusado(
        conta, cdb, "emprestimo_teto_do_parceiro_excedido", pol=pol, carteira=reais("4000.01")
    )
    assert _contratar_com(conta, cdb, pol=pol, carteira=reais("4000.00")).principal == reais(
        "1000.00"
    )


# ------------------------------------------------- juros pro rata na antecipação
def test_antecipar_parcela_cobra_juros_dos_dias_decorridos_para_cima() -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    emprestimo = _contratar(conta, [cdb], valor="10000.00")
    primeira = emprestimo.parcelas[0]
    assert primeira.inicio == HOJE
    dia = HOJE + timedelta(days=10)
    periodo = (primeira.vencimento - HOJE).days  # 30
    juros = -(-primeira.juros.centavos * 10 // periodo)  # para cima
    assert emprestimo.saldo_devedor(dia) == reais("10000.00") + Dinheiro(juros)
    conta.pendencias.limpar()
    with pytest.raises(PagamentoExcedeDivida):
        conta.pagar_parcela(
            emprestimo.id,
            reais("10000.00") + Dinheiro(juros + 1),
            _agora(dia),
            dia,
            prazo_da_baixa(dia),
        )
    conta.pagar_parcela(
        emprestimo.id, emprestimo.saldo_devedor(dia), _agora(dia), dia, prazo_da_baixa(dia)
    )
    assert emprestimo.estado is EstadoEmprestimo.QUITADO
    assert conta.saldos.saldo_total(ContaContabil.RECEITA_DE_JUROS) == Dinheiro(juros)
    _fecha(conta)


def test_juros_de_antecipacao_nunca_passam_dos_juros_do_periodo() -> None:
    conta = nova_conta()
    cdb = _ativo(conta)
    emprestimo = _contratar(conta, [cdb], valor="10000.00")
    primeira = emprestimo.parcelas[0]
    vespera = primeira.vencimento - timedelta(days=1)
    assert primeira.juros_devido(vespera) <= primeira.juros
    # A partir do 2º período, conta desde o vencimento anterior.
    segunda = emprestimo.parcelas[1]
    assert segunda.inicio == primeira.vencimento
    assert segunda.juros_devido(primeira.vencimento).nulo
