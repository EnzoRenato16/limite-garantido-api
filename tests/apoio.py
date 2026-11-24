"""Fábricas usadas pelos testes de domínio."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.garantia import Garantia
from app.dominio.politica import PoliticaRisco, TipoAtivo
from app.dominio.regulacao import CatalogoRegulatorio, Produto, Trilho
from app.infra.regulacao import carregar_catalogo

AGORA = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
HOJE = date(2026, 3, 2)
CATALOGO = carregar_catalogo()

# CDB com liquidez diária (o antigo depósito) é o primeiro a ser vendido.
ORDEM_PADRAO = (
    TipoAtivo.CDB,
    TipoAtivo.FUNDO_DI,
    TipoAtivo.TESOURO_SELIC,
    TipoAtivo.TESOURO_PREFIXADO,
    TipoAtivo.TESOURO_IPCA,
    TipoAtivo.ACOES,
    TipoAtivo.ETF,
    TipoAtivo.FII,
    TipoAtivo.LCI,
    TipoAtivo.LCA,
    TipoAtivo.PREVIDENCIA,
    TipoAtivo.COE,
)

LTV_PADRAO = {
    TipoAtivo.FUNDO_DI: Decimal("0.95"),
    TipoAtivo.TESOURO_SELIC: Decimal("0.95"),
    TipoAtivo.CDB: Decimal("0.85"),
    TipoAtivo.TESOURO_PREFIXADO: Decimal("0.80"),
    TipoAtivo.TESOURO_IPCA: Decimal("0.80"),
    TipoAtivo.ACOES: Decimal("0.60"),
}


# Os tipos que a política de teste aceita, em ordem fixa (para as estratégias do Hypothesis).
ATIVOS_ACEITOS = tuple(sorted(LTV_PADRAO))


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


# Liquidez que o catálogo aceita no cartão para cada tipo, em {dia_util}.
LIQUIDEZ_NO_CARTAO = {TipoAtivo.CDB: 0, TipoAtivo.FUNDO_DI: 1}
# Tipos que entram no cartão com o catálogo padrão (sem a chave do agente do TD).
ATIVOS_DO_CARTAO = (TipoAtivo.CDB, TipoAtivo.FUNDO_DI)


def prazo_da_baixa(hoje: date) -> date:
    """Nos testes de domínio, basta uma data depois de hoje (o calendário é da aplicação)."""
    return hoje + timedelta(days=2)


def custodiar(
    conta: Conta,
    tipo: TipoAtivo,
    valor: str,
    pol: PoliticaRisco | None = None,
    hoje: date = HOJE,
    *,
    agora: datetime = AGORA,
    catalogo: CatalogoRegulatorio = CATALOGO,
    produto: Produto = Produto.CARTAO,
    protocolo: str | None = "SIM-b3_balcao-1",
    vencimento: date | None = None,
) -> Garantia:
    """Custódia na casa com o gravame já registrado (protocolo na mão)."""
    return conta.custodiar_na_casa(
        tipo=tipo,
        valor=reais(valor),
        produto=produto,
        politica=pol or politica(),
        catalogo=catalogo,
        agora=agora,
        hoje=hoje,
        liquidez_dias=LIQUIDEZ_NO_CARTAO.get(tipo),
        vencimento=vencimento,
        protocolo=protocolo,
    )


CONTRAPARTE = "banco_de_origem_simulado"
PRAZO = timedelta(days=7)


def receber(
    conta: Conta,
    trilho: Trilho,
    tipo: TipoAtivo,
    valor: str,
    pol: PoliticaRisco | None = None,
    *,
    produto: Produto = Produto.CARTAO,
    catalogo: CatalogoRegulatorio = CATALOGO,
    contraparte_id: str = CONTRAPARTE,
    plano: str | None = None,
) -> Garantia:
    """Pede a entrada por um trilho de fora. A garantia nasce `solicitado`."""
    return conta.receber_de_fora(
        trilho=trilho,
        tipo=tipo,
        valor=reais(valor),
        produto=produto,
        contraparte_id=contraparte_id,
        prazo_ate=AGORA + PRAZO,
        politica=pol or politica(),
        catalogo=catalogo,
        agora=AGORA,
        hoje=HOJE,
        liquidez_dias=LIQUIDEZ_NO_CARTAO.get(tipo),
        plano=plano,
    )
