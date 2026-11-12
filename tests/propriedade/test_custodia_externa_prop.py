"""Hypothesis contra a garantia externa: o custodiante responde o que quiser, quando quiser.

As regras sorteiam respostas válidas e inválidas (confirmação de pedido velho, recusa
depois de confirmação, execução de garantia que nem foi pedida), compras, faturas e a
passagem do tempo. Depois de cada passo:

- nenhuma garantia gera limite sem ter tido o gravame confirmado;
- nenhum lançamento cita uma garantia que não foi confirmada;
- o ledger fecha em zero e a custódia bate com a contrapartida;
- a dívida nunca fica negativa e nunca se manda vender mais do que a fatura.
"""

from __future__ import annotations

from datetime import timedelta

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ErroDominio
from app.dominio.garantia import EstadoGarantia, Garantia, OrigemGarantia
from app.dominio.ledger import ContaContabil, Lancamento
from app.dominio.politica import TipoAtivo
from tests.apoio import AGORA, CUSTODIANTE, HOJE, nova_conta, politica

ZERO = Dinheiro.zero()
RESPOSTAS = ("aceitar", "confirmar_gravame", "recusar", "confirmar_liberacao", "confirmar_execucao")


class MaquinaDoCustodiante(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.pol = politica()
        self.conta = nova_conta()
        self.horas = 0
        self.lancamentos: list[Lancamento] = []
        # Garantias cujo gravame foi confirmado, e os ids de pedido já vistos (para
        # o custodiante poder responder a um pedido velho).
        self.confirmadas: set[str] = set()
        self.pedidos_vistos: list[str] = ["sol_inventado"]

    @property
    def agora(self):  # type: ignore[no-untyped-def]
        return AGORA + timedelta(hours=self.horas)

    @property
    def hoje(self):  # type: ignore[no-untyped-def]
        return HOJE + timedelta(days=self.horas // 24)

    @property
    def externas(self) -> list[Garantia]:
        return [g for g in self.conta.garantias if g.origem is OrigemGarantia.EXTERNA]

    def _guardar(self) -> None:
        for garantia in self.conta.garantias:
            if garantia.solicitacao and garantia.solicitacao.id not in self.pedidos_vistos:
                self.pedidos_vistos.append(garantia.solicitacao.id)
        self.lancamentos.extend(self.conta.pendencias.lancamentos)
        self.conta.pendencias.limpar()

    # ------------------------------------------------------------- operações
    @initialize(tipo=st.sampled_from(TipoAtivo), centavos=st.integers(100_000, 5_000_000))
    def comecar_com_uma_garantia_confirmada(self, tipo: TipoAtivo, centavos: int) -> None:
        garantia = self.conta.registrar_garantia(
            tipo,
            Dinheiro(centavos),
            self.pol,
            self.agora,
            self.hoje,
            custodiante_id=CUSTODIANTE,
            prazo_ate=self.agora + timedelta(hours=24),
        )
        assert garantia.solicitacao is not None
        self.pedidos_vistos.append(garantia.solicitacao.id)
        self.conta.confirmar_gravame(
            garantia.id, garantia.solicitacao.id, self.pol, self.agora, self.hoje
        )
        self.confirmadas.add(garantia.id)
        self._guardar()

    @rule(tipo=st.sampled_from(TipoAtivo), centavos=st.integers(1, 2_000_000))
    def pedir_gravame(self, tipo: TipoAtivo, centavos: int) -> None:
        self.conta.registrar_garantia(
            tipo,
            Dinheiro(centavos),
            self.pol,
            self.agora,
            self.hoje,
            custodiante_id=CUSTODIANTE,
            prazo_ate=self.agora + timedelta(hours=24),
        )
        self._guardar()

    @precondition(lambda self: self.externas)
    @rule(
        data=st.data(),
        resposta=st.sampled_from(RESPOSTAS),
        centavos=st.integers(-100, 3_000_000),
    )
    def custodiante_responde(self, data: st.DataObject, resposta: str, centavos: int) -> None:
        aguardando = [g for g in self.externas if g.solicitacao is not None]
        # Na maioria das vezes o custodiante responde a um pedido em aberto; às vezes,
        # a qualquer garantia, com qualquer id de pedido já visto (inclusive velho).
        if aguardando and data.draw(st.integers(0, 3)) > 0:
            garantia = data.draw(st.sampled_from(aguardando))
            assert garantia.solicitacao is not None
            solicitacao_id = garantia.solicitacao.id
        else:
            garantia = data.draw(st.sampled_from(self.externas))
            solicitacao_id = data.draw(st.sampled_from(self.pedidos_vistos))
        estado_antes = garantia.estado
        try:
            if resposta == "aceitar":
                self.conta.aceitar_solicitacao(garantia.id, solicitacao_id, self.agora)
            elif resposta == "confirmar_gravame":
                self.conta.confirmar_gravame(
                    garantia.id, solicitacao_id, self.pol, self.agora, self.hoje
                )
            elif resposta == "recusar":
                self.conta.recusar_solicitacao(
                    garantia.id, solicitacao_id, "motivo", self.pol, self.agora, self.hoje
                )
            elif resposta == "confirmar_liberacao":
                self.conta.confirmar_liberacao(
                    garantia.id, solicitacao_id, self.pol, self.agora, self.hoje
                )
            else:
                self.conta.confirmar_execucao(
                    garantia.id, solicitacao_id, Dinheiro(centavos), self.pol, self.agora, self.hoje
                )
        except ErroDominio:
            # Resposta que não corresponde ao pedido: nada pode ter mudado.
            assert garantia.estado is estado_antes
            assert not self.conta.pendencias.lancamentos
        else:
            if resposta == "confirmar_gravame":
                self.confirmadas.add(garantia.id)
        self._guardar()

    @precondition(lambda self: any(g.aguardando_custodiante for g in self.conta.garantias))
    @rule(data=st.data())
    def prazo_passa_sem_resposta(self, data: st.DataObject) -> None:
        garantia = data.draw(
            st.sampled_from([g for g in self.conta.garantias if g.aguardando_custodiante])
        )
        assert garantia.solicitacao is not None
        self.horas = max(
            self.horas, int((garantia.solicitacao.prazo_ate - AGORA).total_seconds() // 3600) + 1
        )
        self.conta.expirar_solicitacao(garantia.id, self.pol, self.agora, self.hoje)
        self._guardar()

    @precondition(lambda self: any(g.ativa for g in self.externas))
    @rule(data=st.data())
    def pedir_liberacao(self, data: st.DataObject) -> None:
        garantia = data.draw(st.sampled_from([g for g in self.externas if g.ativa]))
        try:
            self.conta.solicitar_liberacao(
                garantia.id,
                self.pol,
                self.agora,
                self.hoje,
                prazo_ate=self.agora + timedelta(hours=24),
            )
        except ErroDominio:
            assert garantia.ativa
        self._guardar()

    @rule(centavos=st.integers(1, 2_000_000))
    def comprar(self, centavos: int) -> None:
        self.conta.autorizar_compra(Dinheiro(centavos), "loja", self.pol, self.agora, self.hoje)
        self._guardar()

    @precondition(lambda self: self.conta.divida_total.positivo)
    @rule(data=st.data())
    def pagar(self, data: st.DataObject) -> None:
        centavos = data.draw(st.integers(1, self.conta.divida_total.centavos))
        self.conta.pagar(Dinheiro(centavos), self.pol, self.agora, self.hoje)
        self._guardar()

    @precondition(
        lambda self: self.conta.fatura_em_aberto is None and self.conta.divida_a_faturar.positivo
    )
    @rule()
    def fechar_fatura(self) -> None:
        self.conta.fechar_fatura(self.hoje, self.agora, self.hoje)
        self._guardar()

    @rule(dias=st.integers(1, 10))
    def passar_dias(self, dias: int) -> None:
        self.horas += 24 * dias
        self.conta.virar_o_dia(self.pol, self.agora, self.hoje)
        self._guardar()

    # ------------------------------------------------------------ invariantes
    @invariant()
    def limite_so_depois_da_confirmacao(self) -> None:
        for garantia in self.externas:
            if garantia.gera_limite or garantia.cobre_divida:
                assert garantia.id in self.confirmadas
        externas_nao_confirmadas = [g for g in self.externas if g.id not in self.confirmadas]
        for garantia in externas_nao_confirmadas:
            assert self.conta.valor_de_mercado(garantia.id) == ZERO
            assert garantia.estado in {
                EstadoGarantia.SOLICITADA,
                EstadoGarantia.GRAVAME_PENDENTE,
                EstadoGarantia.GRAVAME_RECUSADO,
                EstadoGarantia.GRAVAME_EXPIRADO,
            }

    @invariant()
    def nenhum_lancamento_de_garantia_nao_confirmada(self) -> None:
        for lancamento in self.lancamentos:
            for partida in lancamento.partidas:
                if partida.garantia_id is not None:
                    assert partida.garantia_id in self.confirmadas

    @invariant()
    def ledger_fecha(self) -> None:
        saldos = self.conta.saldos
        assert saldos.debitos_menos_creditos() == ZERO
        custodiado = saldos.saldo_total(ContaContabil.GARANTIAS_CUSTODIADAS)
        assert custodiado == saldos.saldo_total(ContaContabil.GARANTIAS_A_DEVOLVER)
        assert not custodiado.negativo

    @invariant()
    def divida_nunca_negativa_e_execucao_nunca_maior_que_a_garantia(self) -> None:
        assert not self.conta.divida_a_faturar.negativo
        assert not self.conta.divida_faturada.negativo
        for garantia in self.externas:
            if garantia.valor_em_execucao is not None:
                assert garantia.valor_em_execucao.positivo
                assert garantia.valor_em_execucao <= self.conta.valor_de_mercado(garantia.id)


MaquinaDoCustodiante.TestCase.settings = settings(
    max_examples=300, stateful_step_count=50, deadline=None
)
TestMaquinaDoCustodiante = MaquinaDoCustodiante.TestCase
