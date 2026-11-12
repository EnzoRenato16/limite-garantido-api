"""Rotinas e simulações de risco.

As rotinas tratam uma conta por transação: a trava dura pouco e uma conta com problema
não impede as outras de serem processadas.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.aplicacao import custodia
from app.aplicacao.erros import ContaNaoEncontrada, SemFaturaEmAberto
from app.aplicacao.portas import FabricaDeUnidades, ProvedorDePrecos, UnidadeDeTrabalho
from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import GarantiaNaoEncontrada, ValorInvalido
from app.dominio.garantia import Garantia
from app.dominio.parceiro import Parceiro
from app.dominio.politica import TipoAtivo
from app.dominio.stress import ResultadoDoStress, simular_stress

log = logging.getLogger(__name__)

MAXIMO_DE_DIAS_NO_SANDBOX = 365


@dataclass(frozen=True, slots=True)
class ResumoDaRotina:
    contas_processadas: int
    contas_com_erro: int
    eventos_gerados: int


def marcar_a_mercado(
    abrir_unidade: FabricaDeUnidades,
    provedor: ProvedorDePrecos,
    parceiro_id: str | None = None,
) -> ResumoDaRotina:
    """Atualiza o valor de cada garantia pela variação de preço desde a última marcação."""
    with abrir_unidade() as uow:
        hoje = uow.relogio.hoje()
        alvos = list(uow.contas.contas_com_garantia_a_marcar(hoje, parceiro_id))

    processadas = erros = eventos = 0
    for id_do_parceiro, conta_id in alvos:
        try:
            with abrir_unidade() as uow:
                parceiro, conta = _carregar(uow, id_do_parceiro, conta_id)
                agora, hoje = uow.relogio.agora(), uow.relogio.hoje()
                for garantia in conta.garantias_ativas:
                    if garantia.marcada_em >= hoje:
                        continue
                    # {BRL/unidade} / {BRL/unidade} -> fator adimensional {1}
                    fator = provedor.preco(garantia.tipo_ativo, hoje) / provedor.preco(
                        garantia.tipo_ativo, garantia.marcada_em
                    )
                    # {BRL} * {1} -> {BRL}, para baixo: na dúvida a garantia vale menos.
                    novo_valor = conta.valor_de_mercado(garantia.id).aplicar_razao(fator)
                    conta.remarcar(garantia.id, novo_valor, parceiro.politica, agora, hoje)
                eventos += len(conta.pendencias.eventos)
                uow.contas.salvar(conta, parceiro)
                uow.commit()
                processadas += 1
        except Exception:
            erros += 1
            log.exception("Falha na marcação a mercado", extra={"conta_id": conta_id})
    return ResumoDaRotina(processadas, erros, eventos)


def virar_o_dia(abrir_unidade: FabricaDeUnidades, parceiro_id: str | None = None) -> ResumoDaRotina:
    """Rotina diária: marca atraso, executa garantia no prazo e vence chamadas de margem."""
    with abrir_unidade() as uow:
        alvos = list(uow.contas.contas_com_prazo_vencido(uow.relogio.hoje(), parceiro_id))

    processadas = erros = eventos = 0
    for id_do_parceiro, conta_id in alvos:
        try:
            with abrir_unidade() as uow:
                parceiro, conta = _carregar(uow, id_do_parceiro, conta_id)
                conta.virar_o_dia(
                    parceiro.politica,
                    uow.relogio.agora(),
                    uow.relogio.hoje(),
                    custodia.prazos_dos_custodiantes(uow),
                )
                eventos += len(conta.pendencias.eventos)
                uow.contas.salvar(conta, parceiro)
                uow.commit()
                processadas += 1
        except Exception:
            erros += 1
            log.exception("Falha na virada do dia", extra={"conta_id": conta_id})
    return ResumoDaRotina(processadas, erros, eventos)


def _carregar(uow: UnidadeDeTrabalho, parceiro_id: str, conta_id: str) -> tuple[Parceiro, Conta]:
    parceiro = uow.parceiros.obter(parceiro_id)
    conta = uow.contas.obter_para_atualizar(parceiro_id, conta_id)
    if parceiro is None or conta is None:
        raise ContaNaoEncontrada("Conta não encontrada", conta_id=conta_id)
    return parceiro, conta


def simular_stress_da_carteira(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    choques: Mapping[TipoAtivo, Decimal],
    probabilidade_de_inadimplencia: Decimal,
) -> ResultadoDoStress:
    carteira = uow.contas.carteira_para_stress(parceiro.id)
    return simular_stress(carteira, choques, parceiro.politica, probabilidade_de_inadimplencia)


# --------------------------------------------------------------------- sandbox
def remarcar_no_sandbox(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    garantia_id: str,
    *,
    variacao_percentual: Decimal | None = None,
    valor_de_mercado: Dinheiro | None = None,
) -> tuple[Conta, Garantia]:
    """Simula um movimento de preço numa garantia (só existe com o sandbox ligado)."""
    conta = uow.contas.obter_pela_garantia_para_atualizar(garantia_id, parceiro.id)
    if conta is None:
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)
    if (variacao_percentual is None) == (valor_de_mercado is None):
        raise ValorInvalido("Informe variacao_percentual ou valor_de_mercado, um dos dois")
    if valor_de_mercado is not None:
        novo_valor = valor_de_mercado
    else:
        assert variacao_percentual is not None  # noqa: S101 - garantido pelo teste acima
        if variacao_percentual < -100:
            raise ValorInvalido("Um ativo não cai mais que 100%")
        # {%} / 100 -> {1}; fator = 1 + variação
        fator = Decimal(1) + variacao_percentual / Decimal(100)
        novo_valor = conta.valor_de_mercado(garantia_id).aplicar_razao(fator)
    garantia = conta.remarcar(
        garantia_id, novo_valor, parceiro.politica, uow.relogio.agora(), uow.relogio.hoje()
    )
    uow.contas.salvar(conta, parceiro)
    return conta, garantia


def avancar_dias_no_sandbox(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str, dias: int
) -> Conta:
    """Faz o tempo passar para uma conta: empurra vencimento e prazo `dias` para trás
    e roda a rotina diária dela. É assim que a demo chega ao 7º dia de atraso."""
    if not 1 <= dias <= MAXIMO_DE_DIAS_NO_SANDBOX:
        raise ValorInvalido(f"dias precisa estar entre 1 e {MAXIMO_DE_DIAS_NO_SANDBOX}")
    conta = uow.contas.obter_para_atualizar(parceiro.id, conta_id)
    if conta is None:
        raise ContaNaoEncontrada("Conta não encontrada", conta_id=conta_id)
    fatura, chamada = conta.fatura_em_aberto, conta.chamada_aberta
    if fatura is None and chamada is None:
        raise SemFaturaEmAberto(
            "Não há fatura fechada nem chamada de margem aberta para o tempo agir"
        )
    recuo = timedelta(days=dias)
    if fatura is not None:
        fatura.vencimento -= recuo
    if chamada is not None:
        chamada.prazo_ate -= recuo
    conta.virar_o_dia(
        parceiro.politica,
        uow.relogio.agora(),
        uow.relogio.hoje(),
        custodia.prazos_dos_custodiantes(uow),
    )
    uow.contas.salvar(conta, parceiro)
    return conta
