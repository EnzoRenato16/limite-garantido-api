"""Os trilhos de entrada, o empréstimo e a régua da previdência na /demo, num navegador.

    uv run pytest -m e2e

Sobe a API e o worker de verdade (ver conftest.py). As contrapartes são simuladores: quem
responde aos pedidos é o botão "(simulador)" de cada garantia. Os testes esperam o estado
visível (texto, contagem, `data-pronta`), nunca um tempo fixo.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e.ajudas import (
    abrir_demo,
    contratar,
    em_formato_de_tela,
    esperar,
    fotografar,
    hoje_do_servidor,
    pedir_de_fora,
    somar_dias_uteis,
    webhook,
)

pytestmark = pytest.mark.e2e

TEMPO_DO_WORKER_EM_MS = 15_000


def _garantia_de_fora(pagina: Any) -> Any:
    return pagina.locator("#garantias-de-fora > li").last


def _concluir(pagina: Any, garantia: Any) -> None:
    garantia.get_by_role("button", name="Concluir (simulador)").click()


def test_portabilidade_de_acoes_chega_a_um_emprestimo(pagina: Any) -> None:
    expect = esperar()
    abrir_demo(pagina)
    pedir_de_fora(pagina, "portabilidade_cvm_210", ativo="acoes", produto="emprestimo")
    garantia = _garantia_de_fora(pagina)

    # O prazo é a data de hoje mais 2 dias úteis (Res. CVM 210, art. 16), com a contagem.
    esperado = em_formato_de_tela(somar_dias_uteis(hoje_do_servidor(pagina), 2))
    expect(garantia.locator(".prazo")).to_contain_text(f"até {esperado}")
    expect(garantia.locator(".prazo")).to_contain_text("2 dias úteis")
    expect(garantia.locator(".prazo")).to_contain_text("prazo da norma")
    expect(garantia.locator(".linha-do-tempo .atual")).to_have_text("Solicitado")
    expect(garantia.locator(".tecnico")).to_contain_text("B3 Central Depositária (simulada)")
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    expect(webhook(pagina, "portabilidade.solicitada")).to_have_count(
        1, timeout=TEMPO_DO_WORKER_EM_MS
    )
    fotografar(pagina, "trilhos-1-portabilidade-solicitada.png")

    # A contraparte (simulador) conclui: o gravame é registrado.
    _concluir(pagina, garantia)
    expect(garantia).to_contain_text("gravame registrado")
    expect(garantia.locator(".linha-do-tempo li.feito")).to_have_count(2)
    expect(garantia.locator(".prazo")).to_have_count(0)
    expect(webhook(pagina, "portabilidade.concluida")).to_have_count(1)
    expect(webhook(pagina, "gravame.registrado")).to_have_count(1)
    # Ações só garantem empréstimo: o cartão continua sem limite.
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")

    # E termina num empréstimo.
    expect(pagina.locator("#garantia-do-emprestimo option")).to_have_count(1)
    contratar(pagina, "2.000,00", "3")
    expect(pagina.locator("#resultado-emprestimo")).to_contain_text("contratado")
    emprestimo = pagina.locator("#emprestimos > li").first
    expect(emprestimo).to_contain_text("Empréstimo de R$ 2.000,00")
    expect(emprestimo.locator(".cronograma tbody tr")).to_have_count(3)
    expect(webhook(pagina, "emprestimo.contratado")).to_have_count(1)
    # A garantia agora cobre o empréstimo: não está mais disponível para outro.
    expect(pagina.locator("#garantia-do-emprestimo option")).to_have_count(0)
    fotografar(pagina, "trilhos-2-emprestimo-contratado.png")


def test_transferencia_de_custodia_de_cdb_gera_limite_no_cartao(pagina: Any) -> None:
    expect = esperar()
    abrir_demo(pagina)
    pedir_de_fora(pagina, "transferencia_de_custodia", ativo="cdb", produto="cartao")
    garantia = _garantia_de_fora(pagina)
    esperado = em_formato_de_tela(somar_dias_uteis(hoje_do_servidor(pagina), 5))
    expect(garantia.locator(".prazo")).to_contain_text(f"até {esperado}")
    expect(garantia.locator(".prazo")).to_contain_text("5 dias úteis")
    expect(garantia.locator(".prazo")).to_contain_text("ilustrativo do projeto")
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")

    _concluir(pagina, garantia)
    # R$ 10.000 de CDB com liquidez diária x 85%.
    expect(pagina.locator("#disponivel")).to_have_text("R$ 8.500,00")
    expect(garantia).to_contain_text("gravame registrado")
    expect(garantia.locator(".tecnico")).to_contain_text("B3 Balcão (simulada)")
    expect(webhook(pagina, "transferencia.concluida")).to_have_count(1)


def test_td_garantia_com_o_agente_sem_servico_e_recusada(pagina: Any) -> None:
    expect = esperar()
    abrir_demo(pagina)
    pedir_de_fora(
        pagina,
        "td_garantia",
        contraparte="Agente do Tesouro sem o serviço (simulador)",
    )
    garantia = _garantia_de_fora(pagina)
    expect(garantia.locator(".prazo")).to_contain_text("3 dias úteis")
    expect(garantia.locator(".prazo")).to_contain_text("ilustrativo do projeto")
    garantia.get_by_role("button", name="Recusar (simulador)").click()
    expect(garantia).to_contain_text("recusado")
    expect(garantia.locator(".linha-do-tempo .falhou")).to_have_text("Recusado")
    expect(garantia.locator(".motivo")).to_contain_text("recusa_pelo_sandbox")
    expect(garantia.get_by_role("button", name="Concluir (simulador)")).to_have_count(0)
    expect(pagina.locator("#disponivel")).to_have_text("R$ 0,00")
    expect(webhook(pagina, "gravame.recusado")).to_have_count(1)
    fotografar(pagina, "trilhos-3-td-garantia-recusado.png")


def test_previdencia_avisos_e_execucao_com_o_relogio(pagina: Any) -> None:
    expect = esperar()
    abrir_demo(pagina)
    pedir_de_fora(pagina, "res_conjunta_12", valor="20.000,00")
    garantia = _garantia_de_fora(pagina)
    esperado = em_formato_de_tela(somar_dias_uteis(hoje_do_servidor(pagina), 2))
    expect(garantia.locator(".prazo")).to_contain_text(f"até {esperado}")
    expect(garantia.locator(".prazo")).to_contain_text("2 dias úteis")
    # A entidade operadora não pode recusar (Res. Conjunta 12, art. 18): não há botão.
    expect(garantia.get_by_role("button", name="Recusar (simulador)")).to_have_count(0)

    _concluir(pagina, garantia)
    # Valor disponível R$ 20.000 menos 10% de tributos informados.
    expect(garantia.locator(".informacoes")).to_contain_text("líquido R$ 18.000,00")
    expect(garantia.locator(".informacoes")).to_contain_text("O bloqueio nasce ao contratar")
    expect(webhook(pagina, "previdencia.informacoes_recebidas")).to_have_count(1)

    contratar(pagina, "6.000,00", "3")
    expect(pagina.locator("#resultado-emprestimo")).to_contain_text("contratado")
    expect(garantia).to_contain_text("gravame registrado")
    emprestimo = pagina.locator("#emprestimos > li").first
    marcos = emprestimo.locator(".marco-de-atraso")
    expect(marcos).to_have_count(3)
    expect(emprestimo.locator(".marco-de-atraso.atingido")).to_have_count(0)

    pagina.get_by_role("button", name="+45 dias").click()
    expect(pagina.locator("#resultado-relogio")).to_contain_text("45 dias de atraso")
    expect(emprestimo.locator(".marco-de-atraso.atingido")).to_have_count(1)
    expect(webhook(pagina, "previdencia.aviso_45_dias")).to_have_count(1)
    expect(webhook(pagina, "garantia.executada")).to_have_count(0)

    pagina.get_by_role("button", name="+36 dias").click()
    expect(pagina.locator("#resultado-relogio")).to_contain_text("81 dias de atraso")
    expect(emprestimo.locator(".marco-de-atraso.atingido")).to_have_count(2)
    expect(webhook(pagina, "previdencia.aviso_10_dias")).to_have_count(1)
    expect(webhook(pagina, "garantia.executada")).to_have_count(0)
    fotografar(pagina, "trilhos-4-previdencia-avisos.png")

    pagina.get_by_role("button", name="+10 dias").click()
    expect(webhook(pagina, "garantia.executada")).to_have_count(1)
    expect(pagina.locator("#emprestimos > li").first).to_have_attribute(
        "data-estado", "liquidado_por_execucao"
    )
    # O demonstrativo da execução: líquido de tributos (a previdência já vem líquida).
    demonstrativo = pagina.locator("#emprestimos .demonstrativo")
    expect(demonstrativo.locator('[data-campo="valor_de_mercado"]')).to_have_text("R$ 18.000,00")
    expect(demonstrativo.locator('[data-campo="retencao"]')).to_have_text("R$ 0,00")
    expect(demonstrativo.locator('[data-campo="divida_abatida"]')).to_be_visible()
    expect(demonstrativo.locator('[data-campo="excedente_devolvido"]')).to_be_visible()
    fotografar(pagina, "trilhos-5-previdencia-execucao.png")


def test_quitar_pede_a_baixa_com_prazo_e_depois_o_gravame_e_baixado(pagina: Any) -> None:
    expect = esperar()
    abrir_demo(pagina)
    pedir_de_fora(pagina, "portabilidade_cvm_210", ativo="acoes", produto="emprestimo")
    garantia = _garantia_de_fora(pagina)
    _concluir(pagina, garantia)
    expect(garantia).to_contain_text("gravame registrado")
    contratar(pagina, "2.000,00", "3")
    expect(pagina.locator("#resultado-emprestimo")).to_contain_text("contratado")

    pagina.get_by_role("button", name="Quitar").click()
    expect(pagina.locator("#resultado-emprestimo")).to_contain_text("quitado")
    # A linha do tempo guarda o prazo da baixa: hoje mais 2 dias úteis.
    esperado = em_formato_de_tela(somar_dias_uteis(hoje_do_servidor(pagina), 2))
    expect(garantia.locator(".linha-do-tempo")).to_contain_text(
        f"Baixa pedida (até {esperado}, 2 dias úteis)"
    )
    expect(webhook(pagina, "gravame.baixa_pedida")).to_have_count(1)
    # O worker baixa o gravame na infraestrutura (simulada).
    expect(garantia).to_contain_text("gravame baixado", timeout=TEMPO_DO_WORKER_EM_MS)
    expect(garantia.locator(".linha-do-tempo li.feito")).to_have_count(4)
    expect(webhook(pagina, "gravame.baixado")).to_have_count(1)
    expect(webhook(pagina, "emprestimo.quitado")).to_have_count(1)
    fotografar(pagina, "trilhos-6-baixa-do-gravame.png")


def test_previdencia_no_cartao_mostra_o_erro_e_a_sugestao_da_api(pagina: Any) -> None:
    expect = esperar()
    abrir_demo(pagina)
    pedir_de_fora(pagina, "res_conjunta_12", produto="cartao")
    resultado = pagina.locator("#resultado-fora")
    expect(resultado).to_contain_text("exclui os limites rotativos")
    expect(resultado).to_contain_text("Sugestão:")
    expect(resultado).to_contain_text("POST /v1/contas/{conta_id}/emprestimos")
    expect(pagina.locator("#garantias-de-fora > li")).to_have_count(0)


def test_cdb_na_portabilidade_mostra_o_erro_e_a_sugestao_da_api(pagina: Any) -> None:
    expect = esperar()
    abrir_demo(pagina)
    pedir_de_fora(pagina, "portabilidade_cvm_210", ativo="cdb", produto="cartao")
    resultado = pagina.locator("#resultado-fora")
    expect(resultado).to_contain_text("não atende")
    expect(resultado).to_contain_text("Sugestão:")
    expect(resultado).to_contain_text("transferencias-de-custodia")
    expect(pagina.locator("#garantias-de-fora > li")).to_have_count(0)


def test_acoes_no_cartao_mostram_o_erro_e_a_sugestao_da_api(pagina: Any) -> None:
    expect = esperar()
    abrir_demo(pagina)
    pedir_de_fora(pagina, "portabilidade_cvm_210", ativo="acoes", produto="cartao")
    resultado = pagina.locator("#resultado-fora")
    expect(resultado).to_contain_text("só garante empréstimo")
    expect(resultado).to_contain_text("Sugestão:")
