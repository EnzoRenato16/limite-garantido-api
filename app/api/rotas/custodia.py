"""Contrapartes dos trilhos (simuladas): catálogo, callback assinado e sandbox.

As rotas antigas do custodiante externo respondem 410 (`rota_substituida`, com `rota_nova`).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import ValidationError

from app.api import esquemas
from app.api.dependencias import ParceiroAutenticado, RecursosDaApi, Unidade, exigir_sandbox
from app.api.erros import ErroDeApi, Problema
from app.api.rotas.contas import ERROS_COMUNS, IdDaGarantia, resposta_da_garantia
from app.aplicacao import contrapartes as casos
from app.aplicacao import trilhos
from app.aplicacao.erros import ContraparteNaoConfigurada
from app.aplicacao.portas import RespostaDaContraparte, StatusDaResposta
from app.dominio.dinheiro import Dinheiro
from app.infra.contrapartes import SimuladorDeContrapartes
from app.infra.webhooks.assinatura import AssinaturaInvalida, verificar_assinatura

log = logging.getLogger(__name__)

CABECALHO_DE_ASSINATURA = "X-Contraparte-Assinatura"
# Para contraparte desconhecida a conta do HMAC é feita mesmo assim, com este segredo
# descartável: o tempo de resposta não diz se o id existe.
_SEGREDO_DESCARTAVEL = "segredo-descartavel-para-tempo-constante-0000"

rotas_de_contrapartes = APIRouter(
    prefix="/v1/contrapartes", tags=["Contrapartes (simuladas)"], responses=ERROS_COMUNS
)
# O callback não usa API key de parceiro: quem chama é a contraparte, e ela se prova
# pela assinatura. Por isso não herda as respostas de erro comuns da API de parceiro.
rotas_de_callback = APIRouter(prefix="/v1/contrapartes", tags=["Contrapartes (simuladas)"])
rotas_de_sandbox_de_contrapartes = APIRouter(
    prefix="/v1/sandbox",
    tags=["Sandbox"],
    responses=ERROS_COMUNS,
    dependencies=[Depends(exigir_sandbox)],
)
# Rotas que existiam e foram substituídas.
rotas_substituidas = APIRouter(tags=["Rotas substituídas"], deprecated=True)

IdDaContraparte = Path(
    pattern=r"^[A-Za-z0-9_]{1,40}$",
    description="Id da contraparte, como em GET /v1/contrapartes",
    examples=["banco_de_origem_simulado"],
)


# ------------------------------------------------------------------- 410
def _substituida(rota_nova: str) -> ErroDeApi:
    return ErroDeApi(
        "rota_substituida",
        "O fluxo de custodiante externo foi substituído pelos trilhos de entrada",
        rota_nova=rota_nova,
    )


@rotas_substituidas.get(
    "/v1/custodiantes",
    status_code=410,
    responses={410: {"model": Problema, "description": "Substituída por GET /v1/contrapartes"}},
    summary="(Removida) Listar custodiantes",
)
def listar_custodiantes_removido() -> None:
    """Responde 410: use `GET /v1/contrapartes`."""
    raise _substituida("GET /v1/contrapartes")


@rotas_substituidas.post(
    "/v1/custodiantes/{custodiante_id}/callbacks",
    status_code=410,
    responses={410: {"model": Problema, "description": "Substituída"}},
    summary="(Removido) Callback de custodiante",
)
def callback_de_custodiante_removido(custodiante_id: str) -> None:
    """Responde 410: use `POST /v1/contrapartes/{contraparte_id}/callbacks`."""
    raise _substituida("POST /v1/contrapartes/{contraparte_id}/callbacks")


@rotas_substituidas.post(
    "/v1/sandbox/garantias/{garantia_id}/custodiante",
    status_code=410,
    responses={410: {"model": Problema, "description": "Substituída"}},
    summary="(Removido) Decidir no lugar do custodiante",
)
def decisao_de_custodiante_removida(garantia_id: str) -> None:
    """Responde 410: use `POST /v1/sandbox/garantias/{garantia_id}/contraparte`."""
    raise _substituida("POST /v1/sandbox/garantias/{garantia_id}/contraparte")


# ----------------------------------------------------------- contrapartes
@rotas_de_contrapartes.get(
    "", response_model=list[esquemas.ContraparteResposta], summary="Listar contrapartes"
)
def listar_contrapartes(
    _: ParceiroAutenticado, recursos: RecursosDaApi
) -> list[esquemas.ContraparteResposta]:
    """Contrapartes aceitas em `contraparte_id` nos trilhos de entrada de fora.

    **Todas são simuladas.** Este projeto não integra com nenhuma instituição real; os perfis
    (tempo de resposta, chance de recusa, chance de não responder) são fictícios e estão em
    `app/contrapartes_simuladas.toml`.
    """
    return [
        esquemas.ContraparteResposta(
            id=info.id,
            nome=info.nome,
            descricao=info.descricao,
            simulado=info.simulado,
            trilhos=[t.value for t in info.trilhos],
        )
        for info in recursos.contraparte.contrapartes()
    ]


async def _corpo_bruto(request: Request) -> bytes:
    """A assinatura é sobre os bytes exatos que chegaram, antes de qualquer parse."""
    return await request.body()


@rotas_de_callback.post(
    "/{contraparte_id}/callbacks",
    response_model=esquemas.ResultadoDoCallback,
    summary="Receber a resposta de uma contraparte (callback assinado)",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/CallbackDaContraparte"}
                }
            },
        }
    },
    responses={
        401: {"model": Problema, "description": "Assinatura ausente, errada ou fora da janela"},
        404: {"model": Problema, "description": "Pedido não encontrado para esta contraparte"},
        422: {
            "model": Problema,
            "description": "Corpo inválido, ou resposta que o trilho não admite (a entidade "
            "operadora não recusa: Res. Conjunta 12, art. 18)",
        },
    },
)
def receber_callback(
    corpo: Annotated[bytes, Depends(_corpo_bruto)],
    request: Request,
    recursos: RecursosDaApi,
    contraparte_id: str = IdDaContraparte,
) -> esquemas.ResultadoDoCallback:
    """Por onde a contraparte avisa que concluiu, recusou ou impediu um pedido.

    **Autenticação.** Não há API key: a contraparte assina o corpo com o segredo dela,

        X-Contraparte-Assinatura: t=<unix>,v1=<hex>

    onde `v1` é o HMAC-SHA256 de `"<t>.<corpo>"`, no mesmo formato dos webhooks que a
    plataforma envia. Timestamp fora da janela (5 minutos por padrão) é recusado. Qualquer
    falha de autenticação, inclusive contraparte inexistente, responde o mesmo `401`.

    **Idempotência.** A mesma resposta (mesmo `evento_id`), enviada de novo ou já obtida pela
    consulta de reserva, é registrada uma vez só e responde `duplicada`. A chave é
    (`contraparte_id`, `evento_id`).

    **Trilho.** Callback de contraparte que não é a do pedido responde 404 e não muda nada.
    `recusada` vale no TD Garantia e na transferência; `impedimento`, na portabilidade
    (Res. CVM 210, art. 19); a entidade operadora não recusa (Res. Conjunta 12, art. 18).
    """
    _autenticar(recursos, contraparte_id, corpo, request.headers.get(CABECALHO_DE_ASSINATURA))
    try:
        dados = esquemas.CallbackDaContraparte.model_validate(json.loads(corpo))
    except (ValueError, ValidationError) as erro:
        raise ErroDeApi(
            "requisicao_invalida", "O corpo do callback não está no formato esperado"
        ) from erro
    resposta = RespostaDaContraparte(
        solicitacao_id=dados.solicitacao_id,
        status=StatusDaResposta(dados.status),
        evento_id=dados.evento_id,
        motivo=dados.motivo,
        valor_disponivel=None
        if dados.valor_disponivel is None
        else Dinheiro.de_decimal(dados.valor_disponivel),
        tributos_informados=None
        if dados.tributos_informados is None
        else Dinheiro.de_decimal(dados.tributos_informados),
    )
    with recursos.abrir_unidade() as uow:
        resultado, _, _ = casos.aplicar_resposta(
            uow, contraparte_id, resposta, casos.Canal.CALLBACK
        )
        uow.commit()
    log.info(
        "Callback de contraparte",
        extra={
            "contraparte_id": contraparte_id,
            "evento_id": dados.evento_id,
            "solicitacao_id": dados.solicitacao_id,
            "resultado": str(resultado),
        },
    )
    return esquemas.ResultadoDoCallback(resultado=resultado.value)


def _autenticar(
    recursos: RecursosDaApi, contraparte_id: str, corpo: bytes, cabecalho: str | None
) -> None:
    segredo = recursos.config.contrapartes_segredos.get(contraparte_id)
    conhecido = segredo is not None and recursos.contraparte.conhece(contraparte_id)
    valor = segredo.get_secret_value() if segredo is not None else _SEGREDO_DESCARTAVEL
    try:
        verificar_assinatura(
            valor,
            corpo,
            cabecalho,
            tolerancia_em_segundos=recursos.config.contrapartes_tolerancia_segundos,
            agora=int(recursos.relogio.agora().timestamp()),
        )
        motivo = None
    except AssinaturaInvalida as erro:
        motivo = str(erro)
    if motivo is not None or not conhecido:
        # O motivo vai só para o log; quem chamou recebe sempre a mesma resposta.
        log.warning(
            "Callback de contraparte recusado",
            extra={
                "contraparte_id": contraparte_id,
                "motivo": motivo or "contraparte sem segredo configurado",
            },
        )
        raise ErroDeApi("assinatura_invalida", "Não foi possível autenticar o callback")


# ------------------------------------------------------------------ sandbox
def _simulador(recursos: RecursosDaApi) -> SimuladorDeContrapartes:
    contraparte = recursos.contraparte
    if not isinstance(contraparte, SimuladorDeContrapartes):
        raise ContraparteNaoConfigurada("O simulador de contrapartes não está ligado")
    return contraparte


@rotas_de_sandbox_de_contrapartes.post(
    "/garantias/{garantia_id}/contraparte",
    response_model=esquemas.GarantiaComLimite,
    summary="Decidir no lugar da contraparte simulada",
)
def decidir_no_sandbox(
    garantia_id: IdDaGarantia,
    corpo: esquemas.DecisaoNoSandbox,
    parceiro: ParceiroAutenticado,
    recursos: RecursosDaApi,
    uow: Unidade,
) -> esquemas.GarantiaComLimite:
    """Só existe com `SANDBOX_HABILITADO=true`.

    Faz a contraparte simulada responder agora ao pedido de entrada em aberto da garantia:
    `concluir`, `recusar` (impedimento, na portabilidade) ou `nao_responder` (vence o prazo e
    vale a regra do trilho: transferência e TD cancelam ou recusam; portabilidade e Res.
    Conjunta 12 continuam, marcadas). Concluir e recusar passam pelo mesmo caso de uso do
    callback.
    """
    simulador = _simulador(recursos)
    _, conta, garantia = casos.decidir_pela_contraparte(
        uow,
        garantia_id,
        casos.AcaoDoSandbox(corpo.acao),
        simulador.resposta,
        casos.Canal.SANDBOX,
        parceiro_id=parceiro.id,
    )
    resposta = resposta_da_garantia(uow, parceiro, conta, garantia)
    uow.commit()
    return resposta


@rotas_de_sandbox_de_contrapartes.post(
    "/garantias/{garantia_id}/evento-de-custodia",
    response_model=esquemas.GarantiaComLimite,
    summary="Simular cupom ou amortização do Tesouro",
)
def evento_de_custodia(
    garantia_id: IdDaGarantia,
    corpo: esquemas.EventoDeCustodiaNoSandbox,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
) -> esquemas.GarantiaComLimite:
    """Só existe com `SANDBOX_HABILITADO=true`.

    O Tesouro paga cupom ou amortização ao agente de custódia do garantido (TD Garantia, item
    127). O valor fica **vinculado** à garantia: o valor dela não muda, e a execução vende o
    título e leva os cupons. Só para ativos `tesouro_*` com gravame `registrado`.
    """
    conta, garantia = trilhos.receber_evento_de_custodia(
        uow, parceiro, garantia_id, Dinheiro.de_decimal(corpo.valor)
    )
    resposta = resposta_da_garantia(uow, parceiro, conta, garantia)
    uow.commit()
    return resposta
