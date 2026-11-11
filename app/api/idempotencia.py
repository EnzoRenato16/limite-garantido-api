"""Idempotência persistida.

O registro da chave é inserido na mesma transação da operação. Consequências:

- repetir a requisição devolve a resposta original, sem executar de novo;
- a mesma chave com outro payload é recusada (422);
- duas requisições simultâneas com a mesma chave: a segunda espera a primeira no índice
  único do banco e então recebe a resposta dela;
- se a operação falha, o registro some junto no rollback e a chave pode ser reusada.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from app.api.erros import TIPO_DE_MIDIA, ErroDeApi
from app.aplicacao.erros import ChaveDeIdempotenciaReutilizada, RequisicaoEmAndamento
from app.dominio.parceiro import Parceiro
from app.infra.banco.modelos import ChaveDeIdempotenciaModelo
from app.infra.banco.unidade import UnidadeDeTrabalhoSql

CABECALHO_DE_REPETICAO = "Idempotent-Replayed"
TAMANHO_MAXIMO_DA_CHAVE = 255
_PRIMEIRO_STATUS_DE_ERRO = 400

ChaveObrigatoria = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Obrigatório. Identificador único da operação, gerado pelo parceiro "
        "(um UUID serve). Repetir a mesma chave devolve a resposta original.",
    ),
]
ChaveOpcional = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Opcional. Se enviado, repetir a requisição devolve a resposta original.",
    ),
]


@dataclass(frozen=True, slots=True)
class RespostaHttp:
    status: int
    corpo: dict[str, Any]


def _impressao_digital(request: Request, corpo: BaseModel | None) -> str:
    conteudo = "" if corpo is None else json.dumps(corpo.model_dump(mode="json"), sort_keys=True)
    bruto = f"{request.method}\n{request.url.path}\n{conteudo}"
    return hashlib.sha256(bruto.encode()).hexdigest()


def _validar_chave(chave: str) -> None:
    if (
        not 1 <= len(chave) <= TAMANHO_MAXIMO_DA_CHAVE
        or not chave.isascii()
        or not chave.isprintable()
    ):
        raise ErroDeApi(
            "chave_de_idempotencia_invalida",
            f"Idempotency-Key precisa ter de 1 a {TAMANHO_MAXIMO_DA_CHAVE} caracteres ASCII "
            "visíveis",
        )


def _montar(resposta: RespostaHttp, *, repetida: bool) -> JSONResponse:
    return JSONResponse(
        resposta.corpo,
        status_code=resposta.status,
        media_type=TIPO_DE_MIDIA if resposta.status >= _PRIMEIRO_STATUS_DE_ERRO else None,
        headers={CABECALHO_DE_REPETICAO: "true"} if repetida else None,
    )


def _repetir(registro: ChaveDeIdempotenciaModelo, impressao: str) -> JSONResponse:
    if registro.impressao_digital != impressao:
        raise ChaveDeIdempotenciaReutilizada(
            "Esta Idempotency-Key já foi usada com outro payload ou em outro caminho"
        )
    if registro.status_http is None or registro.corpo is None:
        raise RequisicaoEmAndamento(
            "A requisição original com esta Idempotency-Key ainda está em andamento"
        )
    return _montar(RespostaHttp(registro.status_http, registro.corpo), repetida=True)


def executar_com_idempotencia(
    request: Request,
    uow: UnidadeDeTrabalhoSql,
    parceiro: Parceiro,
    chave: str | None,
    corpo: BaseModel | None,
    operacao: Callable[[], RespostaHttp],
    *,
    obrigatoria: bool = True,
) -> JSONResponse:
    """Roda `operacao` no máximo uma vez por chave e faz o commit."""
    if chave is None:
        if obrigatoria:
            raise ErroDeApi(
                "chave_de_idempotencia_ausente",
                "Envie o cabeçalho Idempotency-Key nesta operação",
            )
        resposta = operacao()
        uow.commit()
        return _montar(resposta, repetida=False)

    _validar_chave(chave)
    impressao = _impressao_digital(request, corpo)
    agora = uow.relogio.agora()
    ttl = timedelta(hours=request.app.state.recursos.config.idempotencia_ttl_horas)

    registro: ChaveDeIdempotenciaModelo | None = None
    for _ in range(2):
        existente = uow.idempotencia.obter(parceiro.id, chave)
        if existente is not None and existente.expira_em <= agora:
            uow.idempotencia.apagar(existente)
            existente = None
        if existente is not None:
            return _repetir(existente, impressao)
        try:
            registro = uow.idempotencia.reservar(parceiro.id, chave, impressao, agora, agora + ttl)
            break
        except IntegrityError:
            # Outra requisição com a mesma chave terminou primeiro: lê a resposta dela.
            uow.rollback()
    if registro is None:
        raise RequisicaoEmAndamento(
            "A requisição original com esta Idempotency-Key ainda está em andamento"
        )

    resposta = operacao()
    registro.status_http = resposta.status
    registro.corpo = resposta.corpo
    uow.commit()
    return _montar(resposta, repetida=False)
