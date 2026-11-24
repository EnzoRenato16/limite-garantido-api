"""A matriz regulatória pública e a política do parceiro validada contra o catálogo."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.matriz import matriz_do_catalogo
from app.infra.regulacao import carregar_catalogo
from tests.conftest import ADMIN, Parceiro, criar_parceiro, politica_de_teste


def test_matriz_e_a_serializacao_do_catalogo_e_nao_exige_chave(cliente: TestClient) -> None:
    resposta = cliente.get("/v1/regulacao/matriz")
    assert resposta.status_code == 200
    assert resposta.json() == matriz_do_catalogo(carregar_catalogo())
    matriz = resposta.json()
    assert set(matriz["trilhos"]) == {
        "casa",
        "portabilidade_cvm_210",
        "transferencia_de_custodia",
        "td_garantia",
        "res_conjunta_12",
    }
    previdencia = matriz["ativos"]["previdencia"]
    assert previdencia["produtos"] == ["emprestimo"]
    assert previdencia["retencao"]["regra"] == "ja_liquido"
    assert matriz["trilhos"]["portabilidade_cvm_210"]["prazos_por_ativo"]["fundo_di"] == 9
    categorias = {a["categoria"] for a in matriz["afirmacoes"]}
    assert categorias <= {
        "norma",
        "pratica_de_mercado",
        "interpretacao",
        "imprensa",
        "nao_verificado",
    }
    # Afirmação de categoria norma sempre traz norma e artigo.
    for ativo in matriz["ativos"].values():
        for afirmacao in ativo["afirmacoes"]:
            if afirmacao["categoria"] == "norma":
                assert afirmacao["norma"]
                assert afirmacao["artigo"]


def test_openapi_documenta_trilho_prazo_e_norma_de_cada_rota(cliente: TestClient) -> None:
    caminhos = cliente.get("/openapi.json").json()["paths"]
    portabilidade = caminhos["/v1/contas/{conta_id}/portabilidades"]["post"]["description"]
    assert "Res. CVM 210" in portabilidade
    assert "art. 16" in portabilidade
    assert "2 dias úteis" in portabilidade
    previdencia = caminhos["/v1/contas/{conta_id}/garantias/previdencia"]["post"]["description"]
    assert "Res. Conjunta 12" in previdencia
    assert "art. 18" in previdencia
    tesouro = caminhos["/v1/contas/{conta_id}/garantias/tesouro"]["post"]["description"]
    assert "Selic" in tesouro
    assert "/v1/regulacao/matriz" in caminhos
    assert "post" in caminhos["/v1/contas/{conta_id}/emprestimos"]
    for caminho in (
        "/v1/contas/{conta_id}/portabilidades",
        "/v1/contas/{conta_id}/transferencias-de-custodia",
        "/v1/contas/{conta_id}/garantias/tesouro",
        "/v1/contas/{conta_id}/garantias/previdencia",
        "/v1/contas/{conta_id}/emprestimos",
    ):
        parametros = {p["name"] for p in caminhos[caminho]["post"]["parameters"]}
        assert "Idempotency-Key" in parametros, caminho


def test_politica_de_parceiro_recusa_coe_e_ltv_acima_da_faixa(
    cliente: TestClient, parceiro: Parceiro
) -> None:
    caminho = f"/v1/admin/parceiros/{parceiro.id}/politica"
    coe = politica_de_teste(
        ltv_por_ativo={"coe": "0.10", "cdb": "0.85"}, ordem_de_execucao=["cdb", "coe"]
    )
    recusada = cliente.put(caminho, json=coe, headers=ADMIN)
    assert recusada.status_code == 422
    assert recusada.json()["codigo"] == "politica_ativo_nao_recomendado"

    alta = politica_de_teste(ltv_por_ativo={"acoes": "0.71"}, ordem_de_execucao=["acoes"])
    recusada = cliente.put(caminho, json=alta, headers=ADMIN)
    assert recusada.status_code == 422
    assert recusada.json()["codigo"] == "politica_ltv_acima_da_faixa"
    assert recusada.json()["ltv_maximo"] == "0.70"

    no_topo = politica_de_teste(ltv_por_ativo={"acoes": "0.70"}, ordem_de_execucao=["acoes"])
    assert cliente.put(caminho, json=no_topo, headers=ADMIN).status_code == 200


def test_criar_parceiro_tambem_valida_a_politica(cliente: TestClient) -> None:
    resposta = cliente.post(
        "/v1/admin/parceiros",
        json={
            "nome": "Parceiro com LTV alto",
            "politica": politica_de_teste(
                ltv_por_ativo={"acoes": "0.99"}, ordem_de_execucao=["acoes"]
            ),
        },
        headers=ADMIN,
    )
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "politica_ltv_acima_da_faixa"
    assert criar_parceiro(cliente).id
