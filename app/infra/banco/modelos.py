"""Tabelas. Repare no que não existe: nenhuma coluna de saldo.

Dívida e valor de garantia são sempre `SUM` sobre `partidas`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.dominio.dinheiro import Dinheiro
from app.infra.banco.tipos import Centavos, DataHoraUTC

Json = JSON().with_variant(JSONB(), "postgresql")
# Sequência crescente que funciona igual no Postgres (BIGSERIAL) e no SQLite (ROWID).
Sequencia = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    pass


class ParceiroModelo(Base):
    __tablename__ = "parceiros"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    # Da API key só ficam o prefixo (para achar a linha) e o hash SHA-256.
    chave_prefixo: Mapped[str] = mapped_column(String(32), unique=True)
    chave_hash: Mapped[str] = mapped_column(String(64))
    politica: Mapped[dict[str, Any]] = mapped_column(Json)
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    webhook_segredo: Mapped[str | None] = mapped_column(String(128))
    limite_de_requisicoes_por_minuto: Mapped[int] = mapped_column(Integer)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC)


class ContaModelo(Base):
    __tablename__ = "contas"
    __table_args__ = (
        UniqueConstraint("parceiro_id", "cliente_ref", name="uq_contas_parceiro_cliente"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"), index=True)
    cliente_ref: Mapped[str] = mapped_column(String(120))
    estado: Mapped[str] = mapped_column(String(20))
    motivo_bloqueio: Mapped[str | None] = mapped_column(String(40))
    # Trava otimista: toda gravação exige a versão que foi lida.
    versao: Mapped[int] = mapped_column(Integer, default=0)
    criada_em: Mapped[datetime] = mapped_column(DataHoraUTC)


class GarantiaModelo(Base):
    """A relação de crédito sobre um ativo. O estado dela é o do gravame (`gravames`)."""

    __tablename__ = "garantias"
    __table_args__ = (
        Index("ix_garantias_conta_id", "conta_id"),
        CheckConstraint("produto IN ('cartao', 'emprestimo')", name="ck_garantias_produto"),
        CheckConstraint(
            "liquidez_dias IS NULL OR liquidez_dias >= 0", name="ck_garantias_liquidez"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"), index=True)
    tipo_ativo: Mapped[str] = mapped_column(String(30))
    valor_aplicado: Mapped[Dinheiro] = mapped_column(Centavos)
    marcada_em: Mapped[date] = mapped_column(Date)
    criada_em: Mapped[datetime] = mapped_column(DataHoraUTC)
    # Migração 0003. Linhas antigas leem como garantia do cartão.
    produto: Mapped[str] = mapped_column(String(12), server_default="cartao")
    emissor: Mapped[str | None] = mapped_column(String(120))
    vencimento: Mapped[date | None] = mapped_column(Date)
    # Prazo de resgate em dias úteis (0 = D+0).
    liquidez_dias: Mapped[int | None] = mapped_column(Integer)
    # Contraparte de fora (nula na custódia da casa).
    contraparte_id: Mapped[str | None] = mapped_column(String(40))
    plano: Mapped[str | None] = mapped_column(String(20))
    fim_da_acumulacao: Mapped[date | None] = mapped_column(Date)
    aplicado_em: Mapped[date | None] = mapped_column(Date)
    emprestimo_id: Mapped[str | None] = mapped_column(ForeignKey("emprestimos.id"), index=True)
    informacoes_valor_disponivel: Mapped[Dinheiro | None] = mapped_column(Centavos)
    informacoes_tributos: Mapped[Dinheiro | None] = mapped_column(Centavos)
    informacoes_recebidas_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)
    # Pedido de entrada em aberto (os dados dele estão em solicitacoes_de_custodia).
    solicitacao_id: Mapped[str | None] = mapped_column(String(40), index=True)
    solicitacao_enviada_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)
    # Quando se constatou que o prazo regulatório do pedido venceu sem resposta.
    solicitacao_prazo_vencido_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)


ESTADOS_DO_GRAVAME = (
    "solicitado",
    "registrado",
    "liberado_para_excussao",
    "executado",
    "baixado",
    "recusado",
    "cancelado",
)
_TRILHOS = (
    "casa",
    "portabilidade_cvm_210",
    "transferencia_de_custodia",
    "td_garantia",
    "res_conjunta_12",
)
_ESTADOS_DO_EMPRESTIMO = ("ativo", "em_atraso", "quitado", "liquidado_por_execucao")


def _em_lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{valor}'" for valor in valores)


# Índices parciais (SQLite e Postgres): só as linhas que o worker procura.
WHERE_AGUARDANDO_CONTRAPARTE = text("estado = 'solicitado'")
WHERE_BAIXA_PENDENTE = text("baixa_pedida_em IS NOT NULL AND estado IN ('registrado', 'executado')")
WHERE_EMPRESTIMO_ABERTO = text("estado IN ('ativo', 'em_atraso')")
WHERE_PARCELA_EM_ABERTO = text("paga_em IS NULL")


class GravameModelo(Base):
    """O registro da garantia na infraestrutura (simulada), com a máquina de estados."""

    __tablename__ = "gravames"
    __table_args__ = (
        UniqueConstraint("garantia_id", name="uq_gravames_garantia"),
        CheckConstraint(f"estado IN ({_em_lista(ESTADOS_DO_GRAVAME)})", name="ck_gravames_estado"),
        CheckConstraint(f"trilho IN ({_em_lista(_TRILHOS)})", name="ck_gravames_trilho"),
        Index("ix_gravames_conta_id", "conta_id"),
        Index(
            "ix_gravames_aguardando_contraparte",
            "solicitado_em",
            sqlite_where=WHERE_AGUARDANDO_CONTRAPARTE,
            postgresql_where=WHERE_AGUARDANDO_CONTRAPARTE,
        ),
        Index(
            "ix_gravames_baixa_pendente",
            "parceiro_id",
            "conta_id",
            sqlite_where=WHERE_BAIXA_PENDENTE,
            postgresql_where=WHERE_BAIXA_PENDENTE,
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    garantia_id: Mapped[str] = mapped_column(ForeignKey("garantias.id"))
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    infraestrutura: Mapped[str] = mapped_column(String(40))
    instrumento: Mapped[str] = mapped_column(String(30))
    trilho: Mapped[str] = mapped_column(String(30))
    estado: Mapped[str] = mapped_column(String(30))
    solicitado_em: Mapped[datetime] = mapped_column(DataHoraUTC)
    registrado_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)
    protocolo: Mapped[str | None] = mapped_column(String(80))
    baixa_pedida_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)
    baixa_prazo: Mapped[date | None] = mapped_column(Date)
    encerrado_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)
    motivo: Mapped[str | None] = mapped_column(String(200))


class EmprestimoModelo(Base):
    __tablename__ = "emprestimos"
    __table_args__ = (
        CheckConstraint(
            f"estado IN ({_em_lista(_ESTADOS_DO_EMPRESTIMO)})", name="ck_emprestimos_estado"
        ),
        Index("ix_emprestimos_conta_id", "conta_id"),
        Index(
            "ix_emprestimos_abertos",
            "parceiro_id",
            "conta_id",
            sqlite_where=WHERE_EMPRESTIMO_ABERTO,
            postgresql_where=WHERE_EMPRESTIMO_ABERTO,
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    principal: Mapped[Dinheiro] = mapped_column(Centavos)
    # Texto, para o Decimal ir e voltar exato nos dois bancos.
    taxa_ao_mes: Mapped[str] = mapped_column(String(20))
    estado: Mapped[str] = mapped_column(String(30))
    contratado_em: Mapped[datetime] = mapped_column(DataHoraUTC)
    dias_minimos_execucao: Mapped[int] = mapped_column(Integer)
    aviso_45_em: Mapped[date | None] = mapped_column(Date)
    aviso_10_em: Mapped[date | None] = mapped_column(Date)


class ParcelaModelo(Base):
    __tablename__ = "parcelas"
    __table_args__ = (
        CheckConstraint("numero >= 1", name="ck_parcelas_numero"),
        Index(
            "ix_parcelas_em_aberto",
            "vencimento",
            sqlite_where=WHERE_PARCELA_EM_ABERTO,
            postgresql_where=WHERE_PARCELA_EM_ABERTO,
        ),
    )

    emprestimo_id: Mapped[str] = mapped_column(ForeignKey("emprestimos.id"), primary_key=True)
    numero: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    vencimento: Mapped[date] = mapped_column(Date)
    # Início do período (vencimento anterior ou contratação), para os juros pro rata.
    inicio: Mapped[date] = mapped_column(Date)
    valor: Mapped[Dinheiro] = mapped_column(Centavos)
    juros: Mapped[Dinheiro] = mapped_column(Centavos)
    amortizacao: Mapped[Dinheiro] = mapped_column(Centavos)
    juros_pago: Mapped[Dinheiro] = mapped_column(Centavos)
    amortizacao_paga: Mapped[Dinheiro] = mapped_column(Centavos)
    paga_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)


class SolicitacaoDeCustodiaModelo(Base):
    """Cada pedido feito a um custodiante externo. Só inserção: é o histórico.

    Existe para que uma resposta atrasada, de um pedido que já não está em aberto, ainda
    seja reconhecida (e registrada como obsoleta) em vez de cair no vazio.
    """

    __tablename__ = "solicitacoes_de_custodia"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    garantia_id: Mapped[str] = mapped_column(ForeignKey("garantias.id"), index=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    custodiante_id: Mapped[str] = mapped_column(String(40))
    tipo: Mapped[str] = mapped_column(String(20))
    criada_em: Mapped[datetime] = mapped_column(DataHoraUTC)
    prazo_ate: Mapped[datetime] = mapped_column(DataHoraUTC)


class RespostaDeCustodianteModelo(Base):
    """Respostas já recebidas. A chave (custodiante, evento) é o que impede a mesma
    confirmação, por callback e por consulta, de ser aplicada duas vezes."""

    __tablename__ = "respostas_de_custodiante"

    custodiante_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evento_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    solicitacao_id: Mapped[str] = mapped_column(
        ForeignKey("solicitacoes_de_custodia.id"), index=True
    )
    garantia_id: Mapped[str] = mapped_column(ForeignKey("garantias.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    status: Mapped[str] = mapped_column(String(20))
    # callback, polling, sandbox ou admin
    canal: Mapped[str] = mapped_column(String(20))
    # aplicada, ou obsoleta (o pedido já não esperava esta resposta)
    resultado: Mapped[str] = mapped_column(String(20))
    valor_obtido: Mapped[Dinheiro | None] = mapped_column(Centavos)
    motivo: Mapped[str | None] = mapped_column(String(200))
    recebida_em: Mapped[datetime] = mapped_column(DataHoraUTC)


class CompraModelo(Base):
    __tablename__ = "compras"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"), index=True)
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    valor: Mapped[Dinheiro] = mapped_column(Centavos)
    descricao: Mapped[str] = mapped_column(String(200))
    estado: Mapped[str] = mapped_column(String(20))
    motivo_negacao: Mapped[str | None] = mapped_column(String(40))
    criada_em: Mapped[datetime] = mapped_column(DataHoraUTC)


class FaturaModelo(Base):
    __tablename__ = "faturas"
    __table_args__ = (
        Index("ix_faturas_conta_estado", "conta_id", "estado"),
        Index("ix_faturas_estado_vencimento", "estado", "vencimento"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    valor_fechado: Mapped[Dinheiro] = mapped_column(Centavos)
    vencimento: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(String(30))
    fechada_em: Mapped[datetime] = mapped_column(DataHoraUTC)
    encerrada_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)


class PagamentoModelo(Base):
    __tablename__ = "pagamentos"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"), index=True)
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    fatura_id: Mapped[str | None] = mapped_column(ForeignKey("faturas.id"))
    valor: Mapped[Dinheiro] = mapped_column(Centavos)
    valor_na_fatura: Mapped[Dinheiro] = mapped_column(Centavos)
    valor_antecipado: Mapped[Dinheiro] = mapped_column(Centavos)
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC)


class ChamadaDeMargemModelo(Base):
    __tablename__ = "chamadas_de_margem"
    __table_args__ = (
        Index("ix_chamadas_conta_estado", "conta_id", "estado"),
        Index("ix_chamadas_estado_prazo", "estado", "prazo_ate"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    deficit_inicial: Mapped[Dinheiro] = mapped_column(Centavos)
    prazo_ate: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(String(20))
    aberta_em: Mapped[datetime] = mapped_column(DataHoraUTC)
    encerrada_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)


class LancamentoModelo(Base):
    """Cabeçalho do lançamento contábil. Só inserção: nunca é alterado nem apagado."""

    __tablename__ = "lancamentos"

    sequencia: Mapped[int] = mapped_column(Sequencia, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"), index=True)
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    tipo: Mapped[str] = mapped_column(String(30))
    referencia_id: Mapped[str] = mapped_column(String(40))
    descricao: Mapped[str] = mapped_column(String(200))
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC)


class PartidaModelo(Base):
    """Cada débito ou crédito de um lançamento."""

    __tablename__ = "partidas"
    __table_args__ = (
        CheckConstraint("valor > 0", name="ck_partidas_valor_positivo"),
        CheckConstraint("sentido IN ('D', 'C')", name="ck_partidas_sentido"),
        Index("ix_partidas_conta_contabil", "conta_id", "conta_contabil"),
        Index("ix_partidas_parceiro_contabil", "parceiro_id", "conta_contabil"),
    )

    id: Mapped[int] = mapped_column(Sequencia, primary_key=True, autoincrement=True)
    lancamento_sequencia: Mapped[int] = mapped_column(
        ForeignKey("lancamentos.sequencia"), index=True
    )
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    conta_contabil: Mapped[str] = mapped_column(String(30))
    sentido: Mapped[str] = mapped_column(String(1))
    valor: Mapped[Dinheiro] = mapped_column(Centavos)
    garantia_id: Mapped[str | None] = mapped_column(ForeignKey("garantias.id"))


class EventoModelo(Base):
    """Log de eventos e, ao mesmo tempo, a outbox: é gravado na transação da operação."""

    __tablename__ = "eventos"
    __table_args__ = (Index("ix_eventos_parceiro_sequencia", "parceiro_id", "sequencia"),)

    sequencia: Mapped[int] = mapped_column(Sequencia, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True)
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(40))
    dados: Mapped[dict[str, Any]] = mapped_column(Json)
    ocorrido_em: Mapped[datetime] = mapped_column(DataHoraUTC)


class EntregaDeWebhookModelo(Base):
    """Uma entrega de um evento para a URL do parceiro, com seu histórico de tentativas."""

    __tablename__ = "entregas_de_webhook"
    __table_args__ = (
        Index("ix_entregas_fila", "estado", "proxima_tentativa_em"),
        Index("ix_entregas_parceiro", "parceiro_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evento_id: Mapped[str] = mapped_column(ForeignKey("eventos.id"), index=True)
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"))
    url: Mapped[str] = mapped_column(String(500))
    # pendente -> entregue | pendente (nova tentativa) | morta (dead letter)
    estado: Mapped[str] = mapped_column(String(20))
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    proxima_tentativa_em: Mapped[datetime] = mapped_column(DataHoraUTC)
    # Enquanto um worker está enviando, a entrega fica reservada até este instante.
    reservada_ate: Mapped[datetime | None] = mapped_column(DataHoraUTC)
    ultimo_status_http: Mapped[int | None] = mapped_column(Integer)
    ultimo_erro: Mapped[str | None] = mapped_column(Text)
    entregue_em: Mapped[datetime | None] = mapped_column(DataHoraUTC)
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC)


class ChaveDeIdempotenciaModelo(Base):
    __tablename__ = "chaves_de_idempotencia"
    __table_args__ = (Index("ix_idempotencia_expira_em", "expira_em"),)

    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"), primary_key=True)
    chave: Mapped[str] = mapped_column(String(255), primary_key=True)
    # SHA-256 de método + caminho + corpo: a mesma chave com outro payload é conflito.
    impressao_digital: Mapped[str] = mapped_column(String(64))
    # Nulos enquanto a requisição original ainda está em andamento.
    status_http: Mapped[int | None] = mapped_column(Integer)
    corpo: Mapped[dict[str, Any] | None] = mapped_column(Json)
    criado_em: Mapped[datetime] = mapped_column(DataHoraUTC)
    expira_em: Mapped[datetime] = mapped_column(DataHoraUTC)
