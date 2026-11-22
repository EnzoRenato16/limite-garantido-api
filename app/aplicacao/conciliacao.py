"""Conciliação entre o ledger (a dívida) e o registro do gravame na infraestrutura.

Três divergências, todas de leitura: nada é corrigido sozinho.

- `gravame_sem_divida`: gravame vigente de garantia de um empréstimo que já não está em
  aberto (quitado ou liquidado): o ativo segue onerado sem dívida que justifique;
- `divida_sem_gravame`: dívida (do cartão ou de um empréstimo em aberto) sem gravame
  vigente que a cubra: crédito sem garantia registrada;
- `baixa_atrasada`: baixa pedida e ainda não feita depois do prazo em dias úteis.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.aplicacao.portas import UnidadeDeTrabalho
from app.dominio.conta import Conta
from app.dominio.emprestimo import EstadoEmprestimo
from app.dominio.regulacao import Produto


class TipoDeDivergencia(StrEnum):
    GRAVAME_SEM_DIVIDA = "gravame_sem_divida"
    DIVIDA_SEM_GRAVAME = "divida_sem_gravame"
    BAIXA_ATRASADA = "baixa_atrasada"


@dataclass(frozen=True, slots=True)
class Divergencia:
    tipo: TipoDeDivergencia
    conta_id: str
    garantia_id: str | None
    emprestimo_id: str | None
    detalhe: str


def conciliar(contas: Iterable[Conta], hoje: date) -> list[Divergencia]:
    """Compara, conta a conta, a dívida com os gravames. Ordem estável (conta, tipo, id)."""
    achados: list[Divergencia] = []
    for conta in contas:
        abertos = {
            e.id: e
            for e in conta.emprestimos
            if e.estado in (EstadoEmprestimo.ATIVO, EstadoEmprestimo.EM_ATRASO)
        }
        for garantia in conta.garantias:
            gravame = garantia.gravame
            if (
                garantia.produto is Produto.EMPRESTIMO
                and garantia.emprestimo_id is not None
                and garantia.emprestimo_id not in abertos
                and gravame.vigente
            ):
                achados.append(
                    Divergencia(
                        TipoDeDivergencia.GRAVAME_SEM_DIVIDA,
                        conta.id,
                        garantia.id,
                        garantia.emprestimo_id,
                        f"Gravame vigente ({gravame.protocolo}) de empréstimo que já não está em "
                        "aberto",
                    )
                )
            if (
                gravame.baixa_pendente
                and gravame.baixa_prazo is not None
                and gravame.baixa_prazo < hoje
            ):
                achados.append(
                    Divergencia(
                        TipoDeDivergencia.BAIXA_ATRASADA,
                        conta.id,
                        garantia.id,
                        garantia.emprestimo_id,
                        f"Baixa pedida para {gravame.baixa_prazo.isoformat()} e ainda não feita",
                    )
                )
        if conta.divida_total.positivo and not any(
            g.produto is Produto.CARTAO and g.cobre_divida for g in conta.garantias
        ):
            achados.append(
                Divergencia(
                    TipoDeDivergencia.DIVIDA_SEM_GRAVAME,
                    conta.id,
                    None,
                    None,
                    f"Dívida do cartão de {conta.divida_total.texto()} sem gravame vigente",
                )
            )
        for emprestimo in abertos.values():
            if not any(conta.garantia(g_id).cobre_divida for g_id in emprestimo.garantia_ids):
                achados.append(
                    Divergencia(
                        TipoDeDivergencia.DIVIDA_SEM_GRAVAME,
                        conta.id,
                        None,
                        emprestimo.id,
                        "Empréstimo em aberto sem gravame vigente que o cubra",
                    )
                )
    return sorted(
        achados,
        key=lambda d: (d.conta_id, d.tipo.value, d.garantia_id or "", d.emprestimo_id or ""),
    )


def conciliar_parceiro(uow: UnidadeDeTrabalho, parceiro_id: str | None = None) -> list[Divergencia]:
    """Carrega as contas com gravame vivo ou dívida (uma a uma, sem trava) e concilia."""
    hoje = uow.relogio.hoje()
    contas: list[Conta] = []
    for id_do_parceiro, conta_id in uow.contas.contas_a_conciliar(parceiro_id):
        conta = uow.contas.obter(id_do_parceiro, conta_id)
        if conta is not None:
            contas.append(conta)
    return conciliar(contas, hoje)
