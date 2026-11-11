"""Gestão de parceiros: quem faz é a plataforma (API administrativa)."""

from __future__ import annotations

from app.aplicacao.chaves import ChaveGerada, gerar_api_key, gerar_segredo_de_webhook
from app.aplicacao.erros import ParceiroNaoEncontrado
from app.aplicacao.portas import UnidadeDeTrabalho
from app.dominio.ids import novo_id
from app.dominio.parceiro import Parceiro
from app.dominio.politica import PoliticaRisco


def criar_parceiro(
    uow: UnidadeDeTrabalho,
    nome: str,
    politica: PoliticaRisco,
    limite_de_requisicoes_por_minuto: int,
    chave: ChaveGerada | None = None,
) -> tuple[Parceiro, ChaveGerada]:
    """Cria o parceiro e devolve a API key, que só aparece neste momento."""
    chave = chave or gerar_api_key()
    parceiro = Parceiro(
        id=novo_id("par"),
        nome=nome,
        politica=politica,
        limite_de_requisicoes_por_minuto=limite_de_requisicoes_por_minuto,
        criado_em=uow.relogio.agora(),
    )
    uow.parceiros.adicionar(parceiro, chave.prefixo, chave.hash)
    return parceiro, chave


def obter_parceiro(uow: UnidadeDeTrabalho, parceiro_id: str) -> Parceiro:
    parceiro = uow.parceiros.obter(parceiro_id)
    if parceiro is None:
        raise ParceiroNaoEncontrado("Parceiro não encontrado", parceiro_id=parceiro_id)
    return parceiro


def atualizar_politica(
    uow: UnidadeDeTrabalho, parceiro_id: str, politica: PoliticaRisco
) -> Parceiro:
    parceiro = obter_parceiro(uow, parceiro_id)
    parceiro.politica = politica
    uow.parceiros.salvar(parceiro)
    return parceiro


def rotacionar_chave(uow: UnidadeDeTrabalho, parceiro_id: str) -> tuple[Parceiro, ChaveGerada]:
    parceiro = obter_parceiro(uow, parceiro_id)
    chave = gerar_api_key()
    uow.parceiros.trocar_chave(parceiro.id, chave.prefixo, chave.hash)
    return parceiro, chave


def configurar_webhook(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    url: str,
    *,
    rotacionar_segredo: bool = False,
    segredo: str | None = None,
) -> Parceiro:
    """Define a URL de destino. O segredo de assinatura é criado na primeira vez."""
    parceiro.webhook_url = url
    if segredo is not None:
        parceiro.webhook_segredo = segredo
    elif rotacionar_segredo or parceiro.webhook_segredo is None:
        parceiro.webhook_segredo = gerar_segredo_de_webhook()
    uow.parceiros.salvar(parceiro)
    return parceiro
