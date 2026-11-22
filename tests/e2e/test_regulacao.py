"""A página /regulacao num navegador: cada célula da tabela é o valor do catálogo.

    uv run pytest -m e2e

A expectativa vem de `scripts/gerar_regulacao_md.py` (`celulas_do_ativo`), a mesma lógica
que gera o documento; o que a página desenha vem da API, em JavaScript.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.e2e

RAIZ = Path(__file__).resolve().parents[2]


def _script() -> Any:
    especificacao = importlib.util.spec_from_file_location(
        "gerar_regulacao_md", RAIZ / "scripts" / "gerar_regulacao_md.py"
    )
    assert especificacao
    assert especificacao.loader
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def _esperar() -> Any:
    return pytest.importorskip("playwright.sync_api").expect


def test_cada_celula_da_tabela_e_o_valor_do_catalogo(pagina: Any) -> None:
    esperar = _esperar()
    script = _script()
    pagina.goto(f"{pagina.endereco}/regulacao")
    esperar(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    matriz = pagina.request.get(f"{pagina.endereco}/v1/regulacao/matriz").json()

    esperar(pagina.locator("#corpo-matriz > tr")).to_have_count(len(matriz["ativos"]))
    for tipo, ativo in matriz["ativos"].items():
        linha = pagina.locator(f'#corpo-matriz > tr[data-ativo="{tipo}"]')
        esperar(linha.locator("th").first).to_contain_text(ativo["nome"])
        for coluna, celula in script.celulas_do_ativo(matriz, tipo).items():
            td = linha.locator(f'td[data-coluna="{coluna}"]')
            if celula["valor"]:
                esperar(td.locator(".valor")).to_have_text(celula["valor"])
            else:
                esperar(td.locator(".valor")).to_have_count(0)
            if coluna == "norma":
                esperar(td.locator("li")).to_have_text([i["texto"] for i in celula["itens"]])
                continue
            esperar(td.locator("li .texto")).to_have_text([i["texto"] for i in celula["itens"]])
            categorias = [i["categoria"] for i in celula["itens"]]
            atuais = [li.get_attribute("data-categoria") for li in td.locator("li").all()]
            assert atuais == categorias, (tipo, coluna)


def test_a_pagina_mostra_os_selos_o_aviso_e_a_navegacao(pagina: Any) -> None:
    esperar = _esperar()
    pagina.goto(f"{pagina.endereco}/regulacao")
    esperar(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    esperar(pagina.locator("#aviso-juridico")).to_contain_text(
        "O autor não é advogado; a modelagem jurídica precisa ser validada."
    )
    esperar(pagina.locator("#legenda-selos .selo")).to_have_text(
        ["norma", "prática de mercado", "interpretação", "imprensa", "não verificado"]
    )
    esperar(pagina.locator("nav.paginas a")).to_have_text(
        ["Demonstração", "Regulação", "Risco", "Negócio"]
    )
    esperar(pagina.locator('nav.paginas a[aria-current="page"]')).to_have_text("Regulação")
    # Um selo de cada categoria aparece na matriz: o catálogo usa as cinco.
    for categoria in ("norma", "pratica_de_mercado", "interpretacao", "nao_verificado"):
        esperar(pagina.locator(f"#matriz .selo-{categoria}").first).to_be_visible()
