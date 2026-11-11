"""Worker: entrega os webhooks da outbox e acompanha os custodiantes externos.

    python -m app.worker

Webhooks: pode rodar em várias instâncias ao mesmo tempo, porque cada lote é reservado
com `FOR UPDATE SKIP LOCKED` e dois workers nunca enviam a mesma entrega.

Custódia (fase 2, simulada): a cada `CUSTODIA_INTERVALO_SEGUNDOS`, envia os pedidos que
ainda não saíram, consulta os que esperam resposta (reserva para quando o callback não
chega) e expira os que passaram do prazo. Com várias instâncias, duas podem consultar a
mesma garantia; a trava de linha da conta e a chave única das respostas fazem a segunda
não ter efeito.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import timedelta
from types import FrameType

from app.aplicacao.custodia import processar_custodia
from app.config import obter_configuracao
from app.infra.banco.repositorios import RepositorioDeIdempotencia
from app.infra.banco.sessao import criar_engine, criar_fabrica_de_sessoes
from app.infra.banco.unidade import criar_fabrica_de_unidades
from app.infra.custodia import montar_custodiante
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
    abrir_unidade = criar_fabrica_de_unidades(
        fabrica_de_sessoes,
        relogio,
        montar_custodiante(
            config.custodia_modo,
            config.custodia_externa_efetiva,
            relogio,
            config.custodia_perfis_arquivo,
        ),
    )
    prazo_legado = timedelta(minutes=config.custodia_prazo_legado_minutos)

    parar = threading.Event()

    def pedir_parada(sinal: int, _: FrameType | None) -> None:
        log.info("Sinal recebido, terminando o lote atual", extra={"sinal": sinal})
        parar.set()

    signal.signal(signal.SIGTERM, pedir_parada)
    signal.signal(signal.SIGINT, pedir_parada)

    log.info("Worker de webhooks no ar")
    proxima_limpeza = time.monotonic()
    proxima_custodia = time.monotonic()
    while not parar.is_set():
        try:
            if time.monotonic() >= proxima_custodia:
                # Etapa separada: uma falha na custódia não pode parar os webhooks.
                proxima_custodia = time.monotonic() + config.custodia_intervalo_segundos
                try:
                    processadas, com_erro, eventos = processar_custodia(abrir_unidade, prazo_legado)
                    if eventos or com_erro:
                        log.info(
                            "Custódia acompanhada",
                            extra={
                                "garantias_processadas": processadas,
                                "garantias_com_erro": com_erro,
                                "eventos_gerados": eventos,
                            },
                        )
                except Exception:
                    log.exception("Falha na rotina de custódia do worker")
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
