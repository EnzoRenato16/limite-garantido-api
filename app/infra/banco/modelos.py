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
    __tablename__ = "garantias"
    __table_args__ = (Index("ix_garantias_conta_estado", "conta_id", "estado"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("contas.id"))
    parceiro_id: Mapped[str] = mapped_column(ForeignKey("parceiros.id"), index=True)
    tipo_ativo: Mapped[str] = mapped_column(String(30))
    valor_aplicado: Mapped[Dinheiro] = mapped_column(Centavos)
    estado: Mapped[str] = mapped_column(String(30))
    marcada_em: Mapped[date] = mapped_column(Date)
    criada_em: Mapped[datetime] = mapped_column(DataHoraUTC)


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
