"""Unidade de trabalho: uma sessão do SQLAlchemy vista como uma transação de negócio."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy.orm import Session, sessionmaker

from app.aplicacao.portas import Custodiante, Relogio, UnidadeDeTrabalho
from app.infra.banco.repositorios import (
    RepositorioDeContasSql,
    RepositorioDeIdempotencia,
    RepositorioDeParceirosSql,
)


class UnidadeDeTrabalhoSql:
    def __init__(
        self,
        sessao: Session,
        relogio: Relogio,
        custodiante: Custodiante,
        ao_confirmar_eventos: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.sessao = sessao
        self._relogio = relogio
        self._custodiante = custodiante
        # Tipos dos eventos gravados nesta transação. Só viram métrica se houver commit.
        self._eventos_gravados: list[str] = []
        self._ao_confirmar_eventos = ao_confirmar_eventos
        self._contas = RepositorioDeContasSql(sessao, self._eventos_gravados)
        self._parceiros = RepositorioDeParceirosSql(sessao)
        self.idempotencia = RepositorioDeIdempotencia(sessao)

    @property
    def contas(self) -> RepositorioDeContasSql:
        return self._contas

    @property
    def parceiros(self) -> RepositorioDeParceirosSql:
        return self._parceiros

    @property
    def relogio(self) -> Relogio:
        return self._relogio

    @property
    def custodiante(self) -> Custodiante:
        return self._custodiante

    def commit(self) -> None:
        self.sessao.commit()
        if self._ao_confirmar_eventos is not None and self._eventos_gravados:
            self._ao_confirmar_eventos(list(self._eventos_gravados))
        self._eventos_gravados.clear()

    def rollback(self) -> None:
        self.sessao.rollback()
        self._eventos_gravados.clear()


def criar_fabrica_de_unidades(
    fabrica_de_sessoes: sessionmaker[Session],
    relogio: Relogio,
    custodiante: Custodiante,
    ao_confirmar_eventos: Callable[[list[str]], None] | None = None,
) -> Callable[[], AbstractContextManager[UnidadeDeTrabalhoSql]]:
    """Devolve um `with abrir_unidade() as uow:`. Sem commit explícito, desfaz tudo."""

    @contextmanager
    def abrir_unidade() -> Iterator[UnidadeDeTrabalhoSql]:
        sessao = fabrica_de_sessoes()
        try:
            yield UnidadeDeTrabalhoSql(sessao, relogio, custodiante, ao_confirmar_eventos)
        finally:
            sessao.rollback()
            sessao.close()

    return abrir_unidade


def _confere_o_protocolo(uow: UnidadeDeTrabalhoSql) -> UnidadeDeTrabalho:
    # Só para o mypy: garante que a implementação SQL satisfaz a porta da aplicação.
    return uow
