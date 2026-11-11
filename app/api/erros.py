"""Erros no formato RFC 7807 / RFC 9457 (`application/problem+json`).

Todo erro tem um `codigo` estável, que é o contrato com o parceiro: o texto de `detail`
pode melhorar com o tempo, o código não muda. O catálogo abaixo é servido em
`GET /v1/erros`, e o campo `type` de cada erro aponta para a entrada dele.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.dominio.erros import ErroDominio
from app.infra.logs import contexto_atual

log = logging.getLogger(__name__)

TIPO_DE_MIDIA = "application/problem+json"

# codigo -> (status HTTP, título, quando acontece)
CATALOGO: dict[str, tuple[int, str, str]] = {
    # 400
    "chave_de_idempotencia_ausente": (
        400,
        "Idempotency-Key ausente",
        "Operações que movimentam dinheiro exigem o cabeçalho Idempotency-Key.",
    ),
    "chave_de_idempotencia_invalida": (
        400,
        "Idempotency-Key inválida",
        "A chave precisa ter de 1 a 255 caracteres ASCII visíveis.",
    ),
    "cursor_invalido": (400, "Cursor inválido", "O cursor de paginação não foi reconhecido."),
    # 401 / 403
    "nao_autenticado": (401, "Não autenticado", "API key ausente, malformada ou incorreta."),
    "parceiro_inativo": (
        403,
        "Parceiro inativo",
        "A API key é válida, mas o parceiro está suspenso.",
    ),
    # 402: a compra foi analisada e negada (fica registrada e gera o evento compra.negada)
    "limite_insuficiente": (
        402,
        "Limite insuficiente",
        "O limite disponível não cobre o valor da compra.",
    ),
    "conta_bloqueada": (
        402,
        "Conta bloqueada",
        "A conta está bloqueada para novas compras: chamada de margem vencida ou dívida "
        "descoberta.",
    ),
    "fatura_em_atraso": (
        402,
        "Fatura em atraso",
        "Há fatura vencida e não paga; novas compras ficam suspensas até a regularização.",
    ),
    "teto_do_parceiro_excedido": (
        402,
        "Teto do parceiro excedido",
        "A carteira do parceiro chegou ao teto definido pela política de risco.",
    ),
    # 404
    "nao_encontrado": (404, "Não encontrado", "O recurso não existe."),
    "conta_nao_encontrada": (
        404,
        "Conta não encontrada",
        "A conta não existe ou pertence a outro parceiro.",
    ),
    "garantia_nao_encontrada": (
        404,
        "Garantia não encontrada",
        "A garantia não existe ou pertence a outro parceiro.",
    ),
    "parceiro_nao_encontrado": (404, "Parceiro não encontrado", "O parceiro não existe."),
    "entrega_nao_encontrada": (
        404,
        "Entrega não encontrada",
        "A entrega de webhook não existe ou pertence a outro parceiro.",
    ),
    "metodo_nao_permitido": (
        405,
        "Método não permitido",
        "O caminho existe, mas não com este método.",
    ),
    # 409
    "conta_ja_existe": (
        409,
        "Conta já existe",
        "Já existe uma conta com este cliente_ref para o parceiro.",
    ),
    "transicao_de_estado_invalida": (
        409,
        "Transição de estado inválida",
        "A garantia não pode ir do estado atual para o estado pedido.",
    ),
    "garantia_cobrindo_divida": (
        409,
        "Garantia cobrindo dívida",
        "Sem esta garantia a dívida ficaria descoberta; pague ou reforce antes de liberar.",
    ),
    "fatura_em_aberto": (
        409,
        "Fatura em aberto",
        "Já existe uma fatura fechada aguardando pagamento nesta conta.",
    ),
    "sem_saldo_para_faturar": (409, "Sem saldo para faturar", "Não há compras a faturar."),
    "sem_fatura_em_aberto": (
        409,
        "Nada para o tempo agir",
        "A conta não tem fatura fechada nem chamada de margem aberta.",
    ),
    "webhook_nao_configurado": (
        409,
        "Webhook não configurado",
        "Configure a URL de webhook antes de usar este recurso.",
    ),
    "conflito_de_concorrencia": (
        409,
        "Conflito de concorrência",
        "Outra operação alterou a conta ao mesmo tempo. Repita a requisição.",
    ),
    "requisicao_em_andamento": (
        409,
        "Requisição em andamento",
        "Outra requisição com a mesma Idempotency-Key ainda está sendo processada.",
    ),
    # 422
    "requisicao_invalida": (
        422,
        "Requisição inválida",
        "O corpo ou os parâmetros não passaram na validação; veja o campo `erros`.",
    ),
    "valor_invalido": (422, "Valor inválido", "O valor informado não é aceito nesta operação."),
    "ativo_nao_aceito": (
        422,
        "Ativo não aceito",
        "A política do parceiro não aceita este tipo de ativo como garantia.",
    ),
    "politica_invalida": (
        422,
        "Política inválida",
        "A política de risco tem valores fora da regra.",
    ),
    "vencimento_invalido": (422, "Vencimento inválido", "O vencimento não pode ficar no passado."),
    "pagamento_excede_divida": (
        422,
        "Pagamento maior que a dívida",
        "O valor pago é maior que o saldo devedor da conta.",
    ),
    "url_de_webhook_invalida": (
        422,
        "URL de webhook inválida",
        "A URL precisa ser https e não pode apontar para rede interna.",
    ),
    "chave_de_idempotencia_reutilizada": (
        422,
        "Idempotency-Key reutilizada",
        "Esta chave já foi usada com outro payload. Use uma chave nova para uma operação nova.",
    ),
    # 429
    "limite_de_requisicoes_excedido": (
        429,
        "Limite de requisições excedido",
        "O parceiro passou do número de requisições por minuto. Veja o cabeçalho Retry-After.",
    ),
    # 5xx
    "lancamento_desbalanceado": (
        500,
        "Erro interno",
        "Um lançamento contábil não fechou. A operação foi desfeita por segurança.",
    ),
    "erro_interno": (500, "Erro interno", "Erro inesperado. Informe o request_id ao suporte."),
    "servico_indisponivel": (503, "Serviço indisponível", "O banco de dados não respondeu."),
}


class Problema(BaseModel):
    """Corpo de erro (RFC 7807). Pode trazer campos extras, conforme o código."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "type": "/v1/erros/limite_insuficiente",
                "title": "Limite insuficiente",
                "status": 402,
                "detail": "Limite disponível de R$ 250,00 não cobre a compra de R$ 500,00",
                "instance": "/v1/contas/cta_01JCD.../compras",
                "codigo": "limite_insuficiente",
                "request_id": "req_01JCD...",
                "limite_disponivel": "250.00",
            }
        },
    )

    type: str = Field(description="Referência para a entrada do erro em /v1/erros")
    title: str = Field(description="Resumo curto e estável do tipo de erro")
    status: int = Field(description="O mesmo status HTTP da resposta")
    detail: str = Field(description="Explicação específica desta ocorrência")
    instance: str | None = Field(default=None, description="Caminho da requisição que falhou")
    codigo: str = Field(description="Código de negócio estável. É por ele que se programa.")
    request_id: str | None = Field(default=None, description="Para rastrear nos logs")


class ErroDeApi(ErroDominio):
    """Erro que nasce na borda HTTP (autenticação, paginação, limite de requisições)."""

    def __init__(self, codigo: str, mensagem: str, **detalhes: object) -> None:
        super().__init__(mensagem, **detalhes)
        self._codigo = codigo

    @property
    def codigo(self) -> str:  # type: ignore[override]
        return self._codigo


def corpo_do_problema(
    codigo: str, detalhe: str, caminho: str | None = None, **extras: Any
) -> dict[str, Any]:
    status, titulo, _ = CATALOGO.get(codigo, CATALOGO["erro_interno"])
    return {
        "type": f"/v1/erros/{codigo}",
        "title": titulo,
        "status": status,
        "detail": detalhe,
        "instance": caminho,
        "codigo": codigo,
        "request_id": contexto_atual().get("request_id"),
        **extras,
    }


def resposta_de_problema(
    codigo: str,
    detalhe: str,
    caminho: str | None = None,
    cabecalhos: dict[str, str] | None = None,
    **extras: Any,
) -> JSONResponse:
    corpo = corpo_do_problema(codigo, detalhe, caminho, **extras)
    return JSONResponse(
        corpo, status_code=corpo["status"], media_type=TIPO_DE_MIDIA, headers=cabecalhos
    )


_CODIGO_POR_STATUS = {
    404: "nao_encontrado",
    405: "metodo_nao_permitido",
    401: "nao_autenticado",
}


def registrar_tratadores(app: FastAPI) -> None:
    @app.exception_handler(ErroDominio)
    async def _erro_de_negocio(request: Request, erro: ErroDominio) -> JSONResponse:
        codigo = erro.codigo if erro.codigo in CATALOGO else "erro_interno"
        cabecalhos = erro.detalhes.pop("_cabecalhos", None)
        if CATALOGO[codigo][0] >= 500:
            log.error("Erro de negócio inesperado", extra={"codigo": erro.codigo}, exc_info=erro)
            return resposta_de_problema(codigo, CATALOGO[codigo][2], request.url.path)
        return resposta_de_problema(
            codigo,
            erro.mensagem,
            request.url.path,
            cabecalhos if isinstance(cabecalhos, dict) else None,
            **erro.detalhes,
        )

    @app.exception_handler(RequestValidationError)
    async def _validacao(request: Request, erro: RequestValidationError) -> JSONResponse:
        erros = [
            {
                "campo": ".".join(str(parte) for parte in item["loc"] if parte != "body"),
                "mensagem": item["msg"],
            }
            for item in erro.errors()
        ]
        return resposta_de_problema(
            "requisicao_invalida",
            "Um ou mais campos não passaram na validação",
            request.url.path,
            erros=erros,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, erro: StarletteHTTPException) -> JSONResponse:
        codigo = _CODIGO_POR_STATUS.get(erro.status_code, "erro_interno")
        corpo = corpo_do_problema(codigo, str(erro.detail), request.url.path)
        corpo["status"] = erro.status_code
        return JSONResponse(
            corpo,
            status_code=erro.status_code,
            media_type=TIPO_DE_MIDIA,
            headers=dict(erro.headers) if erro.headers else None,
        )

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, erro: Exception) -> JSONResponse:
        # O detalhe técnico vai para o log, nunca para quem chamou.
        log.exception("Erro inesperado", exc_info=erro)
        return resposta_de_problema("erro_interno", CATALOGO["erro_interno"][2], request.url.path)
