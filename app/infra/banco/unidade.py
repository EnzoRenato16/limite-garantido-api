"""Unidade de trabalho: uma sessão do SQLAlchemy vista como uma transação de negócio."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy.orm import Session, sessionmaker

from app.aplicacao.portas import (
    Contraparte,
    InfraestruturaDeRegistro,
    Relogio,
    UnidadeDeTrabalho,
)
from app.dominio.calendario import CalendarioDeDiasUteis
from app.dominio.regulacao import CatalogoRegulatorio
from app.infra.banco.repositorios import (
    RepositorioDeContasSql,
    RepositorioDeIdempotencia,
    RepositorioDeParceirosSql,
)
from app.infra.calendario import carregar_calendario
from app.infra.regulacao import carregar_catalogo


class UnidadeDeTrabalhoSql:
    def __init__(
        self,
        sessao: Session,
        relogio: Relogio,
        contraparte: Contraparte,
        infraestrutura: InfraestruturaDeRegistro,
        catalogo: CatalogoRegulatorio,
        calendario: CalendarioDeDiasUteis,
        ao_confirmar_eventos: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.sessao = sessao
        self._relogio = relogio
        self._contraparte = contraparte
        self._infraestrutura = infraestrutura
        self._catalogo = catalogo
        self._calendario = calendario
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
    def contraparte(self) -> Contraparte:
        return self._contraparte

    @property
    def infraestrutura(self) -> InfraestruturaDeRegistro:
        return self._infraestrutura

    @property
    def catalogo(self) -> CatalogoRegulatorio:
        return self._catalogo

    @property
    def calendario(self) -> CalendarioDeDiasUteis:
        return self._calendario

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
    contraparte: Contraparte,
    infraestrutura: InfraestruturaDeRegistro,
    ao_confirmar_eventos: Callable[[list[str]], None] | None = None,
    *,
    catalogo: CatalogoRegulatorio | None = None,
    calendario: CalendarioDeDiasUteis | None = None,
) -> Callable[[], AbstractContextManager[UnidadeDeTrabalhoSql]]:
    """Devolve um `with abrir_unidade() as uow:`. Sem commit explícito, desfaz tudo."""

    catalogo = catalogo or carregar_catalogo()
    calendario = calendario or carregar_calendario()

    @contextmanager
    def abrir_unidade() -> Iterator[UnidadeDeTrabalhoSql]:
        sessao = fabrica_de_sessoes()
        try:
            yield UnidadeDeTrabalhoSql(
                sessao,
                relogio,
                contraparte,
                infraestrutura,
                catalogo,
                calendario,
                ao_confirmar_eventos,
            )
        finally:
            sessao.rollback()
            sessao.close()

    return abrir_unidade


def _confere_o_protocolo(uow: UnidadeDeTrabalhoSql) -> UnidadeDeTrabalho:
    # Só para o mypy: garante que a implementação SQL satisfaz a porta da aplicação.
    return uow
