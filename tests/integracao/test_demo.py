"""Backend da página /demo: configuração, receptor de webhooks e seed."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.fabrica import criar_app
from app.config import Configuracao
from app.infra.relogio import RelogioFixo
from app.infra.webhooks.assinatura import assinar
from app.infra.webhooks.entregador import Entregador
from app.seed import semear
from tests.conftest import configuracao_de_teste

CHAVE = "lgk_demoteste0001_chave-ficticia-so-para-os-testes-da-demo"
SEGREDO = "whsec_segredo-ficticio-so-para-os-testes-da-demo"
RECEPTOR = "http://testserver/demo/webhooks"


@pytest.fixture
def config(url_do_banco: str) -> Configuracao:
    return configuracao_de_teste(
        url_do_banco,
        demo_habilitado=True,
        demo_api_key=CHAVE,
        demo_webhook_segredo=SEGREDO,
        demo_webhook_url=RECEPTOR,
    )


@pytest.fixture
def cliente(app: FastAPI, config: Configuracao) -> Iterator[TestClient]:
    with app.state.recursos.abrir_unidade() as uow:
        semear(uow, config)
    with TestClient(app) as cliente:
        yield cliente


def _enviar(cliente: TestClient, corpo: bytes, assinatura: str | None) -> httpx2.Response:
    cabecalhos = {"Content-Type": "application/json"}
    if assinatura is not None:
        cabecalhos["X-LG-Assinatura"] = assinatura
    return cliente.post("/demo/webhooks", content=corpo, headers=cabecalhos)


def test_pagina_e_servida_com_politica_de_seguranca_restrita(cliente: TestClient) -> None:
    resposta = cliente.get("/demo")
    assert resposta.status_code == 200
    assert "Carteira Exemplo" in resposta.text
    csp = resposta.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    # A página não puxa nada de fora nem tem script embutido.
    assert "http://" not in resposta.text
    assert "https://" not in resposta.text
    assert "<script>" not in resposta.text
    for arquivo in ("demo.css", "demo.js", "fontes/bricolage-grotesque.woff2"):
        assert cliente.get(f"/demo/estatico/{arquivo}").status_code == 200


def test_config_entrega_a_chave_do_parceiro_de_exemplo_e_ela_funciona(
    cliente: TestClient,
) -> None:
    config = cliente.get("/demo/config").json()
    assert config["api_key"] == CHAVE
    assert config["hoje"] == "2026-03-02"
    parceiro = cliente.get("/v1/parceiro", headers={"Authorization": f"Bearer {CHAVE}"}).json()
    assert parceiro["nome"] == "Carteira Exemplo"
    assert parceiro["webhook_url"] == RECEPTOR
    assert parceiro["politica"]["ltv_por_ativo"]["cdb"] == "0.85"


def test_seed_pode_rodar_varias_vezes_sem_duplicar(app: FastAPI, config: Configuracao) -> None:
    with app.state.recursos.abrir_unidade() as uow:
        primeiro = semear(uow, config)
    with app.state.recursos.abrir_unidade() as uow:
        segundo = semear(uow, config)
    assert primeiro == segundo


def test_receptor_aceita_so_webhook_com_assinatura_valida(cliente: TestClient) -> None:
    conta = "cta_receptor_1"
    bom = json.dumps({"id": "evt_1", "tipo": "compra.aprovada", "conta_id": conta}).encode()
    assert _enviar(cliente, bom, assinar(SEGREDO, bom)).status_code == 204

    recusas = [
        (bom, assinar("whsec_outro-segredo", bom)),  # segredo errado
        (bom + b" ", assinar(SEGREDO, bom)),  # corpo alterado depois de assinado
        (bom, assinar(SEGREDO, bom, int(time.time()) - 3600)),  # entrega velha (replay)
        (bom, None),  # sem assinatura
    ]
    for corpo, assinatura in recusas:
        assert _enviar(cliente, corpo, assinatura).status_code == 401

    recebidos = cliente.get("/demo/webhooks", params={"conta_id": conta}).json()
    assert [r["assinatura_valida"] for r in recebidos] == [True, False, False, False, False]
    assert recebidos[0]["evento"]["tipo"] == "compra.aprovada"
    assert "tolerância" in recebidos[3]["motivo_da_recusa"]
    # A página pede só o que ainda não viu, e só da conta dela.
    depois = cliente.get(
        "/demo/webhooks", params={"conta_id": conta, "depois_de": recebidos[0]["numero"]}
    )
    assert len(depois.json()) == 4
    assert cliente.get("/demo/webhooks", params={"conta_id": "cta_outra"}).json() == []
    grande = _enviar(cliente, b"x" * 70_000, "t=1,v1=abc")
    assert grande.status_code == 413


def test_do_evento_ao_receptor_passando_pelo_worker(
    cliente: TestClient, app: FastAPI, relogio: RelogioFixo, engine: Engine
) -> None:
    """A volta completa da demo: operação -> outbox -> worker -> POST assinado -> receptor."""
    from sqlalchemy import text

    with engine.begin() as conexao:
        conexao.execute(
            text("UPDATE entregas_de_webhook SET estado = 'entregue' WHERE estado = 'pendente'")
        )
    cabecalhos = {"Authorization": f"Bearer {CHAVE}"}
    conta = cliente.post(
        "/v1/contas", json={"cliente_ref": f"demo-{time.time_ns()}"}, headers=cabecalhos
    ).json()["id"]
    cliente.post(
        f"/v1/contas/{conta}/garantias",
        json={"tipo_ativo": "cdb", "valor": "5000.00"},
        headers={**cabecalhos, "Idempotency-Key": f"k-{time.time_ns()}"},
    )
    recursos = app.state.recursos
    # O relógio do teste é fixo em março de 2026; o receptor confere o timestamp contra
    # o relógio de verdade. Para a assinatura valer, o worker assina com a hora real.
    from app.infra.relogio import RelogioDoSistema

    entregador = Entregador(
        recursos.fabrica_de_sessoes,
        recursos.config,
        RelogioDoSistema(),
        cliente_http=httpx2.Client(transport=httpx2.WSGITransport(app=_ponte(cliente))),
    )
    assert relogio.agora() < RelogioDoSistema().agora()
    resultado = entregador.processar_lote()
    assert resultado.entregues == 2

    recebidos = cliente.get("/demo/webhooks", params={"conta_id": conta}).json()
    assert sorted(r["evento"]["tipo"] for r in recebidos) == [
        "garantia.bloqueada",
        "limite.atualizado",
    ]
    assert all(r["assinatura_valida"] for r in recebidos)


def _ponte(cliente: TestClient):  # type: ignore[no-untyped-def]
    """Um WSGI mínimo que repassa a requisição do worker para o app em teste."""

    def aplicacao(ambiente, iniciar_resposta):  # type: ignore[no-untyped-def]
        tamanho = int(ambiente.get("CONTENT_LENGTH") or 0)
        corpo = ambiente["wsgi.input"].read(tamanho)
        cabecalhos = {
            chave[5:].replace("_", "-"): valor
            for chave, valor in ambiente.items()
            if chave.startswith("HTTP_")
        }
        cabecalhos["Content-Type"] = ambiente.get("CONTENT_TYPE", "application/json")
        resposta = cliente.post(ambiente["PATH_INFO"], content=corpo, headers=cabecalhos)
        iniciar_resposta(f"{resposta.status_code} -", [])
        return [resposta.content]

    return aplicacao


def test_painel_de_risco_e_servido_com_a_mesma_politica(cliente: TestClient) -> None:
    resposta = cliente.get("/risco")
    assert resposta.status_code == 200
    assert "Painel de risco da carteira" in resposta.text
    assert "sintética" in resposta.text
    csp = resposta.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp
    assert "script-src 'self'" in csp
    assert "http://" not in resposta.text
    assert "https://" not in resposta.text


def test_pagina_negocio_servida_com_csp(cliente: TestClient) -> None:
    resposta = cliente.get("/negocio")
    assert resposta.status_code == 200
    assert "Modelo econômico" in resposta.text
    assert 'id="aviso-ilustrativo"' in resposta.text
    # A mesma política do /risco, sem afrouxar nada para o download do CSV.
    risco = cliente.get("/risco")
    assert resposta.headers["Content-Security-Policy"] == risco.headers["Content-Security-Policy"]
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert "http://" not in resposta.text
    assert "https://" not in resposta.text
    assert "<script>" not in resposta.text
    for arquivo in ("negocio.css", "negocio.js", "demo.css"):
        assert cliente.get(f"/demo/estatico/{arquivo}").status_code == 200


def test_as_tres_paginas_tem_a_mesma_navegacao(cliente: TestClient) -> None:
    for caminho in ("/demo", "/risco", "/negocio"):
        html = cliente.get(caminho).text
        assert '<nav class="paginas" aria-label="Páginas">' in html
        for destino in ('href="/demo"', 'href="/risco"', 'href="/negocio"'):
            assert destino in html
        assert html.count('aria-current="page"') == 1


def test_sem_demo_habilitada_a_pagina_nao_existe(
    url_do_banco: str, engine: Engine, relogio: RelogioFixo
) -> None:
    app = criar_app(configuracao_de_teste(url_do_banco), engine=engine, relogio=relogio)
    with TestClient(app) as cliente:
        for caminho in (
            "/demo",
            "/demo/config",
            "/demo/webhooks",
            "/demo/estatico/demo.js",
            "/risco",
            "/demo/estatico/risco.js",
            "/negocio",
            "/demo/estatico/negocio.js",
        ):
            assert cliente.get(caminho).status_code == 404
        assert cliente.post("/demo/webhooks", content=b"{}").status_code == 404
