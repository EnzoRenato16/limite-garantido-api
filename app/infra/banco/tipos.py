"""Tipos de coluna que protegem as duas regras de armazenamento do projeto."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Dialect
from sqlalchemy.types import TypeDecorator

from app.dominio.dinheiro import Dinheiro


class Centavos(TypeDecorator[Dinheiro]):
    """Dinheiro guardado como inteiro em centavos (BIGINT). Nunca NUMERIC, nunca float."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: Dinheiro | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if not isinstance(value, Dinheiro):
            raise TypeError(f"Esperava Dinheiro, recebi {type(value).__name__}")
        return value.centavos

    def process_result_value(self, value: Any | None, dialect: Dialect) -> Dinheiro | None:
        return None if value is None else Dinheiro(int(value))


class DataHoraUTC(TypeDecorator[datetime]):
    """Instante sempre em UTC e sempre com fuso, inclusive no SQLite (que não guarda fuso)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Use datetime com fuso horário")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
