"""O painel /risco num navegador, com os números conferidos contra a API.

    uv run pytest -m e2e

Usa o mesmo ambiente de test_demo.py (API e worker de verdade, SQLite temporário).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.e2e


def _esperar() -> Any:
    return pytest.importorskip("playwright.sync_api").expect


def _fotografar(pagina: Any, nome: str) -> None:
    pasta = os.environ.get("SALVAR_TELAS")
    if pasta:
        pagina.wait_for_timeout(700)
        pagina.screenshot(path=str(Path(pasta) / nome))


CENARIOS = [
    "dia_normal",
    "crash_da_bolsa",
    "alta_de_juros",
    "quebra_de_emissor",
    "agente_perde_participacao",
]


def test_painel_de_risco_mostra_os_numeros_da_api(pagina: Any) -> None:
    esperar = _esperar()
    chave = pagina.request.get(f"{pagina.endereco}/demo/config").json()["api_key"]
    pagina.goto(f"{pagina.endereco}/risco")
    esperar(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    for cenario in CENARIOS:
        pagina.locator(f"#cenario-{cenario}").click()
        esperar(pagina.locator(f"#cenario-{cenario}")).to_have_attribute("aria-pressed", "true")
        api = pagina.request.post(
            f"{pagina.endereco}/v1/risco/simulacoes-de-carteira",
            data={"cenario": cenario},
            headers={"Authorization": f"Bearer {chave}"},
        ).json()
        esperados = {
            "chamada-antes": str(api["antes"]["contas_em_chamada"]),
            "chamada-depois": str(api["depois"]["contas_em_chamada"]),
            "perda-total": api["perda"]["perda_esperada_total"],
            "perda-imediata": api["perda"]["perda_com_execucao_imediata"],
            "perda-externa": api["perda"]["perda_adicional_externa"],
            "limite-antes": api["antes"]["limite_total"],
            "limite-depois": api["depois"]["limite_total"],
            "exposicao": api["perda"]["exposicao"],
        }
        for id_, valor in esperados.items():
            esperar(pagina.locator(f"#{id_}")).to_have_attribute("data-valor", valor)
        # O texto exibido é o mesmo número, formatado em reais.
        total = api["perda"]["perda_esperada_total"]
        centavos = total.split(".")[1]
        esperar(pagina.locator("#perda-total")).to_contain_text(f",{centavos}")
        esperar(pagina.locator("#grafico-ativos g")).to_have_count(
            len(api["concentracao_por_ativo"])
        )
        esperar(pagina.locator("#grafico-onde-esta g")).to_have_count(
            len(api["concentracao_por_onde_esta"])
        )
        esperar(pagina.locator("#aviso-sintetico")).to_contain_text("sintéticos")
        _fotografar(pagina, f"risco-{cenario}.png")
    esperar(pagina.locator("#premissas")).to_contain_text("Agente de custódia B (simulado)")


def test_painel_de_risco_recusa_choque_invalido(pagina: Any) -> None:
    esperar = _esperar()
    pagina.goto(f"{pagina.endereco}/risco")
    esperar(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    pagina.locator("#choque-acoes").fill("-150")
    pagina.get_by_role("button", name="Simular com estes valores").click()
    esperar(pagina.locator("#erro")).not_to_have_text("")
