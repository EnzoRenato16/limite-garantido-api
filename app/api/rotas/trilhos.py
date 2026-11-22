"""Trilhos de entrada de fora: portabilidade, transferência de custódia, TD Garantia e
Res. Conjunta 12. Todos respondem 202: o pedido vai para a contraparte (simulada) e o
limite só nasce quando o gravame for registrado."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api import esquemas
from app.api.dependencias import ParceiroAutenticado, Unidade
from app.api.idempotencia import ChaveObrigatoria, RespostaHttp, executar_com_idempotencia
from app.api.rotas.contas import ERROS_DE_ESCRITA, IdDaConta, resposta_da_garantia
from app.aplicacao import trilhos
from app.dominio.dinheiro import Dinheiro
from app.dominio.garantia import Garantia
from app.dominio.parceiro import Parceiro
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Instrumento, Produto, Trilho

rotas_de_trilhos = APIRouter(prefix="/v1", tags=["Garantias"], responses=ERROS_DE_ESCRITA)

_RESPOSTAS_DE_ENTRADA: dict[int | str, dict[str, object]] = {
    **ERROS_DE_ESCRITA,
    202: {
        "model": esquemas.GarantiaComLimite,
        "description": "Pedido aceito. O gravame fica `solicitado` e sem efeito no limite até "
        "a contraparte responder (callback assinado ou rotina do worker).",
    },
}


def _entrar(
    uow: Unidade,
    parceiro: Parceiro,
    conta_id: str,
    trilho: Trilho,
    tipo: TipoAtivo,
    corpo: esquemas.NovaEntradaDeFora
    | esquemas.NovaGarantiaDoTesouro
    | esquemas.NovaGarantiaDePrevidencia,
    *,
    produto: Produto,
    plano: str | None = None,
    fim_da_acumulacao: date | None = None,
) -> RespostaHttp:
    conta, garantia = trilhos.receber_de_fora(
        uow,
        parceiro,
        conta_id,
        trilho=trilho,
        tipo=tipo,
        valor=Dinheiro.de_decimal(corpo.valor),
        produto=produto,
        contraparte_id=corpo.contraparte_id,
        instrumento=_instrumento(corpo),
        emissor=getattr(corpo, "emissor", None),
        vencimento=getattr(corpo, "vencimento", None),
        liquidez_dias=getattr(corpo, "liquidez_dias", None),
        plano=plano,
        fim_da_acumulacao=fim_da_acumulacao,
        aplicado_em=getattr(corpo, "aplicado_em", None),
        ja_em_garantia=bool(getattr(corpo, "ja_em_garantia", False)),
    )
    return _resposta(uow, parceiro, conta, garantia)


def _instrumento(corpo: object) -> Instrumento | None:
    valor = getattr(corpo, "instrumento", None)
    return None if valor is None else Instrumento(valor)


def _resposta(uow: Unidade, parceiro: Parceiro, conta: object, garantia: Garantia) -> RespostaHttp:
    resposta = resposta_da_garantia(uow, parceiro, conta, garantia)  # type: ignore[arg-type]
    return RespostaHttp(202, resposta.model_dump(mode="json"))


@rotas_de_trilhos.post(
    "/contas/{conta_id}/portabilidades",
    status_code=202,
    response_model=esquemas.GarantiaComLimite,
    summary="Portabilidade para a custódia do grupo (Res. CVM 210)",
    responses=_RESPOSTAS_DE_ENTRADA,
)
def pedir_portabilidade(
    conta_id: IdDaConta,
    corpo: esquemas.NovaEntradaDeFora,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """**Trilho** `portabilidade_cvm_210`, para ações, FIIs, ETFs e cotas de fundo DI que estão
    em outra corretora ou distribuidora. **Norma**: Res. CVM 210, art. 16: **2 dias úteis**
    para valores mobiliários em depósito centralizado (ações, FIIs, ETFs) e **até 9 dias úteis**
    para cotas de fundos. O prazo vem do catálogo e do calendário de feriados nacionais e sai em
    `solicitacao.prazo_ate`. Passado o prazo sem resposta, o pedido **continua** e fica marcado
    com `prazo_regulatorio_vencido`: a obrigação é da contraparte.

    **Infraestrutura**: B3 Central Depositária (ações, FIIs, ETFs) ou registradora/escriturador
    (cotas de fundo); o projeto só simula o registro. **Instrumento**: alienação fiduciária ou
    cessão fiduciária, conforme o ativo. **Produto**: o que o catálogo permitir; fundo DI até
    D+1 pode ir ao cartão, ações, FIIs e ETFs só garantem empréstimo.

    **Recusa**: só por impedimento (art. 19, §1º, exemplos, não lista fechada). Ativo já dado
    em garantia é impedimento (art. 19, §1º, IV, "b"): `ja_em_garantia=true` responde 422
    `ativo_ja_em_garantia`. CDB, LCI, LCA e Tesouro ficam fora da CVM 210 (interpretação):
    422 `trilho_nao_atende_o_ativo`, com a rota certa em `sugestao`.

    Exige `Idempotency-Key`.
    """

    def operacao() -> RespostaHttp:
        return _entrar(
            uow,
            parceiro,
            conta_id,
            Trilho.PORTABILIDADE_CVM_210,
            corpo.tipo_ativo,
            corpo,
            produto=Produto(corpo.produto),
        )

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)


@rotas_de_trilhos.post(
    "/contas/{conta_id}/transferencias-de-custodia",
    status_code=202,
    response_model=esquemas.GarantiaComLimite,
    summary="Transferência de custódia de CDB, LCI ou LCA",
    responses=_RESPOSTAS_DE_ENTRADA,
)
def pedir_transferencia(
    conta_id: IdDaConta,
    corpo: esquemas.NovaEntradaDeFora,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """**Trilho** `transferencia_de_custodia`, para CDB, LCI e LCA que estão em outro banco.
    **Fora da CVM 210** (interpretação: Lei 6.385, art. 2º, §1º): não há norma de portabilidade
    para esses ativos; é combinação operacional entre as partes, com prazo de referência de
    **5 dias úteis** (`solicitacao.prazo_ate`), que **não é regulatório**. O título é nominativo
    e transferível (Lei 13.986/2020, arts. 34 e 35).

    **Infraestrutura**: B3 Balcão. **Instrumento**: cessão fiduciária (CDB) ou alienação
    fiduciária. **Produto**: CDB com liquidez diária pode ir ao cartão; LCI e LCA só garantem
    empréstimo. Sem resposta no prazo, o pedido é **cancelado** (`gravame.cancelado`); a
    contraparte pode recusar (`gravame.cancelado` com o motivo).

    Exige `Idempotency-Key`.
    """

    def operacao() -> RespostaHttp:
        return _entrar(
            uow,
            parceiro,
            conta_id,
            Trilho.TRANSFERENCIA_DE_CUSTODIA,
            corpo.tipo_ativo,
            corpo,
            produto=Produto(corpo.produto),
        )

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)


@rotas_de_trilhos.post(
    "/contas/{conta_id}/garantias/tesouro",
    status_code=202,
    response_model=esquemas.GarantiaComLimite,
    summary="Gravame de título do Tesouro Direto (TD Garantia)",
    responses=_RESPOSTAS_DE_ENTRADA,
)
def pedir_gravame_do_tesouro(
    conta_id: IdDaConta,
    corpo: esquemas.NovaGarantiaDoTesouro,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """**Trilho** `td_garantia`: Serviço de Gravames do Tesouro Direto (Portaria MF 1.748/2024,
    itens 108 a 127; Res. BCB 409/2024). **Infraestrutura**: Selic. O gravame é instruído pelo
    agente de custódia do investidor, registrado no Selic e **universal** (item 112: não por
    título). O garantido não precisa ser participante do Selic: o agente de custódia dele
    recebe os eventos de custódia (cupons e amortizações; item 127).
    **Instrumento**: alienação fiduciária. **Produto**: empréstimo; o cartão só quando a CTVM do
    grupo for agente de custódia do Tesouro Direto (não há confirmação pública, então hoje não).
    **Prazo**: o regulamento não fixa prazo; a B3 define. O projeto usa **3 dias úteis**, que
    não é regulatório (sem resposta, o gravame fica `recusado`).

    A contraparte (agente do Tesouro, simulado) pode **recusar** quando não opera o serviço:
    `gravame.recusado`.

    Exige `Idempotency-Key`.
    """

    def operacao() -> RespostaHttp:
        return _entrar(
            uow,
            parceiro,
            conta_id,
            Trilho.TD_GARANTIA,
            TipoAtivo(corpo.tipo_ativo),
            corpo,
            produto=Produto(corpo.produto),
        )

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)


@rotas_de_trilhos.post(
    "/contas/{conta_id}/garantias/previdencia",
    status_code=202,
    response_model=esquemas.GarantiaComLimite,
    summary="Previdência em garantia (Res. Conjunta 12)",
    responses=_RESPOSTAS_DE_ENTRADA,
)
def pedir_garantia_de_previdencia(
    conta_id: IdDaConta,
    corpo: esquemas.NovaGarantiaDePrevidencia,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """**Trilho** `res_conjunta_12` (CMN e CNSP, 26/09/2024), para PGBL, VGBL e capitalização
    (FAPI fora: art. 1º, §1º; `plano` ausente é 422 e FAPI é `plano_fora_da_res_conjunta_12`).
    **Infraestrutura**: plataforma da Res. Conjunta 12 (simulada). **Instrumento**: direito de
    resgate. **Produto**: **só empréstimo** (art. 1º, §2º, VII, exclui os limites rotativos de
    cartão e de conta): no cartão, 422 `previdencia_so_no_emprestimo`.

    **Prazo**: a entidade operadora informa em **2 dias úteis** (art. 4º, §1º) o valor
    disponível e os tributos (art. 7º); o valor que vale como garantia é o líquido. **Sem
    recusa**: a operadora não pode recusar pedido que siga as normas (art. 18); recusa dela é
    422 `resposta_invalida_para_o_trilho`. Passado o prazo sem resposta, o pedido continua,
    marcado com `prazo_regulatorio_vencido`.

    O gravame (bloqueio) é registrado na formalização do empréstimo (art. 8º), em
    `POST /v1/contas/{conta_id}/emprestimos`. O prazo do crédito não passa do fim da
    acumulação (art. 6º, §3º).

    Exige `Idempotency-Key`.
    """

    def operacao() -> RespostaHttp:
        return _entrar(
            uow,
            parceiro,
            conta_id,
            Trilho.RES_CONJUNTA_12,
            TipoAtivo.PREVIDENCIA,
            corpo,
            produto=Produto(corpo.produto),
            plano=corpo.plano,
            fim_da_acumulacao=corpo.fim_da_acumulacao,
        )

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)
