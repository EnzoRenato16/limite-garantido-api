"""O bloco do cartão da página /demo num navegador de verdade.

    uv run pytest -m e2e

O teste sobe a API e o worker de webhooks como processos separados, contra um SQLite
temporário, e percorre a mesma história do vídeo: dar em garantia, comprar, compra negada,
queda do ativo, atraso e execução no 7º dia, conferindo os webhooks que chegam. Os trilhos
de fora, o empréstimo e a previdência estão em test_trilhos.py.

Variáveis opcionais:
- `E2E_ENDERECO`: em vez de subir tudo, testa um ambiente que já está no ar
  (por exemplo `http://localhost:8000` depois de um `docker compose up`);
- `CHROMIUM_EXECUTAVEL`: caminho de um Chromium já instalado (senão, usa o do Playwright);
- `SALVAR_TELAS`: pasta onde gravar os screenshots (é assim que `docs/img/` é gerada).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e.ajudas import abrir_demo, esperar, fotografar, webhook

pytestmark = pytest.mark.e2e


def _dar_em_garantia(pagina: Any, ativo: str = "cdb", valor: str = "5.000,00") -> None:
    pagina.locator("#ativo-da-casa").select_option(ativo)
    pagina.locator("#valor-da-garantia").fill(valor)
    pagina.get_by_role("button", name="Dar em garantia").click()


def test_historia_completa_do_cartao_na_pagina_de_demonstracao(pagina: Any) -> None:
    expect = esperar()
    pagina.goto(f"{pagina.endereco}/demo")
    expect(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    # Começa de uma conta nova: o navegador é novo, mas o servidor é o mesmo da sessão.
    pagina.get_by_role("button", name="Começar de novo").click()
    expect(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    expect(pagina.locator("#ativo-da-casa")).to_have_value("cdb")
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    expect(pagina.locator("#trilho-vazio")).to_be_visible()
    # Todo simulador da tela diz que é simulador.
    expect(pagina.locator("#contraparte option").first).to_contain_text("(simulador)")

    # 1. R$ 5.000 em CDB: R$ 4.250 de limite (85%), gravame registrado na B3 (simulador).
    _dar_em_garantia(pagina)
    expect(pagina.locator("#resultado-garantia")).to_contain_text("R$ 4.250,00 de limite (85%)")
    expect(pagina.locator("#resultado-garantia")).to_contain_text("B3 (simulador)")
    expect(pagina.locator("#disponivel")).to_have_text("R$ 4.250,00")
    expect(pagina.locator("#valor-garantia")).to_have_text("R$ 5.000,00")
    expect(pagina.locator("#trilho-vazio")).to_be_hidden()
    garantia = pagina.locator("#garantias-da-casa > li").first
    expect(garantia).to_contain_text("gravame registrado")
    expect(garantia.locator(".tecnico")).to_contain_text("B3 Balcão (simulada)")
    expect(garantia.locator(".tecnico")).to_contain_text("protocolo SIM-")
    expect(garantia.locator(".linha-do-tempo li.feito")).to_have_count(2)
    expect(webhook(pagina, "gravame.registrado")).to_have_count(1)

    # 2. Compra de R$ 4.000 aprovada; a de R$ 500 é negada com 402.
    pagina.get_by_role("button", name="Comprar", exact=True).click()
    expect(pagina.locator("#resultado-compra")).to_contain_text("aprovada")
    expect(pagina.locator("#disponivel")).to_have_text("R$ 250,00")
    pagina.locator("#valor-da-compra").fill("500,00")
    pagina.get_by_role("button", name="Comprar", exact=True).click()
    expect(pagina.locator("#resultado-compra")).to_contain_text(
        "negada: limite insuficiente (HTTP 402)"
    )
    expect(webhook(pagina, "compra.aprovada")).to_have_count(1)
    expect(webhook(pagina, "compra.negada")).to_have_count(1)
    fotografar(pagina, "demo-1-limite-e-compras.png")

    # 3. O CDB cai 10%: limite vai a R$ 3.825 e a dívida de R$ 4.000 passa dele.
    pagina.get_by_role("button", name="Derrubar o ativo em 10%").click()
    expect(pagina.locator("#resultado-queda")).to_contain_text("chamada de margem aberta")
    expect(pagina.locator("#valor-limite")).to_have_text("R$ 3.825,00")
    expect(pagina.locator("#valor-deficit")).to_have_text("R$ 175,00")
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    expect(pagina.locator("#aviso")).to_contain_text("Chamada de margem")
    expect(webhook(pagina, "chamada_de_margem")).to_have_count(1)
    fotografar(pagina, "demo-2-chamada-de-margem.png")

    # 4. A fatura fecha e atrasa. Até o 6º dia nada é vendido; no 7º, a execução.
    pagina.get_by_role("button", name="Fechar a fatura").click()
    expect(pagina.locator("#resultado-fatura")).to_contain_text("fechada. Vence hoje")
    passar_dia = pagina.get_by_role("button", name="Passar 1 dia")
    for dia in range(1, 7):
        passar_dia.click()
        expect(pagina.locator("#dias li.passado")).to_have_count(dia)
        expect(pagina.locator("#resultado-fatura")).to_contain_text(f"{dia} dia")
    expect(pagina.locator("#valor-usado")).to_have_text("R$ 4.000,00")
    expect(webhook(pagina, "garantia.executada")).to_have_count(0)

    passar_dia.click()
    expect(pagina.locator("#resultado-fatura")).to_contain_text("7º dia: a garantia foi executada")
    expect(pagina.locator("#resultado-fatura")).to_contain_text("abateram a dívida")
    # A execução vende a garantia inteira e devolve o que sobra, com demonstrativo.
    expect(pagina.locator("#valor-usado")).to_have_text("R$ 0,00")
    expect(pagina.locator("#valor-garantia")).to_have_text("R$ 0,00")
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    expect(garantia).to_contain_text("gravame")
    expect(webhook(pagina, "fatura.em_atraso")).to_have_count(1)
    expect(webhook(pagina, "garantia.executada")).to_have_count(1)
    fotografar(pagina, "demo-3-execucao-no-setimo-dia.png")

    # Todo webhook que apareceu teve a assinatura HMAC conferida pelo receptor.
    total = pagina.locator("#eventos > li").count()
    assert total >= 10
    expect(pagina.locator(".assinatura.invalida")).to_have_count(0)
    expect(pagina.locator(".assinatura", has_text="assinatura HMAC confere")).to_have_count(total)

    # O JSON entregue está a um clique.
    pagina.locator("#eventos > li").first.locator("summary").click()
    expect(pagina.locator("#eventos > li").first.locator("pre")).to_contain_text('"tipo"')

    # "Começar de novo" abre outra conta, zerada.
    pagina.get_by_role("button", name="Começar de novo").click()
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    expect(pagina.locator("#eventos > li")).to_have_count(0)


def test_pedir_logo_depois_de_comecar_de_novo_vai_para_a_conta_nova(pagina: Any) -> None:
    """Falhou no CI: com a conta nova demorando, o pedido ia para a conta antiga e a
    garantia sumia da tela assim que a nova aparecia. Enquanto a conta nova não existe, a
    página fica inerte e `data-pronta` vale "false"."""
    expect = esperar()
    pagina.goto(f"{pagina.endereco}/demo")
    expect(pagina.locator("body[data-pronta=true]")).to_have_count(1)
    anterior = pagina.locator("#conta-atual").text_content()
    # Atrasa no navegador a criação da conta, como numa rede lenta.
    pagina.evaluate(
        """() => {
          const original = window.fetch;
          window.fetch = async (url, opcoes) => {
            if (opcoes?.method === "POST" && url === "/v1/contas") {
              await new Promise((pronto) => setTimeout(pronto, 1500));
            }
            return original(url, opcoes);
          };
        }"""
    )
    pagina.get_by_role("button", name="Começar de novo").click()
    expect(pagina.locator("body[data-pronta=false]")).to_have_count(1)
    assert pagina.evaluate("document.body.inert") is True
    # O clique "com pressa" não faz nada enquanto a página está inerte: espera a conta
    # nova (sinal visível) e só então pede, e o pedido tem de ficar na conta nova.
    expect(pagina.locator("body[data-pronta=true]")).to_have_count(1, timeout=10_000)
    expect(pagina.locator("#conta-atual")).not_to_have_text(anterior or "")
    _dar_em_garantia(pagina, "fundo_di", "2.000,00")
    expect(pagina.locator("#garantias-da-casa > li")).to_have_count(1)
    # Dá tempo de qualquer consulta antiga responder: a garantia continua na tela.
    expect(pagina.locator("#disponivel")).to_have_text("R$ 1.700,00")
    expect(pagina.locator("#garantias-da-casa > li")).to_have_count(1)


def test_aviso_antigo_some_quando_o_estado_muda(pagina: Any) -> None:
    """A mensagem de chamada de margem não pode continuar na tela depois da execução."""
    expect = esperar()
    abrir_demo(pagina)
    _dar_em_garantia(pagina)
    expect(pagina.locator("#disponivel")).to_have_text("R$ 4.250,00")
    pagina.get_by_role("button", name="Comprar", exact=True).click()  # 4.000
    expect(pagina.locator("#disponivel")).to_have_text("R$ 250,00")
    pagina.get_by_role("button", name="Derrubar o ativo em 10%").click()
    expect(pagina.locator("#resultado-queda")).to_contain_text("chamada de margem aberta")
    expect(pagina.locator("#aviso")).to_contain_text("Chamada de margem")

    pagina.get_by_role("button", name="Fechar a fatura").click()
    expect(pagina.locator("#resultado-fatura")).to_contain_text("fechada")
    for dia in range(1, 8):
        pagina.get_by_role("button", name="Passar 1 dia").click()
        expect(pagina.locator("#resultado-fatura")).to_contain_text(
            "executada" if dia == 7 else f"{dia} dia"
        )
    expect(pagina.locator("#resultado-queda")).to_have_text("")
    expect(pagina.locator("#aviso")).not_to_contain_text("Chamada de margem")


def test_resumo_do_topo_fica_visivel_ao_rolar_no_celular(pagina: Any) -> None:
    expect = esperar()
    pagina.set_viewport_size({"width": 390, "height": 844})
    abrir_demo(pagina)
    pagina.locator("#fechar-fatura").scroll_into_view_if_needed()
    expect(pagina.locator("#disponivel")).to_be_in_viewport()
    expect(pagina.locator("#valor-limite")).to_be_in_viewport()
    expect(pagina.locator("#valor-usado")).to_be_in_viewport()


def test_a_navegacao_leva_as_quatro_paginas(pagina: Any) -> None:
    expect = esperar()
    pagina.goto(f"{pagina.endereco}/demo")
    expect(pagina.locator("nav.paginas a")).to_have_text(
        ["Demonstração", "Regulação", "Risco", "Negócio"]
    )
    for caminho, nome in (
        ("/regulacao", "Regulação"),
        ("/risco", "Risco"),
        ("/negocio", "Negócio"),
    ):
        pagina.goto(f"{pagina.endereco}{caminho}")
        expect(pagina.locator("nav.paginas a")).to_have_text(
            ["Demonstração", "Regulação", "Risco", "Negócio"]
        )
        expect(pagina.locator('nav.paginas a[aria-current="page"]')).to_have_text(nome)
