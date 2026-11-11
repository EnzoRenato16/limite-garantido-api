"""Casos de uso do dia a dia de uma conta.

Cada função carrega a conta com trava de linha, pede a decisão ao domínio e grava o
resultado. Nenhuma faz commit: quem chama decide quando a transação termina (a API faz
isso junto com o registro de idempotência; as rotinas, uma conta por vez).
"""

from __future__ import annotations

from datetime import date

from app.aplicacao.erros import ContaJaExiste, ContaNaoEncontrada
from app.aplicacao.portas import UnidadeDeTrabalho
from app.dominio.conta import Compra, Conta, Fatura, Pagamento
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import GarantiaNaoEncontrada
from app.dominio.garantia import Garantia
from app.dominio.parceiro import Parceiro
from app.dominio.politica import TipoAtivo


def abrir_conta(uow: UnidadeDeTrabalho, parceiro: Parceiro, cliente_ref: str) -> Conta:
    conta = Conta.abrir(parceiro_id=parceiro.id, cliente_ref=cliente_ref, agora=uow.relogio.agora())
    try:
        uow.contas.adicionar(conta)
    except ContaJaExiste:
        raise ContaJaExiste(
            "Já existe uma conta para este cliente neste parceiro", cliente_ref=cliente_ref
        ) from None
    return conta


def consultar_conta(uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str) -> Conta:
    conta = uow.contas.obter(parceiro.id, conta_id)
    if conta is None:
        raise ContaNaoEncontrada("Conta não encontrada", conta_id=conta_id)
    return conta


def _conta_para_atualizar(uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str) -> Conta:
    conta = uow.contas.obter_para_atualizar(parceiro.id, conta_id)
    if conta is None:
        # Conta de outro parceiro também cai aqui: para quem pergunta, ela não existe.
        raise ContaNaoEncontrada("Conta não encontrada", conta_id=conta_id)
    return conta


def _conta_da_garantia(uow: UnidadeDeTrabalho, garantia_id: str, parceiro_id: str | None) -> Conta:
    conta = uow.contas.obter_pela_garantia_para_atualizar(garantia_id, parceiro_id)
    if conta is None:
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)
    return conta


def solicitar_garantia(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    conta_id: str,
    tipo_ativo: TipoAtivo,
    valor: Dinheiro,
) -> tuple[Conta, Garantia]:
    """Aplica o dinheiro do cliente e pede o gravame. Dentro de casa, já sai bloqueada."""
    conta = _conta_para_atualizar(uow, parceiro, conta_id)
    agora, hoje = uow.relogio.agora(), uow.relogio.hoje()
    garantia = conta.registrar_garantia(tipo_ativo, valor, parceiro.politica, agora, hoje)
    if uow.custodiante.solicitar_bloqueio(garantia):
        conta.confirmar_bloqueio(garantia.id, parceiro.politica, agora, hoje)
    uow.contas.salvar(conta, parceiro)
    return conta, garantia


def confirmar_bloqueio(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, garantia_id: str
) -> tuple[Conta, Garantia]:
    """Chamado pela custódia (não pelo parceiro) quando o gravame é confirmado."""
    conta = _conta_da_garantia(uow, garantia_id, parceiro.id)
    garantia = conta.confirmar_bloqueio(
        garantia_id, parceiro.politica, uow.relogio.agora(), uow.relogio.hoje()
    )
    uow.contas.salvar(conta, parceiro)
    return conta, garantia


def liberar_garantia(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, garantia_id: str
) -> tuple[Conta, Garantia]:
    conta = _conta_da_garantia(uow, garantia_id, parceiro.id)
    garantia = conta.liberar(
        garantia_id, parceiro.politica, uow.relogio.agora(), uow.relogio.hoje()
    )
    uow.contas.salvar(conta, parceiro)
    return conta, garantia


def autorizar_compra(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str, valor: Dinheiro, descricao: str
) -> tuple[Conta, Compra]:
    carteira: Dinheiro | None = None
    if parceiro.politica.teto_limite_do_parceiro is not None:
        # Ordem das travas: parceiro primeiro, conta depois. Sempre nessa ordem, para
        # duas compras do mesmo parceiro nunca se travarem uma na outra.
        uow.parceiros.travar(parceiro.id)
        carteira = uow.contas.carteira_do_parceiro(parceiro.id)
    conta = _conta_para_atualizar(uow, parceiro, conta_id)
    compra = conta.autorizar_compra(
        valor, descricao, parceiro.politica, uow.relogio.agora(), uow.relogio.hoje(), carteira
    )
    uow.contas.salvar(conta, parceiro)
    return conta, compra


def fechar_fatura(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str, vencimento: date
) -> tuple[Conta, Fatura]:
    conta = _conta_para_atualizar(uow, parceiro, conta_id)
    fatura = conta.fechar_fatura(vencimento, uow.relogio.agora(), uow.relogio.hoje())
    uow.contas.salvar(conta, parceiro)
    return conta, fatura


def pagar(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str, valor: Dinheiro
) -> tuple[Conta, Pagamento]:
    conta = _conta_para_atualizar(uow, parceiro, conta_id)
    pagamento = conta.pagar(valor, parceiro.politica, uow.relogio.agora(), uow.relogio.hoje())
    uow.contas.salvar(conta, parceiro)
    return conta, pagamento
