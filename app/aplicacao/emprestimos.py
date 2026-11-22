"""Casos de uso do empréstimo parcelado com garantia (Res. Conjunta 12 e demais ativos).

Cada função carrega a conta com trava de linha, pede a decisão ao domínio e grava. Nenhuma
faz commit. O teto da carteira do parceiro entra aqui: a aplicação trava o parceiro, lê a
carteira e a entrega ao domínio, que falha fechado se a carteira não vier.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.aplicacao.erros import ContaNaoEncontrada
from app.aplicacao.portas import UnidadeDeTrabalho
from app.aplicacao.trilhos import prazo_da_baixa
from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.emprestimo import Emprestimo
from app.dominio.parceiro import Parceiro
from app.dominio.regulacao import Trilho

# Sem data informada, a primeira parcela vence em 30 dias corridos.
PRIMEIRO_VENCIMENTO_PADRAO_EM_DIAS = 30


def _conta_para_atualizar(uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str) -> Conta:
    conta = uow.contas.obter_para_atualizar(parceiro.id, conta_id)
    if conta is None:
        raise ContaNaoEncontrada("Conta não encontrada", conta_id=conta_id)
    return conta


def contratar_emprestimo(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    conta_id: str,
    *,
    valor: Dinheiro,
    parcelas: int,
    garantia_ids: list[str],
    primeiro_vencimento: date | None = None,
) -> tuple[Conta, Emprestimo]:
    """Contrata o empréstimo sobre garantias de produto empréstimo.

    Garantia de previdência ainda `solicitado`, com as informações da operadora, é
    formalizada no mesmo passo: o gravame é registrado na plataforma (simulada) e o
    protocolo vai para o domínio (Res. Conjunta 12, art. 8º).
    """
    carteira: Dinheiro | None = None
    if parceiro.politica.teto_limite_do_parceiro is not None:
        # Ordem das travas: parceiro primeiro, conta depois, como na compra do cartão.
        uow.parceiros.travar(parceiro.id)
        carteira = uow.contas.carteira_do_parceiro(parceiro.id)
    conta = _conta_para_atualizar(uow, parceiro, conta_id)
    hoje = uow.relogio.hoje()
    protocolos = {
        g.id: uow.infraestrutura.registrar(g)
        for g in conta.garantias
        if g.id in garantia_ids
        and g.trilho is Trilho.RES_CONJUNTA_12
        and not g.gravame.vigente
        and g.informacoes is not None
    }
    emprestimo = conta.contratar_emprestimo(
        valor=valor,
        parcelas=parcelas,
        garantia_ids=garantia_ids,
        politica=parceiro.politica,
        catalogo=uow.catalogo,
        primeiro_vencimento=primeiro_vencimento
        or hoje + timedelta(days=PRIMEIRO_VENCIMENTO_PADRAO_EM_DIAS),
        agora=uow.relogio.agora(),
        hoje=hoje,
        protocolos_da_previdencia=protocolos,
        carteira_do_parceiro=carteira,
    )
    uow.contas.salvar(conta, parceiro)
    return conta, emprestimo


def pagar_parcela(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str, emprestimo_id: str, valor: Dinheiro
) -> tuple[Conta, Emprestimo]:
    conta = _conta_para_atualizar(uow, parceiro, conta_id)
    emprestimo = conta.pagar_parcela(
        emprestimo_id,
        valor,
        uow.relogio.agora(),
        uow.relogio.hoje(),
        prazo_da_baixa(uow),
    )
    uow.contas.salvar(conta, parceiro)
    return conta, emprestimo
