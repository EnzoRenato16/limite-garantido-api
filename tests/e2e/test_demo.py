"""Fluxo inteiro da página /demo num navegador de verdade.

    uv run pytest -m e2e

O teste sobe a API e o worker de webhooks como processos separados, contra um SQLite
temporário, e percorre a mesma história do vídeo: guardar, comprar, compra negada,
queda do ativo, atraso e execução no 7º dia, conferindo os webhooks que chegam.

Variáveis opcionais:
- `E2E_ENDERECO`: em vez de subir tudo, testa um ambiente que já está no ar
  (por exemplo `http://localhost:8000` depois de um `docker compose up`);
- `CHROMIUM_EXECUTAVEL`: caminho de um Chromium já instalado (senão, usa o do Playwright);
- `SALVAR_TELAS`: pasta onde gravar os screenshots (é assim que `docs/img/` é gerada).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest

pytestmark = pytest.mark.e2e

RAIZ = Path(__file__).resolve().parent.parent.parent
TELA = {"width": 1280, "height": 800}


def _porta_livre() -> int:
    with socket.socket() as soquete:
        soquete.bind(("127.0.0.1", 0))
        return int(soquete.getsockname()[1])


@pytest.fixture(scope="module")
def endereco(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    ja_no_ar = os.environ.get("E2E_ENDERECO")
    if ja_no_ar:
        yield ja_no_ar.rstrip("/")
        return
    pasta = tmp_path_factory.mktemp("e2e")
    porta = _porta_livre()
    base = f"http://127.0.0.1:{porta}"
    ambiente = {
        **os.environ,
        "AMBIENTE": "teste",
        "DATABASE_URL": f"sqlite:///{pasta / 'demo.db'}",
        "SANDBOX_HABILITADO": "true",
        "DEMO_HABILITADO": "true",
        "DEMO_API_KEY": "lgk_exemplo000000_chave-ficticia-so-para-a-demonstracao",
        "DEMO_WEBHOOK_SEGREDO": "whsec_exemplo-segredo-ficticio-so-para-a-demonstracao",
        "DEMO_WEBHOOK_URL": f"{base}/demo/webhooks",
        "WEBHOOK_PERMITIR_REDE_INTERNA": "true",
        "WORKER_INTERVALO_SEGUNDOS": "0.2",
        "LOG_NIVEL": "WARNING",
    }
    python = sys.executable
    subprocess.run([python, "-m", "alembic", "upgrade", "head"], cwd=RAIZ, env=ambiente, check=True)  # noqa: S603
    subprocess.run([python, "-m", "app.seed"], cwd=RAIZ, env=ambiente, check=True)  # noqa: S603
    api = subprocess.Popen(  # noqa: S603
        [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(porta)],
        cwd=RAIZ,
        env=ambiente,
    )
    worker = subprocess.Popen([python, "-m", "app.worker"], cwd=RAIZ, env=ambiente)  # noqa: S603
    try:
        limite = time.monotonic() + 30
        while True:
            try:
                if httpx2.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx2.HTTPError:
                pass
            if time.monotonic() > limite or api.poll() is not None:
                raise RuntimeError("A API não subiu para o teste e2e")
            time.sleep(0.2)
        yield base
    finally:
        for processo in (worker, api):
            processo.terminate()
        for processo in (worker, api):
            processo.wait(timeout=15)


def test_historia_completa_na_pagina_de_demonstracao(endereco: str) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    pasta_das_telas = os.environ.get("SALVAR_TELAS")
    erros_de_javascript: list[str] = []

    with sync_api.sync_playwright() as playwright:
        navegador = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_EXECUTAVEL") or None
        )
        # Escala 2 só quando as telas vão para a documentação.
        pagina = navegador.new_page(viewport=TELA, device_scale_factor=2 if pasta_das_telas else 1)
        pagina.on("pageerror", lambda erro: erros_de_javascript.append(str(erro)))
        esperar = sync_api.expect

        def fotografar(nome: str) -> None:
            if pasta_das_telas:
                pagina.wait_for_timeout(700)  # deixa a animação da régua terminar
                pagina.screenshot(path=str(Path(pasta_das_telas) / nome))

        def webhooks(tipo: str):  # type: ignore[no-untyped-def]
            return pagina.locator("#eventos .evento-tipo", has_text=tipo)

        pagina.goto(f"{endereco}/demo")
        esperar(pagina.locator("#tipo-ativo")).to_have_value("cdb")
        esperar(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
        esperar(pagina.locator("#trilho-vazio")).to_be_visible()

        # 1. Guarda R$ 5.000 em CDB: R$ 4.250 de limite (85%).
        pagina.get_by_role("button", name="Guardar como garantia").click()
        esperar(pagina.locator("#resultado-garantia")).to_contain_text(
            "R$ 4.250,00 de limite (85%)"
        )
        esperar(pagina.locator("#disponivel")).to_have_text("R$ 4.250,00")
        esperar(pagina.locator("#valor-garantia")).to_have_text("R$ 5.000,00")
        esperar(pagina.locator("#trilho-vazio")).to_be_hidden()
        esperar(pagina.locator("#garantias li")).to_contain_text("bloqueado como garantia")
        esperar(webhooks("garantia.bloqueada")).to_have_count(1)

        # 2. Compra de R$ 4.000 aprovada; a de R$ 500 é negada com 402.
        pagina.get_by_role("button", name="Comprar", exact=True).click()
        esperar(pagina.locator("#resultado-compra")).to_contain_text("aprovada")
        esperar(pagina.locator("#disponivel")).to_have_text("R$ 250,00")
        pagina.locator("#valor-da-compra").fill("500,00")
        pagina.get_by_role("button", name="Comprar", exact=True).click()
        esperar(pagina.locator("#resultado-compra")).to_contain_text(
            "negada: limite insuficiente (HTTP 402)"
        )
        esperar(webhooks("compra.aprovada")).to_have_count(1)
        esperar(webhooks("compra.negada")).to_have_count(1)
        fotografar("demo-1-limite-e-compras.png")

        # 3. O CDB cai 10%: limite vai a R$ 3.825 e a dívida de R$ 4.000 passa dele.
        pagina.get_by_role("button", name="Derrubar o ativo em 10%").click()
        esperar(pagina.locator("#resultado-queda")).to_contain_text("chamada de margem aberta")
        esperar(pagina.locator("#valor-limite")).to_have_text("R$ 3.825,00")
        esperar(pagina.locator("#valor-deficit")).to_have_text("R$ 175,00")
        esperar(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
        esperar(pagina.locator("#aviso")).to_contain_text("Chamada de margem")
        esperar(webhooks("chamada_de_margem")).to_have_count(1)
        fotografar("demo-2-chamada-de-margem.png")

        # 4. A fatura fecha e atrasa. Até o 6º dia nada é vendido; no 7º, a execução.
        pagina.get_by_role("button", name="Fechar a fatura").click()
        esperar(pagina.locator("#resultado-fatura")).to_contain_text("fechada. Vence hoje")
        passar_dia = pagina.get_by_role("button", name="Passar 1 dia")
        for dia in range(1, 7):
            passar_dia.click()
            esperar(pagina.locator("#dias li.passado")).to_have_count(dia)
            esperar(pagina.locator("#resultado-fatura")).to_contain_text(f"{dia} dia")
        esperar(pagina.locator("#valor-usado")).to_have_text("R$ 4.000,00")
        esperar(webhooks("garantia.executada")).to_have_count(0)

        passar_dia.click()
        esperar(pagina.locator("#resultado-fatura")).to_contain_text(
            "7º dia: a garantia foi executada"
        )
        esperar(pagina.locator("#resultado-fatura")).to_contain_text("R$ 4.000,00")
        esperar(pagina.locator("#valor-usado")).to_have_text("R$ 0,00")
        esperar(pagina.locator("#valor-garantia")).to_have_text("R$ 500,00")  # a sobra
        esperar(pagina.locator("#disponivel")).to_have_text("R$ 425,00")  # 500 x 85%
        esperar(pagina.locator("#garantias li")).to_contain_text(
            "parte vendida para pagar a fatura"
        )
        esperar(webhooks("fatura.em_atraso")).to_have_count(1)
        esperar(webhooks("garantia.executada")).to_have_count(1)
        fotografar("demo-3-execucao-no-setimo-dia.png")

        # Todo webhook que apareceu teve a assinatura HMAC conferida pelo receptor.
        total = pagina.locator("#eventos > li").count()
        assert total >= 10
        esperar(pagina.locator(".assinatura.invalida")).to_have_count(0)
        esperar(pagina.locator(".assinatura", has_text="assinatura HMAC confere")).to_have_count(
            total
        )

        # O JSON entregue está a um clique.
        pagina.locator("#eventos > li").first.locator("summary").click()
        esperar(pagina.locator("#eventos > li").first.locator("pre")).to_contain_text('"tipo"')

        # "Começar de novo" abre outra conta, zerada.
        pagina.get_by_role("button", name="Começar de novo").click()
        esperar(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
        esperar(pagina.locator("#eventos > li")).to_have_count(0)
        navegador.close()

    assert erros_de_javascript == []
