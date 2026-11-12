"""Modelo econômico pela API: catálogo, simulação em JSON e CSV, erros RFC 7807."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.api.csv_negocio import neutralizar, numero_csv
from app.dominio.dinheiro import Dinheiro
from tests.conftest import Parceiro

PREMISSAS = "/v1/negocio/premissas"
SIMULACOES = "/v1/negocio/simulacoes"


def _simular(parceiro: Parceiro, **corpo: Any) -> dict[str, Any]:
    resposta = parceiro.post(SIMULACOES, corpo, chave=None)
    assert resposta.status_code == 200, resposta.text
    dados: dict[str, Any] = resposta.json()
    return dados


def _csv(parceiro: Parceiro, **corpo: Any) -> str:
    resposta = parceiro.http.post(
        SIMULACOES, json=corpo, headers={**parceiro.cabecalhos, "Accept": "text/csv"}
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "text/csv; charset=utf-8"
    assert 'filename="negocio.csv"' in resposta.headers["content-disposition"]
    return resposta.content.decode("utf-8")


def test_get_premissas_traz_unidade_faixa_origem(parceiro: Parceiro) -> None:
    resposta = parceiro.get(PREMISSAS)
    assert resposta.status_code == 200
    catalogo = resposta.json()
    assert set(catalogo) == {"premissas", "conjuntos", "cenarios_de_risco", "fontes_de_receita"}
    gasto = next(p for p in catalogo["premissas"] if p["nome"] == "gasto_mensal")
    assert {"nome_exibicao", "tipo", "unidade", "minimo", "maximo", "origem"} <= set(gasto)
    assert isinstance(gasto["minimo"], str)
    base = next(c for c in catalogo["conjuntos"] if c["id"] == "base")
    assert isinstance(base["valores"]["gasto_mensal"], str)
    assert catalogo["cenarios_de_risco"][0]["id"] == "dia_normal"
    assert "intercambio" in catalogo["fontes_de_receita"]


def test_post_base_devolve_linhas_margens_equilibrio_tornado(parceiro: Parceiro) -> None:
    r = _simular(parceiro)
    assert {"id", "origem", "destino", "valor", "formula"} <= set(r["linhas"][0])
    assert set(r["por_cliente_mes"]) == {"plataforma", "parceiro", "arranjo"}
    assert set(r["por_cliente_mes"]["plataforma"]) == {"receita", "custo", "margem"}
    assert set(r["ponto_de_equilibrio"]) == {"clientes", "motivo"}
    assert "valor_base" in r["tornado"][0]
    assert r["aviso"].startswith("Números ilustrativos")
    assert r["perda"]["cenario"]["id"] == r["premissas"]["cenario_de_risco"]
    assert _simular(parceiro, incluir_tornado=False)["tornado"] == []


def test_post_sem_autenticacao_401(cliente: TestClient) -> None:
    assert cliente.post(SIMULACOES, json={}).status_code == 401
    assert cliente.get(PREMISSAS).status_code == 401


def test_premissa_fora_da_faixa_vira_rfc7807(parceiro: Parceiro) -> None:
    resposta = parceiro.post(SIMULACOES, {"premissas": {"ltv_medio": "5"}}, chave=None)
    assert resposta.status_code == 422
    assert resposta.headers["content-type"].startswith("application/problem+json")
    corpo = resposta.json()
    assert corpo["codigo"] == "premissas_invalidas"
    assert corpo["erros"][0]["campo"] == "ltv_medio"


def test_premissa_desconhecida_422(parceiro: Parceiro) -> None:
    resposta = parceiro.post(SIMULACOES, {"premissas": {"inventada": "1"}}, chave=None)
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "requisicao_invalida"
    assert parceiro.post(SIMULACOES, {"conjunto": "nao_existe"}, chave=None).status_code == 422


def test_cenario_desconhecido_422(parceiro: Parceiro) -> None:
    resposta = parceiro.post(SIMULACOES, {"cenario_de_risco": "marte"}, chave=None)
    assert resposta.status_code == 422
    assert resposta.json()["erros"][0]["campo"] == "cenario_de_risco"


def test_premissas_e_receitas_do_corpo_sobrescrevem_o_conjunto(parceiro: Parceiro) -> None:
    r = _simular(
        parceiro,
        premissas={"gasto_mensal": 2000, "ltv_medio": "0.6"},
        receitas_ligadas={"intercambio": False},
    )
    assert r["premissas"]["valores"]["gasto_mensal"] == "2000.00"
    assert "intercambio" not in r["premissas"]["receitas_ligadas"]
    assert all(linha["fonte"] != "intercambio" for linha in r["linhas"])


def test_resposta_rapida(parceiro: Parceiro) -> None:
    _simular(parceiro)  # aquece o cache das taxas de perda
    inicio = time.perf_counter()
    _simular(parceiro)
    assert time.perf_counter() - inicio < 1.0


def test_csv_abre_no_excel(parceiro: Parceiro) -> None:
    texto = _csv(parceiro)
    assert texto.startswith("\ufeffPremissas\r\nnome;valor;unidade;origem\r\n")
    assert "\r\n\r\nLinhas por cliente por mês\r\n" in texto
    assert "\r\n\r\nTornado\r\n" in texto
    assert "\n" not in texto.replace("\r\n", "")
    assert ";0,1365;" in texto
    assert "gasto_mensal;1500,00;" in texto


def test_csv_neutraliza_formula() -> None:
    assert neutralizar('=HYPERLINK("http://x")') == '\'=HYPERLINK("http://x")'
    for inicio in ("+", "-", "@", "\t", "\r"):
        assert neutralizar(inicio + "a").startswith("'")
    assert neutralizar("normal") == "normal"


def test_csv_negativo_continua_numero() -> None:
    assert numero_csv(Dinheiro(-123456)) == "-1234,56"
    assert numero_csv(Decimal("0.1365")) == "0,1365"
    assert numero_csv(1000000) == "1000000"


def test_csv_numeros_batem_com_json(parceiro: Parceiro) -> None:
    corpo = {"premissas": {"gasto_mensal": "3000"}}
    json_ = _simular(parceiro, **corpo)
    linhas = [x.split(";") for x in _csv(parceiro, **corpo).split("\r\n")]
    por_id = {x[0]: x[3] for x in linhas if len(x) == 5 and x[1] and x[2]}
    for linha in json_["linhas"]:
        assert por_id[linha["nome"]] == linha["valor"].replace(".", ",")
    margem = json_["por_cliente_mes"]["plataforma"]["margem"].replace(".", ",")
    assert any(x[:2] == ["Margem por cliente por mês", margem] for x in linhas)


def test_openapi_tem_exemplos_e_descricoes(cliente: TestClient) -> None:
    esquema = cliente.get("/openapi.json").json()
    operacao = esquema["paths"]["/v1/negocio/simulacoes"]["post"]
    assert operacao["summary"]
    assert operacao["description"]
    assert "text/csv" in operacao["responses"]["200"]["content"]
    assert "Negócio" in [t["name"] for t in esquema["tags"]]
    assert "example" in esquema["components"]["schemas"]["SimulacaoDeNegocio"]
    assert "example" in esquema["components"]["schemas"]["ResultadoDoNegocio"]
