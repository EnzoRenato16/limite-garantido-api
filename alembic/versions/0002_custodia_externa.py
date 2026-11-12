"""Fase 2: garantia em custodiante externo (simulado).

Revisão: 0002
Anterior: 0001

- `garantias` ganha a origem (padrão `interna`, então as linhas antigas não mudam de
  sentido), o custodiante, o pedido em aberto, o valor em execução e o último motivo.
- `solicitacoes_de_custodia`: histórico de pedidos, só inserção.
- `respostas_de_custodiante`: respostas já recebidas, com chave (custodiante, evento),
  que é a trava contra aplicar a mesma confirmação duas vezes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table: no SQLite (desenvolvimento) a tabela é recriada para receber as
    # restrições; no Postgres vira ALTER TABLE comum.
    with op.batch_alter_table("garantias") as tabela:
        tabela.add_column(
            sa.Column("origem", sa.String(length=10), server_default="interna", nullable=False)
        )
        tabela.add_column(sa.Column("custodiante_id", sa.String(length=40), nullable=True))
        tabela.add_column(sa.Column("solicitacao_id", sa.String(length=40), nullable=True))
        tabela.add_column(
            sa.Column("solicitacao_enviada_em", sa.DateTime(timezone=True), nullable=True)
        )
        tabela.add_column(sa.Column("valor_em_execucao", sa.BigInteger(), nullable=True))
        tabela.add_column(sa.Column("motivo", sa.String(length=200), nullable=True))
        tabela.create_check_constraint("ck_garantias_origem", "origem IN ('interna', 'externa')")
        tabela.create_check_constraint(
            "ck_garantias_custodiante",
            "(origem = 'externa' AND custodiante_id IS NOT NULL)"
            " OR (origem = 'interna' AND custodiante_id IS NULL)",
        )
        tabela.create_index("ix_garantias_solicitacao_id", ["solicitacao_id"], unique=False)

    op.create_table(
        "solicitacoes_de_custodia",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("garantia_id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("custodiante_id", sa.String(length=40), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prazo_ate", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conta_id"], ["contas.id"]),
        sa.ForeignKeyConstraint(["garantia_id"], ["garantias.id"]),
        sa.ForeignKeyConstraint(["parceiro_id"], ["parceiros.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solicitacoes_de_custodia_garantia_id",
        "solicitacoes_de_custodia",
        ["garantia_id"],
        unique=False,
    )
    op.create_table(
        "respostas_de_custodiante",
        sa.Column("custodiante_id", sa.String(length=40), nullable=False),
        sa.Column("evento_id", sa.String(length=80), nullable=False),
        sa.Column("solicitacao_id", sa.String(length=40), nullable=False),
        sa.Column("garantia_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("canal", sa.String(length=20), nullable=False),
        sa.Column("resultado", sa.String(length=20), nullable=False),
        sa.Column("valor_obtido", sa.BigInteger(), nullable=True),
        sa.Column("motivo", sa.String(length=200), nullable=True),
        sa.Column("recebida_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["garantia_id"], ["garantias.id"]),
        sa.ForeignKeyConstraint(["parceiro_id"], ["parceiros.id"]),
        sa.ForeignKeyConstraint(["solicitacao_id"], ["solicitacoes_de_custodia.id"]),
        sa.PrimaryKeyConstraint("custodiante_id", "evento_id"),
    )
    op.create_index(
        "ix_respostas_de_custodiante_solicitacao_id",
        "respostas_de_custodiante",
        ["solicitacao_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_respostas_de_custodiante_solicitacao_id", "respostas_de_custodiante")
    op.drop_table("respostas_de_custodiante")
    op.drop_index("ix_solicitacoes_de_custodia_garantia_id", "solicitacoes_de_custodia")
    op.drop_table("solicitacoes_de_custodia")
    with op.batch_alter_table("garantias") as tabela:
        tabela.drop_index("ix_garantias_solicitacao_id")
        tabela.drop_constraint("ck_garantias_custodiante", type_="check")
        tabela.drop_constraint("ck_garantias_origem", type_="check")
        for coluna in (
            "motivo",
            "valor_em_execucao",
            "solicitacao_enviada_em",
            "solicitacao_id",
            "custodiante_id",
            "origem",
        ):
            tabela.drop_column(coluna)
