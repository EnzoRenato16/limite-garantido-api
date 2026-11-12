"""Eventos de domínio. São eles que viram webhooks para o parceiro."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TipoEvento(StrEnum):
    GARANTIA_BLOQUEADA = "garantia.bloqueada"
    GARANTIA_LIBERADA = "garantia.liberada"
    GARANTIA_EXECUTADA = "garantia.executada"
    LIMITE_ATUALIZADO = "limite.atualizado"
    COMPRA_APROVADA = "compra.aprovada"
    COMPRA_NEGADA = "compra.negada"
    FATURA_PAGA = "fatura.paga"
    FATURA_EM_ATRASO = "fatura.em_atraso"
    CHAMADA_DE_MARGEM = "chamada_de_margem"
    # Garantia em custodiante externo (fase 2).
    GRAVAME_SOLICITADO = "gravame.solicitado"
    GRAVAME_CONFIRMADO = "gravame.confirmado"
    GRAVAME_RECUSADO = "gravame.recusado"
    GRAVAME_EXPIRADO = "gravame.expirado"
    LIBERACAO_SOLICITADA = "liberacao.solicitada"
    LIBERACAO_CONFIRMADA = "liberacao.confirmada"
    LIBERACAO_FALHOU = "liberacao.falhou"
    EXECUCAO_SOLICITADA = "execucao.solicitada"
    EXECUCAO_CONFIRMADA = "execucao.confirmada"
    EXECUCAO_FALHOU = "execucao.falhou"


@dataclass(frozen=True, slots=True)
class EventoDominio:
    id: str
    tipo: TipoEvento
    conta_id: str
    # Só tipos simples de JSON. Dinheiro vai como texto ("4250.00").
    dados: Mapping[str, object]
    ocorrido_em: datetime
