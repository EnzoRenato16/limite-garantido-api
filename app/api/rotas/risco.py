"""Política do parceiro, simulador de stress e sandbox."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends

from app.api import apresentacao, esquemas
from app.api.dependencias import ParceiroAutenticado, Unidade, exigir_sandbox
from app.api.rotas.contas import ERROS_COMUNS, IdDaConta, IdDaGarantia, resposta_da_garantia
from app.aplicacao import risco as casos
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.stress import PremissasDaGarantiaExterna, simular_painel
from app.infra.carteira_sintetica import carregar_parametros, gerar_carteira
from app.infra.politica_referencia import carregar_politica_de_referencia
from app.infra.regulacao import carregar_catalogo

rotas_de_risco = APIRouter(prefix="/v1", tags=["Risco"], responses=ERROS_COMUNS)
rotas_de_sandbox = APIRouter(
    prefix="/v1/sandbox",
    tags=["Sandbox"],
    responses=ERROS_COMUNS,
    dependencies=[Depends(exigir_sandbox)],
)


@rotas_de_risco.get(
    "/parceiro", response_model=esquemas.ParceiroResposta, summary="Meus dados e política de risco"
)
def consultar_parceiro(parceiro: ParceiroAutenticado) -> esquemas.ParceiroResposta:
    """Dados do parceiro dono da API key, com a política de risco em vigor: LTV por
    ativo, ordem de execução, prazos e tetos. Quem define a política é o time de risco
    da plataforma."""
    return apresentacao.parceiro(parceiro)


@rotas_de_risco.post(
    "/risco/simulacoes-de-stress",
    response_model=esquemas.ResultadoDoStress,
    summary="Simular choque de preços na carteira",
)
def simular_stress(
    corpo: esquemas.SimulacaoDeStress, parceiro: ParceiroAutenticado, uow: Unidade
) -> esquemas.ResultadoDoStress:
    """Aplica um choque por tipo de ativo sobre todas as contas do parceiro, sem alterar
    nada, e responde:

    - quantas contas entrariam em chamada de margem;
    - de quanto seria o déficit de margem;
    - quanto da dívida ficaria descoberto mesmo vendendo toda a garantia ao preço
      estressado, e a perda esperada associada.

    Exemplo: `{"choques": {"acoes": "-0.30", "tesouro_prefixado": "-0.10"}}`.
    """
    resultado = casos.simular_stress_da_carteira(
        uow, parceiro, corpo.choques, corpo.probabilidade_de_inadimplencia
    )
    return apresentacao.stress(resultado, detalhar=corpo.detalhar_contas)


@rotas_de_risco.get(
    "/risco/cenarios",
    response_model=list[esquemas.CenarioDoPainel],
    summary="Cenários prontos do painel de risco",
)
def listar_cenarios(_: ParceiroAutenticado) -> list[esquemas.CenarioDoPainel]:
    """Os cenários prontos do painel: dia normal, crash da bolsa, alta de juros, quebra
    de emissor e agente de custódia que perde a participação. Os números são
    ilustrativos, não previsões."""
    return [apresentacao.cenario(c) for c in carregar_parametros().cenarios.values()]


@rotas_de_risco.post(
    "/risco/simulacoes-de-carteira",
    response_model=esquemas.ResultadoDoPainel,
    summary="Simular choques numa carteira sintética (painel de risco)",
)
def simular_carteira(
    corpo: esquemas.SimulacaoDeCarteira, _: ParceiroAutenticado
) -> esquemas.ResultadoDoPainel:
    """Gera uma carteira **sintética** (por padrão 1.000 clientes, sempre a mesma para a
    mesma `semente`) e aplica um cenário. Não lê nem altera a carteira do parceiro; para
    estressar a carteira real, use `POST /v1/risco/simulacoes-de-stress`.

    Responde, antes e depois do choque: clientes em chamada de margem, valor das
    garantias, limite total, utilizado e disponível. E a perda esperada não coberta
    depois de executar as garantias, em duas parcelas:

    - **com execução imediata**: o que se perderia mesmo vendendo toda a garantia na
      hora, ao preço estressado;
    - **adicional da garantia fora da casa**: o que piora porque parte da garantia está
      com um agente de custódia que atrasa, ou é de um emissor em quebra.

    Responde também o painel regulatório: haircut por classe e prazo, concentração de CDB,
    LCI e LCA por conglomerado contra o teto do FGC, e as carteiras de provisão. A garantia
    reduz a perda; não a elimina. As premissas usadas voltam na resposta. Todas são
    ilustrativas, e `aviso_sintetico` lembra que os clientes são sintéticos.
    """
    parametros = carregar_parametros()
    base = None
    if corpo.cenario is not None:
        base = parametros.cenarios.get(corpo.cenario)
        if base is None:
            raise ValorInvalido(
                "Cenário desconhecido. Veja GET /v1/risco/cenarios", cenario=corpo.cenario
            )
    choques = dict(corpo.choques if corpo.choques is not None else (base.choques if base else {}))
    taxa = (
        corpo.taxa_sem_reforco
        if corpo.taxa_sem_reforco is not None
        else (base.taxa_sem_reforco if base else Decimal("0.30"))
    )
    padrao = base.premissas if base else PremissasDaGarantiaExterna()
    premissas = PremissasDaGarantiaExterna(
        custodiantes_indisponiveis=frozenset(corpo.custodiantes_indisponiveis)
        if corpo.custodiantes_indisponiveis is not None
        else padrao.custodiantes_indisponiveis,
        dias_extra_de_execucao=corpo.dias_extra_de_execucao
        if corpo.dias_extra_de_execucao is not None
        else padrao.dias_extra_de_execucao,
        desconto_diario_por_ativo=parametros.desconto_diario_por_ativo,
        atraso_por_onde_esta=corpo.atraso_por_onde_esta
        if corpo.atraso_por_onde_esta is not None
        else padrao.atraso_por_onde_esta,
        emissores_em_quebra=frozenset(corpo.emissores_em_quebra)
        if corpo.emissores_em_quebra is not None
        else padrao.emissores_em_quebra,
    )
    carteira = gerar_carteira(parametros, corpo.tamanho, corpo.semente)
    resultado = simular_painel(
        carteira, choques, carregar_politica_de_referencia(), taxa, premissas, carregar_catalogo()
    )
    return apresentacao.painel(
        resultado,
        tamanho=corpo.tamanho,
        semente=corpo.semente,
        cenario=corpo.cenario,
        choques=choques,
        taxa_sem_reforco=taxa,
        premissas=premissas,
    )


@rotas_de_sandbox.post(
    "/garantias/{garantia_id}/remarcacao",
    response_model=esquemas.GarantiaComLimite,
    summary="Simular movimento de preço de uma garantia",
)
def remarcar_garantia(
    garantia_id: IdDaGarantia,
    corpo: esquemas.RemarcacaoNoSandbox,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
) -> esquemas.GarantiaComLimite:
    """Só existe com `SANDBOX_HABILITADO=true`. Em produção quem marca a mercado é a
    rotina, a partir do provedor de preços.

    Se a dívida passar do novo limite garantido, abre uma chamada de margem.
    """
    conta, garantia = casos.remarcar_no_sandbox(
        uow,
        parceiro,
        garantia_id,
        variacao_percentual=corpo.variacao_percentual,
        valor_de_mercado=None
        if corpo.valor_de_mercado is None
        else Dinheiro.de_decimal(corpo.valor_de_mercado),
    )
    resposta = resposta_da_garantia(uow, parceiro, conta, garantia)
    uow.commit()
    return resposta


@rotas_de_sandbox.post(
    "/contas/{conta_id}/avanco-de-dias",
    response_model=esquemas.ContaResposta,
    summary="Fazer o tempo passar para uma conta",
)
def avancar_dias(
    conta_id: IdDaConta, corpo: esquemas.AvancoDeDias, parceiro: ParceiroAutenticado, uow: Unidade
) -> esquemas.ContaResposta:
    """Só existe com `SANDBOX_HABILITADO=true`.

    Antecipa o vencimento da fatura e o prazo da chamada de margem em `dias` e roda a
    rotina diária da conta. É como testar atraso, execução no 7º dia e vencimento de
    chamada de margem sem esperar uma semana.
    """
    conta = casos.avancar_dias_no_sandbox(uow, parceiro, conta_id, corpo.dias)
    resposta = apresentacao.conta(conta, parceiro.politica, uow.catalogo, uow.relogio.hoje())
    uow.commit()
    return resposta
