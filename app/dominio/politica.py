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
    """Os ativos que o catálogo regulatório (`app/regulacao.toml`) descreve.

    Não há `deposito`: a SCD do grupo não capta do público (Res. CMN 5.050, art. 10, I).
    O que era depósito virou `cdb` de banco parceiro com liquidez diária.
    """

    CDB = "cdb"  # de banco parceiro, custodiado na CTVM do grupo
    LCI = "lci"
    LCA = "lca"
    COE = "coe"  # descrito no catálogo, mas não recomendado: recusado sempre
    FUNDO_DI = "fundo_di"
    TESOURO_SELIC = "tesouro_selic"
    TESOURO_PREFIXADO = "tesouro_prefixado"
    TESOURO_IPCA = "tesouro_ipca"
    ACOES = "acoes"
    FII = "fii"
    ETF = "etf"
    PREVIDENCIA = "previdencia"  # PGBL, VGBL e capitalização (Res. Conjunta 12)


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
    # {dia_corrido} de atraso da parcela a partir dos quais a garantia do empréstimo é
    # executada (prazo contratual, ilustrativo). A previdência tem piso próprio de 90 dias
    # no catálogo regulatório (Res. Conjunta 12, art. 12).
    dias_para_execucao_emprestimo: int = 30
    # Taxa do empréstimo com garantia {1/mês}, ilustrativa.
    taxa_do_emprestimo_ao_mes: Decimal = Decimal("0.0199")

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
        if self.dias_para_execucao_emprestimo < 1:
            raise PoliticaInvalida("dias_para_execucao_emprestimo precisa ser pelo menos 1")
        taxa = self.taxa_do_emprestimo_ao_mes
        if not isinstance(taxa, Decimal) or not taxa.is_finite() or not 0 <= taxa < 1:
            raise PoliticaInvalida("taxa_do_emprestimo_ao_mes precisa estar em [0, 1)")
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
            "dias_para_execucao_emprestimo": self.dias_para_execucao_emprestimo,
            "taxa_do_emprestimo_ao_mes": str(self.taxa_do_emprestimo_ao_mes),
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
                # Política antiga, gravada antes destes campos, fica com o padrão.
                dias_para_execucao_emprestimo=int(dados.get("dias_para_execucao_emprestimo", 30)),
                taxa_do_emprestimo_ao_mes=Decimal(
                    str(dados.get("taxa_do_emprestimo_ao_mes", "0.0199"))
                ),
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
