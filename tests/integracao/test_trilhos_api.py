"""Trilhos de entrada pela API: custódia na casa, portabilidade, transferência de custódia,
TD Garantia e Res. Conjunta 12, com as contrapartes simuladas.

As contrapartes destes testes são perfis com destino fixo (`teste_confirma`, `teste_recusa`,
`teste_mudo`), que respondem 10 minutos depois do pedido.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from app.api.fabrica import criar_app
from app.aplicacao.portas import StatusDaResposta
from app.config import Configuracao
from app.infra.banco.modelos import RespostaDeCustodianteModelo
from app.infra.contrapartes import SimuladorDeContrapartes, evento_id_da_contraparte
from app.infra.infraestrutura_simulada import InfraestruturaSimulada
from app.infra.relogio import RelogioFixo
from app.infra.webhooks.assinatura import assinar
from tests.conftest import (
    ADMIN,
    SEGREDOS_DE_CONTRAPARTES,
    Parceiro,
    configuracao_de_teste,
    criar_parceiro,
    politica_de_teste,
)
from tests.integracao.apoio_contrapartes import PERFIS
from tests.integracao.test_concorrencia import _disparar_juntas

CABECALHO = "X-Contraparte-Assinatura"


@pytest.fixture
def app(config: Configuracao, engine: Engine, relogio: RelogioFixo) -> FastAPI:
    return criar_app(
        config, engine=engine, relogio=relogio, contraparte=SimuladorDeContrapartes(PERFIS, relogio)
    )


# ------------------------------------------------------------------ apoio
def _rotina(cliente: TestClient, parceiro: Parceiro) -> dict[str, Any]:
    resposta = cliente.post(
        "/v1/admin/rotinas/contrapartes", params={"parceiro_id": parceiro.id}, headers=ADMIN
    )
    assert resposta.status_code == 200, resposta.text
    corpo: dict[str, Any] = resposta.json()
    return corpo


def _callback(
    cliente: TestClient,
    relogio: RelogioFixo,
    contraparte_id: str,
    corpo: dict[str, Any],
    *,
    segredo: str | None = None,
    atraso_em_segundos: int = 0,
) -> Any:
    bruto = json.dumps(corpo).encode()
    instante = int(relogio.agora().timestamp()) - atraso_em_segundos
    assinatura = assinar(
        segredo or SEGREDOS_DE_CONTRAPARTES.get(contraparte_id, "x" * 40), bruto, instante
    )
    return cliente.post(
        f"/v1/contrapartes/{contraparte_id}/callbacks",
        content=bruto,
        headers={CABECALHO: assinatura, "Content-Type": "application/json"},
    )


def _resposta(
    contraparte_id: str, solicitacao_id: str, status: StatusDaResposta, **extra: str
) -> dict[str, Any]:
    return {
        "evento_id": evento_id_da_contraparte(contraparte_id, solicitacao_id, status),
        "solicitacao_id": solicitacao_id,
        "status": status.value,
        **extra,
    }


CONCLUIDA = StatusDaResposta.CONCLUIDA


def _portabilidade(
    parceiro: Parceiro, contraparte: str = "teste_confirma", tipo: str = "acoes"
) -> tuple[str, str, str]:
    conta = parceiro.abrir_conta()
    corpo = parceiro.entrar(
        conta,
        "portabilidades",
        {
            "tipo_ativo": tipo,
            "valor": "10000.00",
            "contraparte_id": contraparte,
            "produto": "emprestimo",
        },
    )
    garantia = corpo["garantia"]
    return conta, garantia["id"], garantia["solicitacao"]["id"]


def _td(parceiro: Parceiro, contraparte: str) -> tuple[str, str, str]:
    conta = parceiro.abrir_conta()
    corpo = parceiro.entrar(
        conta,
        "garantias/tesouro",
        {"tipo_ativo": "tesouro_selic", "valor": "10000.00", "contraparte_id": contraparte},
    )
    garantia = corpo["garantia"]
    return conta, garantia["id"], garantia["solicitacao"]["id"]


def _transferencia(parceiro: Parceiro, contraparte: str) -> tuple[str, str, str]:
    conta = parceiro.abrir_conta()
    corpo = parceiro.entrar(
        conta,
        "transferencias-de-custodia",
        {
            "tipo_ativo": "cdb",
            "valor": "10000.00",
            "contraparte_id": contraparte,
            "liquidez_dias": 0,
        },
    )
    garantia = corpo["garantia"]
    return conta, garantia["id"], garantia["solicitacao"]["id"]


def _previdencia(parceiro: Parceiro, contraparte: str = "teste_confirma") -> tuple[str, str, str]:
    conta = parceiro.abrir_conta()
    corpo = parceiro.entrar(
        conta,
        "garantias/previdencia",
        {
            "valor": "20000.00",
            "contraparte_id": contraparte,
            "plano": "pgbl",
            "fim_da_acumulacao": "2040-01-01",
        },
    )
    garantia = corpo["garantia"]
    return conta, garantia["id"], garantia["solicitacao"]["id"]


def _respostas(engine: Engine, garantia_id: str) -> int:
    with engine.connect() as conexao:
        return int(
            conexao.execute(
                select(func.count())
                .select_from(RespostaDeCustodianteModelo)
                .where(RespostaDeCustodianteModelo.garantia_id == garantia_id)
            ).scalar_one()
        )


def _problema_sem_rastro(resposta: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = resposta.json()
    corpo.pop("instance", None)
    corpo.pop("request_id", None)
    return corpo


# ================================================== catálogo e combinações
def test_contrapartes_aparecem_como_simuladores_com_os_trilhos(parceiro: Parceiro) -> None:
    resposta = parceiro.get("/v1/contrapartes")
    assert resposta.status_code == 200
    contrapartes = resposta.json()
    assert [c["id"] for c in contrapartes] == ["teste_confirma", "teste_recusa", "teste_mudo"]
    assert all(c["simulado"] for c in contrapartes)
    assert "res_conjunta_12" in contrapartes[0]["trilhos"]
    assert "res_conjunta_12" not in contrapartes[1]["trilhos"]  # a operadora não recusa


def test_contraparte_desconhecida_ou_de_outro_trilho(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    corpo = {"tipo_ativo": "acoes", "valor": "100.00", "produto": "emprestimo"}
    desconhecida = parceiro.post(
        f"/v1/contas/{conta}/portabilidades", {**corpo, "contraparte_id": "nao_existe"}
    )
    assert desconhecida.status_code == 422
    assert desconhecida.json()["codigo"] == "contraparte_nao_configurada"
    # `teste_recusa` não atende a Res. Conjunta 12.
    de_outro = parceiro.post(
        f"/v1/contas/{conta}/garantias/previdencia",
        {
            "valor": "100.00",
            "contraparte_id": "teste_recusa",
            "plano": "pgbl",
            "fim_da_acumulacao": "2040-01-01",
        },
    )
    assert de_outro.status_code == 422
    assert de_outro.json()["codigo"] == "contraparte_nao_atende_o_trilho"


def test_previdencia_no_cartao_da_422_com_sugestao(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    resposta = parceiro.post(
        f"/v1/contas/{conta}/garantias/previdencia",
        {
            "valor": "1000.00",
            "contraparte_id": "teste_confirma",
            "plano": "pgbl",
            "fim_da_acumulacao": "2040-01-01",
            "produto": "cartao",
        },
    )
    assert resposta.status_code == 422
    corpo = resposta.json()
    assert corpo["codigo"] == "previdencia_so_no_emprestimo"
    assert "garantias/previdencia" in corpo["sugestao"]
    assert "/emprestimos" in corpo["sugestao"]


def test_cdb_em_portabilidade_e_trilho_que_nao_atende_o_ativo(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    resposta = parceiro.post(
        f"/v1/contas/{conta}/portabilidades",
        {"tipo_ativo": "cdb", "valor": "1000.00", "contraparte_id": "teste_confirma"},
    )
    assert resposta.status_code == 422
    corpo = resposta.json()
    assert corpo["codigo"] == "trilho_nao_atende_o_ativo"
    assert "transferencias-de-custodia" in corpo["sugestao"]


def test_ativo_ja_em_garantia_e_impedimento_a_portabilidade(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    resposta = parceiro.post(
        f"/v1/contas/{conta}/portabilidades",
        {
            "tipo_ativo": "acoes",
            "valor": "1000.00",
            "contraparte_id": "teste_confirma",
            "produto": "emprestimo",
            "ja_em_garantia": True,
        },
    )
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "ativo_ja_em_garantia"


def test_previdencia_exige_plano_e_recusa_fapi(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    base = {
        "valor": "1000.00",
        "contraparte_id": "teste_confirma",
        "fim_da_acumulacao": "2040-01-01",
    }
    sem_plano = parceiro.post(f"/v1/contas/{conta}/garantias/previdencia", base)
    assert sem_plano.status_code == 422
    assert sem_plano.json()["erros"][0]["campo"] == "plano"
    fapi = parceiro.post(f"/v1/contas/{conta}/garantias/previdencia", {**base, "plano": "fapi"})
    assert fapi.status_code == 422
    assert fapi.json()["codigo"] == "plano_fora_da_res_conjunta_12"


def test_cartao_com_ativo_que_nao_tem_liquidez_diaria(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    resposta = parceiro.post(
        f"/v1/contas/{conta}/garantias", {"tipo_ativo": "cdb", "valor": "10.00"}
    )
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "cartao_exige_liquidez_diaria"


# ===================================================== rotas antigas em 410
@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("get", "/v1/custodiantes"),
        ("post", "/v1/custodiantes/x/callbacks"),
        ("post", "/v1/sandbox/garantias/x/custodiante"),
    ],
)
def test_rotas_do_custodiante_respondem_410_com_a_rota_nova(
    parceiro: Parceiro, metodo: str, caminho: str
) -> None:
    resposta = getattr(parceiro.http, metodo)(caminho, headers=parceiro.cabecalhos)
    assert resposta.status_code == 410
    corpo = resposta.json()
    assert corpo["codigo"] == "rota_substituida"
    assert corpo["rota_nova"]


def test_custodiante_id_em_garantias_responde_410(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    resposta = parceiro.post(
        f"/v1/contas/{conta}/garantias",
        {"tipo_ativo": "cdb", "valor": "10.00", "custodiante_id": "custodiante_agil"},
    )
    assert resposta.status_code == 410
    assert resposta.json()["codigo"] == "rota_substituida"
    assert "portabilidades" in resposta.json()["rota_nova"]


# ============================================================= custódia na casa
def test_casa_registra_o_gravame_e_o_limite_so_vem_depois(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    corpo = parceiro.bloquear(conta, "cdb", "5000.00")
    garantia = corpo["garantia"]
    assert garantia["estado"] == "registrado"
    assert garantia["produto"] == "cartao"
    assert garantia["ltv"] == "0.85"
    assert garantia["limite_gerado"] == "4250.00"
    gravame = garantia["gravame"]
    assert (gravame["trilho"], gravame["infraestrutura"], gravame["instrumento"]) == (
        "casa",
        "b3_balcao",
        "cessao_fiduciaria",
    )
    assert gravame["protocolo"] == f"SIM-b3_balcao-{garantia['id']}"
    assert gravame["baixa_prazo"] is None
    assert parceiro.tipos_de_evento(conta) == [
        "gravame.solicitado",
        "gravame.registrado",
        "limite.atualizado",
    ]


def test_liberacao_pede_a_baixa_em_dois_dias_uteis_e_o_worker_baixa(
    cliente: TestClient, parceiro: Parceiro
) -> None:
    conta = parceiro.abrir_conta()
    garantia_id = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]["id"]
    liberada = parceiro.post(f"/v1/garantias/{garantia_id}/liberacao")
    assert liberada.status_code == 200
    assert liberada.json()["limite"]["limite_total"] == "0.00"  # o limite cai na hora
    gravame = liberada.json()["garantia"]["gravame"]
    assert (gravame["estado"], gravame["baixa_prazo"]) == ("registrado", "2026-03-04")
    resumo = cliente.post(
        "/v1/admin/rotinas/baixas", params={"parceiro_id": parceiro.id}, headers=ADMIN
    ).json()
    assert (resumo["contas_processadas"], resumo["contas_com_erro"]) == (1, 0)
    assert parceiro.garantia(garantia_id)["estado"] == "baixado"
    tipos = parceiro.tipos_de_evento(conta)
    assert "gravame.baixa_pedida" in tipos
    assert tipos[-1] == "gravame.baixado"


def test_penhor_traz_o_alerta_da_lei_11101(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "1000.00", instrumento="penhor")["garantia"]
    assert any("Lei 11.101" in a and "art. 49, §5º" in a for a in garantia["alertas"])
    sem = parceiro.bloquear(parceiro.abrir_conta(), "cdb", "1000.00")["garantia"]
    assert sem["alertas"] == []


# ================================================================ portabilidade
def test_portabilidade_de_acoes_202_com_prazo_em_dias_uteis_e_emprestimo(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, solicitacao_id = _portabilidade(parceiro)
    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "solicitado"
    assert garantia["gravame"]["trilho"] == "portabilidade_cvm_210"
    assert garantia["gravame"]["infraestrutura"] == "b3_central_depositaria"
    # Segunda-feira 02/03/2026 + 2 dias úteis = quarta-feira 04/03.
    assert garantia["solicitacao"]["prazo_ate"].startswith("2026-03-04")
    assert parceiro.limite(conta)["limite_total"] == "0.00"

    resposta = _callback(
        cliente, relogio, "teste_confirma", _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    )
    assert resposta.json() == {"resultado": "aplicada"}
    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "registrado"
    assert garantia["solicitacao"] is None
    assert parceiro.tipos_de_evento(conta) == [
        "portabilidade.solicitada",
        "gravame.registrado",
        "portabilidade.concluida",
    ]
    # Garantia de produto empréstimo não gera limite de cartão.
    assert parceiro.limite(conta)["limite_total"] == "0.00"

    contratado = parceiro.post(
        f"/v1/contas/{conta}/emprestimos",
        {"valor": "5000.00", "parcelas": 6, "garantia_ids": [garantia_id]},
    )
    assert contratado.status_code == 201, contratado.text
    emprestimo = contratado.json()["emprestimo"]
    assert len(emprestimo["parcelas"]) == 6
    assert emprestimo["estado"] == "ativo"
    assert parceiro.garantia(garantia_id)["emprestimo_id"] == emprestimo["id"]


def test_portabilidade_por_polling_e_recusa_vira_impedimento(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _conta, garantia_id, _ = _portabilidade(parceiro)
    _rotina(cliente, parceiro)  # o worker envia o pedido
    assert parceiro.garantia(garantia_id)["solicitacao"]["enviada_em"] is not None
    relogio.avancar(minutes=10)
    _rotina(cliente, parceiro)
    assert parceiro.garantia(garantia_id)["estado"] == "registrado"

    # Impedimento só na portabilidade, via sandbox.
    _, outra, _ = _portabilidade(parceiro)
    decidida = parceiro.post(f"/v1/sandbox/garantias/{outra}/contraparte", {"acao": "recusar"})
    assert decidida.status_code == 200, decidida.text
    cancelada = parceiro.garantia(outra)
    assert cancelada["estado"] == "cancelado"
    assert cancelada["gravame"]["motivo"] == "recusa_pelo_sandbox"


def test_portabilidade_sem_resposta_continua_marcada_com_prazo_regulatorio_vencido(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, _ = _portabilidade(parceiro, "teste_mudo")
    _rotina(cliente, parceiro)
    relogio.avancar(days=4)  # passou de 2 dias úteis
    assert _rotina(cliente, parceiro)["contas_com_erro"] == 0
    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "solicitado"  # continua
    assert garantia["solicitacao"]["prazo_regulatorio_vencido"] is True
    assert parceiro.tipos_de_evento(conta)[-1] == "garantia.prazo_regulatorio_vencido"


# ==================================================== transferência e TD
def test_transferencia_conclui_e_gera_limite_no_cartao(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    assert parceiro.garantia(garantia_id)["solicitacao"]["prazo_ate"].startswith("2026-03-09")
    _callback(
        cliente, relogio, "teste_confirma", _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    )
    assert parceiro.garantia(garantia_id)["estado"] == "registrado"
    assert parceiro.limite(conta)["limite_total"] == "8500.00"
    assert "transferencia.concluida" in parceiro.tipos_de_evento(conta)


def test_transferencia_e_td_sem_resposta_no_prazo_viram_cancelado_e_recusado(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta_t, transferencia, _ = _transferencia(parceiro, "teste_mudo")
    conta_d, td, _ = _td(parceiro, "teste_mudo")
    _rotina(cliente, parceiro)
    relogio.avancar(days=8)
    assert _rotina(cliente, parceiro)["contas_com_erro"] == 0
    assert parceiro.garantia(transferencia)["estado"] == "cancelado"
    assert parceiro.garantia(td)["estado"] == "recusado"
    assert parceiro.tipos_de_evento(conta_t)[-1] == "gravame.cancelado"
    assert parceiro.tipos_de_evento(conta_d)[-1] == "gravame.recusado"
    assert parceiro.limite(conta_t)["limite_total"] == "0.00"


def test_td_garantia_recusa_quando_o_agente_nao_opera_o_servico(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, _ = _td(parceiro, "teste_recusa")
    _rotina(cliente, parceiro)
    relogio.avancar(minutes=10)
    _rotina(cliente, parceiro)
    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "recusado"
    assert garantia["gravame"]["motivo"] == "ativo_ja_onerado"
    assert garantia["gravame"]["trilho"] == "td_garantia"
    assert parceiro.eventos(conta)[-1]["tipo"] == "gravame.recusado"
    assert parceiro.limite(conta)["limite_total"] == "0.00"


def test_evento_de_custodia_do_tesouro_vincula_o_cupom(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _conta, garantia_id, solicitacao_id = _td(parceiro, "teste_confirma")
    _callback(
        cliente, relogio, "teste_confirma", _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    )
    antes = parceiro.garantia(garantia_id)["valor_de_mercado"]
    resposta = parceiro.post(
        f"/v1/sandbox/garantias/{garantia_id}/evento-de-custodia", {"valor": "300.00"}
    )
    assert resposta.status_code == 200, resposta.text
    assert parceiro.garantia(garantia_id)["valor_de_mercado"] == antes  # título + cupom = igual
    # Só Tesouro: CDB não recebe evento de custódia.
    cdb = parceiro.bloquear(parceiro.abrir_conta(), "cdb", "1000.00")["garantia"]["id"]
    invalido = parceiro.post(f"/v1/sandbox/garantias/{cdb}/evento-de-custodia", {"valor": "10.00"})
    assert invalido.status_code == 422


# ================================================================= previdência
def test_previdencia_informacoes_da_operadora_e_formalizacao_no_emprestimo(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, _solicitacao_id = _previdencia(parceiro)
    garantia = parceiro.garantia(garantia_id)
    assert garantia["gravame"]["infraestrutura"] == "plataforma_res_conjunta_12"
    assert garantia["gravame"]["instrumento"] == "direito_de_resgate"
    assert garantia["solicitacao"]["prazo_ate"].startswith("2026-03-04")  # 2 dias úteis

    relogio.avancar(hours=3)
    _rotina(cliente, parceiro)  # envia
    relogio.avancar(hours=3)
    _rotina(cliente, parceiro)  # a operadora informa
    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "solicitado"  # o bloqueio vem com o empréstimo (art. 8º)
    assert garantia["informacoes"]["valor_liquido"] == "18000.00"

    contratado = parceiro.post(
        f"/v1/contas/{conta}/emprestimos",
        {"valor": "10000.00", "parcelas": 12, "garantia_ids": [garantia_id]},
    )
    assert contratado.status_code == 201, contratado.text
    registrada = parceiro.garantia(garantia_id)
    assert registrada["estado"] == "registrado"
    assert registrada["gravame"]["protocolo"].startswith("SIM-plataforma_res_conjunta_12-")
    assert registrada["emprestimo_id"] is not None


def test_callback_da_operadora_com_recusa_e_invalido_pelo_art_18(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _conta, garantia_id, solicitacao_id = _previdencia(parceiro)
    resposta = _callback(
        cliente,
        relogio,
        "teste_confirma",
        _resposta("teste_confirma", solicitacao_id, StatusDaResposta.RECUSADA),
    )
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "resposta_invalida_para_o_trilho"
    assert "art. 18" in resposta.json()["detail"]
    assert parceiro.garantia(garantia_id)["estado"] == "solicitado"


# ================================================================ idempotência
def test_callback_duplicado_tem_um_efeito_so(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    conta, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    corpo = _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    primeira = _callback(cliente, relogio, "teste_confirma", corpo)
    segunda = _callback(cliente, relogio, "teste_confirma", corpo)
    assert primeira.json() == {"resultado": "aplicada"}
    assert segunda.json() == {"resultado": "duplicada"}
    assert parceiro.tipos_de_evento(conta).count("gravame.registrado") == 1
    assert _respostas(engine, garantia_id) == 1


def test_polling_e_callback_da_mesma_conclusao_se_reconhecem(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    conta, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    relogio.avancar(minutes=10)
    _rotina(cliente, parceiro)
    assert parceiro.garantia(garantia_id)["estado"] == "registrado"
    atrasado = _callback(
        cliente, relogio, "teste_confirma", _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    )
    assert atrasado.json() == {"resultado": "duplicada"}
    assert parceiro.tipos_de_evento(conta).count("gravame.registrado") == 1
    assert _respostas(engine, garantia_id) == 1


def test_resposta_para_pedido_ja_encerrado_e_obsoleta(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_mudo")
    relogio.avancar(days=8)
    _rotina(cliente, parceiro)
    assert parceiro.garantia(garantia_id)["estado"] == "cancelado"
    tardia = _callback(
        cliente, relogio, "teste_mudo", _resposta("teste_mudo", solicitacao_id, CONCLUIDA)
    )
    assert tardia.json() == {"resultado": "obsoleta"}
    assert parceiro.garantia(garantia_id)["estado"] == "cancelado"
    assert parceiro.limite(conta)["limite_total"] == "0.00"


# =================================================================== segurança
def test_assinatura_invalida_nao_muda_nada(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _conta, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    corpo = _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    errada = _callback(cliente, relogio, "teste_confirma", corpo, segredo="outro-segredo" * 4)
    sem_cabecalho = cliente.post(
        "/v1/contrapartes/teste_confirma/callbacks", content=json.dumps(corpo).encode()
    )
    assert errada.status_code == sem_cabecalho.status_code == 401
    assert errada.json()["codigo"] == "assinatura_invalida"
    assert _problema_sem_rastro(errada) == _problema_sem_rastro(sem_cabecalho)
    assert parceiro.garantia(garantia_id)["estado"] == "solicitado"


def test_corpo_alterado_depois_de_assinado_e_recusado(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    assinado = json.dumps(_resposta("teste_confirma", solicitacao_id, CONCLUIDA)).encode()
    adulterado = assinado.replace(b"concluida", b"recusada\\u0000")
    assinatura = assinar(
        SEGREDOS_DE_CONTRAPARTES["teste_confirma"], assinado, int(relogio.agora().timestamp())
    )
    resposta = cliente.post(
        "/v1/contrapartes/teste_confirma/callbacks",
        content=adulterado,
        headers={CABECALHO: assinatura},
    )
    assert resposta.status_code == 401
    assert parceiro.garantia(garantia_id)["estado"] == "solicitado"


def test_timestamp_fora_da_janela_e_recusado(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, config: Configuracao
) -> None:
    _, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    velho = _callback(
        cliente,
        relogio,
        "teste_confirma",
        _resposta("teste_confirma", solicitacao_id, CONCLUIDA),
        atraso_em_segundos=config.contrapartes_tolerancia_segundos + 1,
    )
    assert velho.status_code == 401
    assert parceiro.garantia(garantia_id)["estado"] == "solicitado"


def test_contraparte_desconhecida_responde_igual_a_assinatura_errada(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _, _, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    corpo = _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    desconhecida = _callback(cliente, relogio, "nao_existe", corpo, segredo="x" * 40)
    errada = _callback(cliente, relogio, "teste_confirma", corpo, segredo="x" * 40)
    assert desconhecida.status_code == errada.status_code == 401
    assert _problema_sem_rastro(desconhecida) == _problema_sem_rastro(errada)


def test_callback_de_outro_trilho_ou_pedido_inexistente_da_404_e_nao_muda_nada(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    de_outra = _callback(
        cliente, relogio, "teste_recusa", _resposta("teste_recusa", solicitacao_id, CONCLUIDA)
    )
    inexistente = _callback(
        cliente, relogio, "teste_confirma", _resposta("teste_confirma", "sol_nao_existe", CONCLUIDA)
    )
    assert de_outra.status_code == inexistente.status_code == 404
    assert de_outra.json()["codigo"] == "solicitacao_nao_encontrada"
    assert parceiro.garantia(garantia_id)["estado"] == "solicitado"


def test_callback_com_corpo_invalido_da_422_depois_de_autenticar(
    cliente: TestClient, relogio: RelogioFixo
) -> None:
    resposta = _callback(cliente, relogio, "teste_confirma", {"status": "talvez"})
    assert resposta.status_code == 422


# ===================================================================== sandbox
@pytest.mark.parametrize(
    ("acao", "estado"),
    [("concluir", "registrado"), ("recusar", "cancelado")],
)
def test_sandbox_decide_pela_contraparte(parceiro: Parceiro, acao: str, estado: str) -> None:
    _conta, garantia_id, _ = _transferencia(parceiro, "teste_confirma")
    resposta = parceiro.post(f"/v1/sandbox/garantias/{garantia_id}/contraparte", {"acao": acao})
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["garantia"]["estado"] == estado


def test_sandbox_nao_responder_aplica_a_regra_do_prazo_do_trilho(parceiro: Parceiro) -> None:
    _, td, _ = _td(parceiro, "teste_confirma")
    _, portabilidade, _ = _portabilidade(parceiro)
    parceiro.post(f"/v1/sandbox/garantias/{td}/contraparte", {"acao": "nao_responder"})
    parceiro.post(f"/v1/sandbox/garantias/{portabilidade}/contraparte", {"acao": "nao_responder"})
    assert parceiro.garantia(td)["estado"] == "recusado"
    marcada = parceiro.garantia(portabilidade)
    assert marcada["estado"] == "solicitado"
    assert marcada["solicitacao"]["prazo_regulatorio_vencido"] is True


def test_sandbox_sem_pedido_em_aberto_da_409(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia_id = parceiro.bloquear(conta, "cdb", "100.00")["garantia"]["id"]
    resposta = parceiro.post(
        f"/v1/sandbox/garantias/{garantia_id}/contraparte", {"acao": "concluir"}
    )
    assert resposta.status_code == 409


def test_garantia_de_outro_parceiro_nao_aparece_no_sandbox(
    cliente: TestClient, parceiro: Parceiro
) -> None:
    _, garantia_id, _ = _transferencia(parceiro, "teste_confirma")
    intruso = criar_parceiro(cliente, politica=politica_de_teste())
    resposta = intruso.post(
        f"/v1/sandbox/garantias/{garantia_id}/contraparte", {"acao": "concluir"}
    )
    assert resposta.status_code == 404
    assert parceiro.garantia(garantia_id)["estado"] == "solicitado"


# ============================================================ admin e legado
def test_confirmacao_administrativa_passa_pelo_mesmo_caso_de_uso(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    conta, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    caminho = f"/v1/admin/garantias/{garantia_id}/confirmacao-de-registro"
    assert cliente.post(caminho, headers=parceiro.cabecalhos).status_code == 401
    confirmada = cliente.post(caminho, headers=ADMIN)
    assert confirmada.status_code == 200, confirmada.text
    assert confirmada.json()["garantia"]["estado"] == "registrado"
    depois = _callback(
        cliente, relogio, "teste_confirma", _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    )
    assert depois.json() == {"resultado": "duplicada"}
    assert parceiro.tipos_de_evento(conta).count("gravame.registrado") == 1


def test_custodia_assincrona_deixa_o_gravame_solicitado_ate_a_confirmacao(
    config: Configuracao, engine: Engine, relogio: RelogioFixo
) -> None:
    app = criar_app(
        config,
        engine=engine,
        relogio=relogio,
        infraestrutura=InfraestruturaSimulada(confirma_na_hora=False),
    )
    with TestClient(app) as cliente:
        parceiro = criar_parceiro(cliente, politica=politica_de_teste())
        conta = parceiro.abrir_conta()
        resposta = parceiro.post(
            f"/v1/contas/{conta}/garantias",
            {"tipo_ativo": "cdb", "valor": "1000.00", "liquidez_dias": 0},
        )
        assert resposta.status_code == 202
        garantia = resposta.json()["garantia"]
        assert garantia["estado"] == "solicitado"
        assert resposta.json()["limite"]["limite_total"] == "0.00"
        assert parceiro.comprar(conta, "10.00").status_code == 402

        caminho = f"/v1/admin/garantias/{garantia['id']}/confirmacao-de-registro"
        confirmada = cliente.post(caminho, headers=ADMIN)
        assert confirmada.status_code == 200
        assert confirmada.json()["garantia"]["estado"] == "registrado"
        assert confirmada.json()["limite"]["limite_total"] == "850.00"
        assert cliente.post(caminho, headers=ADMIN).status_code == 409
        assert (
            cliente.post(
                "/v1/admin/garantias/gar_nao_existe/confirmacao-de-registro", headers=ADMIN
            ).status_code
            == 404
        )


def test_confirmacao_administrativa_de_fora_exige_sandbox(
    config: Configuracao, engine: Engine, relogio: RelogioFixo
) -> None:
    sem_sandbox = configuracao_de_teste(config.database_url, sandbox_habilitado=False)
    app = criar_app(
        sem_sandbox,
        engine=engine,
        relogio=relogio,
        contraparte=SimuladorDeContrapartes(PERFIS, relogio),
    )
    with TestClient(app) as cliente:
        parceiro = criar_parceiro(cliente, politica=politica_de_teste())
        _, garantia_id, _ = _transferencia(parceiro, "teste_mudo")
        resposta = cliente.post(
            f"/v1/admin/garantias/{garantia_id}/confirmacao-de-registro", headers=ADMIN
        )
        assert resposta.status_code == 409
        assert parceiro.garantia(garantia_id)["estado"] == "solicitado"


class _ForaDoAr(SimuladorDeContrapartes):
    def consultar(self, pedido):  # type: ignore[no-untyped-def]
        raise ConnectionError("contraparte fora do ar")

    solicitar_entrada = consultar


def test_contraparte_fora_do_ar_ainda_deixa_o_prazo_valer(
    config: Configuracao, engine: Engine, relogio: RelogioFixo
) -> None:
    app = criar_app(config, engine=engine, relogio=relogio, contraparte=_ForaDoAr(PERFIS, relogio))
    with TestClient(app) as cliente:
        parceiro = criar_parceiro(cliente, politica=politica_de_teste())
        _, garantia_id, _ = _transferencia(parceiro, "teste_confirma")
        assert _rotina(cliente, parceiro)["contas_com_erro"] == 1  # o erro aparece
        assert parceiro.garantia(garantia_id)["estado"] == "solicitado"
        relogio.avancar(days=8)
        assert _rotina(cliente, parceiro)["contas_com_erro"] == 0
        assert parceiro.garantia(garantia_id)["estado"] == "cancelado"


# ================================================================ concorrência
@pytest.mark.postgres
def test_callbacks_iguais_simultaneos_tem_um_efeito_so(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    """Dez cópias da mesma conclusão ao mesmo tempo: uma aplica, nove são duplicadas."""
    conta, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    corpo = _resposta("teste_confirma", solicitacao_id, CONCLUIDA)
    respostas = _disparar_juntas(10, lambda _: _callback(cliente, relogio, "teste_confirma", corpo))
    assert {r.status_code for r in respostas} == {200}
    assert Counter(r.json()["resultado"] for r in respostas) == {"aplicada": 1, "duplicada": 9}
    assert parceiro.tipos_de_evento(conta).count("gravame.registrado") == 1
    assert _respostas(engine, garantia_id) == 1


@pytest.mark.postgres
def test_callback_e_polling_simultaneos_tem_um_efeito_so(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    conta, garantia_id, solicitacao_id = _transferencia(parceiro, "teste_confirma")
    _rotina(cliente, parceiro)
    relogio.avancar(minutes=10)
    corpo = _resposta("teste_confirma", solicitacao_id, CONCLUIDA)

    def tarefa(indice: int) -> object:
        if indice % 2:
            return _rotina(cliente, parceiro)
        return _callback(cliente, relogio, "teste_confirma", corpo)

    _disparar_juntas(8, tarefa)
    assert parceiro.garantia(garantia_id)["estado"] == "registrado"
    assert parceiro.tipos_de_evento(conta).count("gravame.registrado") == 1
    assert _respostas(engine, garantia_id) == 1
