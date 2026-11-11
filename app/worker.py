"""Worker de webhooks: lê a outbox e entrega os eventos.

    python -m app.worker

Pode rodar em várias instâncias ao mesmo tempo: cada lote é reservado com
`FOR UPDATE SKIP LOCKED`, então dois workers nunca enviam a mesma entrega.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from types import FrameType

from app.config import obter_configuracao
from app.infra.banco.repositorios import RepositorioDeIdempotencia
from app.infra.banco.sessao import criar_engine, criar_fabrica_de_sessoes
from app.infra.logs import configurar_logs
from app.infra.relogio import RelogioDoSistema
from app.infra.webhooks.entregador import Entregador

log = logging.getLogger("app.worker")

SEGUNDOS_ENTRE_LIMPEZAS = 600
PAUSA_DEPOIS_DE_ERRO_EM_SEGUNDOS = 5.0


def main() -> None:
    config = obter_configuracao()
    configurar_logs(config.log_nivel)
    engine = criar_engine(
        config.database_url, config.banco_pool_tamanho, config.banco_pool_excedente
    )
    fabrica_de_sessoes = criar_fabrica_de_sessoes(engine)
    relogio = RelogioDoSistema(config.fuso_de_negocio)
    entregador = Entregador(fabrica_de_sessoes, config, relogio)

    parar = threading.Event()

    def pedir_parada(sinal: int, _: FrameType | None) -> None:
        log.info("Sinal recebido, terminando o lote atual", extra={"sinal": sinal})
        parar.set()

    signal.signal(signal.SIGTERM, pedir_parada)
    signal.signal(signal.SIGINT, pedir_parada)

    log.info("Worker de webhooks no ar")
    proxima_limpeza = time.monotonic()
    while not parar.is_set():
        try:
            resultado = entregador.processar_lote()
            if resultado.total:
                log.info(
                    "Lote de webhooks processado",
                    extra={
                        "entregues": resultado.entregues,
                        "reagendadas": resultado.reagendadas,
                        "mortas": resultado.mortas,
                    },
                )
            if time.monotonic() >= proxima_limpeza:
                with fabrica_de_sessoes() as sessao, sessao.begin():
                    apagadas = RepositorioDeIdempotencia(sessao).apagar_expiradas(relogio.agora())
                if apagadas:
                    log.info(
                        "Chaves de idempotência expiradas removidas", extra={"total": apagadas}
                    )
                proxima_limpeza = time.monotonic() + SEGUNDOS_ENTRE_LIMPEZAS
        except Exception:
            # Banco fora do ar, por exemplo: registra e tenta de novo, sem morrer.
            log.exception("Falha no ciclo do worker")
            parar.wait(PAUSA_DEPOIS_DE_ERRO_EM_SEGUNDOS)
            continue
        if resultado.total == 0:
            parar.wait(config.worker_intervalo_segundos)

    engine.dispose()
    log.info("Worker encerrado")


if __name__ == "__main__":
    main()
