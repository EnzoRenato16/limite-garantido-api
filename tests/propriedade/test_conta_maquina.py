"""Máquina de estados do Hypothesis sobre a conta.

O Hypothesis sorteia sequências de operações (bloquear, remarcar, comprar, pagar,
fechar fatura, passar os dias, liberar) e confere, depois de cada passo, as regras que
nunca podem quebrar. Quando acha um contraexemplo, reduz para a menor sequência.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from app.dominio.conta import EstadoChamada, EstadoConta
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import GarantiaCobrindoDivida
from app.dominio.eventos import TipoEvento
from app.dominio.garantia import EstadoGarantia
from app.dominio.ledger import ContaContabil, Lancamento, ledger_fecha
from app.dominio.politica import TipoAtivo
from tests.apoio import AGORA, HOJE, nova_conta, politica

ZERO = Dinheiro.zero()


class MaquinaDaConta(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.pol = politica(teto_cliente="80000.00")
        self.conta = nova_conta()
        self.hoje = HOJE
        self.lancamentos: list[Lancamento] = []
        self.comprado = ZERO
        self.pago = ZERO
        self.executado = ZERO

    def _guardar_pendencias(self) -> None:
        self.lancamentos.extend(self.conta.pendencias.lancamentos)
        self.conta.pendencias.limpar()

    # ------------------------------------------------------------- operações
    @rule(tipo=st.sampled_from(TipoAtivo), centavos=st.integers(1, 5_000_000))
    def bloquear_garantia(self, tipo: TipoAtivo, centavos: int) -> None:
        garantia = self.conta.registrar_garantia(
            tipo, Dinheiro(centavos), self.pol, AGORA, self.hoje
        )
        self.conta.confirmar_bloqueio(garantia.id, self.pol, AGORA, self.hoje)
        self._guardar_pendencias()

    @precondition(lambda self: self.conta.garantias_ativas)
    @rule(data=st.data(), fator=st.decimals(Decimal(0), Decimal(2), places=4))
    def remarcar(self, data: st.DataObject, fator: Decimal) -> None:
        garantia = data.draw(st.sampled_from(self.conta.garantias_ativas))
        novo_valor = self.conta.valor_de_mercado(garantia.id).aplicar_razao(fator)
        self.conta.remarcar(garantia.id, novo_valor, self.pol, AGORA, self.hoje)
        self._guardar_pendencias()

    @rule(centavos=st.integers(1, 3_000_000))
    def comprar(self, centavos: int) -> None:
        valor = Dinheiro(centavos)
        antes = self.conta.posicao(self.pol)
        compra = self.conta.autorizar_compra(valor, "loja", self.pol, AGORA, self.hoje)
        depois = self.conta.posicao(self.pol)
        if compra.aprovada:
            # Só aprova o que cabia no limite disponível.
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
        self.conta.pagar(Dinheiro(centavos), self.pol, AGORA, self.hoje)
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
        self.conta.virar_o_dia(self.pol, AGORA, self.hoje)
        executado_agora = Dinheiro.somar(
            Dinheiro.de_decimal(str(evento.dados["valor_executado"]))
            for evento in self.conta.pendencias.eventos
            if evento.tipo is TipoEvento.GARANTIA_EXECUTADA
        )
        # A execução nunca vende mais do que a fatura vencida.
        assert executado_agora <= faturada_antes
        self.executado += executado_agora
        self._guardar_pendencias()

    @precondition(lambda self: self.conta.garantias_ativas)
    @rule(data=st.data())
    def tentar_liberar(self, data: st.DataObject) -> None:
        garantia = data.draw(st.sampled_from(self.conta.garantias_ativas))
        try:
            self.conta.liberar(garantia.id, self.pol, AGORA, self.hoje)
        except GarantiaCobrindoDivida:
            assert garantia.estado is not EstadoGarantia.LIBERADA
        else:
            # Liberou: o que ficou ainda cobre a dívida.
            assert self.conta.posicao(self.pol).deficit.nulo
        self._guardar_pendencias()

    # ------------------------------------------------------------ invariantes
    @invariant()
    def limite_nunca_fica_negativo(self) -> None:
        posicao = self.conta.posicao(self.pol)
        assert posicao.limite_disponivel >= ZERO
        assert posicao.limite_total >= ZERO
        assert posicao.deficit >= ZERO
        assert posicao.divida_total >= ZERO
        assert posicao.limite_disponivel <= posicao.limite_total
        assert posicao.limite_total <= posicao.limite_garantido <= posicao.valor_das_garantias
        # Não existe limite sobrando e déficit ao mesmo tempo.
        assert posicao.limite_disponivel.nulo or posicao.deficit.nulo

    @invariant()
    def ledger_sempre_fecha_em_zero(self) -> None:
        assert ledger_fecha(self.lancamentos)
        assert self.conta.saldos.debitos_menos_creditos() == ZERO

    @invariant()
    def divida_e_compras_menos_pagamentos_menos_execucoes(self) -> None:
        assert self.conta.divida_total == self.comprado - self.pago - self.executado
        # A soma das execuções nunca passa da dívida que existiu.
        assert self.executado <= self.comprado - self.pago

    @invariant()
    def custodia_bate_com_as_garantias(self) -> None:
        saldos = self.conta.saldos
        custodiado = saldos.saldo_total(ContaContabil.GARANTIAS_CUSTODIADAS)
        assert custodiado == saldos.saldo_total(ContaContabil.GARANTIAS_A_DEVOLVER)
        assert custodiado == self.conta.posicao(self.pol).valor_das_garantias
        for garantia in self.conta.garantias:
            valor = self.conta.valor_de_mercado(garantia.id)
            assert valor >= ZERO
            if not garantia.ativa:
                assert valor.nulo

    @invariant()
    def caixa_e_o_que_entrou(self) -> None:
        saldos = self.conta.saldos
        assert saldos.saldo(ContaContabil.CAIXA_RECEBIMENTOS) == self.pago + self.executado
        assert saldos.saldo(ContaContabil.LIQUIDACAO_A_PAGAR) == self.comprado

    @invariant()
    def chamada_de_margem_acompanha_o_deficit(self) -> None:
        deficit = self.conta.posicao(self.pol).deficit
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
