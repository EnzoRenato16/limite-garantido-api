"""Relógio do sistema e relógio fixo (para testes e demonstrações)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


class RelogioDoSistema:
    def __init__(self, fuso_de_negocio: str = "America/Sao_Paulo") -> None:
        self._fuso = ZoneInfo(fuso_de_negocio)

    def agora(self) -> datetime:
        return datetime.now(UTC)

    def hoje(self) -> date:
        # A data de negócio segue Brasília: às 22h de lá ainda é "hoje", mesmo já sendo
        # amanhã em UTC. Atraso e prazo contam por essa data.
        return self.agora().astimezone(self._fuso).date()


class RelogioFixo:
    """Relógio que só anda quando mandam."""

    def __init__(self, instante: datetime, fuso_de_negocio: str = "America/Sao_Paulo") -> None:
        if instante.tzinfo is None:
            raise ValueError("Use datetime com fuso horário")
        self._instante = instante
        self._fuso = ZoneInfo(fuso_de_negocio)

    def agora(self) -> datetime:
        return self._instante

    def hoje(self) -> date:
        return self._instante.astimezone(self._fuso).date()

    def avancar(self, **duracao: float) -> None:
        from datetime import timedelta

        self._instante += timedelta(**duracao)
