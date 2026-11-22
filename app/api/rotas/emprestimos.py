"""Empréstimo parcelado com garantia (Tabela Price)."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api import apresentacao, esquemas
from app.api.dependencias import ParceiroAutenticado, Unidade
from app.api.erros import Problema
from app.api.idempotencia import ChaveObrigatoria, RespostaHttp, executar_com_idempotencia
from app.api.paginacao import LIMITE_PADRAO, Cursor, Limite, Pagina, paginar
from app.api.rotas.contas import ERROS_COMUNS, ERROS_DE_ESCRITA, IdDaConta
from app.aplicacao import contas as casos_de_conta
from app.aplicacao import emprestimos as casos
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import EmprestimoNaoEncontrado
from app.infra.banco.modelos import EmprestimoModelo

rotas_de_emprestimos = APIRouter(prefix="/v1", tags=["Empréstimos"], responses=ERROS_COMUNS)

IdDoEmprestimo = str


@rotas_de_emprestimos.post(
    "/contas/{conta_id}/emprestimos",
    status_code=201,
    response_model=esquemas.EmprestimoComConta,
    summary="Contratar empréstimo com garantia",
    responses={
        **ERROS_DE_ESCRITA,
        422: {
            "model": Problema,
            "description": "Garantia que não serve, conta bloqueada ou em atraso, teto do "
            "parceiro, prazo além da acumulação ou do vencimento do título",
        },
    },
)
def contratar_emprestimo(
    conta_id: IdDaConta,
    corpo: esquemas.NovoEmprestimo,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """Contrata um empréstimo parcelado sobre garantias de **produto empréstimo** com gravame
    `registrado`. A resposta traz o cronograma (Tabela Price, parcela arredondada para cima,
    última parcela fecha o saldo). O valor máximo é a soma de `valor de mercado x LTV efetivo`
    das garantias, para baixo.

    **Previdência** (Res. Conjunta 12): a garantia ainda `solicitado`, com as informações da
    operadora, é formalizada neste passo, com o bloqueio simultâneo à formalização (art. 8º). A
    última parcela não pode vencer depois do fim da acumulação (art. 6º, §3º): 422
    `prazo_alem_da_acumulacao`. Em qualquer ativo com vencimento, a última parcela não passa
    do vencimento do título (`vencimento_nao_casado`).

    **Execução**: atraso acima de 30 dias (política) executa a garantia por inteiro; na
    previdência só **acima de 90 dias** (art. 12), com aviso aos 45 dias e outro 10 dias antes
    da liquidação (art. 6º, §2º, IV).

    Exige `Idempotency-Key`.
    """

    def operacao() -> RespostaHttp:
        conta, emprestimo = casos.contratar_emprestimo(
            uow,
            parceiro,
            conta_id,
            valor=Dinheiro.de_decimal(corpo.valor),
            parcelas=corpo.parcelas,
            garantia_ids=corpo.garantia_ids,
            primeiro_vencimento=corpo.primeiro_vencimento,
        )
        hoje = uow.relogio.hoje()
        resposta = esquemas.EmprestimoComConta(
            emprestimo=apresentacao.emprestimo(emprestimo, hoje),
            limite=apresentacao.posicao(conta.posicao(parceiro.politica, uow.catalogo, hoje)),
        )
        return RespostaHttp(201, resposta.model_dump(mode="json"))

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)


@rotas_de_emprestimos.get(
    "/contas/{conta_id}/emprestimos",
    response_model=Pagina[esquemas.EmprestimoResposta],
    summary="Listar empréstimos da conta",
)
def listar_emprestimos(
    conta_id: IdDaConta,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    limite: Limite = LIMITE_PADRAO,
    cursor: Cursor = None,
) -> Pagina[esquemas.EmprestimoResposta]:
    """Empréstimos de qualquer estado, dos mais antigos aos mais novos."""
    casos_de_conta.consultar_conta(uow, parceiro, conta_id)
    hoje = uow.relogio.hoje()

    def converter(modelos: Sequence[EmprestimoModelo]) -> list[esquemas.EmprestimoResposta]:
        carregados = uow.contas.emprestimos_por_id([m.id for m in modelos])
        return [apresentacao.emprestimo(carregados[m.id], hoje) for m in modelos]

    return paginar(
        uow.sessao,
        select(EmprestimoModelo).where(
            EmprestimoModelo.conta_id == conta_id, EmprestimoModelo.parceiro_id == parceiro.id
        ),
        EmprestimoModelo.id,
        limite,
        cursor,
        converter,
    )


@rotas_de_emprestimos.get(
    "/emprestimos/{emprestimo_id}",
    response_model=esquemas.EmprestimoResposta,
    summary="Consultar empréstimo",
)
def consultar_emprestimo(
    emprestimo_id: IdDoEmprestimo, parceiro: ParceiroAutenticado, uow: Unidade
) -> esquemas.EmprestimoResposta:
    emprestimo = uow.contas.obter_emprestimo(parceiro.id, emprestimo_id)
    if emprestimo is None:
        raise EmprestimoNaoEncontrado("Empréstimo não encontrado", emprestimo_id=emprestimo_id)
    return apresentacao.emprestimo(emprestimo, uow.relogio.hoje())


@rotas_de_emprestimos.post(
    "/emprestimos/{emprestimo_id}/pagamentos",
    status_code=201,
    response_model=esquemas.EmprestimoComConta,
    summary="Pagar parcela de empréstimo",
    responses=ERROS_DE_ESCRITA,
)
def pagar_emprestimo(
    emprestimo_id: IdDoEmprestimo,
    corpo: esquemas.NovoPagamentoDeEmprestimo,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """Abate as parcelas em ordem, juros primeiro; parcela vincenda paga só a amortização
    (sem juros futuros). O valor não passa do saldo devedor (`pagamento_excede_divida`).
    Quitado o empréstimo, as garantias são liberadas e a baixa do gravame fica pedida, com
    prazo de 2 dias úteis.

    Exige `Idempotency-Key`.
    """

    def operacao() -> RespostaHttp:
        existente = uow.contas.obter_emprestimo(parceiro.id, emprestimo_id)
        if existente is None:
            raise EmprestimoNaoEncontrado("Empréstimo não encontrado", emprestimo_id=emprestimo_id)
        conta, emprestimo = casos.pagar_parcela(
            uow, parceiro, existente.conta_id, emprestimo_id, Dinheiro.de_decimal(corpo.valor)
        )
        hoje = uow.relogio.hoje()
        resposta = esquemas.EmprestimoComConta(
            emprestimo=apresentacao.emprestimo(emprestimo, hoje),
            limite=apresentacao.posicao(conta.posicao(parceiro.politica, uow.catalogo, hoje)),
        )
        return RespostaHttp(201, resposta.model_dump(mode="json"))

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)
