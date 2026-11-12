"""Modelo econômico pela API: catálogo, simulação em JSON e CSV, erros RFC 7807."""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.csv_negocio import gerar_csv, neutralizar, numero_csv
from app.api.fabrica import criar_app
from app.api.rotas import negocio as rota_de_negocio
from app.config import Configuracao
from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.analises import MOTIVO_SEM_GRAVAME, simular_negocio
from app.dominio.economia.premissas import NomeDaPremissa
from app.dominio.erros import ValorInvalido
from app.infra import economia
from app.infra.relogio import RelogioFixo
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


def test_toml_quebrado_falha_ao_subir_a_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: Configuracao,
    engine: Engine,
    relogio: RelogioFixo,
) -> None:
    # Arquivo quebrado é erro de implantação: a API nem sobe, em vez de dar 500 depois.
    quebrado = tmp_path / "premissas.toml"
    quebrado.write_text('[conjuntos.base]\nnome = "Base"\n', encoding="utf-8")
    monkeypatch.setattr(economia, "ARQUIVO_PADRAO", quebrado)
    economia.carregar_parametros_da_economia.cache_clear()
    try:
        with pytest.raises(economia.ErroNoArquivoDePremissas, match="premissas"):
            criar_app(config, engine=engine, relogio=relogio)
    finally:
        economia.carregar_parametros_da_economia.cache_clear()


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
    tempos = []
    for _ in range(3):  # o menor de três: uma máquina ocupada não derruba o teste
        inicio = time.perf_counter()
        _simular(parceiro)
        tempos.append(time.perf_counter() - inicio)
    assert min(tempos) < 1.0


def test_erro_interno_do_calculo_e_500_e_nao_culpa_quem_pediu(
    parceiro: Parceiro, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Uma invariante quebrada dentro do domínio (ValorInvalido) não é entrada inválida.
    def explode(*_: object, **__: object) -> None:
        raise ValorInvalido("Fração não pode passar de 1")

    monkeypatch.setattr(rota_de_negocio, "simular_negocio", explode)
    resposta = parceiro.post(SIMULACOES, {}, chave=None)
    assert resposta.status_code == 500
    assert resposta.json()["codigo"] == "erro_interno"
    assert "Fração" not in resposta.text


def test_perda_sem_sentido_e_500(parceiro: Parceiro, monkeypatch: pytest.MonkeyPatch) -> None:
    def sem_exposicao(_: str) -> None:
        raise economia.ErroNaPerdaDoCenario("exposição zero")

    monkeypatch.setattr(rota_de_negocio, "taxas_de_perda", sem_exposicao)
    resposta = parceiro.post(SIMULACOES, {}, chave=None)
    assert resposta.status_code == 500
    assert resposta.json()["codigo"] == "erro_interno"


def test_limites_do_corpo(parceiro: Parceiro) -> None:
    # 34 premissas de verdade mais 7 inventadas: 41 chaves.
    premissas = {nome.value: "1" for nome in NomeDaPremissa}
    premissas |= {f"inventada_{i}": "1" for i in range(7)}
    assert len(premissas) == 41
    resposta = parceiro.post(SIMULACOES, {"premissas": premissas}, chave=None)
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "requisicao_invalida"
    for valor in ("NaN", "1e9999", "Infinity"):
        resposta = parceiro.post(SIMULACOES, {"premissas": {"gasto_mensal": valor}}, chave=None)
        assert resposta.status_code == 422, valor
    grande = {"conjunto": "base", "lixo": "x" * 70_000}
    resposta = parceiro.post(SIMULACOES, grande, chave=None)
    assert resposta.status_code == 413


@pytest.mark.parametrize(
    ("aceitar", "csv"),
    [
        ("text/csv", True),
        ("Text/CSV", True),
        ("text/csv;q=0", False),
        ("text/csv;q=0, application/json", False),
        ("application/json, text/csv;q=0.1", False),
        ("text/csv, application/json;q=0.9", True),
        ("application/json;q=0.5, text/csv;q=0.5", True),
        ("text/csv;q=0.5, */*", False),
        ("text/csv, */*;q=0.1", True),
        ("text/csv;q=abc", False),
        ("application/json", False),
        ("*/*", False),
        ("", False),
    ],
)
def test_accept_escolhe_csv_pelo_q(parceiro: Parceiro, aceitar: str, csv: bool) -> None:
    resposta = parceiro.http.post(
        SIMULACOES,
        json={"incluir_tornado": False},
        headers={**parceiro.cabecalhos, "Accept": aceitar},
    )
    assert resposta.status_code == 200, resposta.text
    tipo = resposta.headers["content-type"]
    assert tipo.startswith("text/csv" if csv else "application/json"), aceitar
    assert "Accept" in resposta.headers["vary"]


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


def test_interna_x_externa_traz_valor_ou_motivo(parceiro: Parceiro) -> None:
    base = _simular(parceiro, incluir_tornado=False)["interna_x_externa"]
    assert base["fee_de_gravame_externo_para_empatar"] is not None
    assert base["motivo_fee"] is None
    assert base["gasto_para_empatar"] is not None
    assert base["motivo_gasto"] is None

    sem_gravame = _simular(parceiro, premissas={"gravames_ao_mes": "0"}, incluir_tornado=False)[
        "interna_x_externa"
    ]
    assert sem_gravame["fee_de_gravame_externo_para_empatar"] is None
    assert sem_gravame["motivo_fee"] == MOTIVO_SEM_GRAVAME

    linhas = _csv(parceiro, premissas={"gravames_ao_mes": "0"}, incluir_tornado=False)
    assert f"Por que não há fee para empatar;{MOTIVO_SEM_GRAVAME};;\r\n" in linhas
    assert "Por que não há gasto para empatar;;;\r\n" in linhas


def test_csv_margem_negativa_sem_apostrofo(parceiro: Parceiro) -> None:
    corpo = {"conjunto": "conservador", "incluir_tornado": False}
    margem = _simular(parceiro, **corpo)["por_cliente_mes"]["plataforma"]["margem"]
    assert margem.startswith("-")
    texto = _csv(parceiro, **corpo)
    assert f"\r\nMargem por cliente por mês;{margem.replace('.', ',')};" in texto
    assert ";'-" not in texto


def test_csv_neutraliza_texto_que_comeca_com_gatilho(parceiro: Parceiro) -> None:
    # Nenhum texto do TOML começa com gatilho de fórmula hoje; o teste troca dois textos
    # do esquema e confere a linha do CSV inteira.
    parametros = economia.carregar_parametros_da_economia()
    premissas = economia.premissas_do_conjunto("base")
    resultado = simular_negocio(
        premissas, economia.taxas_de_perda(premissas.cenario_de_risco), parametros.esquema
    )
    esquema = dict(parametros.esquema)
    gasto = esquema[NomeDaPremissa.gasto_mensal]
    esquema[NomeDaPremissa.gasto_mensal] = replace(
        gasto, origem="=HYPERLINK(1)", nome_exibicao="-Gasto"
    )
    linhas = gerar_csv(resultado, esquema).decode("utf-8").split("\r\n")
    assert "gasto_mensal;1500,00;R$/mês;'=HYPERLINK(1)" in linhas
    assert any(b.premissa is NomeDaPremissa.gasto_mensal for b in resultado.tornado)
    assert any(linha.startswith("'-Gasto;") for linha in linhas)


def test_openapi_documenta_receitas_ligadas(cliente: TestClient) -> None:
    esquema = cliente.get("/openapi.json").json()
    campo = esquema["components"]["schemas"]["SimulacaoDeNegocio"]["properties"]["receitas_ligadas"]
    assert "Sobrescreve só as fontes enviadas" in campo["description"]
    assert "premissas de fonte desligada não entram na conta" in campo["description"]


def test_openapi_tem_exemplos_e_descricoes(cliente: TestClient) -> None:
    esquema = cliente.get("/openapi.json").json()
    operacao = esquema["paths"]["/v1/negocio/simulacoes"]["post"]
    assert operacao["summary"]
    assert operacao["description"]
    assert "text/csv" in operacao["responses"]["200"]["content"]
    assert "Negócio" in [t["name"] for t in esquema["tags"]]
    assert "example" in esquema["components"]["schemas"]["SimulacaoDeNegocio"]
    assert "example" in esquema["components"]["schemas"]["ResultadoDoNegocio"]
