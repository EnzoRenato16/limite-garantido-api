"""Sobe a API e o worker de verdade para os testes de navegador (ver test_demo.py).

Também abre o navegador (`pagina`) para os testes do /risco e do /negocio."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

RAIZ = Path(__file__).resolve().parent.parent.parent
TELA = {"width": 1280, "height": 800}


def _porta_livre() -> int:
    with socket.socket() as soquete:
        soquete.bind(("127.0.0.1", 0))
        return int(soquete.getsockname()[1])


@pytest.fixture(scope="session")
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
        "CUSTODIA_INTERVALO_SEGUNDOS": "0.5",
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
