"""Trilhos de entrada, gravame separado da garantia e empréstimo parcelado.

Revisão: 0003
Anterior: 0002

- `gravames`: o registro na infraestrutura (simulada), com a máquina de estados própria.
  O estado da garantia passa a ser o do gravame; as colunas `estado`, `origem`,
  `valor_em_execucao` e `motivo` saem de `garantias` (o motivo vai para o gravame) e
  `custodiante_id` vira `contraparte_id`.
- `garantias` ganha produto, emissor, vencimento, liquidez, plano, fim da acumulação, data
  da aplicação, empréstimo e as informações da operadora (previdência).
- `emprestimos` e `parcelas`.
- Índices parciais: gravames aguardando contraparte, gravames com baixa pendente,
  empréstimos abertos e parcelas em aberto.
- Políticas dos parceiros: `deposito` sai (vira `cdb`) e os tipos novos entram com os
  valores da política de referência.

Mapeamento dos estados antigos (garantia -> gravame):

| antes                                    | depois                                   |
|------------------------------------------|------------------------------------------|
| interna pendente                         | solicitado                               |
| interna bloqueada, parcialmente_executada| registrado                               |
| liberada, executada                      | baixado                                  |
| liberacao_pendente                       | registrado, com baixa pedida (*)         |
| execucao_solicitada                      | liberado_para_excussao (**)              |
| externa solicitada, gravame_pendente     | cancelado, motivo `trilho_substituido`   |
| externa gravame_recusado                 | recusado (Tesouro) ou cancelado          |
| gravame_expirado                         | cancelado                                |
| externa bloqueada                        | registrado, no trilho de fora do ativo   |

(*) O valor da garantia ainda constava no ledger (a saída só era lançada na confirmação):
a migração lança a compensação (D garantias_a_devolver, C garantias_custodiadas), como a
liberação do modelo novo.
(**) A rotina diária (`Conta.virar_o_dia`) termina a venda desses gravames: executa, lança o
líquido, devolve o excedente e pede a baixa.

O tipo `deposito` vira `cdb` com `liquidez_dias = 0`. O downgrade devolve o esquema e
reconstrói o estado antigo de forma aproximada (a informação nova se perde).
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ESTADOS_DO_GRAVAME = (
    "solicitado",
    "registrado",
    "liberado_para_excussao",
    "executado",
    "baixado",
    "recusado",
    "cancelado",
)
TRILHOS = (
    "casa",
    "portabilidade_cvm_210",
    "transferencia_de_custodia",
    "td_garantia",
    "res_conjunta_12",
)
ESTADOS_DO_EMPRESTIMO = ("ativo", "em_atraso", "quitado", "liquidado_por_execucao")


def _lista(valores: Sequence[str]) -> str:
    return ", ".join(f"'{valor}'" for valor in valores)


WHERE_AGUARDANDO = "estado = 'solicitado'"
WHERE_BAIXA_PENDENTE = "baixa_pedida_em IS NOT NULL AND estado IN ('registrado', 'executado')"
WHERE_EMPRESTIMO_ABERTO = "estado IN ('ativo', 'em_atraso')"
WHERE_PARCELA_ABERTA = "paga_em IS NULL"

# Valores da política de referência no momento desta migração (app/politica_referencia.toml).
LTV_DE_REFERENCIA = {
    "cdb": "0.85",
    "fundo_di": "0.85",
    "tesouro_selic": "0.90",
    "tesouro_prefixado": "0.85",
    "tesouro_ipca": "0.85",
    "acoes": "0.50",
    "etf": "0.50",
    "fii": "0.50",
    "lci": "0.80",
    "lca": "0.80",
    "previdencia": "0.60",
}
ORDEM_DE_REFERENCIA = (
    "cdb",
    "fundo_di",
    "tesouro_selic",
    "tesouro_prefixado",
    "tesouro_ipca",
    "acoes",
    "etf",
    "fii",
    "lci",
    "lca",
    "previdencia",
)

# Tipos que não existiam na 0002 (o `deposito` virou `cdb`).
TIPOS_NOVOS = ("lci", "lca", "fii", "etf", "previdencia")
CONTAS_CONTABEIS_DA_0002 = (
    "garantias_custodiadas",
    "garantias_a_devolver",
    "recebiveis_a_faturar",
    "recebiveis_faturados",
    "liquidacao_a_pagar",
    "caixa_recebimentos",
)

# Trilho de fora do catálogo para cada ativo (o primeiro que não é a casa).
_TRILHO_DE_FORA = """CASE
    WHEN g.tipo_ativo LIKE 'tesouro%' THEN 'td_garantia'
    WHEN g.tipo_ativo IN ('fundo_di', 'acoes', 'fii', 'etf') THEN 'portabilidade_cvm_210'
    WHEN g.tipo_ativo = 'previdencia' THEN 'res_conjunta_12'
    ELSE 'transferencia_de_custodia' END"""
_SO_PEDIDO_DE_FORA = "g.origem = 'externa' AND g.estado IN ('pendente', 'solicitada', 'gravame_pendente')"

_INSERIR_GRAVAMES = f"""
INSERT INTO gravames (
    id, garantia_id, conta_id, parceiro_id, infraestrutura, instrumento, trilho, estado,
    solicitado_em, motivo
)
SELECT
    'grv' || substr(g.id, 4), g.id, g.conta_id, g.parceiro_id,
    CASE
        WHEN g.tipo_ativo LIKE 'tesouro%' THEN 'selic_td_garantia'
        WHEN g.tipo_ativo = 'fundo_di' THEN 'registradora_ou_b3_escriturador'
        WHEN g.tipo_ativo IN ('acoes', 'fii', 'etf') THEN 'b3_central_depositaria'
        WHEN g.tipo_ativo = 'previdencia' THEN 'plataforma_res_conjunta_12'
        ELSE 'b3_balcao' END,
    CASE
        WHEN g.tipo_ativo IN ('acoes', 'fii', 'etf') OR g.tipo_ativo LIKE 'tesouro%'
            THEN 'alienacao_fiduciaria'
        WHEN g.tipo_ativo = 'previdencia' THEN 'direito_de_resgate'
        ELSE 'cessao_fiduciaria' END,
    CASE WHEN g.origem = 'interna' THEN 'casa' ELSE {_TRILHO_DE_FORA} END,
    CASE
        WHEN g.origem = 'interna' AND g.estado = 'pendente' THEN 'solicitado'
        WHEN {_SO_PEDIDO_DE_FORA} THEN 'cancelado'
        WHEN g.origem = 'externa' AND g.estado = 'gravame_recusado'
            THEN CASE WHEN g.tipo_ativo LIKE 'tesouro%' THEN 'recusado' ELSE 'cancelado' END
        WHEN g.estado = 'gravame_expirado' THEN 'cancelado'
        WHEN g.estado IN (
            'bloqueada', 'parcialmente_executada', 'gravame_confirmado', 'liberacao_pendente'
        ) THEN 'registrado'
        WHEN g.estado = 'execucao_solicitada' THEN 'liberado_para_excussao'
        WHEN g.estado IN ('liberada', 'executada') THEN 'baixado'
        ELSE 'cancelado' END,
    g.criada_em,
    CASE WHEN {_SO_PEDIDO_DE_FORA} THEN 'trilho_substituido' ELSE g.motivo END
FROM garantias g
"""

# Datas que o estado antigo não guardava: vale a da criação da garantia.
_COMPLETAR_GRAVAMES = """
UPDATE gravames SET
    registrado_em = (SELECT g.criada_em FROM garantias g WHERE g.id = gravames.garantia_id),
    protocolo = 'MIGRADO-0003-' || garantia_id
WHERE estado IN ('registrado', 'liberado_para_excussao', 'executado', 'baixado')
"""
_BAIXAS = """
UPDATE gravames SET
    baixa_pedida_em = (SELECT g.criada_em FROM garantias g WHERE g.id = gravames.garantia_id)
WHERE estado = 'baixado'
   OR (estado = 'registrado' AND garantia_id IN (
        SELECT id FROM garantias WHERE estado = 'liberacao_pendente'))
"""
_ENCERRAR = """
UPDATE gravames SET
    encerrado_em = (SELECT g.criada_em FROM garantias g WHERE g.id = gravames.garantia_id)
WHERE estado IN ('baixado', 'recusado', 'cancelado')
"""


def _compensar_liberacoes_pendentes() -> None:
    """Garantia em `liberacao_pendente` já saiu do limite, mas o valor só saía do ledger na
    confirmação (modelo antigo). No modelo novo a saída é lançada ao pedir a baixa, então
    o lançamento de compensação entra aqui: o valor deixa de constar como custodiado."""
    ligacao = op.get_bind()
    linhas = ligacao.execute(
        sa.text(
            "SELECT g.id, g.conta_id, g.parceiro_id, g.criada_em, "
            "SUM(CASE WHEN p.sentido = 'D' THEN p.valor ELSE -p.valor END) AS saldo "
            "FROM garantias g JOIN partidas p ON p.garantia_id = g.id "
            "WHERE g.estado = 'liberacao_pendente' "
            "AND p.conta_contabil = 'garantias_custodiadas' "
            "GROUP BY g.id, g.conta_id, g.parceiro_id, g.criada_em"
        )
    ).all()
    for garantia_id, conta_id, parceiro_id, criada_em, saldo in linhas:
        if saldo <= 0:
            continue
        lancamento_id = f"lan_mig0003_{garantia_id}"[:40]
        ligacao.execute(
            sa.text(
                "INSERT INTO lancamentos (id, conta_id, parceiro_id, tipo, referencia_id, "
                "descricao, criado_em) VALUES (:id, :conta, :parceiro, 'liberacao_garantia', "
                ":garantia, 'Compensação da migração 0003: liberação já pedida', :criada_em)"
            ),
            {
                "id": lancamento_id,
                "conta": conta_id,
                "parceiro": parceiro_id,
                "garantia": garantia_id,
                "criada_em": criada_em,
            },
        )
        sequencia = ligacao.execute(
            sa.text("SELECT sequencia FROM lancamentos WHERE id = :id"), {"id": lancamento_id}
        ).scalar_one()
        for conta_contabil, sentido in (
            ("garantias_a_devolver", "D"),
            ("garantias_custodiadas", "C"),
        ):
            ligacao.execute(
                sa.text(
                    "INSERT INTO partidas (lancamento_sequencia, conta_id, parceiro_id, "
                    "conta_contabil, sentido, valor, garantia_id) VALUES "
                    "(:seq, :conta, :parceiro, :cc, :sentido, :valor, :garantia)"
                ),
                {"seq": sequencia, "conta": conta_id, "parceiro": parceiro_id,
                 "cc": conta_contabil, "sentido": sentido, "valor": saldo,
                 "garantia": garantia_id},
            )


def _json_da_politica() -> Any:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def _ajustar_politicas() -> None:
    parceiros = sa.table("parceiros", sa.column("id", sa.String), sa.column("politica", _json_da_politica()))
    conexao = op.get_bind()
    for identificador, politica in conexao.execute(sa.select(parceiros.c.id, parceiros.c.politica)):
        dados = json.loads(politica) if isinstance(politica, str) else dict(politica)
        ltv = dict(dados.get("ltv_por_ativo", {}))
        ordem = list(dados.get("ordem_de_execucao", []))
        deposito = ltv.pop("deposito", None)
        if deposito is not None and "cdb" not in ltv:
            ltv["cdb"] = deposito
        if "deposito" in ordem:
            posicao = ordem.index("deposito")
            ordem.remove("deposito")
            if "cdb" not in ordem:
                ordem.insert(posicao, "cdb")
        # Só entram os tipos que a 0002 não conhecia: o que o parceiro tirou da política
        # (ações, fundo DI, Tesouro, CDB) continua fora.
        for tipo in TIPOS_NOVOS:
            ltv.setdefault(tipo, LTV_DE_REFERENCIA[tipo])
        ordem += [tipo for tipo in ORDEM_DE_REFERENCIA if tipo in TIPOS_NOVOS and tipo not in ordem]
        dados["ltv_por_ativo"] = ltv
        dados["ordem_de_execucao"] = ordem
        conexao.execute(
            sa.update(parceiros).where(parceiros.c.id == identificador).values(politica=dados)
        )


def _reverter_politicas() -> None:
    """Deixa as políticas no formato estrito da 0002: sem os tipos novos e com `deposito`.
    Quem tinha `cdb` ganha `deposito` com o mesmo LTV (a 0002 aceita os dois e não dá para
    saber qual dos dois o parceiro tinha antes)."""
    parceiros = sa.table(
        "parceiros", sa.column("id", sa.String), sa.column("politica", _json_da_politica())
    )
    conexao = op.get_bind()
    for identificador, politica in conexao.execute(sa.select(parceiros.c.id, parceiros.c.politica)):
        dados = json.loads(politica) if isinstance(politica, str) else dict(politica)
        ltv = {t: v for t, v in dict(dados.get("ltv_por_ativo", {})).items() if t not in TIPOS_NOVOS}
        ordem = [t for t in dados.get("ordem_de_execucao", []) if t not in TIPOS_NOVOS]
        if "cdb" in ltv and "deposito" not in ltv:
            ltv["deposito"] = ltv["cdb"]
        if "cdb" in ordem and "deposito" not in ordem:
            ordem.insert(ordem.index("cdb"), "deposito")
        dados["ltv_por_ativo"] = ltv
        dados["ordem_de_execucao"] = ordem
        conexao.execute(
            sa.update(parceiros).where(parceiros.c.id == identificador).values(politica=dados)
        )


def _desfazer_compensacoes() -> None:
    """Apaga os lançamentos `lan_mig0003_*`: sem isso, a confirmação da liberação no modelo
    antigo lançaria a saída de novo. No Postgres o ledger é somente inserção: os gatilhos
    ficam desligados só durante a limpeza."""
    ligacao = op.get_bind()
    no_postgres = ligacao.dialect.name == "postgresql"
    if no_postgres:
        op.execute("ALTER TABLE partidas DISABLE TRIGGER tg_partidas_somente_insercao")
        op.execute("ALTER TABLE lancamentos DISABLE TRIGGER tg_lancamentos_somente_insercao")
    op.execute(
        "DELETE FROM partidas WHERE lancamento_sequencia IN "
        "(SELECT sequencia FROM lancamentos WHERE id LIKE 'lan_mig0003_%')"
    )
    op.execute("DELETE FROM lancamentos WHERE id LIKE 'lan_mig0003_%'")
    if no_postgres:
        op.execute("ALTER TABLE partidas ENABLE TRIGGER tg_partidas_somente_insercao")
        op.execute("ALTER TABLE lancamentos ENABLE TRIGGER tg_lancamentos_somente_insercao")


def _recusar_se_o_codigo_antigo_nao_le() -> None:
    ligacao = op.get_bind()
    problemas = []
    if ligacao.execute(sa.text("SELECT COUNT(*) FROM emprestimos")).scalar():
        problemas.append("há empréstimos")
    novos = ", ".join(f"'{tipo}'" for tipo in TIPOS_NOVOS)
    if ligacao.execute(
        sa.text(f"SELECT COUNT(*) FROM garantias WHERE tipo_ativo IN ({novos})")  # noqa: S608
    ).scalar():
        problemas.append("há garantias de lci, lca, fii, etf ou previdência")
    contas = ", ".join(f"'{conta}'" for conta in CONTAS_CONTABEIS_DA_0002)
    if ligacao.execute(
        sa.text(  # noqa: S608
            f"SELECT COUNT(*) FROM partidas WHERE conta_contabil NOT IN ({contas})"
        )
    ).scalar():
        problemas.append("há partidas em contas contábeis novas")
    if problemas:
        raise RuntimeError(
            "Downgrade da 0003 recusado, o código da 0002 não leria a base: "
            + "; ".join(problemas)
        )


def upgrade() -> None:
    op.create_table(
        "emprestimos",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("principal", sa.BigInteger(), nullable=False),
        sa.Column("taxa_ao_mes", sa.String(length=20), nullable=False),
        sa.Column("estado", sa.String(length=30), nullable=False),
        sa.Column("contratado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dias_minimos_execucao", sa.Integer(), nullable=False),
        sa.Column("aviso_45_em", sa.Date(), nullable=True),
        sa.Column("aviso_10_em", sa.Date(), nullable=True),
        sa.CheckConstraint(
            f"estado IN ({_lista(ESTADOS_DO_EMPRESTIMO)})", name="ck_emprestimos_estado"
        ),
        sa.ForeignKeyConstraint(["conta_id"], ["contas.id"]),
        sa.ForeignKeyConstraint(["parceiro_id"], ["parceiros.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_emprestimos_conta_id", "emprestimos", ["conta_id"])
    op.create_index(
        "ix_emprestimos_abertos",
        "emprestimos",
        ["parceiro_id", "conta_id"],
        sqlite_where=sa.text(WHERE_EMPRESTIMO_ABERTO),
        postgresql_where=sa.text(WHERE_EMPRESTIMO_ABERTO),
    )
    op.create_table(
        "parcelas",
        sa.Column("emprestimo_id", sa.String(length=40), nullable=False),
        sa.Column("numero", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("vencimento", sa.Date(), nullable=False),
        sa.Column("inicio", sa.Date(), nullable=False),
        sa.Column("valor", sa.BigInteger(), nullable=False),
        sa.Column("juros", sa.BigInteger(), nullable=False),
        sa.Column("amortizacao", sa.BigInteger(), nullable=False),
        sa.Column("juros_pago", sa.BigInteger(), nullable=False),
        sa.Column("amortizacao_paga", sa.BigInteger(), nullable=False),
        sa.Column("paga_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("numero >= 1", name="ck_parcelas_numero"),
        sa.ForeignKeyConstraint(["emprestimo_id"], ["emprestimos.id"]),
        sa.PrimaryKeyConstraint("emprestimo_id", "numero"),
    )
    op.create_index(
        "ix_parcelas_em_aberto",
        "parcelas",
        ["vencimento"],
        sqlite_where=sa.text(WHERE_PARCELA_ABERTA),
        postgresql_where=sa.text(WHERE_PARCELA_ABERTA),
    )

    # Fase A: colunas novas de garantias (as antigas ainda ficam, para migrar os dados).
    with op.batch_alter_table("garantias") as tabela:
        tabela.add_column(
            sa.Column("produto", sa.String(length=12), server_default="cartao", nullable=False)
        )
        tabela.add_column(sa.Column("emissor", sa.String(length=120), nullable=True))
        tabela.add_column(sa.Column("vencimento", sa.Date(), nullable=True))
        tabela.add_column(sa.Column("liquidez_dias", sa.Integer(), nullable=True))
        tabela.add_column(sa.Column("plano", sa.String(length=20), nullable=True))
        tabela.add_column(sa.Column("fim_da_acumulacao", sa.Date(), nullable=True))
        tabela.add_column(sa.Column("aplicado_em", sa.Date(), nullable=True))
        tabela.add_column(sa.Column("emprestimo_id", sa.String(length=40), nullable=True))
        tabela.add_column(sa.Column("informacoes_valor_disponivel", sa.BigInteger(), nullable=True))
        tabela.add_column(sa.Column("informacoes_tributos", sa.BigInteger(), nullable=True))
        tabela.add_column(
            sa.Column("informacoes_recebidas_em", sa.DateTime(timezone=True), nullable=True)
        )
        tabela.add_column(
            sa.Column("solicitacao_prazo_vencido_em", sa.DateTime(timezone=True), nullable=True)
        )
        tabela.create_foreign_key(
            "fk_garantias_emprestimo_id_emprestimos", "emprestimos", ["emprestimo_id"], ["id"]
        )
        tabela.create_index("ix_garantias_emprestimo_id", ["emprestimo_id"], unique=False)

    op.create_table(
        "gravames",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("garantia_id", sa.String(length=40), nullable=False),
        sa.Column("conta_id", sa.String(length=40), nullable=False),
        sa.Column("parceiro_id", sa.String(length=40), nullable=False),
        sa.Column("infraestrutura", sa.String(length=40), nullable=False),
        sa.Column("instrumento", sa.String(length=30), nullable=False),
        sa.Column("trilho", sa.String(length=30), nullable=False),
        sa.Column("estado", sa.String(length=30), nullable=False),
        sa.Column("solicitado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registrado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("protocolo", sa.String(length=80), nullable=True),
        sa.Column("baixa_pedida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baixa_prazo", sa.Date(), nullable=True),
        sa.Column("encerrado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo", sa.String(length=200), nullable=True),
        sa.CheckConstraint(f"estado IN ({_lista(ESTADOS_DO_GRAVAME)})", name="ck_gravames_estado"),
        sa.CheckConstraint(f"trilho IN ({_lista(TRILHOS)})", name="ck_gravames_trilho"),
        sa.ForeignKeyConstraint(["conta_id"], ["contas.id"]),
        sa.ForeignKeyConstraint(["garantia_id"], ["garantias.id"]),
        sa.ForeignKeyConstraint(["parceiro_id"], ["parceiros.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("garantia_id", name="uq_gravames_garantia"),
    )
    op.create_index("ix_gravames_conta_id", "gravames", ["conta_id"])
    op.create_index(
        "ix_gravames_aguardando_contraparte",
        "gravames",
        ["solicitado_em"],
        sqlite_where=sa.text(WHERE_AGUARDANDO),
        postgresql_where=sa.text(WHERE_AGUARDANDO),
    )
    op.create_index(
        "ix_gravames_baixa_pendente",
        "gravames",
        ["parceiro_id", "conta_id"],
        sqlite_where=sa.text(WHERE_BAIXA_PENDENTE),
        postgresql_where=sa.text(WHERE_BAIXA_PENDENTE),
    )

    # Fase B: os dados. O depósito vira CDB com liquidez diária.
    op.execute("UPDATE garantias SET liquidez_dias = 0 WHERE tipo_ativo = 'deposito'")
    op.execute("UPDATE garantias SET tipo_ativo = 'cdb' WHERE tipo_ativo = 'deposito'")
    op.execute(_INSERIR_GRAVAMES)
    op.execute(_COMPLETAR_GRAVAMES)
    op.execute(_BAIXAS)
    op.execute(_ENCERRAR)
    _compensar_liberacoes_pendentes()
    # Pedido que a migração cancelou não fica em aberto na garantia.
    op.execute(
        "UPDATE garantias SET solicitacao_id = NULL, solicitacao_enviada_em = NULL "
        "WHERE id IN (SELECT garantia_id FROM gravames WHERE estado IN ('cancelado', 'recusado'))"
    )
    # O único pedido que sobrou é o de entrada; o histórico de liberação e execução fica.
    op.execute("UPDATE solicitacoes_de_custodia SET tipo = 'entrada' WHERE tipo = 'gravame'")

    # Fase C: sai o que o gravame substituiu.
    with op.batch_alter_table("garantias") as tabela:
        tabela.drop_index("ix_garantias_conta_estado")
        tabela.drop_constraint("ck_garantias_custodiante", type_="check")
        tabela.drop_constraint("ck_garantias_origem", type_="check")
        tabela.drop_column("estado")
        tabela.drop_column("origem")
        tabela.drop_column("valor_em_execucao")
        tabela.drop_column("motivo")
        tabela.alter_column("custodiante_id", new_column_name="contraparte_id")
        tabela.create_index("ix_garantias_conta_id", ["conta_id"], unique=False)
        tabela.create_check_constraint("ck_garantias_produto", "produto IN ('cartao', 'emprestimo')")
        tabela.create_check_constraint(
            "ck_garantias_liquidez", "liquidez_dias IS NULL OR liquidez_dias >= 0"
        )

    _ajustar_politicas()


def downgrade() -> None:
    _recusar_se_o_codigo_antigo_nao_le()
    _reverter_politicas()
    _desfazer_compensacoes()
    # O depósito virou CDB com liquidez diária; CDB com liquidez 0 volta a ser depósito
    # (um CDB D+0 criado depois da 0003 também volta: os dois não se distinguem).
    op.execute("UPDATE garantias SET tipo_ativo = 'deposito' WHERE liquidez_dias = 0 AND tipo_ativo = 'cdb'")
    with op.batch_alter_table("garantias") as tabela:
        tabela.drop_constraint("ck_garantias_liquidez", type_="check")
        tabela.drop_constraint("ck_garantias_produto", type_="check")
        tabela.drop_index("ix_garantias_conta_id")
        tabela.alter_column("contraparte_id", new_column_name="custodiante_id")
        tabela.add_column(
            sa.Column("estado", sa.String(length=30), server_default="bloqueada", nullable=False)
        )
        tabela.add_column(
            sa.Column("origem", sa.String(length=10), server_default="interna", nullable=False)
        )
        tabela.add_column(sa.Column("valor_em_execucao", sa.BigInteger(), nullable=True))
        tabela.add_column(sa.Column("motivo", sa.String(length=200), nullable=True))

    # Reconstrução aproximada: o que o gravame sabia e o estado antigo não perde-se.
    op.execute(
        """
        UPDATE garantias SET
            origem = CASE WHEN (SELECT v.trilho FROM gravames v WHERE v.garantia_id = garantias.id)
                               = 'casa' THEN 'interna' ELSE 'externa' END,
            motivo = (SELECT v.motivo FROM gravames v WHERE v.garantia_id = garantias.id),
            estado = COALESCE((
                SELECT CASE
                    WHEN v.estado = 'solicitado' AND v.trilho = 'casa' THEN 'pendente'
                    WHEN v.estado = 'solicitado' THEN 'gravame_pendente'
                    WHEN v.estado = 'registrado' AND v.baixa_pedida_em IS NOT NULL
                        THEN 'liberacao_pendente'
                    WHEN v.estado = 'registrado' THEN 'bloqueada'
                    WHEN v.estado = 'liberado_para_excussao' THEN 'execucao_solicitada'
                    WHEN v.estado = 'executado' THEN 'executada'
                    WHEN v.estado = 'baixado' THEN 'liberada'
                    WHEN v.estado = 'recusado' THEN 'gravame_recusado'
                    WHEN v.trilho = 'casa' THEN 'liberada'
                    ELSE 'gravame_expirado' END
                FROM gravames v WHERE v.garantia_id = garantias.id), 'bloqueada')
        """
    )
    op.execute("UPDATE solicitacoes_de_custodia SET tipo = 'gravame' WHERE tipo = 'entrada'")
    op.drop_table("gravames")
    with op.batch_alter_table("garantias") as tabela:
        tabela.alter_column("estado", server_default=None)
        tabela.alter_column("origem", server_default=None)
        tabela.drop_index("ix_garantias_emprestimo_id")
        tabela.drop_constraint("fk_garantias_emprestimo_id_emprestimos", type_="foreignkey")
        for coluna in (
            "solicitacao_prazo_vencido_em",
            "informacoes_recebidas_em",
            "informacoes_tributos",
            "informacoes_valor_disponivel",
            "emprestimo_id",
            "aplicado_em",
            "fim_da_acumulacao",
            "plano",
            "liquidez_dias",
            "vencimento",
            "emissor",
            "produto",
        ):
            tabela.drop_column(coluna)
        tabela.create_index("ix_garantias_conta_estado", ["conta_id", "estado"], unique=False)
        tabela.create_check_constraint("ck_garantias_origem", "origem IN ('interna', 'externa')")
        tabela.create_check_constraint(
            "ck_garantias_custodiante",
            "(origem = 'externa' AND custodiante_id IS NOT NULL)"
            " OR (origem = 'interna' AND custodiante_id IS NULL)",
        )
    op.drop_index("ix_parcelas_em_aberto", "parcelas")
    op.drop_table("parcelas")
    op.drop_index("ix_emprestimos_abertos", "emprestimos")
    op.drop_index("ix_emprestimos_conta_id", "emprestimos")
    op.drop_table("emprestimos")
