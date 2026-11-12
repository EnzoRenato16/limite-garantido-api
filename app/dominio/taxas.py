"""Taxas com unidade no tipo, e a conversão entre períodos.

Taxa ao ano vira taxa ao mês por juros compostos: (1 + a)^(1/12) - 1. É o mesmo que
tomar 21 dos 252 dias úteis do CDI. Frequência de eventos (vezes por ano) vira mensal
dividindo por 12, porque frequência não se compõe. Dias corridos viram mês por 365/12.

Tudo em `Decimal`, com 34 dígitos de precisão, nunca `float`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.dominio.erros import ValorInvalido

MESES_NO_ANO = 12  # {mês/ano}
DIAS_UTEIS_NO_ANO = 252  # {dia útil/ano}
DIAS_CORRIDOS_NO_ANO = 365  # {dia/ano}

_PRECISAO = 34


def _exigir_nao_negativo(valor: Decimal, nome: str) -> None:
    if not isinstance(valor, Decimal) or not valor.is_finite():
        raise ValorInvalido(f"{nome} precisa ser um Decimal finito", valor=repr(valor))
    if valor < 0:
        raise ValorInvalido(f"{nome} não pode ser negativo", valor=str(valor))


@dataclass(frozen=True, slots=True)
class Fracao:
    """Razão adimensional entre 0 e 1: {1}."""

    valor: Decimal

    def __post_init__(self) -> None:
        _exigir_nao_negativo(self.valor, "Fração")
        if self.valor > 1:
            raise ValorInvalido("Fração não pode passar de 1", valor=str(self.valor))


@dataclass(frozen=True, slots=True)
class TaxaAoMes:
    """Taxa composta de um mês: {1/mês}."""

    valor: Decimal

    def __post_init__(self) -> None:
        _exigir_nao_negativo(self.valor, "Taxa ao mês")


@dataclass(frozen=True, slots=True)
class TaxaAoAno:
    """Taxa composta de um ano: {1/ano}. 0,1365 quer dizer 13,65% ao ano."""

    valor: Decimal

    def __post_init__(self) -> None:
        _exigir_nao_negativo(self.valor, "Taxa ao ano")

    def ao_mes(self) -> TaxaAoMes:
        """(1 + a)^(1/12) - 1: {1/ano} -> {1/mês}."""
        with localcontext() as contexto:
            contexto.prec = _PRECISAO
            mensal = (1 + self.valor) ** (Decimal(1) / Decimal(MESES_NO_ANO)) - 1
        return TaxaAoMes(mensal)

    def composta_com(self, spread: TaxaAoAno) -> TaxaAoAno:
        """CDI + spread por composição: (1 + a)(1 + s) - 1, tudo {1/ano}."""
        with localcontext() as contexto:
            contexto.prec = _PRECISAO
            composta = (1 + self.valor) * (1 + spread.valor) - 1
        return TaxaAoAno(composta)


@dataclass(frozen=True, slots=True)
class FrequenciaAoAno:
    """Eventos por ano: {evento/ano}. Não se compõe, só se divide."""

    valor: Decimal

    def __post_init__(self) -> None:
        _exigir_nao_negativo(self.valor, "Frequência ao ano")

    def ao_mes(self) -> Decimal:
        """Eventos por mês: {evento/ano} / {mês/ano} -> {evento/mês}."""
        with localcontext() as contexto:
            contexto.prec = _PRECISAO
            return self.valor / Decimal(MESES_NO_ANO)


def dias_para_meses(dias: Decimal) -> Decimal:
    """Dias corridos em meses, por 365/12: {dia} / {dia/mês} -> {mês}."""
    _exigir_nao_negativo(dias, "Dias")
    with localcontext() as contexto:
        contexto.prec = _PRECISAO
        return dias * MESES_NO_ANO / Decimal(DIAS_CORRIDOS_NO_ANO)
