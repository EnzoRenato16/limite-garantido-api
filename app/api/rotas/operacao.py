"""Saúde, métricas e catálogo de erros. Não exigem autenticação."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.api import esquemas
from app.api.dependencias import RecursosDaApi
from app.api.erros import CATALOGO, ErroDeApi, Problema, resposta_de_problema

log = logging.getLogger(__name__)

rotas_de_operacao = APIRouter(tags=["Operação"])


class Saude(BaseModel):
    status: str
    banco: str
    versao: str


@rotas_de_operacao.get(
    "/health",
    response_model=Saude,
    summary="Saúde da aplicação",
    responses={503: {"model": Problema, "description": "Banco de dados fora do ar"}},
)
def saude(recursos: RecursosDaApi) -> Saude | Response:
    """Responde 200 quando a API consegue falar com o banco, e 503 quando não."""
    try:
        with recursos.engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
    except Exception:
        log.exception("Banco de dados não respondeu no health check")
        return resposta_de_problema(
            "servico_indisponivel", CATALOGO["servico_indisponivel"][2], "/health"
        )
    return Saude(status="ok", banco="ok", versao=__version__)


@rotas_de_operacao.get("/metrics", summary="Métricas no formato do Prometheus")
def metricas(recursos: RecursosDaApi) -> Response:
    """Requisições por rota e status, tempo de resposta, eventos de negócio por tipo e
    tamanho da fila de webhooks. Em produção, exponha só para a rede de monitoramento."""
    return Response(generate_latest(recursos.metricas.registro), media_type=CONTENT_TYPE_LATEST)


@rotas_de_operacao.get(
    "/v1/erros",
    response_model=list[esquemas.CatalogoDeErro],
    summary="Catálogo de códigos de erro",
    tags=["Operação"],
)
def catalogo_de_erros() -> list[esquemas.CatalogoDeErro]:
    """Todos os códigos de erro que a API pode devolver. O campo `type` de cada erro
    aponta para a entrada dele aqui."""
    return [
        esquemas.CatalogoDeErro(codigo=codigo, status=status, titulo=titulo, descricao=descricao)
        for codigo, (status, titulo, descricao) in sorted(CATALOGO.items())
    ]


@rotas_de_operacao.get(
    "/v1/erros/{codigo}",
    response_model=esquemas.CatalogoDeErro,
    summary="Detalhe de um código de erro",
    responses={404: {"model": Problema}},
)
def detalhe_do_erro(codigo: str) -> esquemas.CatalogoDeErro:
    if codigo not in CATALOGO:
        raise ErroDeApi("nao_encontrado", "Código de erro desconhecido")
    status, titulo, descricao = CATALOGO[codigo]
    return esquemas.CatalogoDeErro(codigo=codigo, status=status, titulo=titulo, descricao=descricao)
