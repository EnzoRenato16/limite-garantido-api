"""Entrada de garantia pelos trilhos de fora: nada gera limite antes do registro."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.dominio.conta import Conta
from app.dominio.erros import CombinacaoNaoPermitida, TransicaoInvalida, ValorInvalido
from app.dominio.eventos import TipoEvento
from app.dominio.garantia import InformacoesDaOperadora, TipoSolicitacao
from app.dominio.gravame import EstadoGravame
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto, Trilho
from tests.apoio import (
    AGORA,
    CATALOGO,
    CONTRAPARTE,
    HOJE,
    PRAZO,
    nova_conta,
    politica,
    reais,
    receber,
)

POL = politica(ltv={t: Decimal("0.50") for t in TipoAtivo if t is not TipoAtivo.COE})
CAT = CATALOGO
E = EstadoGravame
T = TipoEvento
ZERO = reais("0.00")


def _tipos(conta: Conta) -> list[TipoEvento]:
    tipos = [evento.tipo for evento in conta.pendencias.eventos]
    conta.pendencias.limpar()
    return tipos


def _concluir(conta: Conta, garantia_id: str, catalogo=CAT) -> None:  # type: ignore[no-untyped-def]
    conta.concluir_entrada(garantia_id, "SIM-b3_balcao-9", POL, catalogo, AGORA, HOJE)


def test_transferencia_nasce_solicitada_sem_limite_e_conclui_com_registro() -> None:
    conta = nova_conta()
    garantia = receber(conta, Trilho.TRANSFERENCIA_DE_CUSTODIA, TipoAtivo.CDB, "1000.00", POL)
    assert garantia.estado is E.SOLICITADO
    assert garantia.contraparte_id == CONTRAPARTE
    assert garantia.solicitacao is not None
    assert garantia.solicitacao.tipo is TipoSolicitacao.ENTRADA
    assert garantia.solicitacao.prazo_ate == AGORA + PRAZO
    assert not garantia.gera_limite
    assert conta.posicao(POL, CAT, HOJE).limite_total == ZERO
    assert conta.pendencias.lancamentos == []
    evento = conta.pendencias.eventos[0]
    assert evento.tipo is T.TRANSFERENCIA_SOLICITADA
    assert evento.dados["solicitacao_id"] == garantia.solicitacao.id
    conta.pendencias.limpar()

    _concluir(conta, garantia.id)
    assert garantia.estado is E.REGISTRADO
    assert garantia.solicitacao is None
    assert conta.posicao(POL, CAT, HOJE).limite_total == reais("500.00")
    assert _tipos(conta) == [T.TRANSFERENCIA_CONCLUIDA, T.GRAVAME_REGISTRADO, T.LIMITE_ATUALIZADO]


def test_portabilidade_de_fundo_di_ate_d1() -> None:
    conta = nova_conta()
    garantia = receber(conta, Trilho.PORTABILIDADE_CVM_210, TipoAtivo.FUNDO_DI, "1000.00", POL)
    assert _tipos(conta) == [T.PORTABILIDADE_SOLICITADA]
    _concluir(conta, garantia.id)
    assert _tipos(conta)[0] is T.PORTABILIDADE_CONCLUIDA


def test_ativo_ja_em_garantia_nao_porta() -> None:
    conta = nova_conta()
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        conta.receber_de_fora(
            trilho=Trilho.PORTABILIDADE_CVM_210,
            tipo=TipoAtivo.FUNDO_DI,
            valor=reais("1000.00"),
            produto=Produto.CARTAO,
            contraparte_id="corretora_de_origem_simulada",
            prazo_ate=AGORA + PRAZO,
            politica=POL,
            catalogo=CAT,
            agora=AGORA,
            hoje=HOJE,
            liquidez_dias=1,
            ja_em_garantia=True,
        )
    assert erro.value.codigo == "ativo_ja_em_garantia"


@pytest.mark.parametrize(
    ("trilho", "tipo", "produto", "codigo"),
    [
        (
            Trilho.RES_CONJUNTA_12,
            TipoAtivo.PREVIDENCIA,
            Produto.CARTAO,
            "previdencia_so_no_emprestimo",
        ),
        (Trilho.PORTABILIDADE_CVM_210, TipoAtivo.ACOES, Produto.CARTAO, "ativo_so_no_emprestimo"),
        (Trilho.TRANSFERENCIA_DE_CUSTODIA, TipoAtivo.LCI, Produto.CARTAO, "ativo_so_no_emprestimo"),
        (
            Trilho.TD_GARANTIA,
            TipoAtivo.TESOURO_SELIC,
            Produto.CARTAO,
            "tesouro_no_cartao_exige_agente_do_td",
        ),
        (Trilho.TD_GARANTIA, TipoAtivo.CDB, Produto.CARTAO, "trilho_nao_atende_o_ativo"),
    ],
)
def test_catalogo_barra_combinacao_de_fora(
    trilho: Trilho, tipo: TipoAtivo, produto: Produto, codigo: str
) -> None:
    conta = nova_conta()
    with pytest.raises(CombinacaoNaoPermitida) as erro:
        receber(conta, trilho, tipo, "1000.00", POL, produto=produto, plano="pgbl")
    assert erro.value.codigo == codigo
    assert conta.garantias == []


def test_receber_de_fora_exige_trilho_de_fora_e_prazo_no_futuro() -> None:
    conta = nova_conta()
    with pytest.raises(ValorInvalido):
        receber(conta, Trilho.CASA, TipoAtivo.CDB, "1000.00", POL)
    with pytest.raises(ValorInvalido):
        conta.receber_de_fora(
            trilho=Trilho.TRANSFERENCIA_DE_CUSTODIA,
            tipo=TipoAtivo.CDB,
            valor=reais("1.00"),
            produto=Produto.CARTAO,
            contraparte_id=CONTRAPARTE,
            prazo_ate=AGORA,
            politica=POL,
            catalogo=CAT,
            agora=AGORA,
            hoje=HOJE,
            liquidez_dias=0,
        )


def test_td_garantia_recusa_e_os_demais_cancelam() -> None:
    catalogo = CAT.com_agente_do_td(True)
    conta = nova_conta()
    tesouro = receber(
        conta, Trilho.TD_GARANTIA, TipoAtivo.TESOURO_SELIC, "1000.00", POL, catalogo=catalogo
    )
    cdb = receber(conta, Trilho.TRANSFERENCIA_DE_CUSTODIA, TipoAtivo.CDB, "1000.00", POL)
    conta.pendencias.limpar()

    conta.recusar_entrada(tesouro.id, "agente_sem_servico", catalogo, AGORA)
    conta.recusar_entrada(cdb.id, "banco_de_origem_recusou", catalogo, AGORA)

    assert tesouro.estado is E.RECUSADO
    assert cdb.estado is E.CANCELADO
    assert cdb.gravame.motivo == "banco_de_origem_recusou"
    assert tesouro.solicitacao is None
    assert _tipos(conta) == [T.GRAVAME_RECUSADO, T.GRAVAME_CANCELADO]
    assert conta.pendencias.lancamentos == []
    # Uma conclusão que chegue depois da recusa não ressuscita a garantia.
    with pytest.raises(TransicaoInvalida):
        _concluir(conta, cdb.id)
    assert conta.posicao(POL, catalogo, HOJE).limite_total == ZERO


def test_previdencia_nao_recusa_espera_informacoes_e_so_entra_no_emprestimo() -> None:
    conta = nova_conta()
    prev = receber(
        conta,
        Trilho.RES_CONJUNTA_12,
        TipoAtivo.PREVIDENCIA,
        "20000.00",
        POL,
        produto=Produto.EMPRESTIMO,
        contraparte_id="entidade_operadora_simulada",
        plano="vgbl",
    )
    assert _tipos(conta) == [T.GRAVAME_SOLICITADO]
    with pytest.raises(TransicaoInvalida):
        conta.recusar_entrada(prev.id, "nao_quero", CAT, AGORA)
    # Bloqueio só depois das informações (Res. Conj. 12, arts. 7º e 8º).
    with pytest.raises(TransicaoInvalida):
        _concluir(conta, prev.id)
    informacoes = InformacoesDaOperadora(reais("18000.00"), reais("900.00"), AGORA)
    conta.registrar_informacoes_da_operadora(prev.id, informacoes, AGORA)
    evento = conta.pendencias.eventos[0]
    assert evento.tipo is T.PREVIDENCIA_INFORMACOES_RECEBIDAS
    assert evento.dados["valor_disponivel"] == "18000.00"
    _concluir(conta, prev.id)
    assert prev.gravame.vigente
    assert prev.disponivel_para_emprestimo
    # Vale o que a operadora informou, não o que o cliente disse.
    assert conta.valor_de_mercado(prev.id) == reais("18000.00")
    assert conta.posicao(POL, CAT, HOJE).limite_total == ZERO


def test_informacoes_da_operadora_so_na_previdencia_e_coerentes() -> None:
    conta = nova_conta()
    cdb = receber(conta, Trilho.TRANSFERENCIA_DE_CUSTODIA, TipoAtivo.CDB, "1000.00", POL)
    with pytest.raises(TransicaoInvalida):
        conta.registrar_informacoes_da_operadora(
            cdb.id, InformacoesDaOperadora(reais("1.00"), ZERO, AGORA), AGORA
        )
    with pytest.raises(ValorInvalido):
        InformacoesDaOperadora(reais("1.00"), reais("2.00"), AGORA)


def test_cancelar_pedido_encerra_sem_lancamento() -> None:
    conta = nova_conta()
    garantia = receber(conta, Trilho.TRANSFERENCIA_DE_CUSTODIA, TipoAtivo.CDB, "1000.00", POL)
    conta.pendencias.limpar()
    conta.cancelar_pedido(garantia.id, "desistencia", AGORA + timedelta(hours=1))
    assert garantia.estado is E.CANCELADO
    assert garantia.gravame.encerrado_em == AGORA + timedelta(hours=1)
    assert _tipos(conta) == [T.GRAVAME_CANCELADO]
    assert conta.pendencias.lancamentos == []
    with pytest.raises(TransicaoInvalida):
        conta.cancelar_pedido(garantia.id, "de_novo", AGORA)


def test_garantia_de_fora_em_solicitado_nunca_gera_limite_nem_cobre_divida() -> None:
    conta = nova_conta()
    for trilho, tipo in [
        (Trilho.TRANSFERENCIA_DE_CUSTODIA, TipoAtivo.CDB),
        (Trilho.PORTABILIDADE_CVM_210, TipoAtivo.FUNDO_DI),
    ]:
        garantia = receber(conta, trilho, tipo, "1000.00", POL)
        assert not garantia.gera_limite
        assert not garantia.cobre_divida
    posicao = conta.posicao(POL, CAT, HOJE)
    assert posicao.limite_total == ZERO
    assert posicao.valor_das_garantias == ZERO
    assert not conta.autorizar_compra(reais("0.01"), "loja", POL, CAT, AGORA, HOJE).aprovada
