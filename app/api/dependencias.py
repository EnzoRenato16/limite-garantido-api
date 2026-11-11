"""Dependências das rotas: transação, autenticação e limite de requisições."""

from __future__ import annotations

import hmac
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.erros import ErroDeApi
from app.api.recursos import Recursos
from app.aplicacao.chaves import chave_confere, prefixo_da_chave
from app.dominio.parceiro import Parceiro
from app.infra.banco.unidade import UnidadeDeTrabalhoSql
from app.infra.logs import contexto_atual

_HASH_QUE_NUNCA_CONFERE = "0" * 64

esquema_do_parceiro = HTTPBearer(
    scheme_name="ApiKeyDoParceiro",
    description="API key do parceiro: `Authorization: Bearer lgk_...`",
    auto_error=False,
)
esquema_administrativo = HTTPBearer(
    scheme_name="TokenAdministrativo",
    description="Token da plataforma (variável ADMIN_TOKEN). Não é do parceiro.",
    auto_error=False,
)


def obter_recursos(request: Request) -> Recursos:
    recursos: Recursos = request.app.state.recursos
    return recursos


RecursosDaApi = Annotated[Recursos, Depends(obter_recursos)]


def obter_unidade(recursos: RecursosDaApi) -> Iterator[UnidadeDeTrabalhoSql]:
    """Uma transação por requisição. O que não tiver commit explícito é desfeito."""
    with recursos.abrir_unidade() as uow:
        yield uow


Unidade = Annotated[UnidadeDeTrabalhoSql, Depends(obter_unidade)]


def obter_parceiro_autenticado(
    recursos: RecursosDaApi,
    uow: Unidade,
    credenciais: Annotated[HTTPAuthorizationCredentials | None, Security(esquema_do_parceiro)],
) -> Parceiro:
    nao_autenticado = ErroDeApi(
        "nao_autenticado",
        "Envie a API key do parceiro em Authorization: Bearer <chave>",
        _cabecalhos={"WWW-Authenticate": "Bearer"},
    )
    if credenciais is None:
        raise nao_autenticado
    chave = credenciais.credentials
    prefixo = prefixo_da_chave(chave)
    achado = uow.parceiros.obter_pelo_prefixo(prefixo) if prefixo else None
    if achado is None:
        uow.rollback()
        # Compara mesmo assim, para o tempo de resposta não revelar se o prefixo existe.
        chave_confere(chave, _HASH_QUE_NUNCA_CONFERE)
        raise nao_autenticado
    parceiro, hash_guardado = achado
    # Devolve a conexão ao pool já. Esta função e o endpoint rodam em threads
    # diferentes: segurar a conexão entre uma e outra trava tudo quando chegam mais
    # requisições do que o pool tem de conexões (as threads ficam esperando conexão e
    # quem tem conexão fica esperando thread).
    uow.rollback()
    if not chave_confere(chave, hash_guardado):
        raise nao_autenticado
    contexto_atual()["parceiro_id"] = parceiro.id
    if not parceiro.ativo:
        raise ErroDeApi("parceiro_inativo", "Este parceiro está suspenso")

    veredito = recursos.limitador.permitir(parceiro.id, parceiro.limite_de_requisicoes_por_minuto)
    contexto_atual().setdefault("cabecalhos", {}).update(veredito.cabecalhos())
    if not veredito.permitido:
        recursos.metricas.requisicoes_limitadas.inc()
        raise ErroDeApi(
            "limite_de_requisicoes_excedido",
            f"Limite de {veredito.limite} requisições por minuto excedido. "
            f"Tente de novo em {veredito.segundos_para_reiniciar}s.",
        )
    return parceiro


ParceiroAutenticado = Annotated[Parceiro, Depends(obter_parceiro_autenticado)]


def exigir_administrador(
    recursos: RecursosDaApi,
    credenciais: Annotated[HTTPAuthorizationCredentials | None, Security(esquema_administrativo)],
) -> None:
    token = recursos.config.admin_token
    if token is None:
        # Sem token configurado a API administrativa não existe.
        raise ErroDeApi("nao_encontrado", "Recurso não encontrado")
    if credenciais is None or not hmac.compare_digest(
        credenciais.credentials.encode(), token.get_secret_value().encode()
    ):
        raise ErroDeApi(
            "nao_autenticado",
            "Token administrativo ausente ou incorreto",
            _cabecalhos={"WWW-Authenticate": "Bearer"},
        )


def exigir_sandbox(recursos: RecursosDaApi) -> None:
    if not recursos.config.sandbox_habilitado:
        raise ErroDeApi("nao_encontrado", "Recurso não encontrado")
