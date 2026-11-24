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
        "quebra_de_emissor",
        "agente_perde_participacao",
    ]
    crash = cenarios[1]
    assert crash["nome"] == "Crash da bolsa (ações, FII e ETF -30%)"
    assert crash["choques"] == {"acoes": "-0.30", "fii": "-0.30", "etf": "-0.30"}
    assert cenarios[3]["emissores_em_quebra"] == ["conglomerado_b"]
    assert cenarios[4]["atraso_por_onde_esta"] == {"agente_td_b": 14}


def test_simulacao_por_cenario_e_sintetica_e_explica_as_premissas(parceiro: Parceiro) -> None:
    resultado = _simular(parceiro, cenario="agente_perde_participacao")
    assert resultado["carteira"]["sintetica"] is True
    assert resultado["carteira"]["tamanho"] == 1000
    premissas = resultado["premissas"]
    assert premissas["custodiantes_indisponiveis"] == []
    assert premissas["atraso_por_onde_esta"] == {"agente_td_b": 14}
    assert premissas["dias_extra_de_execucao"] == 0
    assert premissas["taxa_sem_reforco"] == "0.30"
    perda = resultado["perda"]
    assert Decimal(perda["perda_adicional_externa"]) >= 0
    assert Decimal(perda["perda_esperada_total"]) == Decimal(
        perda["perda_com_execucao_imediata"]
    ) + Decimal(perda["perda_adicional_externa"])
    onde = {f["chave"] for f in resultado["concentracao_por_onde_esta"]}
    assert onde == {
        "casa",
        "agente_td_a",
        "agente_td_b",
        "entidade_operadora_simulada",
    }
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


def test_aviso_sintetico_e_sem_perda_zero(parceiro: Parceiro) -> None:
    resultado = _simular(parceiro, cenario="quebra_de_emissor")
    assert "sintéticos" in resultado["aviso_sintetico"]
    texto = str(resultado).lower() + str(parceiro.get("/v1/risco/cenarios").json()).lower()
    assert "perda zero" not in texto
    assert "risco zero" not in texto
    assert "reduz a perda" in resultado["regulatorio"]["avisos_de_risco"]
    assert "diário" in resultado["regulatorio"]["marcacao_a_mercado"] or (
        "Todo dia" in resultado["regulatorio"]["marcacao_a_mercado"]
    )


def test_painel_regulatorio_haircut_concentracao_e_provisao(parceiro: Parceiro) -> None:
    regulatorio = _simular(parceiro, cenario="dia_normal")["regulatorio"]
    # Haircut por classe e prazo: é o complemento do LTV do catálogo.
    haircuts = [h for h in regulatorio["haircut_por_classe_e_prazo"] if h["tipo_ativo"] == "cdb"]
    assert haircuts == [
        {"tipo_ativo": "cdb", "ate_anos": None, "haircut_minimo": "0.05", "haircut_maximo": "0.15"}
    ]
    assert len({h["tipo_ativo"] for h in regulatorio["haircut_por_classe_e_prazo"]}) >= 8
    # FGC: teto de R$ 250.000,00; excesso só onde o cliente passa dele.
    assert Decimal(regulatorio["teto_do_fgc"]) == Decimal("250000.00")
    conglomerados = regulatorio["concentracao_por_conglomerado"]
    assert {c["conglomerado"] for c in conglomerados} == {f"conglomerado_{x}" for x in "abcd"}
    assert any(c["clientes_acima_do_teto"] > 0 for c in conglomerados)
    for c in conglomerados:
        assert (Decimal(c["excesso_total"]) > 0) == (c["clientes_acima_do_teto"] > 0)
    # Provisão: C2 (CDB, Tesouro e outros), C3 e C5 (conta sem garantia não ocorre aqui).
    carteiras = {p["carteira"]: p for p in regulatorio["carteiras_de_provisao"]}
    assert set(carteiras) <= {"c2", "c3", "c5"}
    assert sum(p["contas"] for p in carteiras.values()) == 1000
    for p in carteiras.values():
        assert Decimal(p["provisao_primeiro_mes"]) <= Decimal(p["provisao_decimo_terceiro_mes"])
