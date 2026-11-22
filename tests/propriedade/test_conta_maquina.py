"""Máquina de estados do Hypothesis sobre a conta do cartão.

O Hypothesis sorteia sequências de operações (custodiar com ou sem protocolo, concluir
a entrada, remarcar, comprar, pagar, fechar fatura, passar os dias com execução por
inteiro, liberar com pedido de baixa e confirmar a baixa) e confere, depois de cada
passo, as regras que nunca podem quebrar:

- o ledger fecha em zero;
- o limite nunca vem de garantia sem gravame vigente;
- o excedente da execução nunca é negativo e o demonstrativo fecha;
- a execução nunca abate mais que a fatura vencida.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from app.dominio.conta import EstadoChamada, EstadoConta, PosicaoDeLimite
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import GarantiaCobrindoDivida
from app.dominio.eventos import TipoEvento
from app.dominio.gravame import EstadoGravame
from app.dominio.ledger import ContaContabil, Lancamento, ledger_fecha
from app.dominio.politica import TipoAtivo
from tests.apoio import (
    AGORA,
    ATIVOS_DO_CARTAO,
    CATALOGO,
    HOJE,
    custodiar,
    nova_conta,
    politica,
    prazo_da_baixa,
)

ZERO = Dinheiro.zero()


def _dinheiro(texto: object) -> Dinheiro:
    return Dinheiro.de_decimal(str(texto))


class MaquinaDaConta(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.pol = politica(teto_cliente="80000.00")
        self.cat = CATALOGO
        self.conta = nova_conta()
        self.hoje = HOJE
        self.lancamentos: list[Lancamento] = []
        self.comprado = ZERO
        self.pago = ZERO
        self.abatido = ZERO
        self.protocolos = 0

    def _guardar_pendencias(self) -> None:
        self.lancamentos.extend(self.conta.pendencias.lancamentos)
        self.conta.pendencias.limpar()

    def _posicao(self) -> PosicaoDeLimite:
        return self.conta.posicao(self.pol, self.cat, self.hoje)

    # ------------------------------------------------------------- operações
    @rule(
        tipo=st.sampled_from(ATIVOS_DO_CARTAO),
        centavos=st.integers(1, 5_000_000),
        com_protocolo=st.booleans(),
    )
    def custodiar(self, tipo: TipoAtivo, centavos: int, com_protocolo: bool) -> None:
        self.protocolos += 1
        custodiar(
            self.conta,
            tipo,
            Dinheiro(centavos).texto(),
            self.pol,
            self.hoje,
            protocolo=f"SIM-{self.protocolos}" if com_protocolo else None,
        )
        self._guardar_pendencias()

    @precondition(
        lambda self: any(g.estado is EstadoGravame.SOLICITADO for g in self.conta.garantias)
    )
    @rule(data=st.data(), concluir=st.booleans())
    def responder_a_entrada(self, data: st.DataObject, concluir: bool) -> None:
        pendentes = [g for g in self.conta.garantias if g.estado is EstadoGravame.SOLICITADO]
        garantia = data.draw(st.sampled_from(pendentes))
        if concluir:
            self.protocolos += 1
            self.conta.concluir_entrada(
                garantia.id, f"SIM-{self.protocolos}", self.pol, self.cat, AGORA, self.hoje
            )
        else:
            self.conta.cancelar_pedido(garantia.id, "desistencia", AGORA)
        self._guardar_pendencias()

    @precondition(lambda self: self.conta.garantias_vigentes)
    @rule(data=st.data(), fator=st.decimals(Decimal(0), Decimal(2), places=4))
    def remarcar(self, data: st.DataObject, fator: Decimal) -> None:
        garantia = data.draw(st.sampled_from(self.conta.garantias_vigentes))
        novo_valor = self.conta.valor_do_titulo(garantia.id).aplicar_razao(fator)
        self.conta.remarcar(garantia.id, novo_valor, self.pol, self.cat, AGORA, self.hoje)
        self._guardar_pendencias()

    @rule(centavos=st.integers(1, 3_000_000))
    def comprar(self, centavos: int) -> None:
        valor = Dinheiro(centavos)
        antes = self._posicao()
        compra = self.conta.autorizar_compra(valor, "loja", self.pol, self.cat, AGORA, self.hoje)
        depois = self._posicao()
        if compra.aprovada:
            assert valor <= antes.limite_disponivel
            assert depois.divida_total == antes.divida_total + valor
            self.comprado += valor
        else:
            assert depois.divida_total == antes.divida_total
        self._guardar_pendencias()

    @precondition(lambda self: self.conta.divida_total.positivo)
    @rule(data=st.data())
    def pagar(self, data: st.DataObject) -> None:
        centavos = data.draw(st.integers(1, self.conta.divida_total.centavos))
        self.conta.pagar(Dinheiro(centavos), self.pol, self.cat, AGORA, self.hoje)
        self.pago += Dinheiro(centavos)
        self._guardar_pendencias()

    @precondition(
        lambda self: self.conta.divida_a_faturar.positivo and self.conta.fatura_em_aberto is None
    )
    @rule(dias_ate_vencer=st.integers(0, 15))
    def fechar_fatura(self, dias_ate_vencer: int) -> None:
        self.conta.fechar_fatura(self.hoje + timedelta(days=dias_ate_vencer), AGORA, self.hoje)
        self._guardar_pendencias()

    @rule(dias=st.integers(1, 10))
    def passar_dias(self, dias: int) -> None:
        self.hoje += timedelta(days=dias)
        faturada_antes = self.conta.divida_faturada
        self.conta.virar_o_dia(self.pol, self.cat, AGORA, self.hoje, prazo_da_baixa(self.hoje))
        abatido_agora = ZERO
        for evento in self.conta.pendencias.eventos:
            if evento.tipo is not TipoEvento.GARANTIA_EXECUTADA:
                continue
            demonstrativo = evento.dados["demonstrativo"]
            assert isinstance(demonstrativo, dict)
            valor, retencao, liquido, abatida, excedente = (
                _dinheiro(demonstrativo[campo])
                for campo in (
                    "valor_de_mercado",
                    "retencao",
                    "liquido",
                    "divida_abatida",
                    "excedente_devolvido",
                )
            )
            assert excedente >= ZERO
            assert retencao >= ZERO
            assert retencao + liquido == valor
            assert abatida + excedente == liquido
            # Garantia executada é vendida por inteiro e tem a baixa pedida.
            garantia = self.conta.garantia(str(evento.dados["garantia_id"]))
            assert garantia.estado is EstadoGravame.EXECUTADO
            assert garantia.gravame.baixa_prazo == prazo_da_baixa(self.hoje)
            assert self.conta.valor_de_mercado(garantia.id).nulo
            abatido_agora += abatida
        # A execução nunca abate mais do que a fatura vencida.
        assert abatido_agora <= faturada_antes
        self.abatido += abatido_agora
        self._guardar_pendencias()

    @precondition(lambda self: self.conta.garantias_vigentes)
    @rule(data=st.data())
    def tentar_liberar(self, data: st.DataObject) -> None:
        garantia = data.draw(st.sampled_from(self.conta.garantias_vigentes))
        try:
            self.conta.liberar(
                garantia.id, self.pol, self.cat, AGORA, self.hoje, prazo_da_baixa(self.hoje)
            )
        except GarantiaCobrindoDivida:
            assert garantia.gravame.vigente
        else:
            assert not garantia.gravame.vigente
            assert garantia.gravame.baixa_pendente
            assert self._posicao().deficit.nulo
        self._guardar_pendencias()

    @precondition(lambda self: self.conta.gravames_com_baixa_pendente())
    @rule(data=st.data())
    def confirmar_baixa(self, data: st.DataObject) -> None:
        garantia = data.draw(st.sampled_from(self.conta.gravames_com_baixa_pendente()))
        antes = self._posicao()
        self.conta.confirmar_baixa(garantia.id, AGORA)
        assert garantia.estado is EstadoGravame.BAIXADO
        assert self._posicao() == antes
        self._guardar_pendencias()

    # ------------------------------------------------------------ invariantes
    @invariant()
    def limite_nunca_fica_negativo(self) -> None:
        posicao = self._posicao()
        assert posicao.limite_disponivel >= ZERO
        assert posicao.deficit >= ZERO
        assert posicao.divida_total >= ZERO
        assert posicao.limite_disponivel <= posicao.limite_total
        assert posicao.limite_total <= posicao.limite_garantido <= posicao.valor_das_garantias
        assert posicao.limite_disponivel.nulo or posicao.deficit.nulo

    @invariant()
    def limite_so_vem_de_gravame_vigente(self) -> None:
        vigentes = [g for g in self.conta.garantias if g.gravame.vigente]
        teto = Dinheiro.somar(self.conta.valor_de_mercado(g.id) for g in vigentes)
        assert self._posicao().limite_garantido <= teto
        for garantia in self.conta.garantias:
            assert garantia.gera_limite == garantia.gravame.vigente

    @invariant()
    def ledger_sempre_fecha_em_zero(self) -> None:
        assert ledger_fecha(self.lancamentos)
        assert self.conta.saldos.debitos_menos_creditos() == ZERO

    @invariant()
    def divida_e_compras_menos_pagamentos_menos_execucoes(self) -> None:
        assert self.conta.divida_total == self.comprado - self.pago - self.abatido

    @invariant()
    def custodia_bate_com_as_garantias(self) -> None:
        saldos = self.conta.saldos
        custodiado = saldos.saldo_total(ContaContabil.GARANTIAS_CUSTODIADAS) + saldos.saldo_total(
            ContaContabil.CUPONS_VINCULADOS
        )
        assert custodiado == saldos.saldo_total(ContaContabil.GARANTIAS_A_DEVOLVER)
        assert custodiado == self._posicao().valor_das_garantias
        for garantia in self.conta.garantias:
            valor = self.conta.valor_de_mercado(garantia.id)
            assert valor >= ZERO
            if not garantia.cobre_divida:
                assert valor.nulo

    @invariant()
    def caixa_e_o_que_ficou(self) -> None:
        saldos = self.conta.saldos
        # O excedente sai no mesmo passo: o caixa guarda só pagamentos e o abatido.
        assert saldos.saldo(ContaContabil.EXCEDENTE_A_DEVOLVER) == ZERO
        assert saldos.saldo(ContaContabil.CAIXA_RECEBIMENTOS) == self.pago + self.abatido
        assert saldos.saldo(ContaContabil.LIQUIDACAO_A_PAGAR) == self.comprado

    @invariant()
    def chamada_de_margem_acompanha_o_deficit(self) -> None:
        deficit = self._posicao().deficit
        abertas = [c for c in self.conta.chamadas if c.estado is EstadoChamada.ABERTA]
        assert len(abertas) <= 1
        if abertas:
            assert deficit.positivo
        if deficit.positivo and self.conta.estado is EstadoConta.ATIVA:
            assert abertas

    @invariant()
    def fatura_e_bloqueio_sao_coerentes(self) -> None:
        assert len([f for f in self.conta.faturas if f.em_aberto]) <= 1
        if self.conta.fatura_em_aberto is None:
            assert self.conta.divida_faturada.nulo
        else:
            assert self.conta.divida_faturada.positivo
        assert (self.conta.estado is EstadoConta.BLOQUEADA) == (
            self.conta.motivo_bloqueio is not None
        )


MaquinaDaConta.TestCase.settings = settings(max_examples=150, stateful_step_count=40, deadline=None)
TestMaquinaDaConta = MaquinaDaConta.TestCase
