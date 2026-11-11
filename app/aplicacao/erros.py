"""Erros da camada de aplicação. Seguem o mesmo contrato dos erros de domínio."""

from __future__ import annotations

from app.dominio.erros import ErroDominio


class ContaNaoEncontrada(ErroDominio):
    codigo = "conta_nao_encontrada"


class ContaJaExiste(ErroDominio):
    codigo = "conta_ja_existe"


class ParceiroNaoEncontrado(ErroDominio):
    codigo = "parceiro_nao_encontrado"


class EntregaNaoEncontrada(ErroDominio):
    codigo = "entrega_nao_encontrada"


class WebhookNaoConfigurado(ErroDominio):
    codigo = "webhook_nao_configurado"


class UrlDeWebhookInvalida(ErroDominio):
    codigo = "url_de_webhook_invalida"


class ConflitoDeConcorrencia(ErroDominio):
    codigo = "conflito_de_concorrencia"


class ChaveDeIdempotenciaReutilizada(ErroDominio):
    codigo = "chave_de_idempotencia_reutilizada"


class RequisicaoEmAndamento(ErroDominio):
    codigo = "requisicao_em_andamento"


class SemFaturaEmAberto(ErroDominio):
    codigo = "sem_fatura_em_aberto"
