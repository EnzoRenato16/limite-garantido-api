"""Parceiro: a fintech, o varejista ou a carteira que consome a API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.dominio.politica import PoliticaRisco


@dataclass(slots=True)
class Parceiro:
    id: str
    nome: str
    politica: PoliticaRisco
    limite_de_requisicoes_por_minuto: int
    criado_em: datetime
    webhook_url: str | None = None
    webhook_segredo: str | None = None
    ativo: bool = True
