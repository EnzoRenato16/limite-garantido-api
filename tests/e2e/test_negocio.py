"""Página /negocio (modelo econômico) e a navegação comum às três páginas, num navegador.

    uv run pytest -m e2e

Usa o mesmo ambiente de test_demo.py (API e worker de verdade, SQLite temporário). Os
números da tela são conferidos contra a própria API: a página só formata o que ela devolve.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest

pytestmark = pytest.mark.e2e

CONJUNTOS = ["conservador", "base", "otimista"]
DESTAQUES = ["margem-plataforma", "margem-parceiro", "equilibrio", "resultado-carteira"]
BOM = b"\xef\xbb\xbf"


def _esperar() -> Any:
    return pytest.importorskip("playwright.sync_api").expect


def _fotografar(pagina: Any, nome: str) -> None:
    pasta = os.environ.get("SALVAR_TELAS")
    if pasta:
        pagina.wait_for_timeout(700)
        pagina.screenshot(path=str(Path(pasta) / nome), full_page=True)


def _abrir(pagina: Any) -> None:
    pagina.goto(f"{pagina.endereco}/negocio")
    _esperar()(pagina.locator("body[data-pronta=true]")).to_have_count(1)


def _api(endereco: str, corpo: dict[str, Any], aceitar: str = "application/json") -> Any:
    chave = httpx2.get(f"{endereco}/demo/config", timeout=10).json()["api_key"]
    return httpx2.post(
        f"{endereco}/v1/negocio/simulacoes",
        json=corpo,
        headers={"Authorization": f"Bearer {chave}", "Accept": aceitar},
        timeout=30,
    )


def _esperados(resultado: dict[str, Any]) -> dict[str, str]:
    clientes = resultado["ponto_de_equilibrio"]["clientes"]
    return {
        "margem-plataforma": resultado["por_cliente_mes"]["plataforma"]["margem"],
        "margem-parceiro": resultado["por_cliente_mes"]["parceiro"]["margem"],
        "equilibrio": "" if clientes is None else str(clientes),
        "resultado-carteira": resultado["carteira"]["plataforma_mes"],
    }


def _reais(texto: str) -> str:
    """O que Intl.NumberFormat pt-BR mostra, com o menos tipográfico da página."""
    valor = Decimal(texto)
    inteiro, centavos = divmod(abs(valor).quantize(Decimal("0.01")) * 100, 100)
    milhar = f"{int(inteiro):,}".replace(",", ".")
    return f"{'\u2212' if valor < 0 else ''}R$\u00a0{milhar},{int(centavos):02d}"


def _conferir_destaques(pagina: Any, resultado: dict[str, Any]) -> None:
    esperar = _esperar()
    for id_, valor in _esperados(resultado).items():
        esperar(pagina.locator(f"#{id_}")).to_have_attribute("data-valor", valor)
    # O texto exibido é o mesmo número da API, formatado em reais.
    mes = resultado["por_cliente_mes"]
    esperar(pagina.locator("#margem-plataforma")).to_have_text(_reais(mes["plataforma"]["margem"]))
    esperar(pagina.locator("#margem-parceiro")).to_have_text(_reais(mes["parceiro"]["margem"]))
    esperar(pagina.locator("#resultado-carteira")).to_have_text(
        _reais(resultado["carteira"]["plataforma_mes"])
    )


def _simular_com_controles(pagina: Any, valores: dict[str, str]) -> dict[str, Any]:
    """Preenche os controles, envia e devolve o corpo que a página mandou à API."""
    for id_, valor in valores.items():
        if id_ == "cenario-risco":
            pagina.locator("#cenario-risco").select_option(valor)
        else:
            pagina.locator(f"#{id_}").fill(valor)
    with pagina.expect_request(
        lambda r: r.url.endswith("/v1/negocio/simulacoes") and r.method == "POST"
    ) as pedido:
        pagina.get_by_role("button", name="Simular com estes valores").click()
    corpo: dict[str, Any] = pedido.value.post_data_json
    return corpo


def test_negocio_conjuntos_batem_com_a_api(pagina: Any) -> None:
    esperar = _esperar()
    _abrir(pagina)
    # Abre no conjunto base, com os controles preenchidos a partir dele.
    esperar(pagina.locator("#conjunto-base")).to_have_attribute("aria-pressed", "true")
    esperar(pagina.locator("#gasto")).to_have_value("1.500,00")
    esperar(pagina.locator("#externa")).to_have_value("30")
    esperar(pagina.locator("#cdi")).to_have_value("13,65")
    for conjunto in CONJUNTOS:
        pagina.locator(f"#conjunto-{conjunto}").click()
        esperar(pagina.locator(f"#conjunto-{conjunto}")).to_have_attribute("aria-pressed", "true")
        resultado = _api(pagina.endereco, {"conjunto": conjunto}).json()
        _conferir_destaques(pagina, resultado)
        esperar(pagina.locator("#cenario-risco")).to_have_value(
            resultado["premissas"]["cenario_de_risco"]
        )
        fontes = [linha for linha in resultado["linhas"] if linha["destino"] == "plataforma"]
        esperar(pagina.locator("#grafico-fontes g.barra")).to_have_count(len(fontes))
        custos = [linha for linha in resultado["linhas"] if linha["origem"] == "plataforma"]
        # Receita total, cada custo e a margem.
        esperar(pagina.locator("#grafico-cascata g.degrau")).to_have_count(len(custos) + 2)
        esperar(pagina.locator("#grafico-tornado g.barra")).to_have_count(
            min(10, len(resultado["tornado"]))
        )
        esperar(pagina.locator("#quem-paga-quem tbody tr")).to_have_count(len(resultado["linhas"]))
        ix = resultado["interna_x_externa"]
        texto = pagina.locator("#texto-interna-externa")
        for valor, motivo in (
            (ix["fee_de_gravame_externo_para_empatar"], ix["motivo_fee"]),
            (ix["gasto_para_empatar"], ix["motivo_gasto"]),
        ):
            esperar(texto).to_contain_text(motivo if valor is None else _reais(valor))
        _fotografar(pagina, f"negocio-{conjunto}.png")
    esperar(pagina.locator("#aviso-ilustrativo")).to_be_visible()
    esperar(pagina.locator("#aviso-ilustrativo")).to_contain_text("Números ilustrativos")
    esperar(pagina.locator("#aviso-ilustrativo a")).to_have_attribute("href", "/docs#/Negócio")


def test_negocio_controles_mandam_o_que_a_tela_mostra(pagina: Any) -> None:
    _abrir(pagina)
    pagina.locator("#conjunto-otimista").click()
    _esperar()(pagina.locator("#conjunto-otimista")).to_have_attribute("aria-pressed", "true")
    corpo = _simular_com_controles(
        pagina,
        {
            "gasto": "2.000,00",
            "saldo": "10000",
            "externa": "42,5",
            "cdi": "12,5",
            "intercambio": "1,1",
            "fee-api": "3,25",
            "cenario-risco": "crash_da_bolsa",
        },
    )
    assert corpo == {
        "conjunto": "otimista",
        "premissas": {
            "gasto_mensal": "2000.00",
            "saldo_em_garantia": "10000.00",
            "fracao_externa": "0.4250",
            "fracao_interna": "0.5750",
            "cdi_ao_ano": "0.1250",
            "taxa_de_intercambio": "0.0110",
            "fee_por_garantia_ativa": "3.25",
        },
        "cenario_de_risco": "crash_da_bolsa",
    }
    _esperar()(pagina.locator("[aria-pressed=true]")).to_have_count(0)
    _conferir_destaques(pagina, _api(pagina.endereco, corpo).json())


def test_negocio_le_ponto_e_virgula_como_a_pessoa_digita(pagina: Any) -> None:
    _abrir(pagina)
    corpo = _simular_com_controles(pagina, {"fee-api": "3.25", "gasto": "1.500,00"})
    assert corpo["premissas"]["fee_por_garantia_ativa"] == "3.25"
    assert corpo["premissas"]["gasto_mensal"] == "1500.00"
    corpo = _simular_com_controles(pagina, {"fee-api": "1.5", "gasto": "1.500"})
    assert corpo["premissas"]["fee_por_garantia_ativa"] == "1.50"
    assert corpo["premissas"]["gasto_mensal"] == "1500.00"
    corpo = _simular_com_controles(pagina, {"fee-api": "2", "gasto": "12.345,6"})
    assert corpo["premissas"]["gasto_mensal"] == "12345.60"


@pytest.mark.parametrize("digitado", ["1,2,3", "1.50.0", "1.5000", "3,256", "abc"])
def test_negocio_recusa_numero_ambiguo_sem_chamar_a_api(pagina: Any, digitado: str) -> None:
    esperar = _esperar()
    _abrir(pagina)
    pedidos: list[str] = []
    pagina.on(
        "request",
        lambda r: pedidos.append(r.url) if r.url.endswith("/v1/negocio/simulacoes") else None,
    )
    pagina.locator("#gasto").fill(digitado)
    pagina.get_by_role("button", name="Simular com estes valores").click()
    esperar(pagina.locator("#erro")).to_contain_text("Gasto no cartão")
    esperar(pagina.locator("#erro")).to_contain_text(digitado)
    esperar(pagina.locator("#erro")).to_contain_text("vírgula")
    pagina.wait_for_timeout(300)
    assert pedidos == []


def test_negocio_mostra_o_percentual_usado(pagina: Any) -> None:
    esperar = _esperar()
    _abrir(pagina)
    esperar(pagina.locator("#usado-cdi")).to_have_text("usado: 13,65% ao ano")
    esperar(pagina.locator("#usado-intercambio")).to_have_text("usado: 1,2% do gasto")
    esperar(pagina.locator("#usado-externa")).to_have_text("usado: 30% das garantias")
    esperar(pagina.locator("#cdi")).to_have_attribute("placeholder", "em %, ex.: 13,65")
    # Uma fração digitada no campo de % aparece como o que é: 0,1365% ao ano.
    corpo = _simular_com_controles(pagina, {"cdi": "0,1365"})
    assert corpo["premissas"]["cdi_ao_ano"] == "0.001365"
    esperar(pagina.locator("#usado-cdi")).to_have_text("usado: 0,1365% ao ano")


def test_negocio_valor_extremo_mostra_erro_da_api(pagina: Any) -> None:
    esperar = _esperar()
    _abrir(pagina)
    # Base: saldo de R$ 8.000 com LTV 0,85 gera R$ 6.800 de limite.
    _simular_com_controles(pagina, {"gasto": "7.000,00"})
    esperar(pagina.locator("#erro")).to_contain_text("não pode passar do limite")
    esperar(pagina.locator("#erro")).to_contain_text("gasto_mensal")
    _fotografar(pagina, "negocio-erro.png")
    # Corrigido o valor, o erro some.
    _simular_com_controles(pagina, {"gasto": "1.500,00"})
    esperar(pagina.locator("#erro")).to_have_text("")


def test_negocio_sem_equilibrio(pagina: Any) -> None:
    esperar = _esperar()
    _abrir(pagina)
    corpo = _simular_com_controles(pagina, {"fee-api": "0", "intercambio": "0"})
    resultado = _api(pagina.endereco, corpo).json()
    assert resultado["ponto_de_equilibrio"]["clientes"] is None
    _conferir_destaques(pagina, resultado)
    esperar(pagina.locator("#equilibrio")).to_contain_text("não há ponto de equilíbrio")
    esperar(pagina.locator("#detalhe-equilibrio")).to_contain_text(
        resultado["ponto_de_equilibrio"]["motivo"]
    )


def test_negocio_cdi_alto_deixa_a_margem_negativa(pagina: Any) -> None:
    esperar = _esperar()
    _abrir(pagina)
    corpo = _simular_com_controles(pagina, {"cdi": "50"})
    assert corpo["premissas"]["cdi_ao_ano"] == "0.5000"
    resultado = _api(pagina.endereco, corpo).json()
    assert resultado["por_cliente_mes"]["plataforma"]["margem"].startswith("-")
    _conferir_destaques(pagina, resultado)
    esperar(pagina.locator("#margem-plataforma")).to_have_class("negativo")
    esperar(pagina.locator("#equilibrio")).to_contain_text("não há ponto de equilíbrio")
    esperar(pagina.locator("#grafico-cascata g.degrau.margem rect.negativo")).to_have_count(1)


def test_navegacao_entre_as_tres_paginas(pagina: Any) -> None:
    esperar = _esperar()
    paginas = {"/demo": "Demonstração", "/risco": "Risco", "/negocio": "Negócio"}
    for caminho, nome in paginas.items():
        pagina.goto(f"{pagina.endereco}{caminho}")
        esperar(pagina.locator("body[data-pronta=true]")).to_have_count(1)
        navegacao = pagina.get_by_role("navigation", name="Páginas")
        esperar(navegacao.get_by_role("link")).to_have_text(list(paginas.values()))
        for destino, rotulo in paginas.items():
            esperar(navegacao.get_by_role("link", name=rotulo)).to_have_attribute("href", destino)
        esperar(navegacao.locator("[aria-current=page]")).to_have_text(nome)
        esperar(navegacao.locator("[aria-current]")).to_have_count(1)
    # E os links levam de uma à outra.
    pagina.get_by_role("navigation", name="Páginas").get_by_role("link", name="Risco").click()
    esperar(pagina).to_have_url(f"{pagina.endereco}/risco")
    esperar(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    pagina.get_by_role("navigation", name="Páginas").get_by_role("link", name="Negócio").click()
    esperar(pagina).to_have_url(f"{pagina.endereco}/negocio")
    esperar(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    pagina.get_by_role("navigation", name="Páginas").get_by_role(
        "link", name="Demonstração"
    ).click()
    esperar(pagina).to_have_url(f"{pagina.endereco}/demo")
    esperar(pagina.locator("body[data-pronta=true]")).to_have_count(1)


def test_exportar_csv(pagina: Any) -> None:
    _abrir(pagina)
    with pagina.expect_download() as baixado:
        pagina.locator("#exportar").click()
    download = baixado.value
    assert download.suggested_filename == "negocio.csv"
    conteudo = Path(download.path()).read_bytes()
    assert conteudo.startswith(BOM)
    assert b";" in conteudo
    # O mesmo CSV que a API devolve para o mesmo corpo (o da última simulação).
    da_api = _api(pagina.endereco, {"conjunto": "base"}, aceitar="text/csv")
    assert conteudo == da_api.content


def test_negocio_cabe_no_celular(pagina: Any) -> None:
    esperar = _esperar()
    pagina.set_viewport_size({"width": 375, "height": 812})
    _abrir(pagina)
    esperar(pagina.locator("#grafico-tornado g.barra")).not_to_have_count(0)
    largura = pagina.evaluate("document.documentElement.scrollWidth")
    assert largura <= 375
    for grafico in ("grafico-fontes", "grafico-cascata", "grafico-tornado"):
        caixa = pagina.locator(f"#{grafico}").bounding_box()
        assert caixa["x"] >= 0
        assert caixa["x"] + caixa["width"] <= 375
    _fotografar(pagina, "negocio-celular.png")
