"""Custodiante externo (fase 2, simulada): catálogo, callback assinado e sandbox."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import ValidationError

from app.api import apresentacao, esquemas
from app.api.dependencias import ParceiroAutenticado, RecursosDaApi, Unidade, exigir_sandbox
from app.api.erros import ErroDeApi, Problema
from app.api.rotas.contas import ERROS_COMUNS, IdDaGarantia
from app.aplicacao import custodia as casos
from app.aplicacao.erros import CustodianteNaoConfigurado
from app.aplicacao.portas import RespostaDoCustodiante, StatusDaResposta
from app.dominio.dinheiro import Dinheiro
from app.infra.custodia import SimuladorDeCustodiantes
from app.infra.webhooks.assinatura import AssinaturaInvalida, verificar_assinatura

log = logging.getLogger(__name__)

CABECALHO_DE_ASSINATURA = "X-Custodiante-Assinatura"
# Para custodiante desconhecido a conta do HMAC é feita mesmo assim, com este segredo
# descartável: o tempo de resposta não diz se o id existe.
_SEGREDO_DESCARTAVEL = "segredo-descartavel-para-tempo-constante-0000"

rotas_de_custodiantes = APIRouter(
    prefix="/v1/custodiantes", tags=["Custodiantes (simulados)"], responses=ERROS_COMUNS
)
# O callback não usa API key de parceiro: quem chama é o custodiante, e ele se prova
# pela assinatura. Por isso não herda as respostas de erro comuns da API de parceiro.
rotas_de_callback = APIRouter(prefix="/v1/custodiantes", tags=["Custodiantes (simulados)"])
rotas_de_sandbox_de_custodia = APIRouter(
    prefix="/v1/sandbox",
    tags=["Sandbox"],
    responses=ERROS_COMUNS,
    dependencies=[Depends(exigir_sandbox)],
)

IdDoCustodiante = Path(
    pattern=r"^[A-Za-z0-9_]{1,40}$",
    description="Id do custodiante, como em GET /v1/custodiantes",
    examples=["custodiante_agil"],
)


@rotas_de_custodiantes.get(
    "", response_model=list[esquemas.CustodianteResposta], summary="Listar custodiantes"
)
def listar_custodiantes(
    _: ParceiroAutenticado, recursos: RecursosDaApi
) -> list[esquemas.CustodianteResposta]:
    """Custodiantes externos aceitos em `custodiante_id` ao bloquear uma garantia.

    **Todos são simulados.** Este projeto não tem integração com nenhum custodiante
    real; os perfis (tempo de resposta, chance de recusa, chance de não responder) são
    fictícios e estão em `app/custodiantes_simulados.toml`.
    """
    return [
        esquemas.CustodianteResposta(
            id=info.id,
            nome=info.nome,
            descricao=info.descricao,
            simulado=info.simulado,
            prazo_de_resposta_minutos=int(info.prazo_de_resposta.total_seconds() // 60),
        )
        for info in recursos.custodiante.custodiantes()
    ]


async def _corpo_bruto(request: Request) -> bytes:
    """A assinatura é sobre os bytes exatos que chegaram, antes de qualquer parse."""
    return await request.body()


@rotas_de_callback.post(
    "/{custodiante_id}/callbacks",
    response_model=esquemas.ResultadoDoCallback,
    summary="Receber a resposta de um custodiante (callback assinado)",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/CallbackDoCustodiante"}
                }
            },
        }
    },
    responses={
        401: {"model": Problema, "description": "Assinatura ausente, errada ou fora da janela"},
        404: {"model": Problema, "description": "Pedido não encontrado para este custodiante"},
        422: {"model": Problema, "description": "Corpo inválido"},
    },
)
def receber_callback(
    corpo: Annotated[bytes, Depends(_corpo_bruto)],
    request: Request,
    recursos: RecursosDaApi,
    custodiante_id: str = IdDoCustodiante,
) -> esquemas.ResultadoDoCallback:
    """Por onde o custodiante avisa que confirmou ou recusou um pedido.

    **Autenticação.** Não há API key: o custodiante assina o corpo com o segredo dele,

        X-Custodiante-Assinatura: t=<unix>,v1=<hex>

    onde `v1` é o HMAC-SHA256 de `"<t>.<corpo>"`, no mesmo formato dos webhooks que a
    plataforma envia. Timestamp fora da janela (5 minutos por padrão) é recusado, para
    uma resposta capturada não poder ser reapresentada depois. Qualquer falha de
    autenticação, inclusive custodiante inexistente, responde o mesmo `401`, sem dizer
    o que falhou.

    **Idempotência.** A mesma resposta (mesmo `evento_id`), enviada de novo ou já obtida
    pela consulta de reserva, é registrada uma vez só e responde `duplicada`.
    """
    _autenticar(recursos, custodiante_id, corpo, request.headers.get(CABECALHO_DE_ASSINATURA))
    try:
        dados = esquemas.CallbackDoCustodiante.model_validate(json.loads(corpo))
    except (ValueError, ValidationError) as erro:
        raise ErroDeApi(
            "requisicao_invalida", "O corpo do callback não está no formato esperado"
        ) from erro
    resposta = RespostaDoCustodiante(
        solicitacao_id=dados.solicitacao_id,
        status=StatusDaResposta(dados.status),
        evento_id=dados.evento_id,
        valor_obtido=None
        if dados.valor_obtido is None
        else Dinheiro.de_decimal(dados.valor_obtido),
        motivo=dados.motivo,
    )
    with recursos.abrir_unidade() as uow:
        resultado, _, _ = casos.aplicar_resposta(
            uow, custodiante_id, resposta, casos.Canal.CALLBACK
        )
        uow.commit()
    log.info(
        "Callback de custodiante",
        extra={"custodiante_id": custodiante_id, "resultado": str(resultado)},
    )
    return esquemas.ResultadoDoCallback(resultado=resultado.value)


def _autenticar(
    recursos: RecursosDaApi, custodiante_id: str, corpo: bytes, cabecalho: str | None
) -> None:
    segredo = recursos.config.custodia_segredos.get(custodiante_id)
    conhecido = segredo is not None and recursos.custodiante.conhece(custodiante_id)
    valor = segredo.get_secret_value() if segredo is not None else _SEGREDO_DESCARTAVEL
    try:
        verificar_assinatura(
            valor,
            corpo,
            cabecalho,
            tolerancia_em_segundos=recursos.config.custodia_tolerancia_segundos,
            agora=int(recursos.relogio.agora().timestamp()),
        )
        valida = True
    except AssinaturaInvalida:
        valida = False
    if not (valida and conhecido):
        log.warning("Callback de custodiante recusado", extra={"custodiante_id": custodiante_id})
        raise ErroDeApi("assinatura_invalida", "Não foi possível autenticar o callback")


# ------------------------------------------------------------------ sandbox
def _simulador(recursos: RecursosDaApi) -> SimuladorDeCustodiantes:
    custodiante = recursos.custodiante
    if not isinstance(custodiante, SimuladorDeCustodiantes):
        raise CustodianteNaoConfigurado("O simulador de custodiantes não está ligado")
    return custodiante


@rotas_de_sandbox_de_custodia.post(
    "/garantias/{garantia_id}/custodiante",
    response_model=esquemas.GarantiaComLimite,
    summary="Decidir no lugar do custodiante simulado",
)
def decidir_no_sandbox(
    garantia_id: IdDaGarantia,
    corpo: esquemas.DecisaoNoSandbox,
    parceiro: ParceiroAutenticado,
    recursos: RecursosDaApi,
    uow: Unidade,
) -> esquemas.GarantiaComLimite:
    """Só existe com `SANDBOX_HABILITADO=true`.

    Faz o custodiante simulado responder agora ao pedido em aberto da garantia
    (gravame, liberação ou execução): `confirmar`, `recusar` ou `nao_responder` (vence o
    prazo e expira). Confirmar e recusar passam pelo mesmo caso de uso do callback.
    """
    simulador = _simulador(recursos)
    _, conta, garantia = casos.decidir_pelo_custodiante(
        uow,
        garantia_id,
        casos.AcaoDoSandbox(corpo.acao),
        simulador.resposta,
        casos.Canal.SANDBOX,
        parceiro_id=parceiro.id,
        valor_obtido=None
        if corpo.valor_obtido is None
        else Dinheiro.de_decimal(corpo.valor_obtido),
    )
    resposta = esquemas.GarantiaComLimite(
        garantia=apresentacao.garantia(garantia, conta, parceiro.politica),
        limite=apresentacao.posicao(conta.posicao(parceiro.politica)),
    )
    uow.commit()
    return resposta
