"""Fase 2 pela API: garantia em custodiante externo (simulado).

Os custodiantes destes testes são perfis do simulador com destino fixo, para o teste
não depender do sorteio: `teste_confirma`, `teste_recusa` e `teste_mudo` (nunca
responde). Todos respondem 10 minutos depois do pedido, com prazo de 1 hora.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from app.api.fabrica import criar_app
from app.aplicacao.portas import StatusDaResposta
from app.config import Configuracao
from app.infra.banco.modelos import RespostaDeCustodianteModelo
from app.infra.custodia import (
    CustodiaAssincrona,
    PerfilDeCustodiante,
    SimuladorDeCustodiantes,
    evento_id_do_custodiante,
)
from app.infra.relogio import RelogioFixo
from app.infra.webhooks.assinatura import assinar
from tests.conftest import (
    ADMIN,
    SEGREDOS_DE_CUSTODIA,
    Parceiro,
    criar_parceiro,
    politica_de_teste,
)
from tests.integracao.test_concorrencia import _disparar_juntas

CABECALHO = "X-Custodiante-Assinatura"


def _perfil(id: str, recusa: str, mudo: str) -> PerfilDeCustodiante:
    return PerfilDeCustodiante(
        id=id,
        nome=f"{id} (simulador)",
        descricao="Perfil de teste.",
        semente=1,
        latencia=timedelta(minutes=10),
        prazo_de_resposta=timedelta(hours=1),
        probabilidade_de_recusa=Decimal(recusa),
        probabilidade_sem_resposta=Decimal(mudo),
        fator_de_preco_na_venda=Decimal("0.98"),
        motivos_de_recusa=("ativo_ja_onerado",),
    )


PERFIS = [
    _perfil("teste_confirma", "0", "0"),
    _perfil("teste_recusa", "1", "0"),
    _perfil("teste_mudo", "0", "1"),
]


@pytest.fixture
def app(config: Configuracao, engine: Engine, relogio: RelogioFixo) -> FastAPI:
    return criar_app(
        config, engine=engine, relogio=relogio, custodiante=SimuladorDeCustodiantes(PERFIS, relogio)
    )


# ------------------------------------------------------------------ apoio
def _rotina(cliente: TestClient, parceiro: Parceiro) -> dict[str, Any]:
    resposta = cliente.post(
        "/v1/admin/rotinas/custodia", params={"parceiro_id": parceiro.id}, headers=ADMIN
    )
    assert resposta.status_code == 200, resposta.text
    corpo: dict[str, Any] = resposta.json()
    return corpo


def _callback(
    cliente: TestClient,
    relogio: RelogioFixo,
    custodiante_id: str,
    corpo: dict[str, Any],
    *,
    segredo: str | None = None,
    atraso_em_segundos: int = 0,
) -> Any:
    bruto = json.dumps(corpo).encode()
    instante = int(relogio.agora().timestamp()) - atraso_em_segundos
    assinatura = assinar(
        segredo or SEGREDOS_DE_CUSTODIA.get(custodiante_id, "x" * 40), bruto, instante
    )
    return cliente.post(
        f"/v1/custodiantes/{custodiante_id}/callbacks",
        content=bruto,
        headers={CABECALHO: assinatura, "Content-Type": "application/json"},
    )


def _confirmacao(custodiante_id: str, solicitacao_id: str, **extra: str) -> dict[str, Any]:
    return {
        "evento_id": evento_id_do_custodiante(
            custodiante_id, solicitacao_id, StatusDaResposta.CONFIRMADA
        ),
        "solicitacao_id": solicitacao_id,
        "status": "confirmada",
        **extra,
    }


def _pedir(
    parceiro: Parceiro, custodiante_id: str, valor: str = "10000.00"
) -> tuple[str, str, str]:
    conta = parceiro.abrir_conta()
    corpo = parceiro.bloquear_externa(conta, "tesouro_selic", valor, custodiante_id)
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


def _lancamentos_da_garantia(parceiro: Parceiro, conta: str, garantia_id: str) -> list[str]:
    resposta = parceiro.get(f"/v1/contas/{conta}/lancamentos", params={"limite": 100})
    assert resposta.status_code == 200, resposta.text
    return [
        item["tipo"] for item in resposta.json()["dados"] if item["referencia_id"] == garantia_id
    ]


# ================================================================== catálogo
def test_custodiantes_aparecem_como_simuladores(parceiro: Parceiro) -> None:
    resposta = parceiro.get("/v1/custodiantes")
    assert resposta.status_code == 200
    custodiantes = resposta.json()
    assert [c["id"] for c in custodiantes] == ["teste_confirma", "teste_recusa", "teste_mudo"]
    assert all(c["simulado"] for c in custodiantes)
    assert custodiantes[0]["prazo_de_resposta_minutos"] == 60


def test_custodiante_desconhecido_no_pedido(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    resposta = parceiro.post(
        f"/v1/contas/{conta}/garantias",
        {"tipo_ativo": "cdb", "valor": "10.00", "custodiante_id": "nao_existe"},
    )
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "custodiante_nao_configurado"


# ================================================================ fluxo feliz
def test_fluxo_feliz_por_polling(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, _ = _pedir(parceiro, "teste_confirma")
    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "solicitada"
    assert garantia["origem"] == "externa"
    assert garantia["custodiante_id"] == "teste_confirma"
    assert parceiro.limite(conta)["limite_total"] == "0.00"

    # O worker envia o pedido; o custodiante aceita e ainda não respondeu.
    _rotina(cliente, parceiro)
    assert parceiro.garantia(garantia_id)["estado"] == "gravame_pendente"
    assert parceiro.limite(conta)["limite_total"] == "0.00"

    relogio.avancar(minutes=10)
    _rotina(cliente, parceiro)
    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "bloqueada"
    assert garantia["solicitacao"] is None
    assert parceiro.limite(conta)["limite_total"] == "9500.00"
    tipos = parceiro.tipos_de_evento(conta)
    assert tipos == [
        "gravame.solicitado",
        "gravame.confirmado",
        "garantia.bloqueada",
        "limite.atualizado",
    ]
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == ["bloqueio_garantia"]


def test_fluxo_feliz_por_callback(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    resposta = _callback(
        cliente, relogio, "teste_confirma", _confirmacao("teste_confirma", solicitacao_id)
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"resultado": "aplicada"}
    assert parceiro.garantia(garantia_id)["estado"] == "bloqueada"
    assert parceiro.limite(conta)["limite_total"] == "9500.00"


# ===================================================================== desvios
def test_recusa(cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo) -> None:
    conta, garantia_id, _ = _pedir(parceiro, "teste_recusa")
    _rotina(cliente, parceiro)
    relogio.avancar(minutes=10)
    _rotina(cliente, parceiro)

    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "gravame_recusado"
    assert garantia["motivo"] == "ativo_ja_onerado"
    assert parceiro.limite(conta)["limite_total"] == "0.00"
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == []
    recusa = parceiro.eventos(conta)[-1]
    assert recusa["tipo"] == "gravame.recusado"
    assert recusa["dados"]["motivo"] == "ativo_ja_onerado"


def test_timeout_expira_e_avisa_o_parceiro(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, _ = _pedir(parceiro, "teste_mudo")
    _rotina(cliente, parceiro)
    relogio.avancar(minutes=59)
    _rotina(cliente, parceiro)
    assert parceiro.garantia(garantia_id)["estado"] == "gravame_pendente"

    relogio.avancar(minutes=2)
    resumo = _rotina(cliente, parceiro)
    assert resumo["contas_com_erro"] == 0
    assert parceiro.garantia(garantia_id)["estado"] == "gravame_expirado"
    assert parceiro.tipos_de_evento(conta)[-1] == "gravame.expirado"
    assert parceiro.limite(conta)["limite_total"] == "0.00"
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == []


def test_pendente_legado_tambem_expira(
    config: Configuracao, engine: Engine, relogio: RelogioFixo
) -> None:
    app = criar_app(config, engine=engine, relogio=relogio, custodiante=CustodiaAssincrona())
    with TestClient(app) as cliente:
        parceiro = criar_parceiro(cliente, politica=politica_de_teste())
        conta = parceiro.abrir_conta()
        resposta = parceiro.post(
            f"/v1/contas/{conta}/garantias", {"tipo_ativo": "cdb", "valor": "100.00"}
        )
        assert resposta.status_code == 202
        garantia_id = resposta.json()["garantia"]["id"]
        relogio.avancar(minutes=config.custodia_prazo_legado_minutos + 1)
        _rotina(cliente, parceiro)
        assert parceiro.garantia(garantia_id)["estado"] == "gravame_expirado"


# ================================================================ idempotência
def test_callback_duplicado_tem_um_efeito_so(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    corpo = _confirmacao("teste_confirma", solicitacao_id)
    primeira = _callback(cliente, relogio, "teste_confirma", corpo)
    segunda = _callback(cliente, relogio, "teste_confirma", corpo)
    assert primeira.json() == {"resultado": "aplicada"}
    assert segunda.status_code == 200
    assert segunda.json() == {"resultado": "duplicada"}
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == ["bloqueio_garantia"]
    assert _respostas(engine, garantia_id) == 1


def test_polling_e_depois_callback_da_mesma_confirmacao(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    relogio.avancar(minutes=10)
    _rotina(cliente, parceiro)
    assert parceiro.garantia(garantia_id)["estado"] == "bloqueada"

    atrasado = _callback(
        cliente, relogio, "teste_confirma", _confirmacao("teste_confirma", solicitacao_id)
    )
    assert atrasado.json() == {"resultado": "duplicada"}
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == ["bloqueio_garantia"]
    assert _respostas(engine, garantia_id) == 1


def test_callback_e_depois_polling_da_mesma_confirmacao(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    _callback(cliente, relogio, "teste_confirma", _confirmacao("teste_confirma", solicitacao_id))
    relogio.avancar(minutes=10)
    _rotina(cliente, parceiro)
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == ["bloqueio_garantia"]
    assert parceiro.tipos_de_evento(conta).count("garantia.bloqueada") == 1


def test_resposta_para_pedido_ja_encerrado_e_obsoleta(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_mudo")
    relogio.avancar(minutes=61)
    _rotina(cliente, parceiro)
    assert parceiro.garantia(garantia_id)["estado"] == "gravame_expirado"

    tardia = _callback(cliente, relogio, "teste_mudo", _confirmacao("teste_mudo", solicitacao_id))
    assert tardia.status_code == 200
    assert tardia.json() == {"resultado": "obsoleta"}
    assert parceiro.garantia(garantia_id)["estado"] == "gravame_expirado"
    assert parceiro.limite(conta)["limite_total"] == "0.00"


# =================================================================== segurança
def _problema_sem_rastro(resposta: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = resposta.json()
    corpo.pop("instance", None)
    corpo.pop("request_id", None)
    return corpo


def test_assinatura_invalida_nao_muda_nada(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    corpo = _confirmacao("teste_confirma", solicitacao_id)

    errada = _callback(cliente, relogio, "teste_confirma", corpo, segredo="outro-segredo" * 4)
    sem_cabecalho = cliente.post(
        "/v1/custodiantes/teste_confirma/callbacks", content=json.dumps(corpo).encode()
    )
    assert errada.status_code == 401
    assert sem_cabecalho.status_code == 401
    assert errada.json()["codigo"] == "assinatura_invalida"
    # Nada de pista sobre o que deu errado.
    assert _problema_sem_rastro(errada) == _problema_sem_rastro(sem_cabecalho)
    assert parceiro.garantia(garantia_id)["estado"] == "solicitada"
    assert parceiro.limite(conta)["limite_total"] == "0.00"


def test_corpo_alterado_depois_de_assinado_e_recusado(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    assinado = json.dumps(_confirmacao("teste_confirma", solicitacao_id)).encode()
    adulterado = assinado.replace(b"confirmada", b"recusada\\u0000")
    assinatura = assinar(
        SEGREDOS_DE_CUSTODIA["teste_confirma"], assinado, int(relogio.agora().timestamp())
    )
    resposta = cliente.post(
        "/v1/custodiantes/teste_confirma/callbacks",
        content=adulterado,
        headers={CABECALHO: assinatura},
    )
    assert resposta.status_code == 401
    assert parceiro.garantia(garantia_id)["estado"] == "solicitada"


def test_timestamp_fora_da_janela_e_recusado(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, config: Configuracao
) -> None:
    _, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    velho = _callback(
        cliente,
        relogio,
        "teste_confirma",
        _confirmacao("teste_confirma", solicitacao_id),
        atraso_em_segundos=config.custodia_tolerancia_segundos + 1,
    )
    assert velho.status_code == 401
    assert velho.json()["codigo"] == "assinatura_invalida"
    assert parceiro.garantia(garantia_id)["estado"] == "solicitada"


def test_custodiante_desconhecido_responde_igual_a_assinatura_errada(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _, _, solicitacao_id = _pedir(parceiro, "teste_confirma")
    corpo = _confirmacao("teste_confirma", solicitacao_id)
    desconhecido = _callback(cliente, relogio, "nao_existe", corpo, segredo="x" * 40)
    errada = _callback(cliente, relogio, "teste_confirma", corpo, segredo="x" * 40)
    assert desconhecido.status_code == errada.status_code == 401
    assert _problema_sem_rastro(desconhecido) == _problema_sem_rastro(errada)


def test_pedido_de_outro_custodiante_ou_inexistente_da_404_depois_de_autenticar(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    de_outro = _callback(
        cliente, relogio, "teste_recusa", _confirmacao("teste_recusa", solicitacao_id)
    )
    inexistente = _callback(
        cliente, relogio, "teste_confirma", _confirmacao("teste_confirma", "sol_nao_existe")
    )
    assert de_outro.status_code == inexistente.status_code == 404
    assert de_outro.json()["codigo"] == "solicitacao_nao_encontrada"
    assert _problema_sem_rastro(de_outro)["detail"] == _problema_sem_rastro(inexistente)["detail"]
    assert parceiro.garantia(garantia_id)["estado"] == "solicitada"


def test_callback_com_corpo_invalido_da_422_depois_de_autenticar(
    cliente: TestClient, relogio: RelogioFixo
) -> None:
    resposta = _callback(cliente, relogio, "teste_confirma", {"status": "talvez"})
    assert resposta.status_code == 422


# ============================================================ liberação externa
def _bloqueada_por_callback(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> tuple[str, str]:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    _callback(cliente, relogio, "teste_confirma", _confirmacao("teste_confirma", solicitacao_id))
    return conta, garantia_id


def test_liberacao_externa(cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo) -> None:
    conta, garantia_id = _bloqueada_por_callback(cliente, parceiro, relogio)
    resposta = parceiro.post(f"/v1/garantias/{garantia_id}/liberacao", None)
    assert resposta.status_code == 202, resposta.text
    corpo = resposta.json()
    assert corpo["garantia"]["estado"] == "liberacao_pendente"
    assert corpo["limite"]["limite_total"] == "0.00"
    solicitacao_id = corpo["garantia"]["solicitacao"]["id"]

    confirmada = _callback(
        cliente, relogio, "teste_confirma", _confirmacao("teste_confirma", solicitacao_id)
    )
    assert confirmada.json() == {"resultado": "aplicada"}
    assert parceiro.garantia(garantia_id)["estado"] == "liberada"
    assert parceiro.tipos_de_evento(conta)[-3:] == [
        "limite.atualizado",
        "liberacao.confirmada",
        "garantia.liberada",
    ]
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == [
        "liberacao_garantia",
        "bloqueio_garantia",
    ]


# ============================================================= execução externa
def test_execucao_externa(cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo) -> None:
    conta, garantia_id = _bloqueada_por_callback(cliente, parceiro, relogio)
    assert parceiro.comprar(conta, "5000.00").status_code == 201
    assert parceiro.fechar_fatura(conta, relogio.hoje().isoformat()).status_code == 201
    assert parceiro.avancar_dias(conta, 7).status_code == 200

    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "execucao_solicitada"
    assert garantia["valor_em_execucao"] == "5000.00"
    assert parceiro.conta(conta)["fatura_em_aberto"]["estado"] == "em_atraso"

    # O worker envia o pedido; dez minutos depois o custodiante vende (fator 0,98).
    _rotina(cliente, parceiro)
    relogio.avancar(minutes=10)
    _rotina(cliente, parceiro)
    garantia = parceiro.garantia(garantia_id)
    assert garantia["estado"] == "parcialmente_executada"
    assert garantia["valor_de_mercado"] == "5000.00"
    assert parceiro.limite(conta)["divida_total"] == "100.00"
    tipos = parceiro.tipos_de_evento(conta)
    assert "execucao.solicitada" in tipos
    assert "execucao.confirmada" in tipos

    verificacao = cliente.get("/v1/admin/ledger/verificacao", headers=ADMIN).json()
    assert verificacao["fecha"] is True


# ==================================================== atalhos: admin e sandbox
def test_confirmacao_administrativa_passa_pelo_mesmo_caso_de_uso(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_mudo")
    resposta = cliente.post(
        f"/v1/admin/garantias/{garantia_id}/confirmacao-de-bloqueio", headers=ADMIN
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["garantia"]["estado"] == "bloqueada"
    assert _respostas(engine, garantia_id) == 1
    # Se a confirmação do custodiante chegar depois, é a mesma: não aplica de novo.
    tardia = _callback(cliente, relogio, "teste_mudo", _confirmacao("teste_mudo", solicitacao_id))
    assert tardia.json() == {"resultado": "duplicada"}
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == ["bloqueio_garantia"]


@pytest.mark.parametrize(
    ("acao", "estado"),
    [
        ("confirmar", "bloqueada"),
        ("recusar", "gravame_recusado"),
        ("nao_responder", "gravame_expirado"),
    ],
)
def test_sandbox_decide_pelo_custodiante(parceiro: Parceiro, acao: str, estado: str) -> None:
    _, garantia_id, _ = _pedir(parceiro, "teste_mudo")
    resposta = parceiro.post(
        f"/v1/sandbox/garantias/{garantia_id}/custodiante", {"acao": acao}, chave=None
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["garantia"]["estado"] == estado


def test_sandbox_decide_a_execucao_com_valor(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta, garantia_id = _bloqueada_por_callback(cliente, parceiro, relogio)
    parceiro.comprar(conta, "5000.00")
    parceiro.fechar_fatura(conta, relogio.hoje().isoformat())
    parceiro.avancar_dias(conta, 7)
    resposta = parceiro.post(
        f"/v1/sandbox/garantias/{garantia_id}/custodiante",
        {"acao": "confirmar", "valor_obtido": "5000.00"},
        chave=None,
    )
    assert resposta.status_code == 200, resposta.text
    assert parceiro.limite(conta)["divida_total"] == "0.00"
    assert parceiro.conta(conta)["estado"] == "ativa"


def test_sandbox_sem_pedido_em_aberto_da_409(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    _, garantia_id = _bloqueada_por_callback(cliente, parceiro, relogio)
    resposta = parceiro.post(
        f"/v1/sandbox/garantias/{garantia_id}/custodiante", {"acao": "confirmar"}, chave=None
    )
    assert resposta.status_code == 409
    assert resposta.json()["codigo"] == "solicitacao_divergente"


def test_garantia_de_outro_parceiro_nao_aparece_no_sandbox(
    cliente: TestClient, parceiro: Parceiro
) -> None:
    _, garantia_id, _ = _pedir(parceiro, "teste_mudo")
    outro = criar_parceiro(cliente, politica=politica_de_teste())
    resposta = outro.post(
        f"/v1/sandbox/garantias/{garantia_id}/custodiante", {"acao": "confirmar"}, chave=None
    )
    assert resposta.status_code == 404


# ===================================================================== fase 1
def test_a_fase_1_continua_igual(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    corpo = parceiro.bloquear(conta, "cdb", "1000.00")
    assert corpo["garantia"]["estado"] == "bloqueada"
    assert corpo["garantia"]["origem"] == "interna"
    assert corpo["garantia"]["custodiante_id"] is None
    assert corpo["garantia"]["solicitacao"] is None
    assert parceiro.tipos_de_evento(conta) == ["garantia.bloqueada", "limite.atualizado"]


# ================================================================ concorrência
@pytest.mark.postgres
def test_callbacks_iguais_simultaneos_tem_um_efeito_so(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    """Dez cópias da mesma confirmação ao mesmo tempo: uma aplica, nove são duplicadas."""
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    corpo = _confirmacao("teste_confirma", solicitacao_id)

    respostas = _disparar_juntas(10, lambda _: _callback(cliente, relogio, "teste_confirma", corpo))

    assert {r.status_code for r in respostas} == {200}
    assert Counter(r.json()["resultado"] for r in respostas) == {"aplicada": 1, "duplicada": 9}
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == ["bloqueio_garantia"]
    assert parceiro.tipos_de_evento(conta).count("gravame.confirmado") == 1
    assert _respostas(engine, garantia_id) == 1


@pytest.mark.postgres
def test_callback_e_polling_simultaneos_tem_um_efeito_so(
    cliente: TestClient, parceiro: Parceiro, relogio: RelogioFixo, engine: Engine
) -> None:
    conta, garantia_id, solicitacao_id = _pedir(parceiro, "teste_confirma")
    _rotina(cliente, parceiro)  # pedido aceito
    relogio.avancar(minutes=10)  # a resposta já está pronta para a consulta
    corpo = _confirmacao("teste_confirma", solicitacao_id)

    def tarefa(indice: int) -> object:
        if indice % 2:
            return _rotina(cliente, parceiro)
        return _callback(cliente, relogio, "teste_confirma", corpo)

    _disparar_juntas(8, tarefa)

    assert parceiro.garantia(garantia_id)["estado"] == "bloqueada"
    assert _lancamentos_da_garantia(parceiro, conta, garantia_id) == ["bloqueio_garantia"]
    assert parceiro.tipos_de_evento(conta).count("garantia.bloqueada") == 1
    assert _respostas(engine, garantia_id) == 1
