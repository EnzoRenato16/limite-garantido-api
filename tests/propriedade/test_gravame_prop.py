"""Hypothesis sobre o gravame: qualquer sequência de comandos respeita a tabela.

Os comandos são sorteados sem olhar o estado; os inválidos têm de levantar
`TransicaoInvalida` e não mudar nada.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from app.dominio.erros import TransicaoInvalida
from app.dominio.gravame import ESTADOS_FINAIS, TRANSICOES, EstadoGravame, Gravame
from app.dominio.regulacao import Infraestrutura, Instrumento, Trilho

AGORA = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
COMANDOS = (
    "registrar",
    "recusar",
    "cancelar",
    "liberar_para_excussao",
    "marcar_executado",
    "pedir_baixa",
    "confirmar_baixa",
)


class MaquinaDoGravame(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.gravame: Gravame | None = None
        self.minutos = 0

    @rule(trilho=st.sampled_from(Trilho))
    def novo(self, trilho: Trilho) -> None:
        if self.gravame is None:
            self.gravame = Gravame.solicitar(
                infraestrutura=Infraestrutura.B3_BALCAO,
                instrumento=Instrumento.CESSAO_FIDUCIARIA,
                trilho=trilho,
                agora=AGORA,
            )

    @rule(comando=st.sampled_from(COMANDOS), dias=st.integers(0, 5))
    def comandar(self, comando: str, dias: int) -> None:
        if self.gravame is None:
            return
        self.minutos += 1
        agora = AGORA + timedelta(minutes=self.minutos)
        antes = copy.copy(self.gravame)
        argumentos: dict[str, tuple[object, ...]] = {
            "registrar": (agora, f"SIM-{self.minutos}"),
            "recusar": (agora, "m"),
            "cancelar": (agora, "m"),
            "pedir_baixa": (agora, date(2026, 3, 2) + timedelta(days=dias)),
        }
        try:
            getattr(self.gravame, comando)(*argumentos.get(comando, (agora,)))
        except TransicaoInvalida:
            # Comando recusado não muda nada.
            assert self.gravame == antes
            return
        depois = self.gravame.estado
        assert depois is antes.estado or depois in TRANSICOES[antes.estado]
        # Um gravame encerrado não muda mais.
        assert not antes.encerrado

    @invariant()
    def coerente(self) -> None:
        gravame = self.gravame
        if gravame is None:
            return
        assert gravame.estado in TRANSICOES
        if gravame.vigente:
            assert gravame.estado is EstadoGravame.REGISTRADO
            assert gravame.protocolo
        assert gravame.encerrado == (gravame.estado in ESTADOS_FINAIS)
        assert gravame.encerrado == (gravame.encerrado_em is not None)
        if gravame.estado is EstadoGravame.RECUSADO:
            assert gravame.trilho is Trilho.TD_GARANTIA
        if gravame.estado is EstadoGravame.BAIXADO:
            assert gravame.baixa_pedida_em is not None
        assert (gravame.baixa_pedida_em is None) == (gravame.baixa_prazo is None)


MaquinaDoGravame.TestCase.settings = settings(
    max_examples=200, stateful_step_count=30, deadline=None
)
TestMaquinaDoGravame = MaquinaDoGravame.TestCase
