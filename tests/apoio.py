"""Fábricas usadas pelos testes de domínio."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.garantia import Garantia
from app.dominio.politica import PoliticaRisco, TipoAtivo

AGORA = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
HOJE = date(2026, 3, 2)

ORDEM_PADRAO = (
    TipoAtivo.DEPOSITO,
    TipoAtivo.FUNDO_DI,
    TipoAtivo.TESOURO_SELIC,
    TipoAtivo.CDB,
    TipoAtivo.TESOURO_PREFIXADO,
    TipoAtivo.TESOURO_IPCA,
    TipoAtivo.ACOES,
)

LTV_PADRAO = {
    TipoAtivo.DEPOSITO: Decimal("1.00"),
    TipoAtivo.FUNDO_DI: Decimal("0.95"),
    TipoAtivo.TESOURO_SELIC: Decimal("0.95"),
    TipoAtivo.CDB: Decimal("0.85"),
    TipoAtivo.TESOURO_PREFIXADO: Decimal("0.80"),
    TipoAtivo.TESOURO_IPCA: Decimal("0.80"),
    TipoAtivo.ACOES: Decimal("0.60"),
}


def reais(valor: str | int) -> Dinheiro:
    return Dinheiro.de_decimal(valor)


def politica(
    *,
    teto_cliente: str | None = None,
    teto_parceiro: str | None = None,
    dias_para_execucao: int = 7,
    prazo_margem: int = 5,
    ltv: dict[TipoAtivo, Decimal] | None = None,
) -> PoliticaRisco:
    return PoliticaRisco(
        ltv_por_ativo=LTV_PADRAO if ltv is None else ltv,
        ordem_de_execucao=ORDEM_PADRAO,
        dias_para_execucao=dias_para_execucao,
        prazo_chamada_de_margem_dias=prazo_margem,
        teto_limite_por_cliente=reais(teto_cliente) if teto_cliente else None,
        teto_limite_do_parceiro=reais(teto_parceiro) if teto_parceiro else None,
    )


def nova_conta() -> Conta:
    return Conta.abrir(parceiro_id="par_teste", cliente_ref="cliente-1", agora=AGORA)


def bloquear(
    conta: Conta,
    tipo: TipoAtivo,
    valor: str,
    pol: PoliticaRisco | None = None,
    hoje: date = HOJE,
) -> Garantia:
    pol = pol or politica()
    garantia = conta.registrar_garantia(tipo, reais(valor), pol, AGORA, hoje)
    return conta.confirmar_bloqueio(garantia.id, pol, AGORA, hoje)


CUSTODIANTE = "custodiante_agil"
PRAZO = timedelta(hours=24)


def registrar_externa(
    conta: Conta, tipo: TipoAtivo, valor: str, pol: PoliticaRisco | None = None
) -> Garantia:
    """Pede o gravame num custodiante externo. A garantia nasce `solicitada`."""
    pol = pol or politica()
    return conta.registrar_garantia(
        tipo, reais(valor), pol, AGORA, HOJE, custodiante_id=CUSTODIANTE, prazo_ate=AGORA + PRAZO
    )


def bloquear_externa(
    conta: Conta, tipo: TipoAtivo, valor: str, pol: PoliticaRisco | None = None
) -> Garantia:
    """Gravame externo pedido, aceito e confirmado: a garantia termina `bloqueada`."""
    pol = pol or politica()
    garantia = registrar_externa(conta, tipo, valor, pol)
    assert garantia.solicitacao is not None
    solicitacao_id = garantia.solicitacao.id
    conta.aceitar_solicitacao(garantia.id, solicitacao_id, AGORA)
    return conta.confirmar_gravame(garantia.id, solicitacao_id, pol, AGORA, HOJE)
