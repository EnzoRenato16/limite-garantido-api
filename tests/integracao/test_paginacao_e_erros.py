"""Paginação por cursor e formato de erro RFC 7807."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.aplicacao import contas as casos
from app.infra.logs import FormatadorJson
from tests.conftest import Parceiro

CAMPOS_DO_PROBLEMA = {"type", "title", "status", "detail", "instance", "codigo", "request_id"}


def test_paginacao_por_cursor_percorre_tudo_sem_repetir(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "1000.00")
    for numero in range(1, 6):
        parceiro.comprar(conta, f"{numero}.00", f"compra {numero}")

    vistos: list[str] = []
    cursor = None
    paginas = 0
    while True:
        parametros = {"limite": 2, **({"cursor": cursor} if cursor else {})}
        pagina = parceiro.get(f"/v1/contas/{conta}/compras", params=parametros).json()
        vistos.extend(c["descricao"] for c in pagina["dados"])
        paginas += 1
        assert pagina["tem_mais"] == (pagina["proximo_cursor"] is not None)
        if not pagina["tem_mais"]:
            break
        cursor = pagina["proximo_cursor"]
    assert paginas == 3
    # Mais novas primeiro, cada uma exatamente uma vez.
    assert vistos == [f"compra {n}" for n in (5, 4, 3, 2, 1)]


def test_item_novo_nao_baguncca_a_pagina_seguinte(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "1000.00")
    for numero in range(1, 4):
        parceiro.comprar(conta, "1.00", f"compra {numero}")
    primeira = parceiro.get(f"/v1/contas/{conta}/compras", params={"limite": 2}).json()
    parceiro.comprar(conta, "1.00", "compra que chegou no meio")
    segunda = parceiro.get(
        f"/v1/contas/{conta}/compras", params={"limite": 2, "cursor": primeira["proximo_cursor"]}
    ).json()
    # Com OFFSET a "compra 2" apareceria de novo. Com cursor, não.
    assert [c["descricao"] for c in segunda["dados"]] == ["compra 1"]


def test_eventos_paginam_pela_sequencia_e_aceitam_filtros(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "100.00")
    parceiro.comprar(conta, "10.00")
    parceiro.comprar(conta, "500.00")
    pagina = parceiro.get("/v1/eventos", params={"limite": 3}).json()
    assert len(pagina["dados"]) == 3
    assert pagina["tem_mais"] is True
    resto = parceiro.get("/v1/eventos", params={"cursor": pagina["proximo_cursor"]}).json()
    tipos = [e["tipo"] for e in pagina["dados"] + resto["dados"]]
    assert tipos == [
        "compra.negada",
        "limite.atualizado",
        "compra.aprovada",
        "limite.atualizado",
        "garantia.bloqueada",
    ]
    negadas = parceiro.get("/v1/eventos", params={"tipo": "compra.negada"}).json()["dados"]
    assert [e["dados"]["motivo"] for e in negadas] == ["limite_insuficiente"]
    assert parceiro.get("/v1/eventos", params={"tipo": "nao.existe"}).status_code == 422


@pytest.mark.parametrize("cursor", ["lixo", "e30", "eyJrIjp0cnVlfQ", "eyJrIjoxMjN9"])
def test_cursor_invalido_ou_de_outra_listagem_e_400(parceiro: Parceiro, cursor: str) -> None:
    resposta = parceiro.get("/v1/contas", params={"cursor": cursor})
    assert resposta.status_code == 400
    assert resposta.json()["codigo"] == "cursor_invalido"


@pytest.mark.parametrize("limite", [0, 101, -1, "muitos"])
def test_limite_de_pagina_fora_do_intervalo_e_422(parceiro: Parceiro, limite: object) -> None:
    resposta = parceiro.get("/v1/contas", params={"limite": limite})
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "requisicao_invalida"


def test_todo_erro_segue_a_rfc_7807(parceiro: Parceiro, cliente: TestClient) -> None:
    conta = parceiro.abrir_conta()
    respostas = [
        cliente.get("/v1/contas"),  # 401
        parceiro.get("/v1/contas/cta_nao_existe"),  # 404 de negócio
        cliente.get("/v1/rota-que-nao-existe"),  # 404 de roteamento
        cliente.delete("/v1/contas"),  # 405
        parceiro.post("/v1/contas", {"cliente_ref": ""}),  # 422 de validação
        parceiro.comprar(conta, "10.00"),  # 402
        parceiro.fechar_fatura(conta),  # 409
        parceiro.post(
            f"/v1/contas/{conta}/compras", {"valor": "1.00", "descricao": "x"}, chave=None
        ),
    ]
    assert [r.status_code for r in respostas] == [401, 404, 404, 405, 422, 402, 409, 400]
    for resposta in respostas:
        assert resposta.headers["content-type"] == "application/problem+json"
        corpo = resposta.json()
        assert set(corpo) >= CAMPOS_DO_PROBLEMA
        assert corpo["status"] == resposta.status_code
        assert corpo["type"] == f"/v1/erros/{corpo['codigo']}"
        assert corpo["request_id"] == resposta.headers["X-Request-Id"]
        assert corpo["instance"].startswith("/v1/")
        # O `type` é um endereço de verdade: leva à descrição do erro.
        descricao = cliente.get(corpo["type"])
        assert descricao.status_code == 200
        assert descricao.json()["status"] == resposta.status_code


def test_catalogo_lista_os_codigos_com_status_e_descricao(cliente: TestClient) -> None:
    catalogo = cliente.get("/v1/erros").json()
    codigos = {item["codigo"] for item in catalogo}
    esperados = {
        "limite_insuficiente",
        "garantia_cobrindo_divida",
        "transicao_de_estado_invalida",
        "chave_de_idempotencia_reutilizada",
        "limite_de_requisicoes_excedido",
        "conta_nao_encontrada",
    }
    assert esperados <= codigos
    assert all(item["titulo"] and item["descricao"] for item in catalogo)
    assert cliente.get("/v1/erros/nao_existe").status_code == 404


def test_erro_inesperado_vira_500_sem_vazar_detalhe_interno(
    parceiro: Parceiro, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(*_: object, **__: object) -> None:
        raise RuntimeError("senha do banco: hunter2")

    monkeypatch.setattr(casos, "consultar_conta", explode)
    with caplog.at_level(logging.ERROR, logger="app.erros"):
        resposta = parceiro.get("/v1/contas/cta_qualquer")
    assert resposta.status_code == 500
    assert resposta.headers["content-type"] == "application/problem+json"
    corpo = resposta.json()
    assert corpo["codigo"] == "erro_interno"
    # Quem chamou recebe só o id para o suporte rastrear...
    assert corpo["request_id"] == resposta.headers["X-Request-Id"]
    assert "hunter2" not in resposta.text
    assert "RuntimeError" not in resposta.text
    # ...e o detalhe fica no log, com o mesmo id e o parceiro.
    (registro,) = [r for r in caplog.records if r.name == "app.erros"]
    linha = json.loads(FormatadorJson().format(registro))
    assert "hunter2" in linha["excecao"]
    assert linha["mensagem"] == "Erro inesperado"


def test_openapi_esta_em_portugues_e_documenta_os_erros(cliente: TestClient) -> None:
    esquema = cliente.get("/openapi.json").json()
    assert esquema["info"]["title"] == "Limite Garantido as a Service"
    nomes = [tag["name"] for tag in esquema["tags"]]
    assert {"Contas", "Garantias", "Compras", "Eventos e webhooks", "Risco"} <= set(nomes)
    compra = esquema["paths"]["/v1/contas/{conta_id}/compras"]["post"]
    assert compra["summary"] == "Autorizar compra"
    assert {"201", "402", "409", "422", "429"} <= set(compra["responses"])
    parametros = {p["name"] for p in compra["parameters"]}
    assert "Idempotency-Key" in parametros
    assert "ApiKeyDoParceiro" in esquema["components"]["securitySchemes"]
    exemplo = esquema["components"]["schemas"]["NovaGarantia"]["example"]
    assert exemplo == {"tipo_ativo": "cdb", "valor": "5000.00"}
    # Toda operação tem resumo e descrição.
    for caminho, metodos in esquema["paths"].items():
        for metodo, operacao in metodos.items():
            assert operacao.get("summary"), f"{metodo} {caminho} sem resumo"
