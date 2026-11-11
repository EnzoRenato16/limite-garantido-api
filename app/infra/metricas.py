"""Métricas no formato do Prometheus.

Cada aplicação tem o seu registro (nada de estado global), o que também deixa os testes
independentes entre si.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector


class Metricas:
    def __init__(self) -> None:
        self.registro = CollectorRegistry()
        self.requisicoes = Counter(
            "lg_http_requisicoes_total",
            "Requisições HTTP atendidas",
            ["metodo", "rota", "status"],
            registry=self.registro,
        )
        self.duracao = Histogram(
            "lg_http_duracao_segundos",
            "Tempo de resposta por rota",
            ["metodo", "rota"],
            registry=self.registro,
        )
        # Um contador por tipo de evento já responde as perguntas de negócio: quantas
        # compras negadas, quantas chamadas de margem, quantas execuções.
        self.eventos = Counter(
            "lg_eventos_total",
            "Eventos de negócio confirmados (depois do commit)",
            ["tipo"],
            registry=self.registro,
        )
        self.entregas = Counter(
            "lg_webhooks_entregas_total",
            "Tentativas de entrega de webhook, por resultado",
            ["resultado"],
            registry=self.registro,
        )
        self.requisicoes_limitadas = Counter(
            "lg_rate_limit_bloqueios_total",
            "Requisições recusadas pelo limite por parceiro",
            registry=self.registro,
        )

    def observar_fila_de_webhooks(self, contar: Callable[[], dict[str, int]]) -> None:
        """Publica o tamanho da fila de webhooks, lido do banco a cada coleta."""
        self.registro.register(_FilaDeWebhooks(contar))


class _FilaDeWebhooks(Collector):
    def __init__(self, contar: Callable[[], dict[str, int]]) -> None:
        self._contar = contar

    def collect(self) -> Iterable[GaugeMetricFamily]:
        familia = GaugeMetricFamily(
            "lg_webhooks_na_fila", "Entregas de webhook por estado", labels=["estado"]
        )
        try:
            contagem = self._contar()
        except Exception:  # uma métrica com problema não pode derrubar o /metrics
            contagem = {}
        for estado, quantidade in sorted(contagem.items()):
            familia.add_metric([estado], quantidade)
        yield familia
