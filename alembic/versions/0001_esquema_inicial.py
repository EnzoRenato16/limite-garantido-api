"""Esquema inicial da versão 1.0.

Revisão: 0001
Anterior: nenhuma

Dinheiro é sempre BIGINT em centavos. Não existe coluna de saldo: dívida e valor de
garantia são somas sobre `partidas`.

No Postgres, dois gatilhos protegem o ledger mesmo de quem escrever SQL na mão:
- `lancamentos` e `partidas` só aceitam INSERT (nada de UPDATE ou DELETE);
- no commit, todo lançamento precisa ter débitos = créditos.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GATILHOS_DO_LEDGER = """
CREATE FUNCTION ledger_somente_insercao() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'O ledger é somente inserção: % em % não é permitido', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_lancamentos_somente_insercao
    BEFORE UPDATE OR DELETE ON lancamentos
    FOR EACH ROW EXECUTE FUNCTION ledger_somente_insercao();

CREATE TRIGGER tg_partidas_somente_insercao
    BEFORE UPDATE OR DELETE ON partidas
    FOR EACH ROW EXECUTE FUNCTION ledger_somente_insercao();

CREATE FUNCTION lancamento_precisa_fechar() RETURNS trigger AS $$
DECLARE
    diferenca bigint;
BEGIN
    SELECT COALESCE(SUM(CASE WHEN sentido = 'D' THEN valor ELSE -valor END), 0)
      INTO diferenca
      FROM partidas
     WHERE lancamento_sequencia = NEW.lancamento_sequencia;
    IF diferenca <> 0 THEN
        RAISE EXCEPTION 'Lançamento % desbalanceado: débitos - créditos = % centavos',
            NEW.lancamento_sequencia, diferenca
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Adiado para o commit: as partidas de um lançamento entram uma a uma.
CREATE CONSTRAINT TRIGGER tg_partidas_lancamento_fecha
    AFTER INSERT ON partidas
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION lancamento_precisa_fechar();
"""

REMOVER_GATILHOS_DO_LEDGER = """
DROP TRIGGER IF EXISTS tg_partidas_lancamento_fecha ON partidas;
DROP TRIGGER IF EXISTS tg_partidas_somente_insercao ON partidas;
DROP TRIGGER IF EXISTS tg_lancamentos_somente_insercao ON lancamentos;
DROP FUNCTION IF EXISTS lancamento_precisa_fechar();
DROP FUNCTION IF EXISTS ledger_somente_insercao();
"""


def _no_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "parceiros",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("chave_prefixo", sa.String(length=32), nullable=False),
        sa.Column("chave_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "politica",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("webhook_url", sa.String(length=500), nullable=True),
        sa.Column("webhook_segredo", sa.String(length=128), nullable=True),
        sa.Column("limite_de_requisicoes_por_minuto", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chave_prefixo"),
    )
    op.create_table(
        "chaves_de_idempotencia",
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("chave", sa.String(length=255), nullable=False),
        sa.Column("impressao_digital", sa.String(length=64), nullable=False),
        sa.Column("status_http", sa.Integer(), nullable=True),
        sa.Column(
            "corpo",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("parceiro_id", "chave"),
    )
    op.create_index(
        "ix_idempotencia_expira_em", "chaves_de_idempotencia", ["expira_em"], unique=False
    )
    op.create_table(
        "contas",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("cliente_ref", sa.String(length=120), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False),
        sa.Column("motivo_bloqueio", sa.String(length=40), nullable=True),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parceiro_id", "cliente_ref", name="uq_contas_parceiro_cliente"),
    )
    op.create_index(op.f("ix_contas_parceiro_id"), "contas", ["parceiro_id"], unique=False)
    op.create_table(
        "chamadas_de_margem",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("deficit_inicial", sa.BigInteger(), nullable=False),
        sa.Column("prazo_ate", sa.Date(), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False),
        sa.Column("aberta_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("encerrada_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            ["contas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chamadas_conta_estado", "chamadas_de_margem", ["conta_id", "estado"], unique=False
    )
    op.create_index(
        "ix_chamadas_estado_prazo", "chamadas_de_margem", ["estado", "prazo_ate"], unique=False
    )
    op.create_table(
        "compras",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("valor", sa.BigInteger(), nullable=False),
        sa.Column("descricao", sa.String(length=200), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False),
        sa.Column("motivo_negacao", sa.String(length=40), nullable=True),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            ["contas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_compras_conta_id"), "compras", ["conta_id"], unique=False)
    op.create_table(
        "eventos",
        sa.Column(
            "sequencia",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("tipo", sa.String(length=40), nullable=False),
        sa.Column(
            "dados",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("ocorrido_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            ["contas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("sequencia"),
        sa.UniqueConstraint("id"),
    )
    op.create_index(op.f("ix_eventos_conta_id"), "eventos", ["conta_id"], unique=False)
    op.create_index(
        "ix_eventos_parceiro_sequencia", "eventos", ["parceiro_id", "sequencia"], unique=False
    )
    op.create_table(
        "faturas",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("valor_fechado", sa.BigInteger(), nullable=False),
        sa.Column("vencimento", sa.Date(), nullable=False),
        sa.Column("estado", sa.String(length=30), nullable=False),
        sa.Column("fechada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("encerrada_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            ["contas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_faturas_conta_estado", "faturas", ["conta_id", "estado"], unique=False)
    op.create_index(
        "ix_faturas_estado_vencimento", "faturas", ["estado", "vencimento"], unique=False
    )
    op.create_table(
        "garantias",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("tipo_ativo", sa.String(length=30), nullable=False),
        sa.Column("valor_aplicado", sa.BigInteger(), nullable=False),
        sa.Column("estado", sa.String(length=30), nullable=False),
        sa.Column("marcada_em", sa.Date(), nullable=False),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            ["contas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_garantias_conta_estado", "garantias", ["conta_id", "estado"], unique=False)
    op.create_index(op.f("ix_garantias_parceiro_id"), "garantias", ["parceiro_id"], unique=False)
    op.create_table(
        "lancamentos",
        sa.Column(
            "sequencia",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("referencia_id", sa.String(length=40), nullable=False),
        sa.Column("descricao", sa.String(length=200), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            ["contas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("sequencia"),
        sa.UniqueConstraint("id"),
    )
    op.create_index(op.f("ix_lancamentos_conta_id"), "lancamentos", ["conta_id"], unique=False)
    op.create_table(
        "entregas_de_webhook",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("evento_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False),
        sa.Column("tentativas", sa.Integer(), nullable=False),
        sa.Column("proxima_tentativa_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reservada_ate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_status_http", sa.Integer(), nullable=True),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("entregue_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evento_id"],
            ["eventos.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_entregas_de_webhook_evento_id"), "entregas_de_webhook", ["evento_id"], unique=False
    )
    op.create_index(
        "ix_entregas_fila", "entregas_de_webhook", ["estado", "proxima_tentativa_em"], unique=False
    )
    op.create_index(
        "ix_entregas_parceiro", "entregas_de_webhook", ["parceiro_id", "id"], unique=False
    )
    op.create_table(
        "pagamentos",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("fatura_id", sa.String(length=40), nullable=True),
        sa.Column("valor", sa.BigInteger(), nullable=False),
        sa.Column("valor_na_fatura", sa.BigInteger(), nullable=False),
        sa.Column("valor_antecipado", sa.BigInteger(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            ["contas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["fatura_id"],
            ["faturas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pagamentos_conta_id"), "pagamentos", ["conta_id"], unique=False)
    op.create_table(
        "partidas",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "lancamento_sequencia",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("conta_contabil", sa.String(length=30), nullable=False),
        sa.Column("sentido", sa.String(length=1), nullable=False),
        sa.Column("valor", sa.BigInteger(), nullable=False),
        sa.Column("garantia_id", sa.String(length=40), nullable=True),
        sa.CheckConstraint("sentido IN ('D', 'C')", name="ck_partidas_sentido"),
        sa.CheckConstraint("valor > 0", name="ck_partidas_valor_positivo"),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            ["contas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["garantia_id"],
            ["garantias.id"],
        ),
        sa.ForeignKeyConstraint(
            ["lancamento_sequencia"],
            ["lancamentos.sequencia"],
        ),
        sa.ForeignKeyConstraint(
            ["parceiro_id"],
            ["parceiros.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_partidas_conta_contabil", "partidas", ["conta_id", "conta_contabil"], unique=False
    )
    op.create_index(
        op.f("ix_partidas_lancamento_sequencia"), "partidas", ["lancamento_sequencia"], unique=False
    )
    op.create_index(
        "ix_partidas_parceiro_contabil", "partidas", ["parceiro_id", "conta_contabil"], unique=False
    )
    if _no_postgres():
        op.execute(GATILHOS_DO_LEDGER)


def downgrade() -> None:
    if _no_postgres():
        op.execute(REMOVER_GATILHOS_DO_LEDGER)
    op.drop_index("ix_partidas_parceiro_contabil", table_name="partidas")
    op.drop_index(op.f("ix_partidas_lancamento_sequencia"), table_name="partidas")
    op.drop_index("ix_partidas_conta_contabil", table_name="partidas")
    op.drop_table("partidas")
    op.drop_index(op.f("ix_pagamentos_conta_id"), table_name="pagamentos")
    op.drop_table("pagamentos")
    op.drop_index("ix_entregas_parceiro", table_name="entregas_de_webhook")
    op.drop_index("ix_entregas_fila", table_name="entregas_de_webhook")
    op.drop_index(op.f("ix_entregas_de_webhook_evento_id"), table_name="entregas_de_webhook")
    op.drop_table("entregas_de_webhook")
    op.drop_index(op.f("ix_lancamentos_conta_id"), table_name="lancamentos")
    op.drop_table("lancamentos")
    op.drop_index(op.f("ix_garantias_parceiro_id"), table_name="garantias")
    op.drop_index("ix_garantias_conta_estado", table_name="garantias")
    op.drop_table("garantias")
    op.drop_index("ix_faturas_estado_vencimento", table_name="faturas")
    op.drop_index("ix_faturas_conta_estado", table_name="faturas")
    op.drop_table("faturas")
    op.drop_index("ix_eventos_parceiro_sequencia", table_name="eventos")
    op.drop_index(op.f("ix_eventos_conta_id"), table_name="eventos")
    op.drop_table("eventos")
    op.drop_index(op.f("ix_compras_conta_id"), table_name="compras")
    op.drop_table("compras")
    op.drop_index("ix_chamadas_estado_prazo", table_name="chamadas_de_margem")
    op.drop_index("ix_chamadas_conta_estado", table_name="chamadas_de_margem")
    op.drop_table("chamadas_de_margem")
    op.drop_index(op.f("ix_contas_parceiro_id"), table_name="contas")
    op.drop_table("contas")
    op.drop_index("ix_idempotencia_expira_em", table_name="chaves_de_idempotencia")
    op.drop_table("chaves_de_idempotencia")
    op.drop_table("parceiros")
