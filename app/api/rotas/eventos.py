"""Log de eventos e webhooks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api import apresentacao, esquemas
from app.api.dependencias import ParceiroAutenticado, RecursosDaApi, Unidade
from app.api.erros import Problema
from app.api.paginacao import LIMITE_PADRAO, Cursor, Limite, Pagina, paginar
from app.api.rotas.contas import ERROS_COMUNS
from app.aplicacao import parceiros as casos_de_parceiro
from app.aplicacao.erros import WebhookNaoConfigurado
from app.dominio.eventos import TipoEvento
from app.infra.banco.modelos import EntregaDeWebhookModelo, EventoModelo
from app.infra.webhooks.destino import validar_destino
from app.infra.webhooks.entregador import reenviar

rotas_de_eventos = APIRouter(prefix="/v1", tags=["Eventos e webhooks"], responses=ERROS_COMUNS)


@rotas_de_eventos.get(
    "/eventos", response_model=Pagina[esquemas.EventoResposta], summary="Listar eventos"
)
def listar_eventos(
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    limite: Limite = LIMITE_PADRAO,
    cursor: Cursor = None,
    conta_id: Annotated[str | None, Query(description="Só eventos desta conta")] = None,
    tipo: Annotated[TipoEvento | None, Query(description="Só eventos deste tipo")] = None,
) -> Pagina[esquemas.EventoResposta]:
    """Tudo o que aconteceu nas contas do parceiro, do mais novo para o mais antigo.

    É a mesma informação dos webhooks. Serve para conferir, reconciliar ou recuperar o
    que foi perdido enquanto o endpoint do parceiro esteve fora do ar.
    """
    consulta = select(EventoModelo).where(EventoModelo.parceiro_id == parceiro.id)
    if conta_id is not None:
        consulta = consulta.where(EventoModelo.conta_id == conta_id)
    if tipo is not None:
        consulta = consulta.where(EventoModelo.tipo == tipo.value)

    def converter(modelos: Sequence[EventoModelo]) -> list[esquemas.EventoResposta]:
        return [apresentacao.evento(modelo) for modelo in modelos]

    return paginar(uow.sessao, consulta, EventoModelo.sequencia, limite, cursor, converter)


@rotas_de_eventos.put(
    "/parceiro/webhook",
    response_model=esquemas.WebhookResposta,
    summary="Configurar webhook",
    responses={422: {"model": Problema, "description": "URL não aceita"}},
)
def configurar_webhook(
    corpo: esquemas.ConfiguracaoDeWebhook,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    recursos: RecursosDaApi,
) -> esquemas.WebhookResposta:
    """Define para onde os eventos são enviados e devolve o segredo de assinatura.

    Cada entrega leva `X-LG-Assinatura: t=<unix>,v1=<hmac>`, onde o HMAC-SHA256 é
    calculado sobre `"<t>.<corpo>"` com o segredo. Confira a assinatura e recuse `t`
    com mais de 5 minutos: é isso que impede a reapresentação de uma entrega antiga.

    A URL precisa ser https e não pode apontar para rede interna.
    """
    url = str(corpo.url)
    validar_destino(url, permitir_rede_interna=recursos.config.webhook_permitir_rede_interna)
    casos_de_parceiro.configurar_webhook(
        uow, parceiro, url, rotacionar_segredo=corpo.rotacionar_segredo
    )
    uow.commit()
    if parceiro.webhook_segredo is None:
        raise WebhookNaoConfigurado("Não foi possível gerar o segredo do webhook")
    return esquemas.WebhookResposta(url=url, segredo=parceiro.webhook_segredo)


@rotas_de_eventos.get(
    "/webhooks/entregas",
    response_model=Pagina[esquemas.EntregaResposta],
    summary="Listar entregas de webhook",
)
def listar_entregas(
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    limite: Limite = LIMITE_PADRAO,
    cursor: Cursor = None,
    estado: Annotated[
        str | None,
        Query(pattern="^(pendente|entregue|morta)$", description="pendente, entregue ou morta"),
    ] = None,
) -> Pagina[esquemas.EntregaResposta]:
    """Mostra cada entrega, quantas tentativas teve e o último erro.

    `morta` é a dead letter: as tentativas acabaram. Use o reenvio para tentar de novo.
    """
    consulta = select(EntregaDeWebhookModelo).where(
        EntregaDeWebhookModelo.parceiro_id == parceiro.id
    )
    if estado is not None:
        consulta = consulta.where(EntregaDeWebhookModelo.estado == estado)

    def converter(entregas: Sequence[EntregaDeWebhookModelo]) -> list[esquemas.EntregaResposta]:
        tipos: dict[str, str] = {}
        if entregas:
            linhas = uow.sessao.execute(
                select(EventoModelo.id, EventoModelo.tipo).where(
                    EventoModelo.id.in_([e.evento_id for e in entregas])
                )
            )
            tipos = {linha[0]: linha[1] for linha in linhas}
        return [apresentacao.entrega(e, tipos.get(e.evento_id, "")) for e in entregas]

    return paginar(uow.sessao, consulta, EntregaDeWebhookModelo.id, limite, cursor, converter)


@rotas_de_eventos.post(
    "/webhooks/entregas/{entrega_id}/reenvio",
    status_code=202,
    response_model=esquemas.EntregaResposta,
    summary="Reenviar webhook",
)
def reenviar_entrega(
    entrega_id: str, parceiro: ParceiroAutenticado, uow: Unidade
) -> esquemas.EntregaResposta:
    """Cria uma nova entrega do mesmo evento para a URL atual do parceiro.

    Funciona para entregas mortas e também para as já entregues (reprocessamento). O
    evento mantém o mesmo `id`, então quem recebe consegue reconhecer a repetição.
    """
    nova = reenviar(uow.sessao, parceiro.id, entrega_id, uow.relogio.agora())
    tipo = uow.sessao.execute(
        select(EventoModelo.tipo).where(EventoModelo.id == nova.evento_id)
    ).scalar_one()
    uow.commit()
    return apresentacao.entrega(nova, tipo)
