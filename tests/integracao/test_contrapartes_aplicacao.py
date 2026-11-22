"""Casos de uso das contrapartes e das baixas, direto na camada de aplicação."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import Engine

from app.aplicacao import contrapartes as casos
from app.aplicacao import trilhos
from app.aplicacao.erros import RespostaInvalida, SolicitacaoNaoEncontrada
from app.aplicacao.portas import FabricaDeUnidades, RespostaDaContraparte, StatusDaResposta
from app.dominio.garantia import Garantia
from app.dominio.gravame import EstadoGravame
from app.dominio.parceiro import Parceiro
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto, Trilho
from app.infra.contrapartes import evento_id_da_contraparte
from app.infra.relogio import RelogioFixo
from tests.apoio import reais
from tests.integracao.apoio_contrapartes import (
    fabrica_de_unidades,
    nova_conta_do,
    novo_parceiro,
    pedir_entrada,
)

PRAZO_LEGADO = timedelta(days=1)


@pytest.fixture
def abrir_unidade(engine: Engine, relogio: RelogioFixo) -> FabricaDeUnidades:
    return fabrica_de_unidades(engine, relogio)


@pytest.fixture
def parceiro(abrir_unidade: FabricaDeUnidades) -> Parceiro:
    return novo_parceiro(abrir_unidade)


def _garantia(abrir_unidade: FabricaDeUnidades, parceiro: Parceiro, garantia_id: str) -> Garantia:
    with abrir_unidade() as uow:
        conta = uow.contas.obter_pela_garantia_para_atualizar(garantia_id)
        assert conta is not None
        return conta.garantia(garantia_id)


def _resposta(garantia: Garantia, status: StatusDaResposta) -> RespostaDaContraparte:
    assert garantia.solicitacao is not None
    assert garantia.contraparte_id is not None
    return RespostaDaContraparte(
        solicitacao_id=garantia.solicitacao.id,
        status=status,
        evento_id=evento_id_da_contraparte(
            garantia.contraparte_id, garantia.solicitacao.id, status
        ),
        motivo="teste",
    )


def _rodar(abrir_unidade: FabricaDeUnidades, parceiro: Parceiro) -> tuple[int, int, int]:
    return casos.processar_contrapartes(abrir_unidade, PRAZO_LEGADO, parceiro.id)


def test_o_worker_conclui_a_portabilidade_depois_da_latencia(
    abrir_unidade: FabricaDeUnidades, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta_id = nova_conta_do(abrir_unidade, parceiro, "c-port")
    garantia = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.PORTABILIDADE_CVM_210,
        TipoAtivo.ACOES,
        "teste_confirma",
    )
    assert _rodar(abrir_unidade, parceiro) == (1, 0, 0)  # enviou; ainda sem resposta
    assert _garantia(abrir_unidade, parceiro, garantia.id).solicitacao.enviada_em is not None  # type: ignore[union-attr]
    relogio.avancar(minutes=10)
    processadas, erros, eventos = _rodar(abrir_unidade, parceiro)
    assert (processadas, erros) == (1, 0)
    assert eventos >= 2
    depois = _garantia(abrir_unidade, parceiro, garantia.id)
    assert depois.estado is EstadoGravame.REGISTRADO
    assert depois.gravame.protocolo == f"SIM-b3_central_depositaria-{garantia.id}"


def test_transferencia_e_td_sem_resposta_no_prazo_viram_cancelado_e_recusado(
    abrir_unidade: FabricaDeUnidades, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta_id = nova_conta_do(abrir_unidade, parceiro, "c-prazo")
    transferencia = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.TRANSFERENCIA_DE_CUSTODIA,
        TipoAtivo.CDB,
        "teste_mudo",
    )
    td = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.TD_GARANTIA,
        TipoAtivo.TESOURO_SELIC,
        "teste_mudo",
    )
    _rodar(abrir_unidade, parceiro)
    assert _garantia(abrir_unidade, parceiro, td.id).estado is EstadoGravame.SOLICITADO
    relogio.avancar(days=8)  # além de 5 dias úteis (transferência) e 3 (TD)
    assert _rodar(abrir_unidade, parceiro)[1] == 0
    assert _garantia(abrir_unidade, parceiro, transferencia.id).estado is EstadoGravame.CANCELADO
    assert _garantia(abrir_unidade, parceiro, td.id).estado is EstadoGravame.RECUSADO


def test_portabilidade_e_previdencia_sem_resposta_continuam_marcadas_com_prazo_vencido(
    abrir_unidade: FabricaDeUnidades, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta_id = nova_conta_do(abrir_unidade, parceiro, "c-reg")
    portabilidade = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.PORTABILIDADE_CVM_210,
        TipoAtivo.FUNDO_DI,
        "teste_mudo",
        produto=Produto.EMPRESTIMO,
    )
    previdencia = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.RES_CONJUNTA_12,
        TipoAtivo.PREVIDENCIA,
        "teste_mudo",
    )
    _rodar(abrir_unidade, parceiro)
    relogio.avancar(days=15)  # além de 9 dias úteis (fundos) e 2 (previdência)
    assert _rodar(abrir_unidade, parceiro) == (2, 0, 2)
    for garantia in (portabilidade, previdencia):
        depois = _garantia(abrir_unidade, parceiro, garantia.id)
        assert depois.estado is EstadoGravame.SOLICITADO  # continua
        assert depois.solicitacao is not None
        assert depois.solicitacao.prazo_regulatorio_vencido
    # De novo: já marcado, nada a gravar (não repete o evento).
    assert _rodar(abrir_unidade, parceiro) == (2, 0, 0)


def test_callback_da_operadora_com_recusa_e_resposta_invalida_pelo_art_18(
    abrir_unidade: FabricaDeUnidades, parceiro: Parceiro
) -> None:
    conta_id = nova_conta_do(abrir_unidade, parceiro, "c-art18")
    garantia = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.RES_CONJUNTA_12,
        TipoAtivo.PREVIDENCIA,
        "teste_confirma",
    )
    with abrir_unidade() as uow:
        with pytest.raises(RespostaInvalida) as erro:
            casos.aplicar_resposta(
                uow,
                "teste_confirma",
                _resposta(garantia, StatusDaResposta.RECUSADA),
                casos.Canal.CALLBACK,
            )
        assert "art. 18" in erro.value.mensagem
        uow.rollback()
    depois = _garantia(abrir_unidade, parceiro, garantia.id)
    assert depois.estado is EstadoGravame.SOLICITADO
    assert depois.solicitacao is not None  # o pedido segue em aberto


def test_callback_de_outra_contraparte_nao_muda_nada(
    abrir_unidade: FabricaDeUnidades, parceiro: Parceiro
) -> None:
    conta_id = nova_conta_do(abrir_unidade, parceiro, "c-foco1")
    garantia = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.TD_GARANTIA,
        TipoAtivo.TESOURO_SELIC,
        "teste_confirma",
    )
    resposta = _resposta(garantia, StatusDaResposta.CONCLUIDA)
    with abrir_unidade() as uow:
        with pytest.raises(SolicitacaoNaoEncontrada):
            casos.aplicar_resposta(uow, "teste_recusa", resposta, casos.Canal.CALLBACK)
        uow.rollback()
    assert _garantia(abrir_unidade, parceiro, garantia.id).estado is EstadoGravame.SOLICITADO


def test_a_mesma_resposta_duas_vezes_vale_uma_so(
    abrir_unidade: FabricaDeUnidades, parceiro: Parceiro
) -> None:
    conta_id = nova_conta_do(abrir_unidade, parceiro, "c-dup")
    garantia = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.TRANSFERENCIA_DE_CUSTODIA,
        TipoAtivo.CDB,
        "teste_confirma",
        produto=Produto.CARTAO,
    )
    resposta = _resposta(garantia, StatusDaResposta.CONCLUIDA)
    resultados = []
    for _ in range(2):
        with abrir_unidade() as uow:
            resultado, _, _ = casos.aplicar_resposta(
                uow, "teste_confirma", resposta, casos.Canal.CALLBACK
            )
            uow.commit()
            resultados.append(resultado)
    assert resultados == [casos.ResultadoDaResposta.APLICADA, casos.ResultadoDaResposta.DUPLICADA]
    assert _garantia(abrir_unidade, parceiro, garantia.id).estado is EstadoGravame.REGISTRADO


def test_resposta_de_pedido_ja_encerrado_e_obsoleta(
    abrir_unidade: FabricaDeUnidades, parceiro: Parceiro
) -> None:
    conta_id = nova_conta_do(abrir_unidade, parceiro, "c-obs")
    garantia = pedir_entrada(
        abrir_unidade,
        parceiro,
        conta_id,
        Trilho.TD_GARANTIA,
        TipoAtivo.TESOURO_SELIC,
        "teste_recusa",
    )
    with abrir_unidade() as uow:
        resultado, _, _ = casos.aplicar_resposta(
            uow,
            "teste_recusa",
            _resposta(garantia, StatusDaResposta.RECUSADA),
            casos.Canal.CALLBACK,
        )
        uow.commit()
    assert resultado is casos.ResultadoDaResposta.APLICADA
    assert _garantia(abrir_unidade, parceiro, garantia.id).estado is EstadoGravame.RECUSADO
    with abrir_unidade() as uow:
        resultado, _, _ = casos.aplicar_resposta(
            uow,
            "teste_recusa",
            _resposta(garantia, StatusDaResposta.CONCLUIDA),
            casos.Canal.CALLBACK,
        )
        uow.commit()
    assert resultado is casos.ResultadoDaResposta.OBSOLETA
    assert _garantia(abrir_unidade, parceiro, garantia.id).estado is EstadoGravame.RECUSADO


def test_o_worker_baixa_a_garantia_com_baixa_pedida(
    abrir_unidade: FabricaDeUnidades, parceiro: Parceiro
) -> None:
    conta_id = nova_conta_do(abrir_unidade, parceiro, "c-baixa")
    with abrir_unidade() as uow:
        _, garantia = trilhos.custodiar_na_casa(
            uow,
            parceiro,
            conta_id,
            tipo=TipoAtivo.CDB,
            valor=reais("5000.00"),
            produto=Produto.CARTAO,
            liquidez_dias=0,
        )
        assert garantia.estado is EstadoGravame.REGISTRADO
        trilhos.liberar_garantia(uow, parceiro, garantia.id)
        uow.commit()
        garantia_id = garantia.id
    pedida = _garantia(abrir_unidade, parceiro, garantia_id)
    assert pedida.gravame.baixa_pendente
    assert pedida.estado is EstadoGravame.REGISTRADO

    assert casos.processar_baixas(abrir_unidade, parceiro.id) == (1, 0, 1)
    baixada = _garantia(abrir_unidade, parceiro, garantia_id)
    assert baixada.estado is EstadoGravame.BAIXADO
    assert baixada.gravame.encerrado_em is not None
    assert casos.processar_baixas(abrir_unidade, parceiro.id) == (0, 0, 0)  # nada a repetir
