"""Adaptadores de custódia: quem coloca o gravame no investimento.

Fase 1: o investimento fica dentro do próprio grupo da plataforma, então o bloqueio é
uma operação interna e sai confirmado na hora (`CustodiaInterna`). `CustodiaAssincrona`
é o modo legado que deixa a garantia `pendente` até uma confirmação administrativa.

Fase 2: o investimento está em outro custodiante. Aqui não existe integração real:
`SimuladorDeCustodiantes` faz o papel de custodiantes externos fictícios, com perfis em
`custodiantes_simulados.toml`. Onde entrariam os trilhos reais está em
`docs/arquitetura.md`.
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
    Custodiante,
    CustodianteDesconhecido,
    InfoDoCustodiante,
    PedidoAoCustodiante,
    Relogio,
    RespostaDoCustodiante,
    StatusDaResposta,
)
from app.dominio.garantia import Garantia, TipoSolicitacao

ARQUIVO_DE_PERFIS = Path(__file__).resolve().parent.parent / "custodiantes_simulados.toml"
# O sorteio de cada pedido vira um número de 0 a 9.999 (pontos-base), comparado com as
# probabilidades do perfil. Tudo em inteiro: nada de float decidindo o destino.
_PONTOS_BASE = 10_000


class CustodiaInterna:
    """Fase 1: o bloqueio é interno e sai confirmado na hora. Não há custodiante externo."""

    def solicitar_bloqueio(self, garantia: Garantia) -> bool:
        return True

    def custodiantes(self) -> list[InfoDoCustodiante]:
        return []

    def conhece(self, custodiante_id: str) -> bool:
        return False

    def solicitar_gravame(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        raise CustodianteDesconhecido(pedido.custodiante_id)

    def solicitar_liberacao(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        raise CustodianteDesconhecido(pedido.custodiante_id)

    def solicitar_execucao(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        raise CustodianteDesconhecido(pedido.custodiante_id)

    def consultar(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        raise CustodianteDesconhecido(pedido.custodiante_id)


class CustodiaAssincrona(CustodiaInterna):
    """Modo legado: o pedido é aceito e a confirmação chega depois, por
    `POST /v1/admin/garantias/{id}/confirmacao-de-bloqueio`. Se não chegar, a rotina de
    custódia expira a garantia."""

    def solicitar_bloqueio(self, garantia: Garantia) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PerfilDeCustodiante:
    """Comportamento de um custodiante simulado. Números ilustrativos."""

    id: str
    nome: str
    descricao: str
    semente: int
    # Quanto tempo depois do pedido a resposta fica pronta.
    latencia: timedelta
    # Sem resposta até aqui, o pedido expira do nosso lado.
    prazo_de_resposta: timedelta
    probabilidade_de_recusa: Decimal
    probabilidade_sem_resposta: Decimal
    # Venda na execução: valor obtido = valor pedido x fator (para baixo).
    fator_de_preco_na_venda: Decimal
    motivos_de_recusa: tuple[str, ...]

    def __post_init__(self) -> None:
        problemas = []
        for nome in ("probabilidade_de_recusa", "probabilidade_sem_resposta"):
            valor = getattr(self, nome)
            if not 0 <= valor <= 1:
                problemas.append(f"{nome} precisa estar entre 0 e 1")
        if self.probabilidade_de_recusa + self.probabilidade_sem_resposta > 1:
            problemas.append("recusa + sem resposta não pode passar de 1")
        if self.latencia < timedelta(0):
            problemas.append("latência não pode ser negativa")
        if self.prazo_de_resposta <= timedelta(0):
            problemas.append("prazo de resposta precisa ser positivo")
        if not 0 < self.fator_de_preco_na_venda <= 2:
            problemas.append("fator de preço na venda precisa estar em (0, 2]")
        if not self.motivos_de_recusa:
            problemas.append("informe ao menos um motivo de recusa")
        if problemas:
            raise ValueError(f"Perfil de custodiante {self.id!r} inválido: " + "; ".join(problemas))

    def info(self) -> InfoDoCustodiante:
        return InfoDoCustodiante(
            id=self.id,
            nome=self.nome,
            descricao=self.descricao,
            simulado=True,
            prazo_de_resposta=self.prazo_de_resposta,
        )


def carregar_perfis(arquivo: Path = ARQUIVO_DE_PERFIS) -> list[PerfilDeCustodiante]:
    with arquivo.open("rb") as conteudo:
        dados = tomllib.load(conteudo)
    perfis = []
    for custodiante_id, campos in dados.items():
        try:
            perfis.append(
                PerfilDeCustodiante(
                    id=custodiante_id,
                    nome=str(campos["nome"]),
                    descricao=str(campos["descricao"]),
                    semente=int(campos["semente"]),
                    latencia=timedelta(seconds=int(campos["latencia_segundos"])),
                    prazo_de_resposta=timedelta(minutes=int(campos["prazo_de_resposta_minutos"])),
                    probabilidade_de_recusa=Decimal(str(campos["probabilidade_de_recusa"])),
                    probabilidade_sem_resposta=Decimal(str(campos["probabilidade_sem_resposta"])),
                    fator_de_preco_na_venda=Decimal(str(campos["fator_de_preco_na_venda"])),
                    motivos_de_recusa=tuple(str(m) for m in campos["motivos_de_recusa"]),
                )
            )
        except (KeyError, TypeError) as erro:
            raise ValueError(f"Perfil {custodiante_id!r} incompleto: {erro}") from erro
    return perfis


class _Destino(Enum):
    CONFIRMA = "confirma"
    RECUSA = "recusa"
    NAO_RESPONDE = "nao_responde"


class SimuladorDeCustodiantes:
    """SIMULADOR de custodiantes externos. Não fala com instituição nenhuma.

    O destino de cada pedido (confirma, recusa ou nunca responde) sai de um SHA-256 de
    `(semente do perfil, solicitacao_id)`: o mesmo pedido tem sempre o mesmo destino, em
    qualquer máquina. O tempo vem do relógio injetado: a resposta fica pronta quando
    `agora >= pedido.criado_em + latência`. Sem `sleep` e sem estado: pedir de novo
    (idempotência) ou consultar devolvem a mesma resposta.

    `solicitar_bloqueio` (fase 1) é repassado à custódia interna configurada.
    """

    def __init__(
        self,
        perfis: Iterable[PerfilDeCustodiante],
        relogio: Relogio,
        fase_1: Custodiante | None = None,
    ) -> None:
        self._perfis = {perfil.id: perfil for perfil in perfis}
        self._relogio = relogio
        self._fase_1 = fase_1 or CustodiaInterna()

    def solicitar_bloqueio(self, garantia: Garantia) -> bool:
        return self._fase_1.solicitar_bloqueio(garantia)

    def custodiantes(self) -> list[InfoDoCustodiante]:
        return [perfil.info() for perfil in self._perfis.values()]

    def conhece(self, custodiante_id: str) -> bool:
        return custodiante_id in self._perfis

    def perfil(self, custodiante_id: str) -> PerfilDeCustodiante:
        try:
            return self._perfis[custodiante_id]
        except KeyError:
            raise CustodianteDesconhecido(custodiante_id) from None

    def solicitar_gravame(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        return self.consultar(pedido)

    def solicitar_liberacao(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        return self.consultar(pedido)

    def solicitar_execucao(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        return self.consultar(pedido)

    def consultar(self, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
        perfil = self.perfil(pedido.custodiante_id)
        destino = self._destino(perfil, pedido.solicitacao_id)
        pronta = self._relogio.agora() >= pedido.criado_em + perfil.latencia
        if destino is _Destino.NAO_RESPONDE or not pronta:
            return RespostaDoCustodiante(pedido.solicitacao_id, StatusDaResposta.PENDENTE)
        if destino is _Destino.RECUSA:
            motivos = perfil.motivos_de_recusa
            motivo = motivos[self._sorteio(perfil, pedido.solicitacao_id, "motivo") % len(motivos)]
            return self.resposta(pedido, StatusDaResposta.RECUSADA, motivo=motivo)
        return self.resposta(pedido, StatusDaResposta.CONFIRMADA)

    def resposta(
        self, pedido: PedidoAoCustodiante, status: StatusDaResposta, motivo: str | None = None
    ) -> RespostaDoCustodiante:
        """A resposta que este custodiante daria, com o `evento_id` dele.

        Usada também pelo sandbox para forçar um desfecho: o id é o mesmo que o
        simulador produziria, então callback, consulta e sandbox se reconhecem.
        """
        perfil = self.perfil(pedido.custodiante_id)
        valor_obtido = None
        if status is StatusDaResposta.CONFIRMADA and pedido.tipo is TipoSolicitacao.EXECUCAO:
            # {BRL} x fator {1} -> {BRL}, para baixo.
            valor_obtido = pedido.valor.aplicar_razao(perfil.fator_de_preco_na_venda)
        return RespostaDoCustodiante(
            solicitacao_id=pedido.solicitacao_id,
            status=status,
            evento_id=evento_id_do_custodiante(perfil.id, pedido.solicitacao_id, status),
            valor_obtido=valor_obtido,
            motivo=motivo,
        )

    def _destino(self, perfil: PerfilDeCustodiante, solicitacao_id: str) -> _Destino:
        sorteio = self._sorteio(perfil, solicitacao_id, "destino") % _PONTOS_BASE
        sem_resposta = int(perfil.probabilidade_sem_resposta * _PONTOS_BASE)
        recusa = int(perfil.probabilidade_de_recusa * _PONTOS_BASE)
        if sorteio < sem_resposta:
            return _Destino.NAO_RESPONDE
        if sorteio < sem_resposta + recusa:
            return _Destino.RECUSA
        return _Destino.CONFIRMA

    @staticmethod
    def _sorteio(perfil: PerfilDeCustodiante, solicitacao_id: str, para_que: str) -> int:
        semente = f"{perfil.semente}:{solicitacao_id}:{para_que}".encode()
        return int.from_bytes(hashlib.sha256(semente).digest()[:8], "big")


def evento_id_do_custodiante(
    custodiante_id: str, solicitacao_id: str, status: StatusDaResposta
) -> str:
    """Id do evento de resposta, estável para o mesmo pedido e o mesmo desfecho."""
    resumo = hashlib.sha256(f"{custodiante_id}:{solicitacao_id}:{status}".encode()).hexdigest()
    return f"evc_{resumo[:26]}"


def _confere_o_protocolo(
    a: CustodiaInterna, b: CustodiaAssincrona, c: SimuladorDeCustodiantes
) -> Sequence[Custodiante]:
    # Só para o mypy: as três implementações satisfazem a porta.
    return [a, b, c]
