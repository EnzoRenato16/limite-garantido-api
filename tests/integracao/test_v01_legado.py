"""Os 6 testes da versão 0.1."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def api() -> Iterator[TestClient]:
    main.servico.garantias.clear()
    main.servico.clientes.clear()
    main.servico.eventos.clear()
    main.respostas_idempotentes.clear()
    yield TestClient(main.app)


def _bloquear(api: TestClient, tipo: str, valor: str, cliente: str = "cli-1") -> dict:
    resposta = api.post(
        "/v1/garantias", json={"cliente_id": cliente, "tipo_ativo": tipo, "valor": valor}
    )
    assert resposta.status_code == 201
    return resposta.json()


def _tipos_de_evento(api: TestClient) -> list[str]:
    return [evento["tipo"] for evento in api.get("/v1/eventos").json()]


def test_bloqueio_calcula_limite_pelo_ltv(api: TestClient) -> None:
    corpo = _bloquear(api, "cdb", "5000.00")
    assert corpo["garantia"]["estado"] == "bloqueada"
    assert corpo["limite"]["limite_total"] == "4250.00"  # 5.000 x 85%
    # Arredondamento sempre para baixo: 33,33 x 95% = 31,6635 -> 31,66
    corpo = _bloquear(api, "fundo_di", "33.33", cliente="cli-2")
    assert corpo["limite"]["limite_total"] == "31.66"


def test_compra_aprovada_e_compra_negada_com_402(api: TestClient) -> None:
    _bloquear(api, "cdb", "5000.00")
    aprovada = api.post("/v1/compras", json={"cliente_id": "cli-1", "valor": "4000.00"})
    assert aprovada.status_code == 201
    negada = api.post("/v1/compras", json={"cliente_id": "cli-1", "valor": "500.00"})
    assert negada.status_code == 402
    assert negada.json()["codigo"] == "limite_insuficiente"
    limite = api.get("/v1/clientes/cli-1/limite").json()
    assert limite["limite_disponivel"] == "250.00"
    assert "compra.negada" in _tipos_de_evento(api)


def test_remarcacao_dispara_chamada_de_margem(api: TestClient) -> None:
    garantia = _bloquear(api, "cdb", "5000.00")["garantia"]
    api.post("/v1/compras", json={"cliente_id": "cli-1", "valor": "4000.00"})
    resposta = api.post(
        f"/v1/garantias/{garantia['id']}/remarcar", json={"valor_de_mercado": "4500.00"}
    )
    assert resposta.status_code == 200
    assert resposta.json()["limite"]["limite_total"] == "3825.00"
    assert resposta.json()["limite"]["limite_disponivel"] == "0.00"
    assert "chamada_de_margem" in _tipos_de_evento(api)


def test_liberacao_e_bloqueada_enquanto_a_garantia_cobre_divida(api: TestClient) -> None:
    garantia = _bloquear(api, "cdb", "5000.00")["garantia"]
    api.post("/v1/compras", json={"cliente_id": "cli-1", "valor": "1000.00"})
    negada = api.post(f"/v1/garantias/{garantia['id']}/liberar")
    assert negada.status_code == 409
    assert negada.json()["codigo"] == "garantia_cobrindo_divida"
    api.post("/v1/pagamentos", json={"cliente_id": "cli-1", "valor": "1000.00"})
    liberada = api.post(f"/v1/garantias/{garantia['id']}/liberar")
    assert liberada.status_code == 200
    assert liberada.json()["garantia"]["estado"] == "liberada"
    assert liberada.json()["limite"]["limite_total"] == "0.00"


def test_execucao_no_setimo_dia_comeca_pelo_mais_liquido_e_rebloqueia_a_sobra(
    api: TestClient,
) -> None:
    acoes = _bloquear(api, "acoes", "10000.00")["garantia"]
    cdb = _bloquear(api, "cdb", "5000.00")["garantia"]
    api.post("/v1/compras", json={"cliente_id": "cli-1", "valor": "4000.00"})

    # Até o 6º dia nada é executado.
    antes = api.post("/v1/atrasos", json={"cliente_id": "cli-1", "dias": 6}).json()
    assert antes["divida"] == "4000.00"
    assert "garantia.executada" not in _tipos_de_evento(api)

    depois = api.post("/v1/atrasos", json={"cliente_id": "cli-1", "dias": 7}).json()
    assert depois["divida"] == "0.00"
    executadas = [e for e in api.get("/v1/eventos").json() if e["tipo"] == "garantia.executada"]
    # O CDB é mais líquido que ações: só ele é vendido, e sobram R$ 1.000 re-bloqueados.
    assert [e["garantia_id"] for e in executadas] == [cdb["id"]]
    assert executadas[0]["valor_executado"] == "4000.00"
    assert executadas[0]["sobra"] == "1000.00"
    assert acoes["id"] not in [e["garantia_id"] for e in executadas]
    # Limite depois: ações 10.000 x 60% + sobra do CDB 1.000 x 85%.
    assert depois["limite_total"] == "6850.00"


def test_idempotencia_nao_duplica_garantia_nem_compra(api: TestClient) -> None:
    cabecalho = {"Idempotency-Key": "chave-1"}
    corpo = {"cliente_id": "cli-1", "tipo_ativo": "cdb", "valor": "5000.00"}
    primeira = api.post("/v1/garantias", json=corpo, headers=cabecalho).json()
    segunda = api.post("/v1/garantias", json=corpo, headers=cabecalho).json()
    assert primeira["garantia"]["id"] == segunda["garantia"]["id"]
    assert api.get("/v1/clientes/cli-1/limite").json()["limite_total"] == "4250.00"

    compra = {"cliente_id": "cli-1", "valor": "100.00"}
    api.post("/v1/compras", json=compra, headers=cabecalho)
    api.post("/v1/compras", json=compra, headers=cabecalho)
    assert api.get("/v1/clientes/cli-1/limite").json()["divida"] == "100.00"
