"""Propriedade dos trilhos: respostas em qualquer ordem e duplicadas.

O limite do cartão e o valor máximo de empréstimo só nascem com o gravame `registrado`, e a
mesma resposta duas vezes vale uma vez (idempotência por contraparte e evento).
"""

from __future__ import annotations

import itertools

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, func, select

from app.aplicacao import contrapartes as casos
from app.aplicacao.portas import RespostaDaContraparte, StatusDaResposta
from app.dominio.gravame import EstadoGravame
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto, Trilho
from app.infra.banco.modelos import RespostaDeCustodianteModelo
from app.infra.contrapartes import evento_id_da_contraparte
from app.infra.relogio import RelogioFixo
from tests.conftest import INSTANTE_INICIAL
from tests.integracao.apoio_contrapartes import (
    fabrica_de_unidades,
    nova_conta_do,
    novo_parceiro,
    pedir_entrada,
)

# (trilho, ativo, produto, desfecho negativo do trilho)
PEDIDOS = [
    (
        Trilho.TRANSFERENCIA_DE_CUSTODIA,
        TipoAtivo.CDB,
        Produto.CARTAO,
        StatusDaResposta.RECUSADA,
        EstadoGravame.CANCELADO,
    ),
    (
        Trilho.TD_GARANTIA,
        TipoAtivo.TESOURO_SELIC,
        Produto.EMPRESTIMO,
        StatusDaResposta.RECUSADA,
        EstadoGravame.RECUSADO,
    ),
    (
        Trilho.PORTABILIDADE_CVM_210,
        TipoAtivo.ACOES,
        Produto.EMPRESTIMO,
        StatusDaResposta.IMPEDIMENTO,
        EstadoGravame.CANCELADO,
    ),
]
_contador = itertools.count()


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(respostas=st.lists(st.tuples(st.integers(0, 2), st.booleans()), max_size=8))
def test_limite_so_nasce_no_registro_e_a_resposta_repetida_vale_uma_vez(
    engine: Engine, respostas: list[tuple[int, bool]]
) -> None:
    relogio = RelogioFixo(INSTANTE_INICIAL)
    abrir_unidade = fabrica_de_unidades(engine, relogio)
    parceiro = novo_parceiro(abrir_unidade)
    conta_id = nova_conta_do(abrir_unidade, parceiro, f"prop-{next(_contador)}")
    garantias = [
        pedir_entrada(
            abrir_unidade, parceiro, conta_id, trilho, tipo, "teste_confirma", "10000.00", produto
        )
        for trilho, tipo, produto, _, _ in PEDIDOS
    ]

    def estado_atual() -> tuple[object, object]:
        with abrir_unidade() as uow:
            conta = uow.contas.obter(parceiro.id, conta_id)
            assert conta is not None
            return conta, uow.catalogo

    def conferir_invariantes() -> None:
        conta, catalogo = estado_atual()
        registradas = [g for g in conta.garantias if g.estado is EstadoGravame.REGISTRADO]  # type: ignore[attr-defined]
        limite = conta.posicao(parceiro.politica, catalogo, relogio.hoje()).limite_garantido  # type: ignore[attr-defined]
        maximo = conta.valor_maximo_do_emprestimo(parceiro.politica, catalogo, relogio.hoje())  # type: ignore[attr-defined]
        assert limite.positivo == any(g.produto is Produto.CARTAO for g in registradas)
        assert maximo.positivo == any(g.produto is Produto.EMPRESTIMO for g in registradas)

    conferir_invariantes()  # nada respondido: nada de limite
    esperado: dict[int, EstadoGravame] = {}
    distintas: set[tuple[int, bool]] = set()
    # A sequência inteira e depois ela de novo: duplicadas, em qualquer ordem.
    for indice, conclui in [*respostas, *respostas]:
        garantia = garantias[indice]
        _, _, _, negativo, estado_negativo = PEDIDOS[indice]
        status = StatusDaResposta.CONCLUIDA if conclui else negativo
        assert garantia.solicitacao is not None
        resposta = RespostaDaContraparte(
            solicitacao_id=garantia.solicitacao.id,
            status=status,
            evento_id=evento_id_da_contraparte("teste_confirma", garantia.solicitacao.id, status),
            motivo="prop",
        )
        with abrir_unidade() as uow:
            casos.aplicar_resposta(uow, "teste_confirma", resposta, casos.Canal.CALLBACK)
            uow.commit()
        distintas.add((indice, conclui))
        # A primeira resposta de cada pedido decide; as seguintes são obsoletas.
        esperado.setdefault(indice, EstadoGravame.REGISTRADO if conclui else estado_negativo)
        conferir_invariantes()

    for indice, garantia in enumerate(garantias):
        with abrir_unidade() as uow:
            # Por garantia, o repositório traz também a que já foi encerrada.
            conta_da_garantia = uow.contas.obter_pela_garantia_para_atualizar(garantia.id)
            assert conta_da_garantia is not None
            estado = conta_da_garantia.garantia(garantia.id).estado
        assert estado is esperado.get(indice, EstadoGravame.SOLICITADO)
    with engine.connect() as conexao:
        gravadas = conexao.execute(
            select(func.count())
            .select_from(RespostaDeCustodianteModelo)
            .where(RespostaDeCustodianteModelo.garantia_id.in_([g.id for g in garantias]))
        ).scalar_one()
    assert gravadas == len(distintas)  # cada (pedido, desfecho) distinto uma vez só
