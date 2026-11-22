"""Casos de uso da entrada de garantia por uma contraparte de fora (simulada).

Uma resposta da contraparte pode chegar por quatro canais: callback assinado, consulta
de reserva (polling), sandbox e confirmação administrativa. Todos passam por
`aplicar_resposta`, que:

1. acha o pedido pelo id (inclusive pedido já encerrado);
2. trava a conta;
3. confere se o evento da contraparte já foi registrado (idempotência por
   `(contraparte_id, evento_id)`: a mesma resposta, venha por onde vier, tem o mesmo
   `evento_id`);
4. confere se o pedido ainda espera aquela resposta (senão é "obsoleta" e não muda nada)
   e se o trilho admite aquela resposta (a entidade operadora não recusa, art. 18);
5. aplica no domínio e registra a resposta, com chave única no banco.

`processar_contrapartes` é a rotina do worker: envia, consulta e encerra pelos prazos do
trilho. `processar_baixas` baixa os gravames com baixa pedida.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.aplicacao.erros import (
    ContaNaoEncontrada,
    ContraparteNaoConfigurada,
    RespostaDuplicada,
    RespostaInvalida,
    SolicitacaoNaoEncontrada,
)
from app.aplicacao.portas import (
    FabricaDeUnidades,
    PedidoDeEntrada,
    RespostaDaContraparte,
    StatusDaResposta,
    UnidadeDeTrabalho,
)
from app.dominio.conta import Conta
from app.dominio.erros import GarantiaNaoEncontrada, TransicaoInvalida, ValorInvalido
from app.dominio.garantia import Garantia, InformacoesDaOperadora
from app.dominio.gravame import EstadoGravame
from app.dominio.parceiro import Parceiro
from app.dominio.politica import PoliticaRisco
from app.dominio.regulacao import Trilho

log = logging.getLogger(__name__)

# Quantas garantias a rotina acompanha por rodada.
LOTE_DA_ROTINA = 500


class Canal(StrEnum):
    CALLBACK = "callback"
    POLLING = "polling"
    SANDBOX = "sandbox"
    ADMIN = "admin"


class ResultadoDaResposta(StrEnum):
    APLICADA = "aplicada"
    # O mesmo evento já tinha sido recebido: nada a fazer.
    DUPLICADA = "duplicada"
    # O pedido já não esperava esta resposta (cancelado, recusado, concluído).
    OBSOLETA = "obsoleta"


def pedido_da_garantia(garantia: Garantia) -> PedidoDeEntrada:
    """Remonta o pedido em aberto. Mesmo id, mesmo pedido: reenviar é idempotente."""
    solicitacao = garantia.solicitacao
    if solicitacao is None or garantia.contraparte_id is None:
        raise TransicaoInvalida(
            "A garantia não tem pedido de entrada em aberto na contraparte",
            garantia_id=garantia.id,
        )
    return PedidoDeEntrada(
        solicitacao_id=solicitacao.id,
        contraparte_id=garantia.contraparte_id,
        trilho=garantia.trilho,
        garantia_id=garantia.id,
        tipo_ativo=garantia.tipo_ativo,
        valor=garantia.valor_aplicado,
        criado_em=solicitacao.criada_em,
    )


# ----------------------------------------------------------- aplicar resposta
def aplicar_resposta(
    uow: UnidadeDeTrabalho,
    contraparte_id: str,
    resposta: RespostaDaContraparte,
    canal: Canal,
) -> tuple[ResultadoDaResposta, Conta, Garantia]:
    """Aplica uma resposta final (concluída, recusada ou impedimento). Não faz commit."""
    local = uow.contas.localizar_solicitacao(resposta.solicitacao_id)
    if local is None or local.contraparte_id != contraparte_id:
        # Mesma resposta para "não existe" e "é de outra contraparte".
        raise SolicitacaoNaoEncontrada("Pedido não encontrado para esta contraparte")
    parceiro = uow.parceiros.obter(local.parceiro_id)
    conta = uow.contas.obter_pela_garantia_para_atualizar(local.garantia_id)
    if parceiro is None or conta is None:
        raise ContaNaoEncontrada("Conta do pedido não encontrada")
    resultado, _ = _aplicar_na_conta(uow, parceiro, conta, local.solicitacao_id, resposta, canal)
    return resultado, conta, conta.garantia(local.garantia_id)


def _salvar(uow: UnidadeDeTrabalho, conta: Conta, parceiro: Parceiro) -> int:
    """Grava e devolve quantos eventos saíram (o salvar limpa as pendências)."""
    eventos = len(conta.pendencias.eventos)
    uow.contas.salvar(conta, parceiro)
    return eventos


def _exigir_resposta_possivel(
    uow: UnidadeDeTrabalho, trilho: Trilho, status: StatusDaResposta
) -> None:
    """Recusa a resposta que o trilho não admite. Vale mesmo para resposta já assinada."""
    if status not in (StatusDaResposta.RECUSADA, StatusDaResposta.IMPEDIMENTO):
        return
    regra = uow.catalogo.trilhos[trilho]
    if not regra.pode_recusar:
        raise RespostaInvalida(
            f"O trilho {trilho} não admite recusa nem impedimento: a entidade operadora não "
            "pode recusar pedido que siga as normas (Res. Conjunta 12, art. 18)",
            trilho=str(trilho),
            status=str(status),
        )
    # Só a portabilidade tem impedimento (Res. CVM 210, art. 19); os demais recusam.
    esperado = (
        StatusDaResposta.IMPEDIMENTO
        if trilho is Trilho.PORTABILIDADE_CVM_210
        else StatusDaResposta.RECUSADA
    )
    if status is not esperado:
        raise RespostaInvalida(
            f"No trilho {trilho} o desfecho negativo é `{esperado}`, não `{status}`",
            trilho=str(trilho),
            status=str(status),
        )


def _aplicar_na_conta(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    conta: Conta,
    solicitacao_id: str,
    resposta: RespostaDaContraparte,
    canal: Canal,
) -> tuple[ResultadoDaResposta, int]:
    """Devolve o resultado e quantos eventos foram gerados."""
    local = uow.contas.localizar_solicitacao(solicitacao_id)
    assert local is not None  # noqa: S101 - quem chama já achou o pedido
    if resposta.status is StatusDaResposta.PENDENTE or resposta.evento_id is None:
        raise ValorInvalido(
            "Só respostas finais (concluída, recusada ou impedimento) são aplicadas"
        )
    garantia = conta.garantia(local.garantia_id)
    # Antes de qualquer registro: resposta impossível no trilho é erro, não histórico.
    _exigir_resposta_possivel(uow, garantia.trilho, resposta.status)
    # Com a conta travada, a segunda cópia de uma resposta sempre enxerga a primeira.
    if uow.contas.resposta_ja_registrada(local.contraparte_id, resposta.evento_id):
        return ResultadoDaResposta.DUPLICADA, 0

    agora, hoje = uow.relogio.agora(), uow.relogio.hoje()
    politica = parceiro.politica
    catalogo = uow.catalogo
    resultado = ResultadoDaResposta.APLICADA
    solicitacao = garantia.solicitacao
    em_aberto = (
        solicitacao is not None
        and solicitacao.id == solicitacao_id
        and garantia.estado is EstadoGravame.SOLICITADO
        # Previdência com as informações já recebidas só espera a formalização do empréstimo.
        and garantia.informacoes is None
    )
    if not em_aberto:
        resultado = ResultadoDaResposta.OBSOLETA
        log.warning(
            "Resposta de contraparte para pedido que não está mais em aberto",
            extra={
                "contraparte_id": local.contraparte_id,
                "solicitacao_id": solicitacao_id,
                "estado_do_gravame": str(garantia.estado),
                "canal": str(canal),
            },
        )
    elif resposta.status is StatusDaResposta.CONCLUIDA:
        _concluir(uow, conta, garantia, resposta, politica, agora, hoje)
    else:
        conta.recusar_entrada(
            garantia.id, resposta.motivo or "recusado_sem_motivo", catalogo, agora
        )
    try:
        uow.contas.registrar_resposta(
            contraparte_id=local.contraparte_id,
            evento_id=resposta.evento_id,
            solicitacao_id=solicitacao_id,
            garantia_id=garantia.id,
            parceiro_id=parceiro.id,
            status=resposta.status,
            canal=canal.value,
            resultado=resultado.value,
            recebida_em=agora,
            motivo=resposta.motivo,
        )
    except RespostaDuplicada:
        # Só acontece numa corrida sem trava de linha (SQLite): a outra cópia venceu.
        conta.pendencias.limpar()
        return ResultadoDaResposta.DUPLICADA, 0
    if resultado is ResultadoDaResposta.APLICADA:
        return resultado, _salvar(uow, conta, parceiro)
    return resultado, 0


def _concluir(
    uow: UnidadeDeTrabalho,
    conta: Conta,
    garantia: Garantia,
    resposta: RespostaDaContraparte,
    politica: PoliticaRisco,
    agora: datetime,
    hoje: date,
) -> None:
    if garantia.trilho is Trilho.RES_CONJUNTA_12:
        # A operadora só informa valor e tributos (art. 7º). O gravame é registrado na
        # formalização do empréstimo (art. 8º), não aqui.
        if resposta.valor_disponivel is None or resposta.tributos_informados is None:
            raise ValorInvalido(
                "A conclusão da Res. Conjunta 12 precisa trazer o valor disponível e os tributos"
            )
        conta.registrar_informacoes_da_operadora(
            garantia.id,
            InformacoesDaOperadora(
                resposta.valor_disponivel, resposta.tributos_informados, recebidas_em=agora
            ),
            agora,
        )
        return
    conta.concluir_entrada(
        garantia.id, uow.infraestrutura.registrar(garantia), politica, uow.catalogo, agora, hoje
    )


# ------------------------------------------------------------- envio e polling
def acompanhar_garantia(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    conta: Conta,
    garantia: Garantia,
    prazo_legado: timedelta,
) -> int | None:
    """Um passo da rotina para uma garantia com gravame `solicitado`.

    Envia o pedido se ainda não foi aceito, consulta se já foi, aplica a resposta que
    estiver pronta e encerra pelo prazo do trilho: transferência e TD Garantia sem resposta
    viram `cancelado` e `recusado`; portabilidade e Res. Conjunta 12 continuam, marcadas
    com `prazo_regulatorio_vencido`. Devolve quantos eventos saíram, ou `None` se nada
    mudou (e não há o que gravar).
    """
    agora = uow.relogio.agora()
    if garantia.contraparte_id is None:
        # Custódia na casa em modo legado: sem pedido externo, só o prazo.
        if agora <= garantia.criada_em + prazo_legado:
            return None
        conta.cancelar_pedido(garantia.id, "prazo_legado_vencido", agora)
        return _salvar(uow, conta, parceiro)

    solicitacao = garantia.solicitacao
    if solicitacao is None or garantia.informacoes is not None:
        return None  # previdência com informações: espera a formalização do empréstimo
    pedido = pedido_da_garantia(garantia)
    try:
        resposta = (
            uow.contraparte.consultar(pedido)
            if solicitacao.enviada_em is not None
            else uow.contraparte.solicitar_entrada(pedido)
        )
        if resposta.status is not StatusDaResposta.PENDENTE:
            resultado, eventos = _aplicar_na_conta(
                uow, parceiro, conta, solicitacao.id, resposta, Canal.POLLING
            )
            # Duplicada ou obsoleta ainda precisa de commit: a obsoleta fica registrada.
            return eventos if resultado is not ResultadoDaResposta.DUPLICADA else None
    except RespostaInvalida:
        raise
    except Exception:
        # Contraparte fora do ar, ou resposta que não dá para aplicar. Dentro do prazo, o
        # erro sobe (a rotina conta e registra). Passado o prazo, vale a regra do prazo:
        # falha da contraparte não pode deixar a garantia esperando para sempre.
        if not solicitacao.vencida(agora):
            raise
        log.warning(
            "Contraparte falhou e o prazo do pedido venceu",
            extra={"contraparte_id": garantia.contraparte_id, "solicitacao_id": solicitacao.id},
            exc_info=True,
        )
        conta.pendencias.limpar()
        return _encerrar_pelo_prazo(uow, parceiro, conta, garantia, agora)
    if solicitacao.vencida(agora):
        return _encerrar_pelo_prazo(uow, parceiro, conta, garantia, agora)
    if solicitacao.enviada_em is None:
        conta.registrar_envio_do_pedido(garantia.id, agora)
        return _salvar(uow, conta, parceiro)
    return None


def _encerrar_pelo_prazo(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, conta: Conta, garantia: Garantia, agora: datetime
) -> int | None:
    regra = uow.catalogo.trilhos[garantia.trilho]
    if regra.prazo_e_regulatorio:
        # Portabilidade e Res. Conjunta 12: o prazo é obrigação da contraparte; o pedido
        # continua e fica marcado.
        solicitacao = garantia.solicitacao
        if solicitacao is not None and solicitacao.prazo_regulatorio_vencido:
            return None
        conta.marcar_prazo_regulatorio_vencido(garantia.id, agora)
    else:
        conta.recusar_entrada(garantia.id, "prazo_vencido_sem_resposta", uow.catalogo, agora)
    return _salvar(uow, conta, parceiro)


def processar_contrapartes(
    abrir_unidade: FabricaDeUnidades,
    prazo_legado: timedelta,
    parceiro_id: str | None = None,
    limite: int = LOTE_DA_ROTINA,
) -> tuple[int, int, int]:
    """Rotina de reserva: (garantias processadas, com erro, eventos gerados).

    Uma garantia por transação, como as outras rotinas: a trava dura pouco e uma contraparte
    fora do ar não impede as outras de serem processadas.
    """
    with abrir_unidade() as uow:
        alvos = list(uow.contas.garantias_aguardando_contraparte(limite, parceiro_id))

    processadas = erros = eventos = 0
    for id_do_parceiro, conta_id, garantia_id in alvos:
        try:
            with abrir_unidade() as uow:
                parceiro = uow.parceiros.obter(id_do_parceiro)
                conta = uow.contas.obter_para_atualizar(id_do_parceiro, conta_id)
                if parceiro is None or conta is None:
                    raise ContaNaoEncontrada("Conta não encontrada", conta_id=conta_id)
                garantia = conta.garantia(garantia_id)
                gerados = acompanhar_garantia(uow, parceiro, conta, garantia, prazo_legado)
                if gerados is not None:
                    eventos += gerados
                    uow.commit()
                processadas += 1
        except Exception:
            erros += 1
            log.exception(
                "Falha ao acompanhar garantia na contraparte",
                extra={
                    "garantia_id": garantia_id,
                    "conta_id": conta_id,
                    "parceiro_de_origem": id_do_parceiro,
                },
            )
    return processadas, erros, eventos


def processar_baixas(
    abrir_unidade: FabricaDeUnidades, parceiro_id: str | None = None
) -> tuple[int, int, int]:
    """Baixa na infraestrutura (simulada) os gravames com baixa pedida.

    Devolve (contas processadas, com erro, eventos gerados). Uma conta por transação.
    """
    with abrir_unidade() as uow:
        alvos = [
            alvo
            for alvo in uow.contas.contas_com_baixa_pendente()
            if parceiro_id is None or alvo[0] == parceiro_id
        ]

    processadas = erros = eventos = 0
    for id_do_parceiro, conta_id in alvos:
        try:
            with abrir_unidade() as uow:
                parceiro = uow.parceiros.obter(id_do_parceiro)
                conta = uow.contas.obter_para_atualizar(id_do_parceiro, conta_id)
                if parceiro is None or conta is None:
                    raise ContaNaoEncontrada("Conta não encontrada", conta_id=conta_id)
                agora = uow.relogio.agora()
                for garantia in conta.gravames_com_baixa_pendente():
                    uow.infraestrutura.baixar(garantia)
                    conta.confirmar_baixa(garantia.id, agora)
                eventos += _salvar(uow, conta, parceiro)
                uow.commit()
                processadas += 1
        except Exception:
            erros += 1
            log.exception(
                "Falha ao baixar gravame",
                extra={"conta_id": conta_id, "parceiro_de_origem": id_do_parceiro},
            )
    return processadas, erros, eventos


# ------------------------------------------------------------- atalhos
FabricaDeResposta = Callable[[PedidoDeEntrada, StatusDaResposta, str | None], RespostaDaContraparte]


class AcaoDoSandbox(StrEnum):
    CONCLUIR = "concluir"
    RECUSAR = "recusar"
    NAO_RESPONDER = "nao_responder"


def decidir_pela_contraparte(
    uow: UnidadeDeTrabalho,
    garantia_id: str,
    acao: AcaoDoSandbox,
    fabricar_resposta: FabricaDeResposta,
    canal: Canal,
    *,
    parceiro_id: str | None = None,
) -> tuple[ResultadoDaResposta, Conta, Garantia]:
    """Sandbox e confirmação administrativa: alguém decide no lugar da contraparte.

    Concluir e recusar passam por `aplicar_resposta`, como um callback. A resposta é
    fabricada pelo próprio simulador, com o mesmo `evento_id` que ele daria: se a resposta
    "de verdade" chegar depois, é reconhecida como a mesma. "Não responder" faz o prazo do
    pedido vencer agora e aplica a regra do prazo do trilho.
    """
    conta = uow.contas.obter_pela_garantia_para_atualizar(garantia_id, parceiro_id)
    if conta is None:
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)
    garantia = conta.garantia(garantia_id)
    pedido = pedido_da_garantia(garantia)
    parceiro = uow.parceiros.obter(conta.parceiro_id)
    assert parceiro is not None  # noqa: S101 - a conta existe, o parceiro também
    if acao is AcaoDoSandbox.NAO_RESPONDER:
        solicitacao = garantia.solicitacao
        assert solicitacao is not None  # noqa: S101 - pedido_da_garantia já conferiu
        agora = uow.relogio.agora()
        solicitacao.prazo_ate = min(solicitacao.prazo_ate, agora - timedelta(seconds=1))
        uow.contas.antecipar_prazo(solicitacao.id, solicitacao.prazo_ate)
        _encerrar_pelo_prazo(uow, parceiro, conta, garantia, agora)
        return ResultadoDaResposta.APLICADA, conta, garantia

    if acao is AcaoDoSandbox.CONCLUIR:
        resposta = fabricar_resposta(pedido, StatusDaResposta.CONCLUIDA, None)
    else:
        status = (
            StatusDaResposta.IMPEDIMENTO
            if garantia.trilho is Trilho.PORTABILIDADE_CVM_210
            else StatusDaResposta.RECUSADA
        )
        resposta = fabricar_resposta(pedido, status, f"recusa_pelo_{canal}")
    assert garantia.contraparte_id is not None  # noqa: S101 - garantia de fora
    return aplicar_resposta(uow, garantia.contraparte_id, resposta, canal)


def exigir_contraparte(uow: UnidadeDeTrabalho, contraparte_id: str) -> None:
    if not uow.contraparte.conhece(contraparte_id):
        raise ContraparteNaoConfigurada(
            "Contraparte não configurada", contraparte_id=contraparte_id
        )
