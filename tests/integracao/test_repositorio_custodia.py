"""Persistência da garantia externa: ida e volta, histórico de pedidos e respostas."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text

from app.aplicacao.erros import RespostaDuplicada
from app.aplicacao.portas import StatusDaResposta
from app.dominio.garantia import EstadoGarantia, OrigemGarantia, TipoSolicitacao
from app.dominio.parceiro import Parceiro as ParceiroDominio
from app.dominio.politica import TipoAtivo
from app.infra.banco.sessao import criar_fabrica_de_sessoes
from app.infra.banco.unidade import UnidadeDeTrabalhoSql
from app.infra.custodia import CustodiaInterna
from app.infra.relogio import RelogioFixo
from tests.apoio import AGORA, HOJE, politica, reais
from tests.conftest import Parceiro


def _uow(engine: Engine) -> UnidadeDeTrabalhoSql:
    return UnidadeDeTrabalhoSql(
        criar_fabrica_de_sessoes(engine)(), RelogioFixo(AGORA), CustodiaInterna()
    )


def _parceiro(uow: UnidadeDeTrabalhoSql, parceiro: Parceiro) -> ParceiroDominio:
    dominio = uow.parceiros.obter(parceiro.id)
    assert dominio is not None
    return dominio


def _pedir_gravame(engine: Engine, parceiro: Parceiro) -> tuple[str, str, str]:
    conta_id = parceiro.abrir_conta()
    uow = _uow(engine)
    dono = _parceiro(uow, parceiro)
    conta = uow.contas.obter_para_atualizar(parceiro.id, conta_id)
    assert conta is not None
    garantia = conta.registrar_garantia(
        TipoAtivo.TESOURO_SELIC,
        reais("2500.00"),
        politica(),
        AGORA,
        HOJE,
        custodiante_id="custodiante_lento",
        prazo_ate=AGORA + timedelta(hours=24),
    )
    assert garantia.solicitacao is not None
    conta.aceitar_solicitacao(garantia.id, garantia.solicitacao.id, AGORA)
    uow.contas.salvar(conta, dono)
    uow.commit()
    uow.sessao.close()
    return conta_id, garantia.id, garantia.solicitacao.id


def test_garantia_externa_vai_e_volta_do_banco(engine: Engine, parceiro: Parceiro) -> None:
    conta_id, garantia_id, solicitacao_id = _pedir_gravame(engine, parceiro)

    uow = _uow(engine)
    conta = uow.contas.obter(parceiro.id, conta_id)
    assert conta is not None
    garantia = conta.garantia(garantia_id)
    assert garantia.origem is OrigemGarantia.EXTERNA
    assert garantia.custodiante_id == "custodiante_lento"
    assert garantia.estado is EstadoGarantia.GRAVAME_PENDENTE
    assert garantia.solicitacao is not None
    assert garantia.solicitacao.id == solicitacao_id
    assert garantia.solicitacao.tipo is TipoSolicitacao.GRAVAME
    assert garantia.solicitacao.prazo_ate == AGORA + timedelta(hours=24)
    assert garantia.solicitacao.enviada_em == AGORA

    pedido = uow.contas.localizar_solicitacao(solicitacao_id)
    assert pedido is not None
    assert (pedido.parceiro_id, pedido.conta_id, pedido.garantia_id) == (
        parceiro.id,
        conta_id,
        garantia_id,
    )
    assert pedido.custodiante_id == "custodiante_lento"
    assert uow.contas.localizar_solicitacao("sol_nao_existe") is None
    assert (parceiro.id, conta_id, garantia_id) in uow.contas.garantias_aguardando_custodiante()
    uow.sessao.close()


def test_resposta_repetida_e_recusada_pelo_banco(engine: Engine, parceiro: Parceiro) -> None:
    _, garantia_id, solicitacao_id = _pedir_gravame(engine, parceiro)
    argumentos = {
        "custodiante_id": "custodiante_lento",
        "evento_id": f"evc_{solicitacao_id}",
        "solicitacao_id": solicitacao_id,
        "garantia_id": garantia_id,
        "parceiro_id": parceiro.id,
        "status": StatusDaResposta.CONFIRMADA,
        "canal": "callback",
        "resultado": "aplicada",
        "recebida_em": AGORA,
    }
    uow = _uow(engine)
    assert not uow.contas.resposta_ja_registrada("custodiante_lento", f"evc_{solicitacao_id}")
    uow.contas.registrar_resposta(**argumentos)  # type: ignore[arg-type]
    uow.commit()
    uow.sessao.close()

    uow = _uow(engine)
    assert uow.contas.resposta_ja_registrada("custodiante_lento", f"evc_{solicitacao_id}")
    with pytest.raises(RespostaDuplicada):
        uow.contas.registrar_resposta(**argumentos)  # type: ignore[arg-type]
    # A transação continua usável depois da duplicata (o erro ficou num savepoint).
    assert uow.contas.localizar_solicitacao(solicitacao_id) is not None
    uow.sessao.close()


def test_garantia_gravada_sem_origem_le_como_interna(engine: Engine, parceiro: Parceiro) -> None:
    conta_id = parceiro.abrir_conta()
    garantia_id = f"gar_legado_{uuid.uuid4().hex[:20]}"
    with engine.begin() as conexao:
        conexao.execute(
            text(
                "INSERT INTO garantias (id, conta_id, parceiro_id, tipo_ativo, valor_aplicado,"
                " estado, marcada_em, criada_em) VALUES (:id, :conta, :parceiro, 'cdb', 100,"
                " 'liberada', :hoje, :agora)"
            ),
            {
                "id": garantia_id,
                "conta": conta_id,
                "parceiro": parceiro.id,
                "hoje": HOJE.isoformat(),
                "agora": AGORA.isoformat(),
            },
        )
        origem = conexao.execute(
            text("SELECT origem FROM garantias WHERE id = :id"), {"id": garantia_id}
        ).scalar_one()
    assert origem == "interna"


def test_violacao_que_nao_e_duplicata_nao_vira_duplicata(
    engine: Engine, parceiro: Parceiro
) -> None:
    from sqlalchemy.exc import IntegrityError

    uow = _uow(engine)
    with pytest.raises(IntegrityError):
        uow.contas.registrar_resposta(
            custodiante_id="custodiante_lento",
            evento_id="evc_sem_pedido",
            solicitacao_id="sol_que_nao_existe",
            garantia_id="gar_que_nao_existe",
            parceiro_id=parceiro.id,
            status=StatusDaResposta.CONFIRMADA,
            canal="callback",
            resultado="aplicada",
            recebida_em=AGORA,
        )
    uow.sessao.close()
