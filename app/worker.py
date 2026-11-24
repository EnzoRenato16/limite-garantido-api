"""Worker: entrega os webhooks da outbox, acompanha as contrapartes dos trilhos e baixa gravames.

    python -m app.worker

Webhooks: pode rodar em várias instâncias ao mesmo tempo, porque cada lote é reservado
com `FOR UPDATE SKIP LOCKED` e dois workers nunca enviam a mesma entrega.

Contrapartes (simuladas): a cada `CONTRAPARTES_INTERVALO_SEGUNDOS`, envia os pedidos que
ainda não saíram, consulta os que esperam resposta (reserva para quando o callback não
chega) e encerra pelo prazo do trilho: transferência e TD Garantia sem resposta viram
`cancelado` e `recusado`; portabilidade e Res. Conjunta 12 continuam, marcadas com o prazo
regulatório vencido. Depois baixa na infraestrutura (simulada) os gravames com baixa pedida.
Com várias instâncias, duas podem consultar a mesma garantia; a trava de linha da conta e a
chave única das respostas fazem a segunda não ter efeito.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import timedelta
from types import FrameType

from app.aplicacao.contrapartes import processar_baixas, processar_contrapartes
from app.config import obter_configuracao
from app.infra.banco.repositorios import RepositorioDeIdempotencia
from app.infra.banco.sessao import criar_engine, criar_fabrica_de_sessoes
from app.infra.banco.unidade import criar_fabrica_de_unidades
from app.infra.contrapartes import montar_contraparte
from app.infra.infraestrutura_simulada import montar_infraestrutura
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
        montar_contraparte(
            config.contrapartes_efetivas, relogio, config.contrapartes_perfis_arquivo
        ),
        montar_infraestrutura(config),
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
    proxima_rodada_das_contrapartes = time.monotonic()
    while not parar.is_set():
        try:
            if time.monotonic() >= proxima_rodada_das_contrapartes:
                # Etapa separada: uma falha nas contrapartes não pode parar os webhooks.
                proxima_rodada_das_contrapartes = (
                    time.monotonic() + config.contrapartes_intervalo_segundos
                )
                try:
                    processadas, com_erro, eventos = processar_contrapartes(
                        abrir_unidade, prazo_legado
                    )
                    if eventos or com_erro:
                        log.info(
                            "Contrapartes acompanhadas",
                            extra={
                                "garantias_processadas": processadas,
                                "garantias_com_erro": com_erro,
                                "eventos_gerados": eventos,
                            },
                        )
                    baixas, baixas_com_erro, baixados = processar_baixas(abrir_unidade)
                    if baixas or baixas_com_erro:
                        log.info(
                            "Baixas de gravame processadas",
                            extra={
                                "contas_processadas": baixas,
                                "contas_com_erro": baixas_com_erro,
                                "eventos_gerados": baixados,
                            },
                        )
                except Exception:
                    log.exception("Falha na rotina das contrapartes do worker")
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
