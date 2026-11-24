"""LTV efetivo de uma garantia: o menor entre a política do parceiro e o teto do catálogo."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.dominio.garantia import Garantia
from app.dominio.politica import PoliticaRisco
from app.dominio.regulacao import CatalogoRegulatorio

# {dia_corrido} por {ano}, para o prazo até o vencimento. 365 (e não 365,25) dá um prazo
# um pouco maior, que cai na faixa de LTV menor ou igual: o lado seguro.
_DIAS_POR_ANO = Decimal(365)


def anos_ate_o_vencimento(vencimento: date | None, hoje: date) -> Decimal | None:
    """{ano} até o vencimento, nunca negativo; None quando o título não vence."""
    if vencimento is None:
        return None
    dias = max((vencimento - hoje).days, 0)  # {dia_corrido}
    return Decimal(dias) / _DIAS_POR_ANO  # {dia_corrido} / {dia_corrido/ano} -> {ano}


def ltv_efetivo(
    garantia: Garantia, politica: PoliticaRisco, catalogo: CatalogoRegulatorio, hoje: date
) -> Decimal:
    """LTV {1} da garantia hoje: min(política, teto do catálogo para o prazo).

    Zero quando o parceiro deixou de aceitar o ativo ou o catálogo não o recomenda: a
    garantia continua onerada e pode ser executada ou liberada, mas não gera limite.
    """
    tipo = garantia.tipo_ativo
    if not politica.aceita(tipo) or not catalogo.regra(tipo).recomendado:
        return Decimal(0)
    teto = catalogo.ltv_maximo(tipo, anos_ate_o_vencimento(garantia.vencimento, hoje))
    return min(politica.ltv(tipo), teto)
