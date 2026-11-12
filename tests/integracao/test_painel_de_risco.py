"""Painel de risco pela API: carteira sintética, cenários e erros."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from tests.conftest import Parceiro

CAMINHO = "/v1/risco/simulacoes-de-carteira"


def _simular(parceiro: Parceiro, **corpo: Any) -> dict[str, Any]:
    resposta = parceiro.post(CAMINHO, corpo, chave=None)
    assert resposta.status_code == 200, resposta.text
    dados: dict[str, Any] = resposta.json()
    return dados


def test_cenarios_prontos(parceiro: Parceiro) -> None:
    resposta = parceiro.get("/v1/risco/cenarios")
    assert resposta.status_code == 200
    cenarios = resposta.json()
    assert [c["id"] for c in cenarios] == [
        "dia_normal",
        "crash_da_bolsa",
        "alta_de_juros",
        "custodiante_fora_do_ar",
    ]
    crash = cenarios[1]
    assert crash["nome"] == "Crash da bolsa (ações -30%)"
    assert crash["choques"] == {"acoes": "-0.30"}


def test_simulacao_por_cenario_e_sintetica_e_explica_as_premissas(parceiro: Parceiro) -> None:
    resultado = _simular(parceiro, cenario="custodiante_fora_do_ar")
    assert resultado["carteira"]["sintetica"] is True
    assert resultado["carteira"]["tamanho"] == 1000
    premissas = resultado["premissas"]
    assert premissas["custodiantes_indisponiveis"] == ["custodiante_lento"]
    assert premissas["dias_extra_de_execucao"] == 10
    assert premissas["taxa_sem_reforco"] == "0.30"
    perda = resultado["perda"]
    assert Decimal(perda["perda_adicional_externa"]) > 0
    assert Decimal(perda["perda_esperada_total"]) == Decimal(
        perda["perda_com_execucao_imediata"]
    ) + Decimal(perda["perda_adicional_externa"])
    custodiantes = {f["chave"] for f in resultado["concentracao_por_custodiante"]}
    assert custodiantes == {"interna", "custodiante_agil", "custodiante_lento"}
    percentuais = sum(Decimal(f["percentual_antes"]) for f in resultado["concentracao_por_ativo"])
    assert Decimal("99.90") <= percentuais <= Decimal("100.00")


def test_mesma_semente_mesmo_resultado(parceiro: Parceiro) -> None:
    primeiro = _simular(parceiro, cenario="crash_da_bolsa", semente=7)
    assert primeiro == _simular(parceiro, cenario="crash_da_bolsa", semente=7)
    assert primeiro != _simular(parceiro, cenario="crash_da_bolsa", semente=8)


def test_campos_explicitos_mudam_o_cenario(parceiro: Parceiro) -> None:
    resultado = _simular(
        parceiro, cenario="dia_normal", choques={"acoes": "-0.90"}, taxa_sem_reforco="1"
    )
    assert resultado["premissas"]["choques"] == {"acoes": "-0.90"}
    assert resultado["depois"]["contas_em_chamada"] > resultado["antes"]["contas_em_chamada"]


def test_choque_zero_nao_muda_quem_esta_em_chamada(parceiro: Parceiro) -> None:
    resultado = _simular(parceiro, choques={}, taxa_sem_reforco="0.30")
    assert resultado["depois"]["contas_em_chamada"] == resultado["antes"]["contas_em_chamada"]


@pytest.mark.parametrize(
    "corpo",
    [
        {"choques": {"acoes": "-1.01"}},
        {"choques": {"acoes": "1.5"}},
        {"tamanho": 5001},
        {"tamanho": 0},
        {"taxa_sem_reforco": "1.2"},
        {"dias_extra_de_execucao": -1},
        {"cenario": "nao_existe"},
        {"choques": {"bitcoin": "-0.1"}},
    ],
)
def test_entrada_invalida_e_rfc_7807(parceiro: Parceiro, corpo: dict[str, Any]) -> None:
    resposta = parceiro.post(CAMINHO, corpo, chave=None)
    assert resposta.status_code == 422, resposta.text
    assert resposta.headers["content-type"].startswith("application/problem+json")
    assert resposta.json()["codigo"] in {"requisicao_invalida", "valor_invalido"}


def test_painel_exige_api_key(parceiro: Parceiro) -> None:
    assert parceiro.http.post(CAMINHO, json={}).status_code == 401
