"""Contas, garantias, compras, faturas, pagamentos e extrato."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api import apresentacao, esquemas
from app.api.dependencias import ParceiroAutenticado, Unidade
from app.api.erros import Problema, corpo_do_problema
from app.api.idempotencia import (
    ChaveObrigatoria,
    ChaveOpcional,
    RespostaHttp,
    executar_com_idempotencia,
)
from app.api.paginacao import LIMITE_PADRAO, Cursor, Limite, Pagina, paginar
from app.aplicacao import contas as casos
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import GarantiaNaoEncontrada
from app.infra.banco.modelos import (
    CompraModelo,
    ContaModelo,
    FaturaModelo,
    GarantiaModelo,
    LancamentoModelo,
    PartidaModelo,
)

PRAZO_PADRAO_DE_VENCIMENTO_EM_DIAS = 10

IdDaConta = Annotated[
    str, Path(description="Id da conta", examples=["cta_01JCD3N8X5T9Q0W2R4Y6U8I0PA"])
]
IdDaGarantia = Annotated[
    str, Path(description="Id da garantia", examples=["gar_01JCD3N8X5T9Q0W2R4Y6U8I0PB"])
]

ERROS_COMUNS: dict[int | str, dict[str, object]] = {
    401: {"model": Problema, "description": "API key ausente ou incorreta"},
    404: {"model": Problema, "description": "Não existe, ou é de outro parceiro"},
    422: {"model": Problema, "description": "Requisição inválida"},
    429: {"model": Problema, "description": "Limite de requisições por minuto excedido"},
}
ERROS_DE_ESCRITA: dict[int | str, dict[str, object]] = {
    **ERROS_COMUNS,
    400: {"model": Problema, "description": "Idempotency-Key ausente ou inválida"},
    409: {"model": Problema, "description": "Conflito com o estado atual do recurso"},
}

rotas_de_contas = APIRouter(prefix="/v1", tags=["Contas"], responses=ERROS_COMUNS)
rotas_de_garantias = APIRouter(prefix="/v1", tags=["Garantias"], responses=ERROS_COMUNS)
rotas_de_compras = APIRouter(prefix="/v1", tags=["Compras"], responses=ERROS_COMUNS)
rotas_de_faturas = APIRouter(prefix="/v1", tags=["Faturas e pagamentos"], responses=ERROS_COMUNS)


# =================================================================== contas
@rotas_de_contas.post(
    "/contas",
    status_code=201,
    response_model=esquemas.ContaResposta,
    summary="Abrir conta",
    responses=ERROS_DE_ESCRITA,
)
def abrir_conta(
    corpo: esquemas.NovaConta,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveOpcional = None,
) -> JSONResponse:
    """Abre a conta de um cliente do parceiro. Ela nasce sem garantia e sem limite.

    `cliente_ref` é único por parceiro: repetir devolve `409 conta_ja_existe`.
    """

    def operacao() -> RespostaHttp:
        conta = casos.abrir_conta(uow, parceiro, corpo.cliente_ref)
        resposta = apresentacao.conta(conta, parceiro.politica, uow.relogio.hoje())
        return RespostaHttp(201, resposta.model_dump(mode="json"))

    return executar_com_idempotencia(
        request, uow, parceiro, chave, corpo, operacao, obrigatoria=False
    )


@rotas_de_contas.get(
    "/contas", response_model=Pagina[esquemas.ContaResposta], summary="Listar contas"
)
def listar_contas(
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    limite: Limite = LIMITE_PADRAO,
    cursor: Cursor = None,
) -> Pagina[esquemas.ContaResposta]:
    """Lista as contas do parceiro, das mais novas para as mais antigas."""
    hoje = uow.relogio.hoje()

    def converter(modelos: Sequence[ContaModelo]) -> list[esquemas.ContaResposta]:
        contas = (uow.contas.obter(parceiro.id, modelo.id) for modelo in modelos)
        return [apresentacao.conta(c, parceiro.politica, hoje) for c in contas if c is not None]

    return paginar(
        uow.sessao,
        select(ContaModelo).where(ContaModelo.parceiro_id == parceiro.id),
        ContaModelo.id,
        limite,
        cursor,
        converter,
    )


@rotas_de_contas.get(
    "/contas/{conta_id}", response_model=esquemas.ContaResposta, summary="Consultar conta e limite"
)
def consultar_conta(
    conta_id: IdDaConta, parceiro: ParceiroAutenticado, uow: Unidade
) -> esquemas.ContaResposta:
    """Devolve a conta com a posição de limite, a fatura em aberto e a chamada de margem.

    O limite é recalculado a cada consulta a partir do ledger e da política do parceiro.
    """
    conta = casos.consultar_conta(uow, parceiro, conta_id)
    return apresentacao.conta(conta, parceiro.politica, uow.relogio.hoje())


@rotas_de_contas.get(
    "/contas/{conta_id}/lancamentos",
    response_model=Pagina[esquemas.LancamentoResposta],
    summary="Extrato contábil da conta",
)
def listar_lancamentos(
    conta_id: IdDaConta,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    limite: Limite = LIMITE_PADRAO,
    cursor: Cursor = None,
) -> Pagina[esquemas.LancamentoResposta]:
    """Lançamentos do ledger de partidas dobradas, do mais novo para o mais antigo.

    Em cada lançamento, a soma dos débitos é igual à soma dos créditos.
    """
    casos.consultar_conta(uow, parceiro, conta_id)

    def converter(lancamentos: Sequence[LancamentoModelo]) -> list[esquemas.LancamentoResposta]:
        partidas: dict[int, list[PartidaModelo]] = {}
        if lancamentos:
            for partida in uow.sessao.scalars(
                select(PartidaModelo)
                .where(PartidaModelo.lancamento_sequencia.in_([x.sequencia for x in lancamentos]))
                .order_by(PartidaModelo.id)
            ):
                partidas.setdefault(partida.lancamento_sequencia, []).append(partida)
        return [apresentacao.lancamento(x, partidas.get(x.sequencia, [])) for x in lancamentos]

    return paginar(
        uow.sessao,
        select(LancamentoModelo).where(
            LancamentoModelo.conta_id == conta_id, LancamentoModelo.parceiro_id == parceiro.id
        ),
        LancamentoModelo.sequencia,
        limite,
        cursor,
        converter,
    )


# ================================================================ garantias
@rotas_de_garantias.post(
    "/contas/{conta_id}/garantias",
    status_code=201,
    response_model=esquemas.GarantiaComLimite,
    summary="Bloquear investimento como garantia",
    responses={
        **ERROS_DE_ESCRITA,
        202: {
            "model": esquemas.GarantiaComLimite,
            "description": "Pedido aceito; a custódia ainda vai confirmar o gravame "
            "(garantia `pendente`, sem efeito no limite)",
        },
    },
)
def bloquear_garantia(
    conta_id: IdDaConta,
    corpo: esquemas.NovaGarantia,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """Aplica o dinheiro do cliente num investimento e o bloqueia como garantia.

    O limite liberado é `valor de mercado x LTV`, arredondado para baixo, e já vem na
    resposta. Gera os eventos `garantia.bloqueada` e `limite.atualizado`.

    Exige `Idempotency-Key`: repetir a requisição não bloqueia duas vezes.
    """

    def operacao() -> RespostaHttp:
        conta, garantia = casos.solicitar_garantia(
            uow, parceiro, conta_id, corpo.tipo_ativo, Dinheiro.de_decimal(corpo.valor)
        )
        resposta = esquemas.GarantiaComLimite(
            garantia=apresentacao.garantia(garantia, conta, parceiro.politica),
            limite=apresentacao.posicao(conta.posicao(parceiro.politica)),
        )
        status = 202 if apresentacao.garantia_pendente(garantia.estado.value) else 201
        return RespostaHttp(status, resposta.model_dump(mode="json"))

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)


@rotas_de_garantias.get(
    "/contas/{conta_id}/garantias",
    response_model=Pagina[esquemas.GarantiaResposta],
    summary="Listar garantias da conta",
)
def listar_garantias(
    conta_id: IdDaConta,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    limite: Limite = LIMITE_PADRAO,
    cursor: Cursor = None,
) -> Pagina[esquemas.GarantiaResposta]:
    casos.consultar_conta(uow, parceiro, conta_id)

    def converter(garantias: Sequence[GarantiaModelo]) -> list[esquemas.GarantiaResposta]:
        valores = uow.contas.valores_de_mercado([g.id for g in garantias])
        return [
            apresentacao.garantia_do_modelo(g, valores[g.id], parceiro.politica) for g in garantias
        ]

    return paginar(
        uow.sessao,
        select(GarantiaModelo).where(
            GarantiaModelo.conta_id == conta_id, GarantiaModelo.parceiro_id == parceiro.id
        ),
        GarantiaModelo.id,
        limite,
        cursor,
        converter,
    )


@rotas_de_garantias.get(
    "/garantias/{garantia_id}",
    response_model=esquemas.GarantiaResposta,
    summary="Consultar garantia",
)
def consultar_garantia(
    garantia_id: IdDaGarantia, parceiro: ParceiroAutenticado, uow: Unidade
) -> esquemas.GarantiaResposta:
    modelo = uow.sessao.execute(
        select(GarantiaModelo).where(
            GarantiaModelo.id == garantia_id, GarantiaModelo.parceiro_id == parceiro.id
        )
    ).scalar_one_or_none()
    if modelo is None:
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)
    valor = uow.contas.valores_de_mercado([modelo.id])[modelo.id]
    return apresentacao.garantia_do_modelo(modelo, valor, parceiro.politica)


@rotas_de_garantias.post(
    "/garantias/{garantia_id}/liberacao",
    response_model=esquemas.GarantiaComLimite,
    summary="Liberar garantia",
    responses=ERROS_DE_ESCRITA,
)
def liberar_garantia(
    garantia_id: IdDaGarantia,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveOpcional = None,
) -> JSONResponse:
    """Devolve o investimento ao cliente e tira o gravame.

    Só é aceita se as outras garantias da conta continuarem cobrindo a dívida. Senão,
    responde `409 garantia_cobrindo_divida`. Gera `garantia.liberada`.
    """

    def operacao() -> RespostaHttp:
        conta, garantia = casos.liberar_garantia(uow, parceiro, garantia_id)
        resposta = esquemas.GarantiaComLimite(
            garantia=apresentacao.garantia(garantia, conta, parceiro.politica),
            limite=apresentacao.posicao(conta.posicao(parceiro.politica)),
        )
        return RespostaHttp(200, resposta.model_dump(mode="json"))

    return executar_com_idempotencia(
        request, uow, parceiro, chave, None, operacao, obrigatoria=False
    )


# ================================================================== compras
@rotas_de_compras.post(
    "/contas/{conta_id}/compras",
    status_code=201,
    response_model=esquemas.CompraComLimite,
    summary="Autorizar compra",
    responses={
        **ERROS_DE_ESCRITA,
        402: {
            "model": Problema,
            "description": "Compra negada. `codigo` traz o motivo: `limite_insuficiente`, "
            "`conta_bloqueada`, `fatura_em_atraso` ou `teto_do_parceiro_excedido`",
        },
    },
)
def autorizar_compra(
    conta_id: IdDaConta,
    corpo: esquemas.NovaCompra,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """Autoriza uma compra no cartão contra o limite disponível.

    A conta é travada durante a decisão: duas compras simultâneas nunca passam juntas do
    limite. Compra negada responde `402`, fica registrada e gera `compra.negada`.

    Exige `Idempotency-Key`: a mesma chave devolve a mesma decisão, aprovada ou negada.
    """

    def operacao() -> RespostaHttp:
        conta, compra = casos.autorizar_compra(
            uow, parceiro, conta_id, Dinheiro.de_decimal(corpo.valor), corpo.descricao
        )
        limite = conta.posicao(parceiro.politica)
        if not compra.aprovada and compra.motivo_negacao is not None:
            return RespostaHttp(
                402,
                corpo_do_problema(
                    compra.motivo_negacao.value,
                    f"Compra de {compra.valor.formatado()} negada: limite disponível de "
                    f"{limite.limite_disponivel.formatado()}",
                    request.url.path,
                    compra_id=compra.id,
                    limite_disponivel=limite.limite_disponivel.texto(),
                ),
            )
        resposta = esquemas.CompraComLimite(
            compra=apresentacao.compra(compra), limite=apresentacao.posicao(limite)
        )
        return RespostaHttp(201, resposta.model_dump(mode="json"))

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)


@rotas_de_compras.get(
    "/contas/{conta_id}/compras",
    response_model=Pagina[esquemas.CompraResposta],
    summary="Listar compras da conta",
)
def listar_compras(
    conta_id: IdDaConta,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    limite: Limite = LIMITE_PADRAO,
    cursor: Cursor = None,
) -> Pagina[esquemas.CompraResposta]:
    """Compras aprovadas e negadas, das mais novas para as mais antigas."""
    casos.consultar_conta(uow, parceiro, conta_id)

    def converter(modelos: Sequence[CompraModelo]) -> list[esquemas.CompraResposta]:
        return [apresentacao.compra_do_modelo(modelo) for modelo in modelos]

    return paginar(
        uow.sessao,
        select(CompraModelo).where(
            CompraModelo.conta_id == conta_id, CompraModelo.parceiro_id == parceiro.id
        ),
        CompraModelo.id,
        limite,
        cursor,
        converter,
    )


# ===================================================== faturas e pagamentos
@rotas_de_faturas.post(
    "/contas/{conta_id}/faturas",
    status_code=201,
    response_model=esquemas.FaturaResposta,
    summary="Fechar fatura",
    responses=ERROS_DE_ESCRITA,
)
def fechar_fatura(
    conta_id: IdDaConta,
    corpo: esquemas.FechamentoDeFatura,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveOpcional = None,
) -> JSONResponse:
    """Fecha o ciclo: as compras a faturar viram uma fatura com vencimento.

    Só pode haver uma fatura em aberto por conta. A partir do dia seguinte ao vencimento
    conta-se o atraso, e no dia definido pela política (7º, na referência) a garantia é
    executada.
    """

    def operacao() -> RespostaHttp:
        hoje = uow.relogio.hoje()
        vencimento = corpo.vencimento or hoje + timedelta(days=PRAZO_PADRAO_DE_VENCIMENTO_EM_DIAS)
        conta, fatura = casos.fechar_fatura(uow, parceiro, conta_id, vencimento)
        resposta = apresentacao.fatura(fatura, conta.divida_faturada, hoje)
        return RespostaHttp(201, resposta.model_dump(mode="json"))

    return executar_com_idempotencia(
        request, uow, parceiro, chave, corpo, operacao, obrigatoria=False
    )


@rotas_de_faturas.get(
    "/contas/{conta_id}/faturas",
    response_model=Pagina[esquemas.FaturaResposta],
    summary="Listar faturas da conta",
)
def listar_faturas(
    conta_id: IdDaConta,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    limite: Limite = LIMITE_PADRAO,
    cursor: Cursor = None,
) -> Pagina[esquemas.FaturaResposta]:
    conta = casos.consultar_conta(uow, parceiro, conta_id)
    hoje = uow.relogio.hoje()

    def converter(modelos: Sequence[FaturaModelo]) -> list[esquemas.FaturaResposta]:
        return [
            apresentacao.fatura_do_modelo(modelo, conta.divida_faturada, hoje) for modelo in modelos
        ]

    return paginar(
        uow.sessao,
        select(FaturaModelo).where(
            FaturaModelo.conta_id == conta_id, FaturaModelo.parceiro_id == parceiro.id
        ),
        FaturaModelo.id,
        limite,
        cursor,
        converter,
    )


@rotas_de_faturas.post(
    "/contas/{conta_id}/pagamentos",
    status_code=201,
    response_model=esquemas.PagamentoComLimite,
    summary="Registrar pagamento",
    responses=ERROS_DE_ESCRITA,
)
def registrar_pagamento(
    conta_id: IdDaConta,
    corpo: esquemas.NovoPagamento,
    request: Request,
    parceiro: ParceiroAutenticado,
    uow: Unidade,
    chave: ChaveObrigatoria = None,
) -> JSONResponse:
    """Registra um pagamento do cliente.

    Abate primeiro a fatura fechada; o que sobrar antecipa compras ainda não faturadas.
    Quando a fatura zera, gera `fatura.paga`. Pagar também é um jeito de atender uma
    chamada de margem.

    Exige `Idempotency-Key`.
    """

    def operacao() -> RespostaHttp:
        conta, pagamento = casos.pagar(uow, parceiro, conta_id, Dinheiro.de_decimal(corpo.valor))
        resposta = esquemas.PagamentoComLimite(
            pagamento=apresentacao.pagamento(pagamento),
            limite=apresentacao.posicao(conta.posicao(parceiro.politica)),
        )
        return RespostaHttp(201, resposta.model_dump(mode="json"))

    return executar_com_idempotencia(request, uow, parceiro, chave, corpo, operacao)
