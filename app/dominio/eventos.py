"""Eventos de domínio. São eles que viram webhooks para o parceiro."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TipoEvento(StrEnum):
    GARANTIA_LIBERADA = "garantia.liberada"
    # Execução por inteiro, com o demonstrativo (Lei 4.728/1965, art. 66-B, §3º).
    GARANTIA_EXECUTADA = "garantia.executada"
    LIMITE_ATUALIZADO = "limite.atualizado"
    COMPRA_APROVADA = "compra.aprovada"
    COMPRA_NEGADA = "compra.negada"
    FATURA_PAGA = "fatura.paga"
    FATURA_EM_ATRASO = "fatura.em_atraso"
    CHAMADA_DE_MARGEM = "chamada_de_margem"
    # Gravame: o registro (simulado) na infraestrutura. `gravame.registrado` substitui
    # o antigo `garantia.bloqueada`: é o registro que faz a garantia valer.
    GRAVAME_SOLICITADO = "gravame.solicitado"
    GRAVAME_REGISTRADO = "gravame.registrado"
    GRAVAME_RECUSADO = "gravame.recusado"
    GRAVAME_CANCELADO = "gravame.cancelado"
    GRAVAME_LIBERADO_PARA_EXCUSSAO = "gravame.liberado_para_excussao"
    GRAVAME_BAIXA_PEDIDA = "gravame.baixa_pedida"
    GRAVAME_BAIXADO = "gravame.baixado"
    # Trilhos de entrada de fora.
    PORTABILIDADE_SOLICITADA = "portabilidade.solicitada"
    PORTABILIDADE_CONCLUIDA = "portabilidade.concluida"
    # O prazo regulatório do pedido venceu sem resposta: o pedido continua, marcado.
    PRAZO_REGULATORIO_VENCIDO = "garantia.prazo_regulatorio_vencido"
    TRANSFERENCIA_SOLICITADA = "transferencia.solicitada"
    TRANSFERENCIA_CONCLUIDA = "transferencia.concluida"
    PREVIDENCIA_INFORMACOES_RECEBIDAS = "previdencia.informacoes_recebidas"
    PREVIDENCIA_AVISO_45_DIAS = "previdencia.aviso_45_dias"
    PREVIDENCIA_AVISO_10_DIAS = "previdencia.aviso_10_dias"
    # Empréstimo com garantia (Tarefa 5).
    EMPRESTIMO_CONTRATADO = "emprestimo.contratado"
    EMPRESTIMO_PARCELA_PAGA = "emprestimo.parcela_paga"
    EMPRESTIMO_EM_ATRASO = "emprestimo.em_atraso"
    EMPRESTIMO_QUITADO = "emprestimo.quitado"


@dataclass(frozen=True, slots=True)
class EventoDominio:
    id: str
    tipo: TipoEvento
    conta_id: str
    # Só tipos simples de JSON. Dinheiro vai como texto ("4250.00").
    dados: Mapping[str, object]
    ocorrido_em: datetime
