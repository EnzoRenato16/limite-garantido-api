"""Fase 2 (custodiante externo simulado) no /demo e o painel /risco, num navegador.

    uv run pytest -m e2e

Usa o mesmo ambiente de test_demo.py (API e worker de verdade, SQLite temporário).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.e2e

TELA = {"width": 1280, "height": 800}
ESPERA_DO_WORKER_EM_MS = 15_000


@pytest.fixture
def pagina(endereco: str) -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    pasta_das_telas = os.environ.get("SALVAR_TELAS")
    erros: list[str] = []
    with sync_api.sync_playwright() as playwright:
        navegador = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_EXECUTAVEL") or None
        )
        pagina = navegador.new_page(viewport=TELA, device_scale_factor=2 if pasta_das_telas else 1)
        pagina.on("pageerror", lambda erro: erros.append(str(erro)))
        pagina.endereco = endereco  # type: ignore[attr-defined]
        yield pagina
        navegador.close()
    assert erros == [], f"Erros de JavaScript na página: {erros}"


def _esperar() -> Any:
    return pytest.importorskip("playwright.sync_api").expect


def _fotografar(pagina: Any, nome: str) -> None:
    pasta = os.environ.get("SALVAR_TELAS")
    if pasta:
        pagina.wait_for_timeout(700)
        pagina.screenshot(path=str(Path(pasta) / nome))


def _abrir_demo(pagina: Any) -> None:
    pagina.goto(f"{pagina.endereco}/demo")
    _esperar()(pagina.locator("#tipo-ativo")).to_have_value("cdb")
    # Conta nova a cada teste: a sessão do navegador é a mesma do servidor.
    pagina.get_by_role("button", name="Começar de novo").click()
    _esperar()(pagina.locator("#disponivel")).to_have_text("R$ 0,00")


def _guardar_externa(pagina: Any, custodiante: str, valor: str = "10.000,00") -> Any:
    pagina.locator("#origem").select_option("externa")
    pagina.locator("#custodiante").select_option(custodiante)
    pagina.locator("#tipo-ativo").select_option("tesouro_selic")
    pagina.locator("#valor-da-garantia").fill(valor)
    pagina.get_by_role("button", name="Guardar como garantia").click()
    _esperar()(pagina.locator("#resultado-garantia")).to_contain_text("O limite só aparece")
    return pagina.locator("#garantias > li").last


def _webhook(pagina: Any, tipo: str) -> Any:
    return pagina.locator("#eventos .evento-tipo", has_text=re.compile(rf"^{re.escape(tipo)}$"))


def test_fase_2_com_confirmacao_e_resgate(pagina: Any) -> None:
    esperar = _esperar()
    _abrir_demo(pagina)
    garantia = _guardar_externa(pagina, "custodiante_lento")

    # O worker envia o pedido; o custodiante lento leva horas. Nenhum limite ainda.
    esperar(garantia).to_contain_text("custodiante analisando", timeout=ESPERA_DO_WORKER_EM_MS)
    esperar(garantia.locator(".linha-do-tempo .atual")).to_have_text("Custodiante analisando")
    esperar(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    esperar(_webhook(pagina, "gravame.solicitado")).to_have_count(1)
    _fotografar(pagina, "fase2-1-gravame-pendente.png")

    garantia.get_by_role("button", name="Custodiante confirma").click()
    esperar(pagina.locator("#disponivel")).to_have_text("R$ 9.500,00")
    esperar(garantia).to_contain_text("bloqueado como garantia")
    esperar(garantia.locator(".linha-do-tempo .feito")).to_have_count(3)
    esperar(_webhook(pagina, "gravame.confirmado")).to_have_count(1)
    esperar(_webhook(pagina, "garantia.bloqueada")).to_have_count(1)
    _fotografar(pagina, "fase2-2-gravame-confirmado.png")

    # Resgate externo: o limite sai já no pedido.
    garantia.get_by_role("button", name="Resgatar").click()
    esperar(pagina.locator("#resultado-garantia")).to_contain_text("pedido ao custodiante")
    esperar(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    garantia.get_by_role("button", name="Custodiante confirma").click()
    esperar(garantia).to_contain_text("devolvido ao cliente")
    esperar(_webhook(pagina, "liberacao.confirmada")).to_have_count(1)


def test_fase_2_o_custodiante_agil_confirma_sozinho(pagina: Any) -> None:
    esperar = _esperar()
    _abrir_demo(pagina)
    garantia = _guardar_externa(pagina, "custodiante_agil", "2.000,00")
    # Ninguém clica: o worker consulta o custodiante (polling) e aplica a confirmação.
    esperar(pagina.locator("#disponivel")).to_have_text(
        "R$ 1.900,00", timeout=ESPERA_DO_WORKER_EM_MS
    )
    esperar(garantia).to_contain_text("bloqueado como garantia")


def test_fase_2_com_recusa(pagina: Any) -> None:
    esperar = _esperar()
    _abrir_demo(pagina)
    garantia = _guardar_externa(pagina, "custodiante_lento")
    garantia.get_by_role("button", name="Custodiante recusa").click()
    esperar(garantia).to_contain_text("recusado pelo custodiante")
    esperar(garantia.locator(".linha-do-tempo .falhou")).to_have_text("Recusado")
    esperar(garantia.get_by_role("button", name="Custodiante confirma")).to_have_count(0)
    esperar(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    esperar(_webhook(pagina, "gravame.recusado")).to_have_count(1)
    _fotografar(pagina, "fase2-3-gravame-recusado.png")


def test_fase_2_com_timeout(pagina: Any) -> None:
    esperar = _esperar()
    _abrir_demo(pagina)
    garantia = _guardar_externa(pagina, "custodiante_lento")
    garantia.get_by_role("button", name="Custodiante não responde").click()
    esperar(garantia).to_contain_text("não respondeu no prazo")
    esperar(garantia.locator(".linha-do-tempo .falhou")).to_have_text("Sem resposta no prazo")
    esperar(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    esperar(_webhook(pagina, "gravame.expirado")).to_have_count(1)


def test_fase_2_execucao_externa(pagina: Any) -> None:
    esperar = _esperar()
    _abrir_demo(pagina)
    garantia = _guardar_externa(pagina, "custodiante_lento")
    garantia.get_by_role("button", name="Custodiante confirma").click()
    esperar(pagina.locator("#disponivel")).to_have_text("R$ 9.500,00")

    pagina.locator("#valor-da-compra").fill("5.000,00")
    pagina.get_by_role("button", name="Comprar", exact=True).click()
    esperar(pagina.locator("#resultado-compra")).to_contain_text("aprovada")
    pagina.get_by_role("button", name="Fechar a fatura").click()
    esperar(pagina.locator("#resultado-fatura")).to_contain_text("fechada")
    for _ in range(7):
        pagina.get_by_role("button", name="Passar 1 dia").click()
        pagina.wait_for_timeout(150)
    esperar(pagina.locator("#resultado-fatura")).to_contain_text("pedida ao custodiante")
    esperar(garantia).to_contain_text("venda pedida ao custodiante")
    esperar(_webhook(pagina, "execucao.solicitada")).to_have_count(1)
    _fotografar(pagina, "fase2-4-execucao-pedida.png")

    garantia.get_by_role("button", name="Custodiante confirma").click()
    esperar(_webhook(pagina, "execucao.confirmada")).to_have_count(1)
    esperar(pagina.locator("#valor-usado")).not_to_have_text("R$ 5.000,00")


def test_aviso_antigo_some_quando_o_estado_muda(pagina: Any) -> None:
    """A mensagem de chamada de margem não pode continuar na tela depois da execução."""
    esperar = _esperar()
    _abrir_demo(pagina)
    pagina.get_by_role("button", name="Guardar como garantia").click()  # CDB 5.000
    esperar(pagina.locator("#disponivel")).to_have_text("R$ 4.250,00")
    pagina.get_by_role("button", name="Comprar", exact=True).click()  # 4.000
    pagina.get_by_role("button", name="Derrubar o ativo em 10%").click()
    esperar(pagina.locator("#resultado-queda")).to_contain_text("chamada de margem aberta")
    esperar(pagina.locator("#aviso")).to_contain_text("Chamada de margem")

    pagina.get_by_role("button", name="Fechar a fatura").click()
    for _ in range(7):
        pagina.get_by_role("button", name="Passar 1 dia").click()
        pagina.wait_for_timeout(150)
    esperar(pagina.locator("#resultado-fatura")).to_contain_text("executada")
    esperar(pagina.locator("#resultado-queda")).to_have_text("")
    esperar(pagina.locator("#aviso")).not_to_contain_text("Chamada de margem")


def test_resumo_do_topo_fica_visivel_ao_rolar_no_celular(pagina: Any) -> None:
    esperar = _esperar()
    pagina.set_viewport_size({"width": 390, "height": 844})
    _abrir_demo(pagina)
    pagina.locator("#fechar-fatura").scroll_into_view_if_needed()
    esperar(pagina.locator("#disponivel")).to_be_in_viewport()
    esperar(pagina.locator("#valor-limite")).to_be_in_viewport()
    esperar(pagina.locator("#valor-usado")).to_be_in_viewport()


CENARIOS = ["dia_normal", "crash_da_bolsa", "alta_de_juros", "custodiante_fora_do_ar"]


def test_painel_de_risco_mostra_os_numeros_da_api(pagina: Any) -> None:
    esperar = _esperar()
    chave = pagina.request.get(f"{pagina.endereco}/demo/config").json()["api_key"]
    pagina.goto(f"{pagina.endereco}/risco")
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
        esperar(pagina.locator("#grafico-custodiantes g")).to_have_count(
            len(api["concentracao_por_custodiante"])
        )
        _fotografar(pagina, f"risco-{cenario}.png")
    esperar(pagina.locator("#premissas")).to_contain_text("Custodiante Lento")


def test_painel_de_risco_recusa_choque_invalido(pagina: Any) -> None:
    esperar = _esperar()
    pagina.goto(f"{pagina.endereco}/risco")
    esperar(pagina.locator("#cenario-dia_normal")).to_have_attribute("aria-pressed", "true")
    pagina.locator("#choque-acoes").fill("-150")
    pagina.get_by_role("button", name="Simular com estes valores").click()
    esperar(pagina.locator("#erro")).not_to_have_text("")
