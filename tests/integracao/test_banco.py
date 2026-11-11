"""Garantias que moram no próprio banco: migração e gatilhos do ledger."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.infra.banco.modelos import Base
from app.infra.banco.sessao import criar_engine
from tests.conftest import Parceiro, rodar_migracoes


def test_migracao_alembic_gera_exatamente_o_esquema_dos_modelos(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migracao.db'}"
    rodar_migracoes(url)
    engine = criar_engine(url)
    with engine.connect() as conexao:
        diferencas = compare_metadata(MigrationContext.configure(conexao), Base.metadata)
    engine.dispose()
    assert diferencas == []


@pytest.mark.postgres
def test_no_postgres_a_migracao_tambem_bate_com_os_modelos(engine: Engine) -> None:
    with engine.connect() as conexao:
        contexto = MigrationContext.configure(conexao, opts={"compare_type": True})
        assert compare_metadata(contexto, Base.metadata) == []


def _conta_com_movimento(parceiro: Parceiro) -> str:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "100.00")
    parceiro.comprar(conta, "10.00")
    return conta


@pytest.mark.postgres
@pytest.mark.parametrize(
    "comando",
    [
        "UPDATE partidas SET valor = valor + 1 WHERE conta_id = :conta",
        "DELETE FROM partidas WHERE conta_id = :conta",
        "UPDATE lancamentos SET descricao = 'adulterado' WHERE conta_id = :conta",
        "DELETE FROM lancamentos WHERE conta_id = :conta",
    ],
)
def test_ledger_e_somente_insercao_ate_para_sql_escrito_a_mao(
    parceiro: Parceiro, engine: Engine, comando: str
) -> None:
    conta = _conta_com_movimento(parceiro)
    with pytest.raises(DBAPIError, match="somente inserção"), engine.begin() as conexao:
        conexao.execute(text(comando), {"conta": conta})
    assert parceiro.limite(conta)["divida_total"] == "10.00"


@pytest.mark.postgres
def test_o_banco_recusa_lancamento_desbalanceado_no_commit(
    parceiro: Parceiro, engine: Engine
) -> None:
    conta = _conta_com_movimento(parceiro)
    inserir_cabecalho = text(
        "INSERT INTO lancamentos (id, conta_id, parceiro_id, tipo, referencia_id, descricao, "
        "criado_em) VALUES (:id, :conta, :parceiro, 'compra', 'cmp_x', 'na mão', now()) "
        "RETURNING sequencia"
    )
    inserir_partida = text(
        "INSERT INTO partidas (lancamento_sequencia, conta_id, parceiro_id, conta_contabil, "
        "sentido, valor) VALUES (:seq, :conta, :parceiro, :contabil, :sentido, :valor)"
    )
    base = {"conta": conta, "parceiro": parceiro.id}

    def lancar_na_mao(id_do_lancamento: str, debito: int, credito: int) -> None:
        with engine.begin() as conexao:
            seq = conexao.execute(inserir_cabecalho, {**base, "id": id_do_lancamento}).scalar_one()
            for contabil, sentido, valor in (
                ("recebiveis_a_faturar", "D", debito),
                ("liquidacao_a_pagar", "C", credito),
            ):
                conexao.execute(
                    inserir_partida,
                    {**base, "seq": seq, "contabil": contabil, "sentido": sentido, "valor": valor},
                )

    # Débito de R$ 50 com crédito de R$ 49,99: o commit não passa.
    with pytest.raises(DBAPIError, match="desbalanceado"):
        lancar_na_mao("lan_torto", 5000, 4999)
    assert parceiro.limite(conta)["divida_total"] == "10.00"  # nada foi gravado

    # O mesmo lançamento, fechado, é aceito: o gatilho só barra o que não fecha.
    lancar_na_mao("lan_certo", 5000, 5000)
    assert parceiro.limite(conta)["divida_total"] == "60.00"


@pytest.mark.postgres
def test_partida_com_valor_zero_ou_sentido_invalido_e_recusada(
    parceiro: Parceiro, engine: Engine
) -> None:
    conta = _conta_com_movimento(parceiro)
    for sentido, valor, restricao in (("D", 0, "valor_positivo"), ("X", 100, "sentido")):
        with pytest.raises(DBAPIError, match=restricao), engine.begin() as conexao:
            conexao.execute(
                text(
                    "INSERT INTO partidas (lancamento_sequencia, conta_id, parceiro_id, "
                    "conta_contabil, sentido, valor) SELECT sequencia, conta_id, parceiro_id, "
                    "'caixa_recebimentos', :sentido, :valor FROM lancamentos "
                    "WHERE conta_id = :conta LIMIT 1"
                ),
                {"conta": conta, "sentido": sentido, "valor": valor},
            )
