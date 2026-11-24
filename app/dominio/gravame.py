"""Gravame: o registro da garantia na infraestrutura, com máquina de estados própria.

O gravame é o que vale perante terceiros: só se constitui na registradora ou no
depositário central (Lei 12.810/2013, art. 26). A registradora ou o depositário central
registra; o custodiante comanda. O projeto SIMULA esse registro; não integra com a B3, o
Selic nem a plataforma da Res. Conjunta 12.

::

    solicitado ──► registrado ──► liberado_para_excussao ──► executado ──► baixado
        │              └──────────────────────────────────────────────────► baixado
        ├──► recusado   (só no TD Garantia)
        └──► cancelado

A baixa é pedida antes de acontecer (`pedir_baixa`, com prazo em data) e confirmada
depois (`confirmar_baixa`). Um gravame `registrado` com baixa pedida não é mais
`vigente`: não gera limite nem valor de empréstimo.

Este módulo não conhece dias úteis: o prazo da baixa chega pronto, como `date`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import NoReturn

from app.dominio.erros import TransicaoInvalida, ValorInvalido
from app.dominio.ids import novo_id
from app.dominio.regulacao import Infraestrutura, Instrumento, Trilho


class EstadoGravame(StrEnum):
    SOLICITADO = "solicitado"
    REGISTRADO = "registrado"
    LIBERADO_PARA_EXCUSSAO = "liberado_para_excussao"
    EXECUTADO = "executado"
    BAIXADO = "baixado"
    RECUSADO = "recusado"
    CANCELADO = "cancelado"


_E = EstadoGravame

TRANSICOES: Mapping[EstadoGravame, frozenset[EstadoGravame]] = {
    _E.SOLICITADO: frozenset({_E.REGISTRADO, _E.RECUSADO, _E.CANCELADO}),
    _E.REGISTRADO: frozenset({_E.LIBERADO_PARA_EXCUSSAO, _E.BAIXADO}),
    _E.LIBERADO_PARA_EXCUSSAO: frozenset({_E.EXECUTADO}),
    _E.EXECUTADO: frozenset({_E.BAIXADO}),
    _E.BAIXADO: frozenset(),
    _E.RECUSADO: frozenset(),
    _E.CANCELADO: frozenset(),
}

# Estados de onde não se sai mais.
ESTADOS_FINAIS = frozenset(e for e, saidas in TRANSICOES.items() if not saidas)


@dataclass(slots=True)
class Gravame:
    id: str
    infraestrutura: Infraestrutura
    instrumento: Instrumento
    trilho: Trilho
    estado: EstadoGravame
    solicitado_em: datetime
    registrado_em: datetime | None = None
    # Protocolo devolvido pela infraestrutura (simulada) no registro.
    protocolo: str | None = None
    baixa_pedida_em: datetime | None = None
    # Último dia para a baixa acontecer: calculado fora (dias úteis do catálogo).
    baixa_prazo: date | None = None
    encerrado_em: datetime | None = None
    motivo: str | None = None

    @classmethod
    def solicitar(
        cls,
        *,
        infraestrutura: Infraestrutura,
        instrumento: Instrumento,
        trilho: Trilho,
        agora: datetime,
    ) -> Gravame:
        return cls(
            id=novo_id("grv"),
            infraestrutura=infraestrutura,
            instrumento=instrumento,
            trilho=trilho,
            estado=EstadoGravame.SOLICITADO,
            solicitado_em=agora,
        )

    # ------------------------------------------------------------- consultas
    @property
    def vigente(self) -> bool:
        """Registrado e sem baixa pedida: só assim a garantia gera limite ou empréstimo."""
        return self.estado is EstadoGravame.REGISTRADO and self.baixa_pedida_em is None

    @property
    def encerrado(self) -> bool:
        return self.estado in ESTADOS_FINAIS

    @property
    def baixa_pendente(self) -> bool:
        return self.baixa_pedida_em is not None and not self.encerrado

    # ------------------------------------------------------------- comandos
    def registrar(self, agora: datetime, protocolo: str) -> None:
        if not protocolo or not protocolo.strip():
            raise ValorInvalido("O registro do gravame exige protocolo", gravame_id=self.id)
        self._ir_para(EstadoGravame.REGISTRADO)
        self.registrado_em = agora
        self.protocolo = protocolo

    def recusar(self, agora: datetime, motivo: str) -> None:
        """Só o TD Garantia recusa: o agente do cliente não opera o serviço ou não confirma."""
        if self.trilho is not Trilho.TD_GARANTIA:
            raise TransicaoInvalida(
                f"Só o TD Garantia recusa gravame; o trilho {self.trilho} cancela",
                gravame_id=self.id,
                trilho=str(self.trilho),
                estado_atual=str(self.estado),
            )
        self._ir_para(EstadoGravame.RECUSADO)
        self._encerrar(agora, motivo)

    def cancelar(self, agora: datetime, motivo: str) -> None:
        self._ir_para(EstadoGravame.CANCELADO)
        self._encerrar(agora, motivo)

    def liberar_para_excussao(self, agora: datetime) -> None:
        """Comandada pelo participante indicado no registro (B3) ou pelo agente do garantido."""
        if not self.vigente:
            self._recusar_transicao(EstadoGravame.LIBERADO_PARA_EXCUSSAO)
        self._ir_para(EstadoGravame.LIBERADO_PARA_EXCUSSAO)

    def marcar_executado(self, agora: datetime) -> None:
        self._ir_para(EstadoGravame.EXECUTADO)

    def pedir_baixa(self, agora: datetime, prazo: date) -> None:
        """Pede a baixa até `prazo`. Vale para gravame registrado ou já executado."""
        if self.baixa_pedida_em is not None or EstadoGravame.BAIXADO not in TRANSICOES[self.estado]:
            self._recusar_transicao(EstadoGravame.BAIXADO)
        self.baixa_pedida_em = agora
        self.baixa_prazo = prazo

    def confirmar_baixa(self, agora: datetime) -> None:
        if self.baixa_pedida_em is None:
            raise TransicaoInvalida(
                "A baixa só é confirmada depois de pedida",
                gravame_id=self.id,
                estado_atual=str(self.estado),
            )
        self._ir_para(EstadoGravame.BAIXADO)
        self._encerrar(agora, self.motivo)

    # -------------------------------------------------------------- internos
    def _ir_para(self, novo: EstadoGravame) -> None:
        if novo not in TRANSICOES[self.estado]:
            self._recusar_transicao(novo)
        self.estado = novo

    def _recusar_transicao(self, novo: EstadoGravame) -> NoReturn:
        raise TransicaoInvalida(
            f"Gravame {self.id} está {self.estado} e não pode ir para {novo}",
            gravame_id=self.id,
            estado_atual=str(self.estado),
            estado_pedido=str(novo),
            baixa_pedida=self.baixa_pedida_em is not None,
        )

    def _encerrar(self, agora: datetime, motivo: str | None) -> None:
        self.encerrado_em = agora
        self.motivo = motivo
