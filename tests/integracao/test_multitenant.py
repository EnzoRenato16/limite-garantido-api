"""Autenticação por parceiro e isolamento entre parceiros."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.fabrica import criar_app
from app.infra.relogio import RelogioFixo
from tests.conftest import ADMIN, Parceiro, configuracao_de_teste, criar_parceiro, politica_de_teste


def test_sem_chave_ou_com_chave_errada_e_401(parceiro: Parceiro, cliente: TestClient) -> None:
    sem_cabecalho = cliente.get("/v1/contas")
    assert sem_cabecalho.status_code == 401
    assert sem_cabecalho.headers["WWW-Authenticate"] == "Bearer"
    assert sem_cabecalho.headers["content-type"] == "application/problem+json"
    assert sem_cabecalho.json()["codigo"] == "nao_autenticado"

    prefixo, _, _ = parceiro.api_key.rpartition("_")
    for chave in ("qualquer-coisa", "lgk_naoexiste_segredo", f"{prefixo}_segredo-errado", ""):
        resposta = cliente.get("/v1/contas", headers={"Authorization": f"Bearer {chave}"})
        assert resposta.status_code == 401, chave
    assert cliente.get("/v1/contas", headers={"Authorization": "Basic abc"}).status_code == 401


def test_um_parceiro_nao_enxerga_nem_altera_a_conta_de_outro(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    parceiro.comprar(conta, "100.00")
    intruso = criar_parceiro(cliente)

    # Para o intruso a conta simplesmente não existe: 404, nunca 403.
    leituras = [
        f"/v1/contas/{conta}",
        f"/v1/contas/{conta}/garantias",
        f"/v1/contas/{conta}/compras",
        f"/v1/contas/{conta}/faturas",
        f"/v1/contas/{conta}/lancamentos",
        f"/v1/garantias/{garantia['id']}",
    ]
    for caminho in leituras:
        assert intruso.get(caminho).status_code == 404, caminho
    escritas = [
        (f"/v1/contas/{conta}/garantias", {"tipo_ativo": "cdb", "valor": "1.00"}),
        (f"/v1/contas/{conta}/compras", {"valor": "1.00", "descricao": "x"}),
        (f"/v1/contas/{conta}/pagamentos", {"valor": "1.00"}),
        (f"/v1/contas/{conta}/faturas", {}),
        (f"/v1/garantias/{garantia['id']}/liberacao", None),
        (f"/v1/sandbox/garantias/{garantia['id']}/remarcacao", {"variacao_percentual": "-50"}),
        (f"/v1/sandbox/contas/{conta}/avanco-de-dias", {"dias": 7}),
    ]
    for caminho, corpo in escritas:
        assert intruso.post(caminho, corpo).status_code == 404, caminho

    assert intruso.get("/v1/contas").json()["dados"] == []
    assert intruso.get("/v1/eventos").json()["dados"] == []
    assert intruso.get(f"/v1/eventos?conta_id={conta}").json()["dados"] == []
    # E nada mudou para o dono.
    assert parceiro.limite(conta)["divida_total"] == "100.00"
    assert parceiro.limite(conta)["valor_das_garantias"] == "5000.00"


def test_cada_parceiro_tem_a_sua_politica_de_ltv(cliente: TestClient) -> None:
    conservador = criar_parceiro(
        cliente,
        politica=politica_de_teste(ltv_por_ativo={"cdb": "0.50"}, ordem_de_execucao=["cdb"]),
    )
    padrao = criar_parceiro(cliente, politica=politica_de_teste())
    for parceiro, esperado in ((conservador, "2500.00"), (padrao, "4250.00")):
        conta = parceiro.abrir_conta()
        assert parceiro.bloquear(conta, "cdb", "5000.00")["limite"]["limite_total"] == esperado
    assert conservador.get("/v1/parceiro").json()["politica"]["ltv_por_ativo"] == {"cdb": "0.50"}


def test_politica_alterada_pela_plataforma_vale_na_proxima_consulta(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")
    nova = politica_de_teste(teto_limite_por_cliente="1000.00")
    resposta = cliente.put(f"/v1/admin/parceiros/{parceiro.id}/politica", json=nova, headers=ADMIN)
    assert resposta.status_code == 200
    limite = parceiro.limite(conta)
    assert limite["limite_garantido"] == "4250.00"
    assert limite["limite_total"] == "1000.00"

    invalida = politica_de_teste(ltv_por_ativo={"cdb": "1.50"})
    recusada = cliente.put(
        f"/v1/admin/parceiros/{parceiro.id}/politica", json=invalida, headers=ADMIN
    )
    assert recusada.status_code == 422
    assert recusada.json()["codigo"] == "politica_invalida"
    inexistente = cliente.put("/v1/admin/parceiros/par_x/politica", json=nova, headers=ADMIN)
    assert inexistente.status_code == 404


def test_parceiro_sem_politica_informada_recebe_a_de_referencia(cliente: TestClient) -> None:
    parceiro = criar_parceiro(cliente)
    politica = parceiro.get("/v1/parceiro").json()["politica"]
    assert politica["ltv_por_ativo"]["cdb"] == "0.85"
    assert politica["ltv_por_ativo"]["acoes"] == "0.60"
    assert politica["dias_para_execucao"] == 7
    assert politica["teto_limite_por_cliente"] == "50000.00"


def test_o_banco_guarda_so_o_hash_da_api_key(parceiro: Parceiro, engine: Engine) -> None:
    with engine.connect() as conexao:
        linha = conexao.execute(
            text("SELECT chave_prefixo, chave_hash FROM parceiros WHERE id = :id"),
            {"id": parceiro.id},
        ).one()
    segredo = parceiro.api_key.split("_", 2)[2]
    assert parceiro.api_key.startswith(linha.chave_prefixo)
    assert len(linha.chave_hash) == 64
    assert segredo not in linha.chave_hash
    assert segredo not in linha.chave_prefixo


def test_rotacao_de_chave_invalida_a_anterior(parceiro: Parceiro, cliente: TestClient) -> None:
    antiga = parceiro.api_key
    resposta = cliente.post(f"/v1/admin/parceiros/{parceiro.id}/chave", headers=ADMIN)
    assert resposta.status_code == 200
    nova = resposta.json()["api_key"]
    assert nova != antiga
    assert (
        cliente.get("/v1/contas", headers={"Authorization": f"Bearer {antiga}"}).status_code == 401
    )
    assert cliente.get("/v1/contas", headers={"Authorization": f"Bearer {nova}"}).status_code == 200


def test_api_administrativa_exige_o_token_da_plataforma(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    assert cliente.get("/v1/admin/parceiros").status_code == 401
    errado = cliente.get("/v1/admin/parceiros", headers={"Authorization": "Bearer errado"})
    assert errado.status_code == 401
    # A chave de um parceiro não abre a API administrativa.
    assert cliente.get("/v1/admin/parceiros", headers=parceiro.cabecalhos).status_code == 401
    ok = cliente.get("/v1/admin/parceiros", headers=ADMIN)
    assert ok.status_code == 200
    assert parceiro.id in [p["id"] for p in ok.json()]
    assert "api_key" not in ok.text
    assert "chave_hash" not in ok.text


def test_sem_admin_token_configurado_a_api_administrativa_nao_existe(
    url_do_banco: str, engine: Engine, relogio: RelogioFixo
) -> None:
    app = criar_app(
        configuracao_de_teste(url_do_banco, admin_token=None), engine=engine, relogio=relogio
    )
    with TestClient(app) as cliente:
        for cabecalhos in ({}, ADMIN, {"Authorization": "Bearer "}):
            assert cliente.get("/v1/admin/parceiros", headers=cabecalhos).status_code == 404


@pytest.mark.parametrize("caminho", ["/v1/sandbox/contas/cta_x/avanco-de-dias"])
def test_sandbox_desligado_responde_404(
    caminho: str, url_do_banco: str, engine: Engine, relogio: RelogioFixo
) -> None:
    app = criar_app(
        configuracao_de_teste(url_do_banco, sandbox_habilitado=False),
        engine=engine,
        relogio=relogio,
    )
    with TestClient(app) as cliente:
        parceiro = criar_parceiro(cliente)
        assert parceiro.post(caminho, {"dias": 1}).status_code == 404
