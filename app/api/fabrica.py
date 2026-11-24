"""Monta a aplicação FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from app import __version__
from app.api.erros import registrar_tratadores
from app.api.observabilidade import MiddlewareDeObservabilidade
from app.api.recursos import Recursos
from app.api.rotas.admin import rotas_de_admin
from app.api.rotas.contas import (
    rotas_de_compras,
    rotas_de_contas,
    rotas_de_faturas,
    rotas_de_garantias,
)
from app.api.rotas.custodia import (
    rotas_de_callback,
    rotas_de_contrapartes,
    rotas_de_sandbox_de_contrapartes,
    rotas_substituidas,
)
from app.api.rotas.emprestimos import rotas_de_emprestimos
from app.api.rotas.eventos import rotas_de_eventos
from app.api.rotas.negocio import rotas_de_negocio
from app.api.rotas.operacao import rotas_de_operacao
from app.api.rotas.regulacao import rotas_de_regulacao
from app.api.rotas.risco import rotas_de_risco, rotas_de_sandbox
from app.api.rotas.trilhos import rotas_de_trilhos
from app.aplicacao.portas import (
    Contraparte,
    InfraestruturaDeRegistro,
    ProvedorDePrecos,
    Relogio,
)
from app.config import Configuracao, obter_configuracao
from app.dominio.regulacao import CatalogoRegulatorio
from app.infra.banco.sessao import criar_engine, criar_fabrica_de_sessoes
from app.infra.banco.unidade import criar_fabrica_de_unidades
from app.infra.calendario import carregar_calendario
from app.infra.contrapartes import montar_contraparte
from app.infra.economia import carregar_parametros_da_economia
from app.infra.infraestrutura_simulada import montar_infraestrutura
from app.infra.logs import configurar_logs
from app.infra.metricas import Metricas
from app.infra.precos import ProvedorDePrecosFake
from app.infra.regulacao import carregar_catalogo
from app.infra.relogio import RelogioDoSistema
from app.infra.webhooks.entregador import contar_fila

DESCRICAO = """
API de **limite de crédito garantido por investimento**, pensada para uma plataforma de
Banking as a Service oferecer aos seus parceiros (fintechs, varejistas, carteiras).

O parceiro chama a API, o investimento do cliente entra como garantia por um de cinco
**trilhos** (custódia na casa, portabilidade, transferência de custódia, TD Garantia ou
Res. Conjunta 12), o gravame é registrado e, só então, o limite do cartão ou o valor do
empréstimo é liberado. O projeto **simula** o registro: não integra com B3, Selic nem
com nenhuma instituição.

> Projeto pessoal de estudo. Não é um produto real nem tem vínculo com nenhuma instituição.

## Como usar

- **Autenticação.** `Authorization: Bearer <api key>`. Cada parceiro tem a sua chave e só
  enxerga as próprias contas.
- **Dinheiro.** Sempre texto decimal com duas casas (`"4250.00"`). Nunca ponto flutuante.
- **Idempotência.** Toda operação de dinheiro (garantia, portabilidade, empréstimo, compra,
  pagamento) exige `Idempotency-Key`.
  Repetir a chave devolve a resposta original (cabeçalho `Idempotent-Replayed: true`);
  a mesma chave com outro payload dá `422`.
- **Erros.** `application/problem+json` (RFC 7807). Programe pelo campo `codigo`, que é
  estável; a lista completa está em `GET /v1/erros`.
- **Paginação.** Por cursor: passe o `proximo_cursor` da resposta em `?cursor=`.
- **Webhooks.** Todo evento é entregue com assinatura HMAC-SHA256 em `X-LG-Assinatura`
  e pode ser consultado em `GET /v1/eventos`.
- **Rastreio.** Toda resposta traz `X-Request-Id`.

## Regras de crédito em uma página

- Limite = soma de `valor de mercado x LTV` de cada garantia, arredondado para baixo e
  limitado pelo teto por cliente.
- Dívida acima do limite garantido abre **chamada de margem** com prazo. Sem reforço
  nem pagamento até o prazo, a conta é bloqueada para novas compras.
- Fatura não paga entra em atraso. No dia definido pela política (7º, na referência) a
  garantia é **executada**: vende-se só o necessário, do ativo mais líquido para o menos,
  e o que sobra continua bloqueado.
"""

TAGS = [
    {
        "name": "Contas",
        "description": "A conta de um cliente do parceiro e a sua posição de limite.",
    },
    {
        "name": "Garantias",
        "description": "O ativo dado em garantia e o gravame dele: `solicitado → registrado → "
        "liberado_para_excussao → executado → baixado`, com desvios `recusado` (TD Garantia) e "
        "`cancelado`. O limite só aparece em `registrado`. Cada trilho tem a sua rota, o seu "
        "prazo e a sua norma; a matriz está em `GET /v1/regulacao/matriz`.",
    },
    {
        "name": "Empréstimos",
        "description": "Empréstimo parcelado com garantia (Tabela Price), com cronograma, "
        "pagamento de parcela e régua de execução.",
    },
    {
        "name": "Regulação",
        "description": "A matriz regulatória do catálogo: infraestrutura, instrumento, trilho, "
        "produto, prazo e norma de cada ativo. Pública.",
    },
    {"name": "Compras", "description": "Autorização de compras contra o limite disponível."},
    {"name": "Faturas e pagamentos", "description": "Fechamento de fatura e recebimento."},
    {
        "name": "Eventos e webhooks",
        "description": "Log de eventos, configuração do webhook, entregas e reenvio manual.",
    },
    {
        "name": "Contrapartes (simuladas)",
        "description": "A outra ponta dos trilhos de fora: corretora, banco, agente do Tesouro "
        "e entidade operadora. **Não há integração real**: são simuladores com perfis "
        "fictícios. A resposta chega por callback assinado (HMAC) ou pela consulta de reserva "
        "do worker.",
    },
    {
        "name": "Rotas substituídas",
        "description": "O fluxo de custodiante externo da fase 2. Respondem 410 com `rota_nova`.",
    },
    {"name": "Risco", "description": "Política de risco do parceiro e simulador de stress."},
    {
        "name": "Negócio",
        "description": "Modelo econômico: receitas, custos e margem por cliente, da "
        "plataforma e do parceiro. Premissas editáveis, carteira sintética, números ilustrativos.",
    },
    {
        "name": "Sandbox",
        "description": "Simulações para teste: queda de preço, passagem de dias, resposta da "
        "contraparte e evento de custódia do Tesouro. "
        "Só existem com `SANDBOX_HABILITADO=true`.",
    },
    {
        "name": "Administração",
        "description": "Uso da plataforma: parceiros, políticas, rotinas e conferência do ledger. "
        "Exige o token administrativo.",
    },
    {"name": "Operação", "description": "Saúde, métricas e catálogo de erros."},
]


def criar_app(
    config: Configuracao | None = None,
    *,
    engine: Engine | None = None,
    relogio: Relogio | None = None,
    contraparte: Contraparte | None = None,
    catalogo: CatalogoRegulatorio | None = None,
    infraestrutura: InfraestruturaDeRegistro | None = None,
    provedor_de_precos: ProvedorDePrecos | None = None,
) -> FastAPI:
    config = config or obter_configuracao()
    configurar_logs(config.log_nivel)
    # O TOML do modelo econômico é lido e validado aqui: quebrado, a aplicação não sobe.
    carregar_parametros_da_economia()
    # O catálogo regulatório também: sem ele não há regra de ativo, trilho e produto.
    catalogo = catalogo or carregar_catalogo()
    calendario = carregar_calendario()

    engine_proprio = engine is None
    engine = engine or criar_engine(
        config.database_url, config.banco_pool_tamanho, config.banco_pool_excedente
    )
    fabrica_de_sessoes = criar_fabrica_de_sessoes(engine)
    relogio = relogio or RelogioDoSistema(config.fuso_de_negocio)
    if contraparte is None:
        contraparte = montar_contraparte(
            config.contrapartes_efetivas, relogio, config.contrapartes_perfis_arquivo
        )
    if infraestrutura is None:
        infraestrutura = montar_infraestrutura(config)

    metricas = Metricas()
    metricas.observar_fila_de_webhooks(lambda: contar_fila(fabrica_de_sessoes))

    def contar_eventos(tipos: list[str]) -> None:
        for tipo in tipos:
            metricas.eventos.labels(tipo=tipo).inc()

    recursos = Recursos(
        config=config,
        engine=engine,
        fabrica_de_sessoes=fabrica_de_sessoes,
        relogio=relogio,
        contraparte=contraparte,
        infraestrutura=infraestrutura,
        catalogo=catalogo,
        calendario=calendario,
        provedor_de_precos=provedor_de_precos or ProvedorDePrecosFake(),
        metricas=metricas,
        abrir_unidade=criar_fabrica_de_unidades(
            fabrica_de_sessoes,
            relogio,
            contraparte,
            infraestrutura,
            contar_eventos,
            catalogo=catalogo,
            calendario=calendario,
        ),
    )

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine_proprio:
            engine.dispose()

    app = FastAPI(
        title="Limite Garantido as a Service",
        version=__version__,
        description=DESCRICAO,
        openapi_tags=TAGS,
        lifespan=ciclo_de_vida,
    )
    app.state.recursos = recursos
    app.add_middleware(MiddlewareDeObservabilidade, metricas=metricas)
    registrar_tratadores(app)

    for rotas in (
        rotas_de_contas,
        rotas_de_garantias,
        rotas_de_trilhos,
        rotas_de_emprestimos,
        rotas_de_regulacao,
        rotas_de_compras,
        rotas_de_faturas,
        rotas_de_eventos,
        rotas_de_contrapartes,
        rotas_de_callback,
        rotas_de_risco,
        rotas_de_negocio,
        rotas_de_sandbox,
        rotas_de_sandbox_de_contrapartes,
        rotas_substituidas,
        rotas_de_admin,
        rotas_de_operacao,
    ):
        app.include_router(rotas)

    if config.demo_habilitado:
        from app.api.demo import montar_demo

        montar_demo(app, recursos)
    return app
