"""Cria (ou atualiza) o parceiro de exemplo usado pela página /demo.

    python -m app.seed

Usa DEMO_API_KEY, DEMO_WEBHOOK_SEGREDO e DEMO_WEBHOOK_URL. Pode rodar quantas vezes
quiser: se o parceiro já existe, só confere o webhook.
"""

from __future__ import annotations

import logging

from app.aplicacao import parceiros as casos
from app.aplicacao.chaves import descrever_api_key
from app.config import Configuracao, obter_configuracao
from app.infra.banco.sessao import criar_engine, criar_fabrica_de_sessoes
from app.infra.banco.unidade import UnidadeDeTrabalhoSql, criar_fabrica_de_unidades
from app.infra.custodia import CustodiaInterna
from app.infra.logs import configurar_logs
from app.infra.politica_referencia import carregar_politica_de_referencia
from app.infra.relogio import RelogioDoSistema

log = logging.getLogger("app.seed")

NOME_DO_PARCEIRO_DE_EXEMPLO = "Carteira Exemplo"
REQUISICOES_POR_MINUTO_NA_DEMO = 600


def semear(uow: UnidadeDeTrabalhoSql, config: Configuracao) -> str:
    """Garante o parceiro de exemplo e devolve o id dele."""
    if config.demo_api_key is None or config.demo_webhook_segredo is None:
        raise SystemExit("Defina DEMO_API_KEY e DEMO_WEBHOOK_SEGREDO (veja .env.example)")
    chave = descrever_api_key(config.demo_api_key.get_secret_value())
    achado = uow.parceiros.obter_pelo_prefixo(chave.prefixo)
    if achado is None:
        parceiro, _ = casos.criar_parceiro(
            uow,
            NOME_DO_PARCEIRO_DE_EXEMPLO,
            carregar_politica_de_referencia(),
            REQUISICOES_POR_MINUTO_NA_DEMO,
            chave=chave,
        )
        log.info("Parceiro de exemplo criado", extra={"parceiro_id": parceiro.id})
    else:
        parceiro = achado[0]
    casos.configurar_webhook(
        uow,
        parceiro,
        config.demo_webhook_url,
        segredo=config.demo_webhook_segredo.get_secret_value(),
    )
    uow.commit()
    return parceiro.id


def main() -> None:
    config = obter_configuracao()
    configurar_logs(config.log_nivel)
    if config.em_producao:
        raise SystemExit("O seed é só para desenvolvimento e demonstração")
    engine = criar_engine(config.database_url)
    abrir_unidade = criar_fabrica_de_unidades(
        criar_fabrica_de_sessoes(engine),
        RelogioDoSistema(config.fuso_de_negocio),
        CustodiaInterna(),
    )
    with abrir_unidade() as uow:
        parceiro_id = semear(uow, config)
    engine.dispose()
    log.info("Seed concluído", extra={"parceiro_id": parceiro_id})


if __name__ == "__main__":
    main()
