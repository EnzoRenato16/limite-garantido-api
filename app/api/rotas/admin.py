"""API administrativa: uso da plataforma, não do parceiro."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import BigInteger, case, func, select, type_coerce

from app.api import apresentacao, esquemas
from app.api.dependencias import RecursosDaApi, Unidade, exigir_administrador
from app.api.erros import Problema
from app.aplicacao import contas as casos_de_conta
from app.aplicacao import custodia as casos_de_custodia
from app.aplicacao import parceiros as casos_de_parceiro
from app.aplicacao import risco as casos_de_risco
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import GarantiaNaoEncontrada
from app.dominio.garantia import OrigemGarantia
from app.dominio.politica import PoliticaRisco
from app.infra.banco.modelos import GarantiaModelo, LancamentoModelo, PartidaModelo
from app.infra.custodia import SimuladorDeCustodiantes
from app.infra.politica_referencia import carregar_politica_de_referencia

rotas_de_admin = APIRouter(
    prefix="/v1/admin",
    tags=["Administração"],
    dependencies=[Depends(exigir_administrador)],
    responses={
        401: {"model": Problema, "description": "Token administrativo ausente ou incorreto"},
        404: {"model": Problema, "description": "Não existe (ou ADMIN_TOKEN não configurado)"},
    },
)


FiltroDeParceiro = Annotated[
    str | None,
    Query(description="Roda só para as contas deste parceiro. Sem ele, roda para todos."),
]


def _politica(esquema: esquemas.PoliticaDeRisco) -> PoliticaRisco:
    return PoliticaRisco.de_dict(esquema.model_dump(mode="json"))


@rotas_de_admin.post(
    "/parceiros", status_code=201, response_model=esquemas.ParceiroCriado, summary="Criar parceiro"
)
def criar_parceiro(
    corpo: esquemas.NovoParceiro, uow: Unidade, recursos: RecursosDaApi
) -> esquemas.ParceiroCriado:
    """Cria um parceiro e devolve a API key dele. A chave aparece só aqui: no banco fica
    apenas o hash."""
    politica = _politica(corpo.politica) if corpo.politica else carregar_politica_de_referencia()
    parceiro, chave = casos_de_parceiro.criar_parceiro(
        uow,
        corpo.nome,
        politica,
        corpo.limite_de_requisicoes_por_minuto or recursos.config.limite_de_requisicoes_por_minuto,
    )
    uow.commit()
    return esquemas.ParceiroCriado(parceiro=apresentacao.parceiro(parceiro), api_key=chave.chave)


@rotas_de_admin.get(
    "/parceiros", response_model=list[esquemas.ParceiroResposta], summary="Listar parceiros"
)
def listar_parceiros(uow: Unidade) -> list[esquemas.ParceiroResposta]:
    return [apresentacao.parceiro(parceiro) for parceiro in uow.parceiros.listar()]


@rotas_de_admin.put(
    "/parceiros/{parceiro_id}/politica",
    response_model=esquemas.ParceiroResposta,
    summary="Definir política de risco do parceiro",
)
def definir_politica(
    parceiro_id: str, corpo: esquemas.PoliticaDeRisco, uow: Unidade
) -> esquemas.ParceiroResposta:
    """Troca LTVs, prazos e tetos. Vale a partir da próxima operação de cada conta; o
    efeito sobre contas já abertas aparece na próxima rotina de marcação."""
    parceiro = casos_de_parceiro.atualizar_politica(uow, parceiro_id, _politica(corpo))
    uow.commit()
    return apresentacao.parceiro(parceiro)


@rotas_de_admin.post(
    "/parceiros/{parceiro_id}/chave",
    response_model=esquemas.ParceiroCriado,
    summary="Rotacionar API key",
)
def rotacionar_chave(parceiro_id: str, uow: Unidade) -> esquemas.ParceiroCriado:
    """Gera uma API key nova. A anterior para de funcionar na hora."""
    parceiro, chave = casos_de_parceiro.rotacionar_chave(uow, parceiro_id)
    uow.commit()
    return esquemas.ParceiroCriado(parceiro=apresentacao.parceiro(parceiro), api_key=chave.chave)


@rotas_de_admin.post(
    "/garantias/{garantia_id}/confirmacao-de-bloqueio",
    response_model=esquemas.GarantiaComLimite,
    summary="Confirmar o gravame de uma garantia pendente",
)
def confirmar_bloqueio(
    garantia_id: str, uow: Unidade, recursos: RecursosDaApi
) -> esquemas.GarantiaComLimite:
    """Confirmação manual do gravame. Fica na API administrativa de propósito: o
    parceiro não pode atestar a própria garantia.

    - Garantia interna `pendente` (modo legado de custódia assíncrona): bloqueia.
    - Garantia externa com pedido de gravame em aberto: é um atalho de sandbox para o
      custodiante simulado. Passa pelo mesmo caso de uso do callback, com o mesmo id de
      evento que o custodiante daria; se a confirmação dele chegar depois, é reconhecida
      como a mesma e não é aplicada de novo.
    """
    linha = uow.sessao.execute(
        select(GarantiaModelo.parceiro_id, GarantiaModelo.origem).where(
            GarantiaModelo.id == garantia_id
        )
    ).first()
    if linha is None:
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)
    parceiro = casos_de_parceiro.obter_parceiro(uow, linha.parceiro_id)
    if linha.origem == OrigemGarantia.EXTERNA.value:
        custodiante = recursos.custodiante
        if not isinstance(custodiante, SimuladorDeCustodiantes):
            raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)
        _, conta, garantia = casos_de_custodia.decidir_pelo_custodiante(
            uow,
            garantia_id,
            casos_de_custodia.AcaoDoSandbox.CONFIRMAR,
            custodiante.resposta,
            casos_de_custodia.Canal.ADMIN,
        )
    else:
        conta, garantia = casos_de_conta.confirmar_bloqueio(uow, parceiro, garantia_id)
    resposta = esquemas.GarantiaComLimite(
        garantia=apresentacao.garantia(garantia, conta, parceiro.politica),
        limite=apresentacao.posicao(conta.posicao(parceiro.politica)),
    )
    uow.commit()
    return resposta


@rotas_de_admin.post(
    "/rotinas/marcacao-a-mercado",
    response_model=esquemas.ResumoDaRotina,
    summary="Rodar a marcação a mercado",
)
def rodar_marcacao(
    recursos: RecursosDaApi, parceiro_id: FiltroDeParceiro = None
) -> esquemas.ResumoDaRotina:
    """Atualiza o valor de todas as garantias pelo provedor de preços. Em produção isso é
    agendado (`python -m app.rotinas`); o endpoint existe para operação manual."""
    resumo = casos_de_risco.marcar_a_mercado(
        recursos.abrir_unidade, recursos.provedor_de_precos, parceiro_id
    )
    return esquemas.ResumoDaRotina(
        contas_processadas=resumo.contas_processadas,
        contas_com_erro=resumo.contas_com_erro,
        eventos_gerados=resumo.eventos_gerados,
    )


@rotas_de_admin.post(
    "/rotinas/virada-do-dia",
    response_model=esquemas.ResumoDaRotina,
    summary="Rodar a rotina diária",
)
def rodar_virada_do_dia(
    recursos: RecursosDaApi, parceiro_id: FiltroDeParceiro = None
) -> esquemas.ResumoDaRotina:
    """Marca faturas em atraso, executa garantias no prazo da política e vence chamadas
    de margem. Pode rodar mais de uma vez no mesmo dia sem repetir efeito."""
    resumo = casos_de_risco.virar_o_dia(recursos.abrir_unidade, parceiro_id)
    return esquemas.ResumoDaRotina(
        contas_processadas=resumo.contas_processadas,
        contas_com_erro=resumo.contas_com_erro,
        eventos_gerados=resumo.eventos_gerados,
    )


@rotas_de_admin.post(
    "/rotinas/custodia",
    response_model=esquemas.ResumoDaRotina,
    summary="Rodar a rotina de custódia (polling de reserva)",
)
def rodar_custodia(
    recursos: RecursosDaApi, parceiro_id: FiltroDeParceiro = None
) -> esquemas.ResumoDaRotina:
    """Para cada garantia que espera um custodiante externo (simulado): envia o pedido
    que ainda não saiu, consulta o que já saiu, aplica a resposta que estiver pronta e
    expira o que passou do prazo (avisando o parceiro por webhook). O worker roda isto
    a cada `CUSTODIA_INTERVALO_SEGUNDOS`; o endpoint existe para operação manual."""
    processadas, erros, eventos = casos_de_custodia.processar_custodia(
        recursos.abrir_unidade,
        timedelta(minutes=recursos.config.custodia_prazo_legado_minutos),
        parceiro_id,
    )
    return esquemas.ResumoDaRotina(
        contas_processadas=processadas, contas_com_erro=erros, eventos_gerados=eventos
    )


@rotas_de_admin.get(
    "/ledger/verificacao",
    response_model=esquemas.VerificacaoDoLedger,
    summary="Conferir se o ledger fecha",
)
def verificar_ledger(uow: Unidade) -> esquemas.VerificacaoDoLedger:
    """Soma todos os débitos e todos os créditos do ledger. Têm que ser iguais, sempre."""
    centavos = type_coerce(PartidaModelo.valor, BigInteger)
    debitos, creditos = uow.sessao.execute(
        select(
            func.coalesce(func.sum(case((PartidaModelo.sentido == "D", centavos), else_=0)), 0),
            func.coalesce(func.sum(case((PartidaModelo.sentido == "C", centavos), else_=0)), 0),
        )
    ).one()
    liquido = func.sum(case((PartidaModelo.sentido == "D", centavos), else_=-centavos))
    desbalanceados = uow.sessao.execute(
        select(func.count()).select_from(
            select(PartidaModelo.lancamento_sequencia)
            .group_by(PartidaModelo.lancamento_sequencia)
            .having(liquido != 0)
            .subquery()
        )
    ).scalar_one()
    lancamentos = uow.sessao.execute(
        select(func.count()).select_from(LancamentoModelo)
    ).scalar_one()
    return esquemas.VerificacaoDoLedger(
        fecha=int(debitos) == int(creditos) and desbalanceados == 0,
        total_de_debitos=Dinheiro(int(debitos)).texto(),
        total_de_creditos=Dinheiro(int(creditos)).texto(),
        lancamentos=int(lancamentos),
        lancamentos_desbalanceados=int(desbalanceados),
    )
