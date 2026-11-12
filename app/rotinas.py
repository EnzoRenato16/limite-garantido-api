"""Rotinas de risco, para rodar agendadas (cron, CronJob do Kubernetes).

    python -m app.rotinas marcacao-a-mercado
    python -m app.rotinas virada-do-dia
    python -m app.rotinas custodia
    python -m app.rotinas tudo

A ordem de `tudo` importa: primeiro o preço do dia, depois os prazos, por último a
custódia (que envia os pedidos de execução que a virada do dia acabou de criar).
Rodar duas vezes no mesmo dia não repete efeito.
"""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta

from app.aplicacao import custodia, risco
from app.config import obter_configuracao
from app.infra.banco.sessao import criar_engine, criar_fabrica_de_sessoes
from app.infra.banco.unidade import criar_fabrica_de_unidades
from app.infra.custodia import montar_custodiante
from app.infra.logs import configurar_logs
from app.infra.precos import ProvedorDePrecosFake
from app.infra.relogio import RelogioDoSistema

log = logging.getLogger("app.rotinas")


def main() -> int:
    analisador = argparse.ArgumentParser(description="Rotinas de risco do Limite Garantido")
    analisador.add_argument(
        "rotina", choices=["marcacao-a-mercado", "virada-do-dia", "custodia", "tudo"]
    )
    argumentos = analisador.parse_args()

    config = obter_configuracao()
    configurar_logs(config.log_nivel)
    engine = criar_engine(
        config.database_url, config.banco_pool_tamanho, config.banco_pool_excedente
    )
    relogio = RelogioDoSistema(config.fuso_de_negocio)
    abrir_unidade = criar_fabrica_de_unidades(
        criar_fabrica_de_sessoes(engine),
        relogio,
        montar_custodiante(
            config.custodia_modo,
            config.custodia_externa_efetiva,
            relogio,
            config.custodia_perfis_arquivo,
        ),
    )

    erros = 0
    if argumentos.rotina in ("marcacao-a-mercado", "tudo"):
        resumo = risco.marcar_a_mercado(abrir_unidade, ProvedorDePrecosFake())
        log.info("Marcação a mercado concluída", extra=_campos(resumo))
        erros += resumo.contas_com_erro
    if argumentos.rotina in ("virada-do-dia", "tudo"):
        resumo = risco.virar_o_dia(abrir_unidade)
        log.info("Virada do dia concluída", extra=_campos(resumo))
        erros += resumo.contas_com_erro
    if argumentos.rotina in ("custodia", "tudo"):
        processadas, com_erro, eventos = custodia.processar_custodia(
            abrir_unidade, timedelta(minutes=config.custodia_prazo_legado_minutos)
        )
        log.info(
            "Rotina de custódia concluída",
            extra={
                "garantias_processadas": processadas,
                "garantias_com_erro": com_erro,
                "eventos_gerados": eventos,
            },
        )
        erros += com_erro
    engine.dispose()
    # Código de saída diferente de zero para o agendador perceber e alertar.
    return 1 if erros else 0


def _campos(resumo: risco.ResumoDaRotina) -> dict[str, int]:
    return {
        "contas_processadas": resumo.contas_processadas,
        "contas_com_erro": resumo.contas_com_erro,
        "eventos_gerados": resumo.eventos_gerados,
    }


if __name__ == "__main__":
    raise SystemExit(main())
