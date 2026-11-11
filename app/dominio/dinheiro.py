"""Dinheiro em reais, guardado como inteiro em centavos.

Unidade: {BRL}. Escala: centavos (2 casas). Nunca passa por `float`.

Multiplicar dinheiro por uma razão (LTV, choque de preço) é o único ponto em que surge
fração de centavo. Por isso `aplicar_razao` obriga a escolher o lado do arredondamento:
para baixo por padrão (limite e valor de garantia), para cima só quando o lado seguro é
o maior (estimativa de perda).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

_CASAS = 2
_CENTAVOS_POR_REAL = 10**_CASAS


class ValorMonetarioInvalido(ValueError):
    """O valor informado não pode ser tratado como dinheiro."""


@dataclass(frozen=True, order=True, slots=True)
class Dinheiro:
    centavos: int

    def __post_init__(self) -> None:
        if isinstance(self.centavos, bool) or not isinstance(self.centavos, int):
            raise TypeError("Dinheiro guarda centavos inteiros")

    # ------------------------------------------------------------ construção
    @classmethod
    def zero(cls) -> Dinheiro:
        return cls(0)

    @classmethod
    def de_decimal(cls, valor: Decimal | int | str) -> Dinheiro:
        """Converte reais em `Dinheiro`. Recusa float e mais de duas casas decimais."""
        if isinstance(valor, bool) or not isinstance(valor, Decimal | int | str):
            raise ValorMonetarioInvalido(
                f"Use Decimal, int ou texto para dinheiro, não {type(valor).__name__}"
            )
        try:
            decimal = Decimal(valor)
        except (InvalidOperation, ValueError) as erro:
            raise ValorMonetarioInvalido(f"Valor monetário inválido: {valor!r}") from erro
        if not decimal.is_finite():
            raise ValorMonetarioInvalido(f"Valor monetário inválido: {valor!r}")
        em_centavos = decimal * _CENTAVOS_POR_REAL  # {BRL} * {centavos/BRL} -> {centavos}
        if em_centavos != em_centavos.to_integral_value():
            raise ValorMonetarioInvalido(f"Dinheiro tem no máximo {_CASAS} casas: {valor!r}")
        return cls(int(em_centavos))

    @staticmethod
    def somar(valores: Iterable[Dinheiro]) -> Dinheiro:
        return Dinheiro(sum(valor.centavos for valor in valores))

    # ------------------------------------------------------------ aritmética
    def __add__(self, outro: Dinheiro) -> Dinheiro:
        return Dinheiro(self.centavos + outro.centavos)

    def __sub__(self, outro: Dinheiro) -> Dinheiro:
        return Dinheiro(self.centavos - outro.centavos)

    def aplicar_razao(self, razao: Decimal, *, para_cima: bool = False) -> Dinheiro:
        """Multiplica por uma razão adimensional: {BRL} * {1} -> {BRL}.

        Arredonda para baixo, a menos que `para_cima` seja pedido de forma explícita.
        """
        if not isinstance(razao, Decimal) or not razao.is_finite():
            raise ValorMonetarioInvalido("A razão precisa ser um Decimal finito")
        if razao < 0 or self.centavos < 0:
            raise ValorMonetarioInvalido("Razão e valor precisam ser não negativos")
        modo = ROUND_CEILING if para_cima else ROUND_FLOOR
        return Dinheiro(int((self.centavos * razao).to_integral_value(rounding=modo)))

    # ------------------------------------------------------------- consultas
    @property
    def positivo(self) -> bool:
        return self.centavos > 0

    @property
    def negativo(self) -> bool:
        return self.centavos < 0

    @property
    def nulo(self) -> bool:
        return self.centavos == 0

    def nao_negativo(self) -> Dinheiro:
        """Devolve o próprio valor, ou zero se for negativo."""
        return self if self.centavos > 0 else Dinheiro(0)

    # ---------------------------------------------------------- apresentação
    def para_decimal(self) -> Decimal:
        return Decimal(self.centavos).scaleb(-_CASAS)

    def texto(self) -> str:
        """Formato de API: `"4250.00"`."""
        return str(self.para_decimal())

    def formatado(self) -> str:
        """Formato para gente: `R$ 4.250,00`."""
        reais, centavos = divmod(abs(self.centavos), _CENTAVOS_POR_REAL)
        milhar = f"{reais:,}".replace(",", ".")
        sinal = "-" if self.centavos < 0 else ""
        return f"{sinal}R$ {milhar},{centavos:02d}"

    def __str__(self) -> str:
        return self.texto()
