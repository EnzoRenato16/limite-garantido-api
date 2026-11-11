"""Middleware ASGI: request_id, log de acesso estruturado e métricas por rota."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.erros import CATALOGO, resposta_de_problema
from app.dominio.ids import novo_id
from app.infra.logs import contexto_da_requisicao
from app.infra.metricas import Metricas

log = logging.getLogger("app.acesso")
log_de_erros = logging.getLogger("app.erros")

CABECALHO_DE_REQUEST_ID = "X-Request-Id"
# Aceitamos o id que o chamador mandar, desde que seja curto e sem caracteres estranhos:
# ele vai parar em log e em cabeçalho de resposta.
_REQUEST_ID_ACEITAVEL = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_ROTAS_SEM_LOG = frozenset({"/health", "/metrics"})
_STATUS_DE_ERRO_DO_SERVIDOR = 500


class MiddlewareDeObservabilidade:
    def __init__(self, app: ASGIApp, metricas: Metricas) -> None:
        self._app = app
        self._metricas = metricas

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        recebido = (
            dict(scope["headers"])
            .get(CABECALHO_DE_REQUEST_ID.lower().encode(), b"")
            .decode("latin-1")
        )
        request_id = recebido if _REQUEST_ID_ACEITAVEL.match(recebido) else novo_id("req")
        contexto: dict[str, Any] = {"request_id": request_id, "parceiro_id": None, "cabecalhos": {}}
        ficha = contexto_da_requisicao.set(contexto)
        inicio = time.perf_counter()
        status = _STATUS_DE_ERRO_DO_SERVIDOR
        resposta_iniciada = False

        async def enviar(mensagem: Message) -> None:
            nonlocal status, resposta_iniciada
            if mensagem["type"] == "http.response.start":
                resposta_iniciada = True
                status = mensagem["status"]
                cabecalhos = MutableHeaders(scope=mensagem)
                cabecalhos[CABECALHO_DE_REQUEST_ID] = request_id
                for nome, valor in contexto["cabecalhos"].items():
                    cabecalhos[nome] = valor
            await send(mensagem)

        try:
            await self._app(scope, receive, enviar)
        except Exception:
            # Erro que ninguém previu. Tratado aqui, e não no tratador genérico do
            # framework, porque aqui o request_id e o parceiro ainda estão no contexto:
            # o log sai completo e a resposta leva o id para o suporte rastrear.
            # O detalhe técnico vai para o log, nunca para quem chamou.
            log_de_erros.exception("Erro inesperado")
            if resposta_iniciada:
                raise
            resposta = resposta_de_problema(
                "erro_interno", CATALOGO["erro_interno"][2], scope["path"]
            )
            await resposta(scope, receive, enviar)
        finally:
            duracao = time.perf_counter() - inicio
            # O template da rota (/v1/contas/{conta_id}), não o caminho real: senão cada
            # id de conta viraria uma série nova no Prometheus.
            rota = getattr(scope.get("route"), "path", None) or "nao_mapeada"
            metodo = scope["method"]
            self._metricas.requisicoes.labels(metodo=metodo, rota=rota, status=str(status)).inc()
            self._metricas.duracao.labels(metodo=metodo, rota=rota).observe(duracao)
            if scope["path"] not in _ROTAS_SEM_LOG:
                log.log(
                    logging.ERROR if status >= _STATUS_DE_ERRO_DO_SERVIDOR else logging.INFO,
                    "requisicao",
                    extra={
                        "metodo": metodo,
                        "caminho": scope["path"],
                        "rota": rota,
                        "status": status,
                        "duracao_ms": round(duracao * 1000, 2),
                    },
                )
            contexto_da_requisicao.reset(ficha)
