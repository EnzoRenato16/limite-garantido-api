"""Peças comuns dos testes de navegador da página /demo."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.dominio.calendario import DiasUteis
from app.infra.calendario import carregar_calendario

ESPERA_DO_WORKER_EM_MS = 15_000


def esperar() -> Any:
    return pytest.importorskip("playwright.sync_api").expect


def fotografar(pagina: Any, nome: str) -> None:
    pasta = os.environ.get("SALVAR_TELAS")
    if pasta:
        pagina.wait_for_timeout(700)  # deixa a animação da régua terminar
        pagina.screenshot(path=str(Path(pasta) / nome))


def abrir_demo(pagina: Any) -> None:
    """Abre a /demo e começa de novo, para cada teste ter a sua conta."""
    expect = esperar()
    pagina.goto(f"{pagina.endereco}/demo")
    expect(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    anterior = pagina.locator("#conta-atual").text_content()
    pagina.get_by_role("button", name="Começar de novo").click()
    # "R$ 0,00" não serve de sinal: a conta anterior também podia estar zerada.
    expect(pagina.locator("#conta-atual")).not_to_have_text(anterior or "")
    expect(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")


def webhook(pagina: Any, tipo: str) -> Any:
    return pagina.locator("#eventos .evento-tipo", has_text=re.compile(rf"^{re.escape(tipo)}$"))


def hoje_do_servidor(pagina: Any) -> date:
    return date.fromisoformat(pagina.request.get(f"{pagina.endereco}/demo/config").json()["hoje"])


def somar_dias_uteis(inicio: date, dias: int) -> date:
    return carregar_calendario().somar(inicio, DiasUteis(dias))


def em_formato_de_tela(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def pedir_de_fora(
    pagina: Any,
    trilho: str,
    *,
    ativo: str | None = None,
    produto: str | None = None,
    contraparte: str | None = None,
    valor: str | None = None,
) -> None:
    """Preenche o formulário de "Trazer de fora" e pede a entrada."""
    pagina.locator("#trilho-de-entrada").select_option(trilho)
    if ativo:
        pagina.locator("#ativo-de-fora").select_option(ativo)
    if produto:
        pagina.locator("#produto-de-fora").select_option(produto)
    if contraparte:
        pagina.locator("#contraparte").select_option(label=contraparte)
    if valor:
        pagina.locator("#valor-de-fora").fill(valor)
    pagina.get_by_role("button", name="Pedir a entrada").click()


def contratar(pagina: Any, valor: str = "2.000,00", parcelas: str = "3") -> None:
    pagina.locator("#valor-do-emprestimo").fill(valor)
    pagina.locator("#parcelas").select_option(parcelas)
    pagina.get_by_role("button", name="Contratar empréstimo").click()
