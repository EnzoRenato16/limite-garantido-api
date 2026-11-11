"""Página /demo: um "app do parceiro" de mentira para mostrar o fluxo inteiro.

Só é montada com `DEMO_HABILITADO=true` (e a aplicação se recusa a subir assim em
produção). Tem três partes:

- a página estática (HTML, CSS e JS sem framework);
- `/demo/config`, que entrega ao navegador a API key do parceiro de exemplo;
- `/demo/webhooks`, que faz o papel do servidor do parceiro: recebe as entregas do
  worker, confere a assinatura HMAC e guarda as últimas em memória para a página mostrar.
"""

from __future__ import annotations

import itertools
import json
import threading
from collections import deque
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.api.erros import ErroDeApi
from app.api.recursos import Recursos
from app.infra.webhooks.assinatura import (
    CABECALHO_DE_ASSINATURA,
    AssinaturaInvalida,
    verificar_assinatura,
)

PASTA = Path(__file__).resolve().parent / "estatico"
MAXIMO_DE_WEBHOOKS_GUARDADOS = 300
TAMANHO_MAXIMO_DO_CORPO = 64 * 1024

# A página não carrega nada de fora: sem CDN, sem fonte remota, sem script inline.
POLITICA_DE_SEGURANCA = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


class ConfiguracaoDaDemo(BaseModel):
    api_key: str
    parceiro: str
    # Data de negócio do servidor: a página fecha a fatura com vencimento "hoje".
    hoje: date
    aviso: str


class WebhookRecebido(BaseModel):
    numero: int
    recebido_em: datetime
    assinatura_valida: bool
    motivo_da_recusa: str | None
    evento: dict[str, Any]


class CaixaDeEntrada:
    """Os últimos webhooks recebidos, em memória. Vale para um processo só: é demo."""

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._contador = itertools.count(1)
        self._itens: deque[WebhookRecebido] = deque(maxlen=MAXIMO_DE_WEBHOOKS_GUARDADOS)

    def guardar(self, evento: dict[str, Any], valida: bool, motivo: str | None) -> WebhookRecebido:
        with self._trava:
            recebido = WebhookRecebido(
                numero=next(self._contador),
                recebido_em=datetime.now(UTC),
                assinatura_valida=valida,
                motivo_da_recusa=motivo,
                evento=evento,
            )
            self._itens.append(recebido)
            return recebido

    def listar(self, depois_de: int, conta_id: str | None) -> list[WebhookRecebido]:
        with self._trava:
            return [
                item
                for item in self._itens
                if item.numero > depois_de
                and (conta_id is None or item.evento.get("conta_id") == conta_id)
            ]


def montar_demo(app: FastAPI, recursos: Recursos) -> None:
    config = recursos.config
    caixa = CaixaDeEntrada()
    rotas = APIRouter(prefix="/demo", include_in_schema=False)

    @rotas.get("")
    def pagina() -> FileResponse:
        return FileResponse(
            PASTA / "index.html",
            headers={
                "Content-Security-Policy": POLITICA_DE_SEGURANCA,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            },
        )

    @rotas.get("/config", response_model=ConfiguracaoDaDemo)
    def configuracao() -> ConfiguracaoDaDemo:
        if config.demo_api_key is None:
            raise ErroDeApi("nao_encontrado", "Defina DEMO_API_KEY e rode `python -m app.seed`")
        return ConfiguracaoDaDemo(
            api_key=config.demo_api_key.get_secret_value(),
            parceiro="Carteira Exemplo",
            hoje=recursos.relogio.hoje(),
            aviso="Ambiente de demonstração. A chave é fictícia e os dados são inventados.",
        )

    @rotas.post("/webhooks", status_code=204)
    async def receber_webhook(request: Request) -> Response:
        """O que um parceiro faria: conferir a assinatura antes de confiar no conteúdo."""
        corpo = await request.body()
        if len(corpo) > TAMANHO_MAXIMO_DO_CORPO:
            return JSONResponse({"erro": "corpo grande demais"}, status_code=413)
        segredo = config.demo_webhook_segredo
        valida = False
        motivo: str | None = "DEMO_WEBHOOK_SEGREDO não configurado"
        if segredo is not None:
            try:
                verificar_assinatura(
                    segredo.get_secret_value(),
                    corpo,
                    request.headers.get(CABECALHO_DE_ASSINATURA),
                    tolerancia_em_segundos=config.webhook_tolerancia_segundos,
                )
                valida, motivo = True, None
            except AssinaturaInvalida as erro:
                motivo = str(erro)
        try:
            evento = json.loads(corpo)
        except ValueError:
            evento = {}
        if not isinstance(evento, dict):
            evento = {}
        caixa.guardar(evento, valida, motivo)
        # Assinatura ruim é recusada: o worker vai registrar a falha e tentar de novo.
        return Response(status_code=204 if valida else 401)

    @rotas.get("/webhooks", response_model=list[WebhookRecebido])
    def listar_webhooks(
        depois_de: Annotated[int, Query(ge=0)] = 0,
        conta_id: Annotated[str | None, Query(max_length=40)] = None,
    ) -> list[WebhookRecebido]:
        return caixa.listar(depois_de, conta_id)

    app.include_router(rotas)
    app.mount("/demo/estatico", StaticFiles(directory=PASTA), name="demo-estatico")
    app.state.caixa_da_demo = caixa
