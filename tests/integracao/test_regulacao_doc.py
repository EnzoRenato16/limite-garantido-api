"""docs/regulacao.md sai do catálogo regulatório, e a navegação das páginas é a mesma."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

RAIZ = Path(__file__).resolve().parents[2]
DOCUMENTO = RAIZ / "docs" / "regulacao.md"
ESTATICO = RAIZ / "app" / "api" / "estatico"


def _script() -> ModuleType:
    especificacao = importlib.util.spec_from_file_location(
        "gerar_regulacao_md", RAIZ / "scripts" / "gerar_regulacao_md.py"
    )
    assert especificacao
    assert especificacao.loader
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def test_a_matriz_do_documento_e_a_que_o_script_gera_do_toml() -> None:
    script = _script()
    texto = DOCUMENTO.read_text(encoding="utf-8")
    assert script.bloco_entre_marcadores(texto, "matriz") == script.BLOCOS["matriz"]()


def test_o_vocabulario_do_documento_e_o_que_o_script_gera() -> None:
    script = _script()
    texto = DOCUMENTO.read_text(encoding="utf-8")
    assert script.bloco_entre_marcadores(texto, "vocabulario") == script.gerar_vocabulario()


def test_o_documento_inteiro_esta_em_dia() -> None:
    script = _script()
    texto = DOCUMENTO.read_text(encoding="utf-8")
    assert script.atualizar(texto) == texto


def test_o_documento_traz_o_aviso_e_nenhum_ativo_sem_categoria() -> None:
    texto = DOCUMENTO.read_text(encoding="utf-8")
    assert "O autor não é advogado; a modelagem jurídica precisa ser validada." in texto
    script = _script()
    matriz = script.BLOCOS["matriz"]()
    for linha in matriz.splitlines():
        if linha.startswith("  - "):
            assert re.match(
                r"  - \[(norma|prática de mercado|interpretação|imprensa|não verificado)\] ", linha
            )


@pytest.mark.parametrize("pagina", ["index", "regulacao", "risco", "negocio"])
def test_a_navegacao_tem_os_quatro_links_na_mesma_ordem(pagina: str) -> None:
    html = (ESTATICO / f"{pagina}.html").read_text(encoding="utf-8")
    nav = re.search(r'<nav class="paginas".*?</nav>', html, re.S)
    assert nav
    links = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', nav.group(0))
    assert links == [
        ("/demo", "Demonstração"),
        ("/regulacao", "Regulação"),
        ("/risco", "Risco"),
        ("/negocio", "Negócio"),
    ]
    atual = {"index": "/demo"}.get(pagina, f"/{pagina}")
    assert f'<a href="{atual}" aria-current="page">' in nav.group(0)
