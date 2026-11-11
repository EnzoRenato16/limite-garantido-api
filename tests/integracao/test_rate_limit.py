"""Rate limit por parceiro."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.limite_de_requisicoes import LimitadorDeRequisicoes
from tests.conftest import criar_parceiro


def test_parceiro_que_passa_do_limite_recebe_429_e_os_outros_nao(cliente: TestClient) -> None:
    apressado = criar_parceiro(cliente, limite_de_requisicoes_por_minuto=3)
    tranquilo = criar_parceiro(cliente)

    for restantes in (2, 1, 0):
        resposta = apressado.get("/v1/contas")
        assert resposta.status_code == 200
        assert resposta.headers["RateLimit-Limit"] == "3"
        assert resposta.headers["RateLimit-Remaining"] == str(restantes)

    bloqueada = apressado.get("/v1/contas")
    assert bloqueada.status_code == 429
    assert bloqueada.headers["content-type"] == "application/problem+json"
    assert bloqueada.json()["codigo"] == "limite_de_requisicoes_excedido"
    assert int(bloqueada.headers["Retry-After"]) >= 1
    assert bloqueada.headers["RateLimit-Remaining"] == "0"

    # O limite é por parceiro: um não consome a cota do outro.
    assert tranquilo.get("/v1/contas").status_code == 200
    assert "lg_rate_limit_bloqueios_total 1.0" in cliente.get("/metrics").text


def test_a_janela_reinicia_a_cada_minuto() -> None:
    agora = [0.0]
    limitador = LimitadorDeRequisicoes(relogio=lambda: agora[0])
    assert [limitador.permitir("par_1", 2).permitido for _ in range(3)] == [True, True, False]
    assert limitador.permitir("par_1", 2).segundos_para_reiniciar == 60
    agora[0] = 59.0
    veredito = limitador.permitir("par_1", 2)
    assert not veredito.permitido
    assert veredito.segundos_para_reiniciar == 1
    assert veredito.cabecalhos()["Retry-After"] == "1"
    agora[0] = 60.0
    liberado = limitador.permitir("par_1", 2)
    assert liberado.permitido
    assert liberado.restantes == 1
    assert "Retry-After" not in liberado.cabecalhos()
