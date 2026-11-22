"""Contrapartes simuladas dos trilhos de entrada.

Não existe integração real: `SimuladorDeContrapartes` faz o papel da corretora de origem,
do banco de origem, do agente de custódia do Tesouro e da entidade operadora, com perfis em
`contrapartes_simuladas.toml`. Onde entrariam os trilhos reais está em `docs/arquitetura.md`.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

from app.aplicacao.portas import (
    Contraparte,
    ContraparteDesconhecida,
    InfoDaContraparte,
    PedidoDeEntrada,
    Relogio,
    RespostaDaContraparte,
    StatusDaResposta,
)
from app.dominio.regulacao import Trilho

ARQUIVO_DE_PERFIS = Path(__file__).resolve().parent.parent / "contrapartes_simuladas.toml"
# O sorteio de cada pedido vira um número de 0 a 9.999 (pontos-base), comparado com as
# probabilidades do perfil. Tudo em inteiro: nada de float decidindo o destino.
_PONTOS_BASE = 10_000


@dataclass(frozen=True, slots=True)
class PerfilDeContraparte:
    """Comportamento de uma contraparte simulada. Números ilustrativos."""

    id: str
    nome: str
    descricao: str
    trilhos: tuple[Trilho, ...]
    semente: int
    # Quanto tempo depois do pedido a resposta fica pronta.
    latencia: timedelta
    probabilidade_de_recusa: Decimal
    probabilidade_sem_resposta: Decimal
    motivos_de_recusa: tuple[str, ...]
    simulado: bool = True
    # Só a entidade operadora: tributos informados como razão {1} do valor disponível.
    percentual_de_tributos_informados: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        problemas = []
        if not self.simulado:
            problemas.append("toda contraparte deste projeto é simulada")
        if not self.nome.endswith("(simulador)"):
            problemas.append('o nome precisa terminar em "(simulador)"')
        if not self.trilhos:
            problemas.append("informe ao menos um trilho")
        if Trilho.CASA in self.trilhos:
            problemas.append("a custódia na casa não tem contraparte")
        for nome in (
            "probabilidade_de_recusa",
            "probabilidade_sem_resposta",
            "percentual_de_tributos_informados",
        ):
            valor = getattr(self, nome)
            if not 0 <= valor <= 1:
                problemas.append(f"{nome} precisa estar entre 0 e 1")
        if self.probabilidade_de_recusa + self.probabilidade_sem_resposta > 1:
            problemas.append("recusa + sem resposta não pode passar de 1")
        if Trilho.RES_CONJUNTA_12 in self.trilhos and self.probabilidade_de_recusa != 0:
            problemas.append("a entidade operadora não recusa (Res. Conjunta 12, art. 18)")
        if self.latencia < timedelta(0):
            problemas.append("latência não pode ser negativa")
        if not self.motivos_de_recusa:
            problemas.append("informe ao menos um motivo de recusa")
        if problemas:
            raise ValueError(f"Perfil de contraparte {self.id!r} inválido: " + "; ".join(problemas))

    def info(self) -> InfoDaContraparte:
        return InfoDaContraparte(
            id=self.id,
            nome=self.nome,
            descricao=self.descricao,
            simulado=self.simulado,
            trilhos=self.trilhos,
        )


def carregar_perfis(arquivo: Path = ARQUIVO_DE_PERFIS) -> list[PerfilDeContraparte]:
    with arquivo.open("rb") as conteudo:
        dados = tomllib.load(conteudo)
    perfis = []
    for contraparte_id, campos in dados.items():
        try:
            perfis.append(
                PerfilDeContraparte(
                    id=contraparte_id,
                    nome=str(campos["nome"]),
                    descricao=str(campos["descricao"]),
                    simulado=bool(campos["simulado"]),
                    trilhos=tuple(Trilho(str(t)) for t in campos["trilhos"]),
                    semente=int(campos["semente"]),
                    latencia=timedelta(seconds=int(campos["latencia_segundos"])),
                    probabilidade_de_recusa=Decimal(str(campos["probabilidade_de_recusa"])),
                    probabilidade_sem_resposta=Decimal(str(campos["probabilidade_sem_resposta"])),
                    motivos_de_recusa=tuple(str(m) for m in campos["motivos_de_recusa"]),
                    percentual_de_tributos_informados=Decimal(
                        str(campos.get("percentual_de_tributos_informados", "0"))
                    ),
                )
            )
        except (KeyError, TypeError) as erro:
            raise ValueError(f"Perfil {contraparte_id!r} incompleto: {erro}") from erro
    return perfis


class _Destino(Enum):
    CONCLUI = "conclui"
    RECUSA = "recusa"
    NAO_RESPONDE = "nao_responde"


class SemContrapartes:
    """Contrapartes desligadas (`CONTRAPARTES=desligada`): nenhum trilho de fora."""

    def contrapartes(self) -> list[InfoDaContraparte]:
        return []

    def conhece(self, contraparte_id: str) -> bool:
        return False

    def solicitar_entrada(self, pedido: PedidoDeEntrada) -> RespostaDaContraparte:
        raise ContraparteDesconhecida(pedido.contraparte_id)

    def consultar(self, pedido: PedidoDeEntrada) -> RespostaDaContraparte:
        raise ContraparteDesconhecida(pedido.contraparte_id)


class SimuladorDeContrapartes:
    """SIMULADOR das contrapartes dos trilhos. Não fala com instituição nenhuma.

    O destino de cada pedido (conclui, recusa ou nunca responde) sai de um SHA-256 de
    `(semente do perfil, solicitacao_id)`: o mesmo pedido tem sempre o mesmo destino, em
    qualquer máquina. O tempo vem do relógio injetado: a resposta fica pronta quando
    `agora >= pedido.criado_em + latência`. Sem `sleep` e sem estado: pedir de novo
    (idempotência) ou consultar devolvem a mesma resposta.
    """

    def __init__(self, perfis: Iterable[PerfilDeContraparte], relogio: Relogio) -> None:
        self._perfis = {perfil.id: perfil for perfil in perfis}
        self._relogio = relogio

    def contrapartes(self) -> list[InfoDaContraparte]:
        return [perfil.info() for perfil in self._perfis.values()]

    def conhece(self, contraparte_id: str) -> bool:
        return contraparte_id in self._perfis

    def perfil(self, contraparte_id: str) -> PerfilDeContraparte:
        try:
            return self._perfis[contraparte_id]
        except KeyError:
            raise ContraparteDesconhecida(contraparte_id) from None

    def solicitar_entrada(self, pedido: PedidoDeEntrada) -> RespostaDaContraparte:
        return self.consultar(pedido)

    def consultar(self, pedido: PedidoDeEntrada) -> RespostaDaContraparte:
        perfil = self.perfil(pedido.contraparte_id)
        destino = self._destino(perfil, pedido.solicitacao_id)
        pronta = self._relogio.agora() >= pedido.criado_em + perfil.latencia
        if destino is _Destino.NAO_RESPONDE or not pronta:
            return RespostaDaContraparte(pedido.solicitacao_id, StatusDaResposta.PENDENTE)
        if destino is _Destino.RECUSA:
            motivos = perfil.motivos_de_recusa
            motivo = motivos[self._sorteio(perfil, pedido.solicitacao_id, "motivo") % len(motivos)]
            return self.resposta(pedido, self.status_de_recusa(pedido.trilho), motivo=motivo)
        return self.resposta(pedido, StatusDaResposta.CONCLUIDA)

    @staticmethod
    def status_de_recusa(trilho: Trilho) -> StatusDaResposta:
        """Na portabilidade só há impedimento (CVM 210, art. 19); nos demais, recusa."""
        if trilho is Trilho.PORTABILIDADE_CVM_210:
            return StatusDaResposta.IMPEDIMENTO
        return StatusDaResposta.RECUSADA

    def resposta(
        self, pedido: PedidoDeEntrada, status: StatusDaResposta, motivo: str | None = None
    ) -> RespostaDaContraparte:
        """A resposta que esta contraparte daria, com o `evento_id` dela.

        Usada também pelo sandbox para forçar um desfecho: o id é o mesmo que o simulador
        produziria, então callback, consulta e sandbox se reconhecem.
        """
        perfil = self.perfil(pedido.contraparte_id)
        disponivel = tributos = None
        if status is StatusDaResposta.CONCLUIDA and pedido.trilho is Trilho.RES_CONJUNTA_12:
            disponivel = pedido.valor
            # {BRL} x {1} -> {BRL}, para cima: o tributo informado nunca fica abaixo.
            tributos = disponivel.aplicar_razao(
                perfil.percentual_de_tributos_informados, para_cima=True
            )
        return RespostaDaContraparte(
            solicitacao_id=pedido.solicitacao_id,
            status=status,
            evento_id=evento_id_da_contraparte(perfil.id, pedido.solicitacao_id, status),
            motivo=motivo,
            valor_disponivel=disponivel,
            tributos_informados=tributos,
        )

    def _destino(self, perfil: PerfilDeContraparte, solicitacao_id: str) -> _Destino:
        sorteio = self._sorteio(perfil, solicitacao_id, "destino") % _PONTOS_BASE
        sem_resposta = int(perfil.probabilidade_sem_resposta * _PONTOS_BASE)
        recusa = int(perfil.probabilidade_de_recusa * _PONTOS_BASE)
        if sorteio < sem_resposta:
            return _Destino.NAO_RESPONDE
        if sorteio < sem_resposta + recusa:
            return _Destino.RECUSA
        return _Destino.CONCLUI

    @staticmethod
    def _sorteio(perfil: PerfilDeContraparte, solicitacao_id: str, para_que: str) -> int:
        semente = f"{perfil.semente}:{solicitacao_id}:{para_que}".encode()
        return int.from_bytes(hashlib.sha256(semente).digest()[:8], "big")


def evento_id_da_contraparte(
    contraparte_id: str, solicitacao_id: str, status: StatusDaResposta
) -> str:
    """Id do evento de resposta, estável para o mesmo pedido e o mesmo desfecho."""
    resumo = hashlib.sha256(f"{contraparte_id}:{solicitacao_id}:{status}".encode()).hexdigest()
    return f"evc_{resumo[:26]}"


def montar_contraparte(
    modo: str, relogio: Relogio, arquivo_de_perfis: Path | None = None
) -> Contraparte:
    """A contraparte que a aplicação e o worker usam, a partir da configuração."""
    if modo != "simulador":
        return SemContrapartes()
    return SimuladorDeContrapartes(carregar_perfis(arquivo_de_perfis or ARQUIVO_DE_PERFIS), relogio)


def _confere_o_protocolo(a: SemContrapartes, b: SimuladorDeContrapartes) -> Sequence[Contraparte]:
    # Só para o mypy: as duas implementações satisfazem a porta.
    return [a, b]
