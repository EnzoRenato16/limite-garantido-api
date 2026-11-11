"""Ambiente do Alembic. A URL do banco vem da configuração da aplicação."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from app.config import obter_configuracao
from app.infra.banco.modelos import Base
from app.infra.banco.sessao import criar_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configurar_logs", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or obter_configuracao().database_url


def rodar_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


def rodar_online() -> None:
    conexao_pronta = config.attributes.get("connection")
    if conexao_pronta is not None:
        context.configure(
            connection=conexao_pronta, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = criar_engine(_url())
    with engine.connect() as conexao:
        context.configure(connection=conexao, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    rodar_offline()
else:
    rodar_online()
