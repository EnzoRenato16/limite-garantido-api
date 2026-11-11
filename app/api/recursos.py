"""O que uma instância da API carrega consigo: banco, relógio, portas e métricas."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.limite_de_requisicoes import LimitadorDeRequisicoes
from app.aplicacao.portas import Custodiante, ProvedorDePrecos, Relogio
from app.config import Configuracao
from app.infra.banco.unidade import UnidadeDeTrabalhoSql
from app.infra.metricas import Metricas


@dataclass(slots=True)
class Recursos:
    config: Configuracao
    engine: Engine
    fabrica_de_sessoes: sessionmaker[Session]
    relogio: Relogio
    custodiante: Custodiante
    provedor_de_precos: ProvedorDePrecos
    metricas: Metricas
    abrir_unidade: Callable[[], AbstractContextManager[UnidadeDeTrabalhoSql]]
    limitador: LimitadorDeRequisicoes = field(default_factory=LimitadorDeRequisicoes)
