"""Engine e fábrica de sessões."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def criar_engine(database_url: str, pool_tamanho: int = 10, pool_excedente: int = 20) -> Engine:
    if database_url.startswith("sqlite"):
        argumentos: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in database_url:
            # Um banco em memória só existe dentro de uma conexão: todo mundo usa a mesma.
            argumentos["poolclass"] = StaticPool
        engine = create_engine(database_url, **argumentos)

        @event.listens_for(engine, "connect")
        def _ligar_chaves_estrangeiras(conexao: Any, _: Any) -> None:
            cursor = conexao.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(
        database_url, pool_pre_ping=True, pool_size=pool_tamanho, max_overflow=pool_excedente
    )


def criar_fabrica_de_sessoes(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
