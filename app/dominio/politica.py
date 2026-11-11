"""Política de risco: o que o time de risco configura para cada parceiro.

O domínio só conhece o formato e as validações. Os números (LTV, prazos, tetos) vêm de
fora, da configuração do parceiro.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from app.dominio.dinheiro import Dinheiro, ValorMonetarioInvalido
from app.dominio.erros import AtivoNaoAceito, PoliticaInvalida


class TipoAtivo(StrEnum):
    DEPOSITO = "deposito"
    FUNDO_DI = "fundo_di"
    TESOURO_SELIC = "tesouro_selic"
    TESOURO_PREFIXADO = "tesouro_prefixado"
    TESOURO_IPCA = "tesouro_ipca"
    CDB = "cdb"
    ACOES = "acoes"


@dataclass(frozen=True, slots=True)
class PoliticaRisco:
    # LTV por tipo de ativo aceito: razão adimensional {1}, com 0 < LTV <= 1.
    ltv_por_ativo: Mapping[TipoAtivo, Decimal]
    # Na execução, vende primeiro quem aparece primeiro (os mais líquidos).
    ordem_de_execucao: tuple[TipoAtivo, ...]
    # {dias} de atraso da fatura a partir dos quais a garantia é executada.
    dias_para_execucao: int
    # {dias} que o cliente tem para reforçar a garantia ou pagar numa chamada de margem.
    prazo_chamada_de_margem_dias: int
    # Teto do limite de cada conta, por maior que seja a garantia.
    teto_limite_por_cliente: Dinheiro | None = None
    # Teto da carteira do parceiro: soma dos saldos devedores de todas as contas dele.
    teto_limite_do_parceiro: Dinheiro | None = None

    def __post_init__(self) -> None:
        if not self.ltv_por_ativo:
            raise PoliticaInvalida("A política precisa aceitar pelo menos um tipo de ativo")
        for tipo, ltv in self.ltv_por_ativo.items():
            if not isinstance(ltv, Decimal) or not ltv.is_finite() or not 0 < ltv <= 1:
                raise PoliticaInvalida(f"LTV de {tipo} precisa estar entre 0 (exclusivo) e 1")
        if len(set(self.ordem_de_execucao)) != len(self.ordem_de_execucao):
            raise PoliticaInvalida("A ordem de execução não pode repetir ativos")
        faltando = set(self.ltv_por_ativo) - set(self.ordem_de_execucao)
        if faltando:
            nomes = ", ".join(sorted(faltando))
            raise PoliticaInvalida(f"Ativos aceitos sem posição na ordem de execução: {nomes}")
        if self.dias_para_execucao < 1:
            raise PoliticaInvalida("dias_para_execucao precisa ser pelo menos 1")
        if self.prazo_chamada_de_margem_dias < 1:
            raise PoliticaInvalida("prazo_chamada_de_margem_dias precisa ser pelo menos 1")
        for nome in ("teto_limite_por_cliente", "teto_limite_do_parceiro"):
            teto = getattr(self, nome)
            if teto is not None and not teto.positivo:
                raise PoliticaInvalida(f"{nome} precisa ser positivo quando informado")

    # ------------------------------------------------------------- consultas
    def aceita(self, tipo: TipoAtivo) -> bool:
        return tipo in self.ltv_por_ativo

    def ltv(self, tipo: TipoAtivo) -> Decimal:
        try:
            return self.ltv_por_ativo[tipo]
        except KeyError:
            raise AtivoNaoAceito(
                f"A política deste parceiro não aceita {tipo} como garantia", tipo_ativo=str(tipo)
            ) from None

    def limite_da_garantia(self, tipo: TipoAtivo, valor_de_mercado: Dinheiro) -> Dinheiro:
        """Quanto de limite a garantia libera: {BRL} * LTV {1} -> {BRL}, para baixo."""
        return valor_de_mercado.aplicar_razao(self.ltv(tipo))

    def prioridade_de_execucao(self, tipo: TipoAtivo) -> int:
        """Posição na fila de execução. Ativo fora da política vai para o fim."""
        try:
            return self.ordem_de_execucao.index(tipo)
        except ValueError:
            return len(self.ordem_de_execucao)

    # ---------------------------------------------------------- serialização
    def para_dict(self) -> dict[str, Any]:
        return {
            "ltv_por_ativo": {str(tipo): str(ltv) for tipo, ltv in self.ltv_por_ativo.items()},
            "ordem_de_execucao": [str(tipo) for tipo in self.ordem_de_execucao],
            "dias_para_execucao": self.dias_para_execucao,
            "prazo_chamada_de_margem_dias": self.prazo_chamada_de_margem_dias,
            "teto_limite_por_cliente": _texto_ou_nada(self.teto_limite_por_cliente),
            "teto_limite_do_parceiro": _texto_ou_nada(self.teto_limite_do_parceiro),
        }

    @classmethod
    def de_dict(cls, dados: Mapping[str, Any]) -> PoliticaRisco:
        try:
            return cls(
                ltv_por_ativo={
                    TipoAtivo(tipo): Decimal(str(ltv))
                    for tipo, ltv in dict(dados["ltv_por_ativo"]).items()
                },
                ordem_de_execucao=tuple(TipoAtivo(tipo) for tipo in dados["ordem_de_execucao"]),
                dias_para_execucao=int(dados["dias_para_execucao"]),
                prazo_chamada_de_margem_dias=int(dados["prazo_chamada_de_margem_dias"]),
                teto_limite_por_cliente=_dinheiro_ou_nada(dados.get("teto_limite_por_cliente")),
                teto_limite_do_parceiro=_dinheiro_ou_nada(dados.get("teto_limite_do_parceiro")),
            )
        except (KeyError, ValueError, TypeError, InvalidOperation) as erro:
            raise PoliticaInvalida(f"Política de risco malformada: {erro}") from erro


def _texto_ou_nada(valor: Dinheiro | None) -> str | None:
    return None if valor is None else valor.texto()


def _dinheiro_ou_nada(valor: object) -> Dinheiro | None:
    if valor is None:
        return None
    if not isinstance(valor, str | int):
        raise ValorMonetarioInvalido(f"Teto inválido: {valor!r}")
    return Dinheiro.de_decimal(valor)
