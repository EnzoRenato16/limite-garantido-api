"""Entrega de webhooks a partir da outbox.

O evento e a entrega são gravados na transação da operação (veja o repositório de
contas). Aqui o worker só lê o que está pendente e tenta entregar:

1. reserva um lote (`FOR UPDATE SKIP LOCKED`, para vários workers não pegarem o mesmo);
2. envia fora de qualquer transação, para não segurar conexão durante a chamada HTTP;
3. grava o resultado: entregue, nova tentativa com backoff exponencial, ou morta.

A garantia é "pelo menos uma vez": o receptor deve tratar o `id` do evento como chave
para não processar em dobro.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.aplicacao.erros import EntregaNaoEncontrada, UrlDeWebhookInvalida, WebhookNaoConfigurado
from app.aplicacao.portas import Relogio
from app.config import Configuracao
from app.dominio.ids import novo_id
from app.infra.banco.modelos import EntregaDeWebhookModelo, EventoModelo, ParceiroModelo
from app.infra.banco.repositorios import (
    ESTADO_DE_ENTREGA_ENTREGUE,
    ESTADO_DE_ENTREGA_MORTA,
    ESTADO_DE_ENTREGA_PENDENTE,
)
from app.infra.metricas import Metricas
from app.infra.webhooks.assinatura import (
    CABECALHO_DA_ENTREGA,
    CABECALHO_DE_ASSINATURA,
    CABECALHO_DO_EVENTO,
    CABECALHO_DO_TIPO,
    assinar,
)
from app.infra.webhooks.destino import validar_destino

log = logging.getLogger(__name__)

TAMANHO_DO_LOTE = 20
SEGUNDOS_DE_RESERVA = 60
TAMANHO_MAXIMO_DO_ERRO = 500
_PRIMEIRO_STATUS_DE_SUCESSO, _PRIMEIRO_STATUS_DE_FALHA = 200, 300


def calcular_espera(
    tentativas: int,
    base_em_segundos: int,
    teto_em_segundos: int,
    aleatorio: Callable[[], float] = random.random,
) -> timedelta:
    """Backoff exponencial: base, 2x, 4x, 8x... até o teto, com até 10% de folga aleatória
    para as retentativas de muitos eventos não baterem no parceiro no mesmo segundo."""
    espera = min(base_em_segundos * 2 ** max(tentativas - 1, 0), teto_em_segundos)
    return timedelta(seconds=espera * (1 + 0.1 * aleatorio()))


def montar_corpo(evento: EventoModelo) -> bytes:
    """O JSON que o parceiro recebe. É sobre estes bytes exatos que vai a assinatura."""
    conteudo = {
        "id": evento.id,
        "tipo": evento.tipo,
        "conta_id": evento.conta_id,
        "ocorrido_em": evento.ocorrido_em.isoformat(),
        "dados": evento.dados,
    }
    return json.dumps(conteudo, ensure_ascii=False, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class EntregaReservada:
    id: str
    url: str
    segredo: str | None
    evento_id: str
    evento_tipo: str
    corpo: bytes
    tentativas: int


@dataclass(frozen=True, slots=True)
class ResultadoDoLote:
    entregues: int = 0
    reagendadas: int = 0
    mortas: int = 0

    @property
    def total(self) -> int:
        return self.entregues + self.reagendadas + self.mortas


class Entregador:
    def __init__(
        self,
        fabrica_de_sessoes: sessionmaker[Session],
        config: Configuracao,
        relogio: Relogio,
        cliente_http: httpx2.Client | None = None,
        metricas: Metricas | None = None,
    ) -> None:
        self._sessoes = fabrica_de_sessoes
        self._config = config
        self._relogio = relogio
        self._metricas = metricas
        # Não segue redirecionamento: um 302 poderia levar a entrega para a rede interna.
        self._http = cliente_http or httpx2.Client(
            timeout=config.webhook_timeout_segundos, follow_redirects=False
        )

    def processar_lote(self, tamanho: int = TAMANHO_DO_LOTE) -> ResultadoDoLote:
        entregues = reagendadas = mortas = 0
        for entrega in self._reservar(tamanho):
            status, erro = self._enviar(entrega)
            desfecho = self._registrar(entrega, status, erro)
            if self._metricas is not None:
                self._metricas.entregas.labels(resultado=desfecho).inc()
            if desfecho == "entregue":
                entregues += 1
            elif desfecho == "morta":
                mortas += 1
            else:
                reagendadas += 1
        return ResultadoDoLote(entregues, reagendadas, mortas)

    # ------------------------------------------------------------- etapas
    def _reservar(self, tamanho: int) -> list[EntregaReservada]:
        agora = self._relogio.agora()
        with self._sessoes() as sessao, sessao.begin():
            linhas = sessao.execute(
                select(EntregaDeWebhookModelo, EventoModelo, ParceiroModelo.webhook_segredo)
                .join(EventoModelo, EventoModelo.id == EntregaDeWebhookModelo.evento_id)
                .join(ParceiroModelo, ParceiroModelo.id == EntregaDeWebhookModelo.parceiro_id)
                .where(
                    EntregaDeWebhookModelo.estado == ESTADO_DE_ENTREGA_PENDENTE,
                    EntregaDeWebhookModelo.proxima_tentativa_em <= agora,
                    or_(
                        EntregaDeWebhookModelo.reservada_ate.is_(None),
                        EntregaDeWebhookModelo.reservada_ate <= agora,
                    ),
                )
                # Dentro do mesmo instante, vale a ordem em que os eventos aconteceram.
                .order_by(EntregaDeWebhookModelo.proxima_tentativa_em, EventoModelo.sequencia)
                .limit(tamanho)
                .with_for_update(of=EntregaDeWebhookModelo, skip_locked=True)
            ).all()
            reservadas = []
            for entrega, evento, segredo in linhas:
                # Se o worker morrer no meio do envio, a reserva expira e outro assume.
                entrega.reservada_ate = agora + timedelta(seconds=SEGUNDOS_DE_RESERVA)
                reservadas.append(
                    EntregaReservada(
                        id=entrega.id,
                        url=entrega.url,
                        segredo=segredo,
                        evento_id=evento.id,
                        evento_tipo=evento.tipo,
                        corpo=montar_corpo(evento),
                        tentativas=entrega.tentativas,
                    )
                )
            return reservadas

    def _enviar(self, entrega: EntregaReservada) -> tuple[int | None, str | None]:
        if not entrega.segredo:
            return None, "Parceiro sem segredo de webhook"
        try:
            validar_destino(
                entrega.url, permitir_rede_interna=self._config.webhook_permitir_rede_interna
            )
        except UrlDeWebhookInvalida as erro:
            return None, erro.mensagem
        cabecalhos = {
            "Content-Type": "application/json",
            "User-Agent": "limite-garantido-webhooks/1.0",
            CABECALHO_DE_ASSINATURA: assinar(
                entrega.segredo, entrega.corpo, int(self._relogio.agora().timestamp())
            ),
            CABECALHO_DO_EVENTO: entrega.evento_id,
            CABECALHO_DO_TIPO: entrega.evento_tipo,
            CABECALHO_DA_ENTREGA: entrega.id,
        }
        try:
            resposta = self._http.post(entrega.url, content=entrega.corpo, headers=cabecalhos)
        except httpx2.HTTPError as erro:
            return None, f"{type(erro).__name__}: {erro}"[:TAMANHO_MAXIMO_DO_ERRO]
        if _PRIMEIRO_STATUS_DE_SUCESSO <= resposta.status_code < _PRIMEIRO_STATUS_DE_FALHA:
            return resposta.status_code, None
        return resposta.status_code, f"O destino respondeu HTTP {resposta.status_code}"

    def _registrar(self, entrega: EntregaReservada, status: int | None, erro: str | None) -> str:
        agora = self._relogio.agora()
        with self._sessoes() as sessao, sessao.begin():
            modelo = sessao.get(EntregaDeWebhookModelo, entrega.id, with_for_update=True)
            if modelo is None:
                return "ignorada"
            modelo.tentativas += 1
            modelo.ultimo_status_http = status
            modelo.ultimo_erro = erro
            modelo.reservada_ate = None
            if erro is None:
                modelo.estado = ESTADO_DE_ENTREGA_ENTREGUE
                modelo.entregue_em = agora
                return "entregue"
            if modelo.tentativas >= self._config.webhook_max_tentativas:
                # Dead letter: para de tentar sozinho. Sai daqui por reenvio manual.
                modelo.estado = ESTADO_DE_ENTREGA_MORTA
                log.warning(
                    "Webhook foi para a dead letter",
                    extra={"entrega_id": modelo.id, "evento_id": modelo.evento_id, "erro": erro},
                )
                return "morta"
            modelo.proxima_tentativa_em = agora + calcular_espera(
                modelo.tentativas,
                self._config.webhook_backoff_base_segundos,
                self._config.webhook_backoff_teto_segundos,
            )
            return "reagendada"


def reenviar(
    sessao: Session, parceiro_id: str, entrega_id: str, agora: datetime
) -> EntregaDeWebhookModelo:
    """Reenvio manual: cria uma entrega nova para o mesmo evento, na URL atual do parceiro.

    A entrega antiga fica como está: o histórico de tentativas não é reescrito.
    """
    original = sessao.execute(
        select(EntregaDeWebhookModelo).where(
            EntregaDeWebhookModelo.id == entrega_id,
            EntregaDeWebhookModelo.parceiro_id == parceiro_id,
        )
    ).scalar_one_or_none()
    if original is None:
        raise EntregaNaoEncontrada("Entrega não encontrada", entrega_id=entrega_id)
    url = sessao.execute(
        select(ParceiroModelo.webhook_url).where(ParceiroModelo.id == parceiro_id)
    ).scalar_one_or_none()
    if not url:
        raise WebhookNaoConfigurado("O parceiro não tem URL de webhook configurada")
    nova = EntregaDeWebhookModelo(
        id=novo_id("ent"),
        evento_id=original.evento_id,
        parceiro_id=parceiro_id,
        url=url,
        estado=ESTADO_DE_ENTREGA_PENDENTE,
        tentativas=0,
        proxima_tentativa_em=agora,
        criado_em=agora,
    )
    sessao.add(nova)
    sessao.flush()
    return nova


def contar_fila(fabrica_de_sessoes: sessionmaker[Session]) -> dict[str, int]:
    with fabrica_de_sessoes() as sessao:
        linhas: Any = sessao.execute(
            select(EntregaDeWebhookModelo.estado, func.count()).group_by(
                EntregaDeWebhookModelo.estado
            )
        ).all()
    contagem = dict.fromkeys(
        (ESTADO_DE_ENTREGA_PENDENTE, ESTADO_DE_ENTREGA_ENTREGUE, ESTADO_DE_ENTREGA_MORTA), 0
    )
    contagem.update({estado: int(quantidade) for estado, quantidade in linhas})
    return contagem
