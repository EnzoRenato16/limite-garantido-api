"""Calendário de dias úteis bancários nacionais.

Vale para os prazos regulatórios (portabilidade, Res. Conjunta 12), os prazos
operacionais dos trilhos e a baixa do gravame. O atraso do cartão e do empréstimo
continua em dias corridos.

Unidades: `DiasUteis` conta {dia_util}; `DiasCorridos` conta {dia_corrido}. São tipos
distintos de propósito: somar dias corridos num calendário de dias úteis (ou um `int`
solto) é erro de tipo, não um prazo errado em silêncio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.dominio.erros import ForaDaCobertura, ValorInvalido

_UM_DIA = timedelta(days=1)
_SABADO = 5  # date.weekday(): segunda = 0 ... sábado = 5, domingo = 6


def _quantidade_valida(quantidade: object, unidade: str) -> None:
    if isinstance(quantidade, bool) or not isinstance(quantidade, int):
        raise TypeError(f"{unidade} conta dias inteiros")
    if quantidade < 0:
        raise ValorInvalido(f"{unidade} não pode ser negativo: {quantidade}")


@dataclass(frozen=True, slots=True)
class DiasUteis:
    quantidade: int  # {dia_util}

    def __post_init__(self) -> None:
        _quantidade_valida(self.quantidade, "DiasUteis")


@dataclass(frozen=True, slots=True)
class DiasCorridos:
    quantidade: int  # {dia_corrido}

    def __post_init__(self) -> None:
        _quantidade_valida(self.quantidade, "DiasCorridos")


@dataclass(frozen=True, slots=True)
class CalendarioDeDiasUteis:
    """Dia útil = segunda a sexta que não é feriado nacional, dentro da cobertura."""

    feriados: frozenset[date]
    cobertura_inicio: date
    cobertura_fim: date

    def __post_init__(self) -> None:
        if self.cobertura_inicio > self.cobertura_fim:
            raise ValorInvalido("A cobertura do calendário começa depois de terminar")
        fora = sorted(d for d in self.feriados if not self._coberta(d))
        if fora:
            raise ValorInvalido(f"Feriados fora da cobertura: {', '.join(map(str, fora))}")

    def _coberta(self, dia: date) -> bool:
        return self.cobertura_inicio <= dia <= self.cobertura_fim

    def _exigir_cobertura(self, dia: date) -> None:
        if not self._coberta(dia):
            raise ForaDaCobertura(
                f"{dia.isoformat()} fora do calendário de dias úteis, que cobre de "
                f"{self.cobertura_inicio.isoformat()} a {self.cobertura_fim.isoformat()}",
                data=dia.isoformat(),
            )

    def e_dia_util(self, dia: date) -> bool:
        self._exigir_cobertura(dia)
        return dia.weekday() < _SABADO and dia not in self.feriados

    def somar(self, dia: date, dias: DiasUteis) -> date:
        """O n-ésimo dia útil depois de `dia` (n = 0 devolve o próprio `dia`).

        {date} + {dia_util} -> {date}. Levanta `ForaDaCobertura` se o prazo passar do fim
        da cobertura, em vez de contar fim de ano sem os feriados.
        """
        if not isinstance(dias, DiasUteis):
            raise TypeError(f"somar espera DiasUteis, não {type(dias).__name__}")
        self._exigir_cobertura(dia)
        faltam = dias.quantidade
        atual = dia
        while faltam:
            atual += _UM_DIA
            if not self._coberta(atual):
                raise ForaDaCobertura(
                    f"{dia.isoformat()} + {dias.quantidade} dias úteis passa do fim do "
                    f"calendário ({self.cobertura_fim.isoformat()})",
                    data=dia.isoformat(),
                    dias_uteis=dias.quantidade,
                )
            if self.e_dia_util(atual):
                faltam -= 1
        return atual

    def dias_uteis_entre(self, inicio: date, fim: date) -> int:
        """Dias úteis depois de `inicio`, até `fim` inclusive: {dia_util}.

        É o inverso de `somar`: `dias_uteis_entre(d, somar(d, n)) == n`.
        """
        if fim < inicio:
            raise ValorInvalido(f"Fim {fim.isoformat()} antes do início {inicio.isoformat()}")
        self._exigir_cobertura(inicio)
        self._exigir_cobertura(fim)
        quantidade = 0
        atual = inicio
        while atual < fim:
            atual += _UM_DIA
            quantidade += self.e_dia_util(atual)
        return quantidade
