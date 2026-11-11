"""Política do parceiro, simulador de stress e sandbox."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api import apresentacao, esquemas
from app.api.dependencias import ParceiroAutenticado, Unidade, exigir_sandbox
from app.api.rotas.contas import ERROS_COMUNS, IdDaConta, IdDaGarantia
from app.aplicacao import risco as casos
from app.dominio.dinheiro import Dinheiro

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
      estressado, e a perda esperada correspondente.

    Exemplo: `{"choques": {"acoes": "-0.30", "tesouro_prefixado": "-0.10"}}`.
    """
    resultado = casos.simular_stress_da_carteira(
        uow, parceiro, corpo.choques, corpo.probabilidade_de_inadimplencia
    )
    return apresentacao.stress(resultado, detalhar=corpo.detalhar_contas)


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
    resposta = esquemas.GarantiaComLimite(
        garantia=apresentacao.garantia(garantia, conta, parceiro.politica),
        limite=apresentacao.posicao(conta.posicao(parceiro.politica)),
    )
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
    resposta = apresentacao.conta(conta, parceiro.politica, uow.relogio.hoje())
    uow.commit()
    return resposta
