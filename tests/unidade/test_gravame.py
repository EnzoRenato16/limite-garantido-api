"""Máquina de estados do gravame: o registro na infraestrutura, separado da garantia."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.dominio.erros import TransicaoInvalida, ValorInvalido
from app.dominio.gravame import ESTADOS_FINAIS, TRANSICOES, EstadoGravame, Gravame
from app.dominio.regulacao import Infraestrutura, Instrumento, Trilho

AGORA = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
PRAZO = date(2026, 3, 4)
E = EstadoGravame


def _gravame(trilho: Trilho = Trilho.CASA) -> Gravame:
    return Gravame.solicitar(
        infraestrutura=Infraestrutura.B3_BALCAO,
        instrumento=Instrumento.CESSAO_FIDUCIARIA,
        trilho=trilho,
        agora=AGORA,
    )


def _levar_a(estado: EstadoGravame, trilho: Trilho = Trilho.TD_GARANTIA) -> Gravame:
    gravame = _gravame(trilho)
    caminhos = {
        E.SOLICITADO: [],
        E.REGISTRADO: [lambda g: g.registrar(AGORA, "SIM-1")],
        E.LIBERADO_PARA_EXCUSSAO: [
            lambda g: g.registrar(AGORA, "SIM-1"),
            lambda g: g.liberar_para_excussao(AGORA),
        ],
        E.EXECUTADO: [
            lambda g: g.registrar(AGORA, "SIM-1"),
            lambda g: g.liberar_para_excussao(AGORA),
            lambda g: g.marcar_executado(AGORA),
        ],
        E.BAIXADO: [
            lambda g: g.registrar(AGORA, "SIM-1"),
            lambda g: g.pedir_baixa(AGORA, PRAZO),
            lambda g: g.confirmar_baixa(AGORA),
        ],
        E.RECUSADO: [lambda g: g.recusar(AGORA, "agente_sem_servico")],
        E.CANCELADO: [lambda g: g.cancelar(AGORA, "desistencia")],
    }
    for passo in caminhos[estado]:
        passo(gravame)
    assert gravame.estado is estado
    return gravame


def test_a_tabela_e_a_do_desenho() -> None:
    assert {
        E.SOLICITADO: frozenset({E.REGISTRADO, E.RECUSADO, E.CANCELADO}),
        E.REGISTRADO: frozenset({E.LIBERADO_PARA_EXCUSSAO, E.BAIXADO}),
        E.LIBERADO_PARA_EXCUSSAO: frozenset({E.EXECUTADO}),
        E.EXECUTADO: frozenset({E.BAIXADO}),
        E.BAIXADO: frozenset(),
        E.RECUSADO: frozenset(),
        E.CANCELADO: frozenset(),
    } == TRANSICOES
    assert len(EstadoGravame) == 7
    assert frozenset({E.BAIXADO, E.RECUSADO, E.CANCELADO}) == ESTADOS_FINAIS


def test_caminho_completo_registra_datas_e_protocolo() -> None:
    gravame = _gravame()
    assert gravame.estado is E.SOLICITADO
    assert gravame.solicitado_em == AGORA
    assert not gravame.vigente
    gravame.registrar(AGORA, "SIM-b3_balcao-1")
    assert gravame.vigente
    assert gravame.protocolo == "SIM-b3_balcao-1"
    assert gravame.registrado_em == AGORA
    gravame.liberar_para_excussao(AGORA)
    assert not gravame.vigente
    gravame.marcar_executado(AGORA)
    gravame.pedir_baixa(AGORA, PRAZO)
    assert gravame.baixa_prazo == PRAZO
    assert gravame.baixa_pedida_em == AGORA
    assert gravame.baixa_pendente
    gravame.confirmar_baixa(AGORA)
    assert gravame.estado is E.BAIXADO
    assert gravame.encerrado
    assert gravame.encerrado_em == AGORA
    assert not gravame.baixa_pendente


def test_registrado_com_baixa_pedida_nao_e_vigente() -> None:
    gravame = _levar_a(E.REGISTRADO)
    gravame.pedir_baixa(AGORA, PRAZO)
    assert gravame.estado is E.REGISTRADO
    assert not gravame.vigente
    # Depois da baixa pedida, não há mais excussão nem segundo pedido.
    with pytest.raises(TransicaoInvalida):
        gravame.liberar_para_excussao(AGORA)
    with pytest.raises(TransicaoInvalida):
        gravame.pedir_baixa(AGORA, PRAZO)


def test_de_liberado_para_excussao_nao_vai_direto_a_baixado() -> None:
    gravame = _levar_a(E.LIBERADO_PARA_EXCUSSAO)
    with pytest.raises(TransicaoInvalida):
        gravame.pedir_baixa(AGORA, PRAZO)
    with pytest.raises(TransicaoInvalida):
        gravame.confirmar_baixa(AGORA)
    assert gravame.estado is E.LIBERADO_PARA_EXCUSSAO


def test_confirmar_baixa_exige_baixa_pedida() -> None:
    gravame = _levar_a(E.REGISTRADO)
    with pytest.raises(TransicaoInvalida):
        gravame.confirmar_baixa(AGORA)


def test_recusar_so_no_td_garantia() -> None:
    for trilho in Trilho:
        gravame = _gravame(trilho)
        if trilho is Trilho.TD_GARANTIA:
            gravame.recusar(AGORA, "agente_sem_servico")
            assert gravame.estado is E.RECUSADO
            assert gravame.motivo == "agente_sem_servico"
            assert gravame.encerrado_em == AGORA
        else:
            with pytest.raises(TransicaoInvalida):
                gravame.recusar(AGORA, "qualquer")
            assert gravame.estado is E.SOLICITADO


def test_cancelar_so_do_solicitado() -> None:
    gravame = _levar_a(E.CANCELADO)
    assert gravame.motivo == "desistencia"
    registrado = _levar_a(E.REGISTRADO)
    with pytest.raises(TransicaoInvalida):
        registrado.cancelar(AGORA, "tarde")


def test_registrar_exige_protocolo() -> None:
    with pytest.raises(ValorInvalido):
        _gravame().registrar(AGORA, "  ")


# Cada comando e o estado de chegada; o que não está na tabela levanta TransicaoInvalida.
COMANDOS = {
    "registrar": (lambda g: g.registrar(AGORA, "SIM-2"), E.REGISTRADO),
    "recusar": (lambda g: g.recusar(AGORA, "m"), E.RECUSADO),
    "cancelar": (lambda g: g.cancelar(AGORA, "m"), E.CANCELADO),
    "liberar_para_excussao": (lambda g: g.liberar_para_excussao(AGORA), E.LIBERADO_PARA_EXCUSSAO),
    "marcar_executado": (lambda g: g.marcar_executado(AGORA), E.EXECUTADO),
}


@pytest.mark.parametrize("de", list(EstadoGravame))
@pytest.mark.parametrize("comando", list(COMANDOS))
def test_cada_comando_respeita_a_tabela(de: EstadoGravame, comando: str) -> None:
    gravame = _levar_a(de)
    executar, para = COMANDOS[comando]
    if para in TRANSICOES[de]:
        executar(gravame)
        assert gravame.estado is para
    else:
        with pytest.raises(TransicaoInvalida):
            executar(gravame)
        assert gravame.estado is de
