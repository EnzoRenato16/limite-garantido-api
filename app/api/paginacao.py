"""Paginação por cursor.

O cursor é opaco para o cliente e, por dentro, é só a chave do último item da página.
Como as chaves são ordenáveis (ULID ou sequência), buscar "os próximos" é um `WHERE`
no índice: não fica mais lento na página 1.000 e não pula nem repete item quando chegam
registros novos, que é o defeito do `OFFSET`.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import Select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.api.erros import ErroDeApi

LIMITE_PADRAO = 20
LIMITE_MAXIMO = 100

Limite = Annotated[
    int,
    Query(ge=1, le=LIMITE_MAXIMO, description="Quantidade de itens por página"),
]
Cursor = Annotated[
    str | None,
    Query(description="Valor de `proximo_cursor` da página anterior. Omita na primeira página."),
]


class Pagina[T](BaseModel):
    dados: list[T]
    proximo_cursor: str | None = Field(
        description="Passe em `cursor` para buscar a próxima página. Nulo na última."
    )
    tem_mais: bool


def codificar_cursor(chave: str | int) -> str:
    bruto = json.dumps({"k": chave}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(bruto).decode().rstrip("=")


def decodificar_cursor(cursor: str) -> str | int:
    try:
        bruto = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        chave = json.loads(bruto)["k"]
    except (binascii.Error, ValueError, KeyError, TypeError) as erro:
        raise ErroDeApi("cursor_invalido", "Cursor de paginação inválido") from erro
    if isinstance(chave, bool) or not isinstance(chave, str | int):
        raise ErroDeApi("cursor_invalido", "Cursor de paginação inválido")
    return chave


def paginar[M, T](
    sessao: Session,
    consulta: Select[M],
    coluna: InstrumentedAttribute[Any],
    limite: int,
    cursor: str | None,
    converter: Callable[[Sequence[M]], list[T]],
    *,
    mais_recentes_primeiro: bool = True,
) -> Pagina[T]:
    if cursor is not None:
        chave = decodificar_cursor(cursor)
        tipo_esperado = int if coluna.type.python_type is int else str
        if not isinstance(chave, tipo_esperado):
            raise ErroDeApi("cursor_invalido", "Este cursor é de outra listagem")
        consulta = consulta.where(coluna < chave if mais_recentes_primeiro else coluna > chave)
    ordem = coluna.desc() if mais_recentes_primeiro else coluna.asc()
    # Busca um a mais só para saber se existe próxima página.
    linhas = sessao.scalars(consulta.order_by(ordem).limit(limite + 1)).all()
    pagina, tem_mais = linhas[:limite], len(linhas) > limite
    proximo = codificar_cursor(getattr(pagina[-1], coluna.key)) if tem_mais and pagina else None
    return Pagina(dados=converter(pagina), proximo_cursor=proximo, tem_mais=tem_mais)
