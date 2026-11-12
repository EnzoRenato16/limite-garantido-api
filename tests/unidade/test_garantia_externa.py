"""Máquina de estados da garantia em custodiante externo."""

from datetime import timedelta

import pytest

from app.dominio.erros import TransicaoInvalida, ValorInvalido
from app.dominio.garantia import (
    TRANSICOES_EXTERNAS,
    TRANSICOES_INTERNAS,
    EstadoGarantia,
    Garantia,
    OrigemGarantia,
    Solicitacao,
    TipoSolicitacao,
)
from app.dominio.politica import TipoAtivo
from tests.apoio import AGORA, HOJE, reais

E = EstadoGarantia


def _externa(estado: EstadoGarantia) -> Garantia:
    return Garantia(
        id="gar_1",
        conta_id="cta_1",
        tipo_ativo=TipoAtivo.TESOURO_SELIC,
        valor_aplicado=reais("5000.00"),
        estado=estado,
        criada_em=AGORA,
        marcada_em=HOJE,
        origem=OrigemGarantia.EXTERNA,
        custodiante_id="custodiante_agil",
    )


TODOS = list(EstadoGarantia)
VALIDAS_EXTERNAS = [(de, para) for de, destinos in TRANSICOES_EXTERNAS.items() for para in destinos]
INVALIDAS_EXTERNAS = [
    (de, para) for de in TODOS for para in TODOS if (de, para) not in VALIDAS_EXTERNAS
]


def test_a_tabela_externa_e_a_do_desenho() -> None:
    esperado = {
        E.SOLICITADA: {E.GRAVAME_PENDENTE, E.GRAVAME_RECUSADO, E.GRAVAME_EXPIRADO},
        E.GRAVAME_PENDENTE: {E.GRAVAME_CONFIRMADO, E.GRAVAME_RECUSADO, E.GRAVAME_EXPIRADO},
        E.GRAVAME_CONFIRMADO: {E.BLOQUEADA},
        E.BLOQUEADA: {E.LIBERACAO_PENDENTE, E.EXECUCAO_SOLICITADA},
        E.PARCIALMENTE_EXECUTADA: {E.LIBERACAO_PENDENTE, E.EXECUCAO_SOLICITADA},
        E.LIBERACAO_PENDENTE: {E.LIBERADA, E.BLOQUEADA},
        E.EXECUCAO_SOLICITADA: {E.EXECUTADA, E.PARCIALMENTE_EXECUTADA, E.BLOQUEADA},
    }
    assert {de: set(para) for de, para in TRANSICOES_EXTERNAS.items() if para} == esperado


def test_a_interna_so_ganhou_a_saida_por_expiracao() -> None:
    assert TRANSICOES_INTERNAS[E.PENDENTE] == {E.BLOQUEADA, E.GRAVAME_EXPIRADO}
    assert TRANSICOES_INTERNAS[E.BLOQUEADA] == {
        E.LIBERADA,
        E.EXECUTADA,
        E.PARCIALMENTE_EXECUTADA,
    }
    # Os estados externos não são alcançáveis por uma garantia interna.
    for estado in (E.SOLICITADA, E.LIBERACAO_PENDENTE, E.EXECUCAO_SOLICITADA):
        assert not any(estado in destinos for destinos in TRANSICOES_INTERNAS.values())


@pytest.mark.parametrize(("de", "para"), VALIDAS_EXTERNAS)
def test_transicoes_externas_validas(de: EstadoGarantia, para: EstadoGarantia) -> None:
    garantia = _externa(de)
    garantia.transicionar(para)
    assert garantia.estado is para


@pytest.mark.parametrize(("de", "para"), INVALIDAS_EXTERNAS)
def test_transicoes_externas_invalidas_dao_erro_claro(
    de: EstadoGarantia, para: EstadoGarantia
) -> None:
    garantia = _externa(de)
    with pytest.raises(TransicaoInvalida) as erro:
        garantia.transicionar(para)
    assert garantia.estado is de
    assert erro.value.detalhes["origem"] == "externa"


def test_garantia_externa_sem_custodiante_e_recusada() -> None:
    with pytest.raises(ValorInvalido):
        Garantia(
            id="gar_1",
            conta_id="cta_1",
            tipo_ativo=TipoAtivo.CDB,
            valor_aplicado=reais("10.00"),
            estado=E.SOLICITADA,
            criada_em=AGORA,
            marcada_em=HOJE,
            origem=OrigemGarantia.EXTERNA,
        )


def test_garantia_interna_com_custodiante_e_recusada() -> None:
    with pytest.raises(ValorInvalido):
        Garantia(
            id="gar_1",
            conta_id="cta_1",
            tipo_ativo=TipoAtivo.CDB,
            valor_aplicado=reais("10.00"),
            estado=E.BLOQUEADA,
            criada_em=AGORA,
            marcada_em=HOJE,
            custodiante_id="custodiante_agil",
        )


@pytest.mark.parametrize(
    ("estado", "gera_limite", "cobre_divida", "aguardando"),
    [
        (E.SOLICITADA, False, False, True),
        (E.GRAVAME_PENDENTE, False, False, True),
        (E.GRAVAME_CONFIRMADO, False, False, False),
        (E.GRAVAME_RECUSADO, False, False, False),
        (E.GRAVAME_EXPIRADO, False, False, False),
        (E.BLOQUEADA, True, True, False),
        (E.PARCIALMENTE_EXECUTADA, True, True, False),
        (E.LIBERACAO_PENDENTE, False, False, True),
        (E.EXECUCAO_SOLICITADA, False, True, True),
        (E.LIBERADA, False, False, False),
        (E.EXECUTADA, False, False, False),
    ],
)
def test_o_que_cada_estado_conta(
    estado: EstadoGarantia, gera_limite: bool, cobre_divida: bool, aguardando: bool
) -> None:
    garantia = _externa(estado)
    assert garantia.gera_limite is gera_limite
    assert garantia.ativa is gera_limite
    assert garantia.cobre_divida is cobre_divida
    assert garantia.aguardando_custodiante is aguardando


def test_solicitacao_sabe_se_venceu() -> None:
    solicitacao = Solicitacao(
        id="sol_1",
        tipo=TipoSolicitacao.GRAVAME,
        criada_em=AGORA,
        prazo_ate=AGORA + timedelta(hours=1),
    )
    assert not solicitacao.vencida(AGORA + timedelta(minutes=59))
    assert not solicitacao.vencida(AGORA + timedelta(hours=1))
    assert solicitacao.vencida(AGORA + timedelta(hours=1, seconds=1))
    assert solicitacao.enviada_em is None
