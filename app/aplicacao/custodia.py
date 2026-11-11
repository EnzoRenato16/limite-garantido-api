"""Casos de uso da garantia em custodiante externo.

Uma resposta do custodiante pode chegar por quatro canais: callback assinado, consulta
de reserva (polling), sandbox e confirmação administrativa. Todos passam por
`aplicar_resposta`, que:

1. acha o pedido pelo id (inclusive pedido já encerrado);
2. trava a conta;
3. confere se o evento do custodiante já foi registrado (idempotência: a mesma
   confirmação, venha por onde vier, tem o mesmo `evento_id`);
4. pede a decisão ao domínio, que recusa resposta que não corresponde ao pedido em
   aberto (essa vira "obsoleta" e não muda nada);
5. registra a resposta, com chave única no banco.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from enum import StrEnum

from app.aplicacao.erros import (
    ContaNaoEncontrada,
    CustodianteNaoConfigurado,
    RespostaDuplicada,
    SolicitacaoNaoEncontrada,
)
from app.aplicacao.portas import (
    FabricaDeUnidades,
    PedidoAoCustodiante,
    RespostaDoCustodiante,
    StatusDaResposta,
    UnidadeDeTrabalho,
)
from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import (
    GarantiaNaoEncontrada,
    SolicitacaoDivergente,
    TransicaoInvalida,
    ValorInvalido,
)
from app.dominio.garantia import EstadoGarantia, Garantia, TipoSolicitacao
from app.dominio.parceiro import Parceiro

log = logging.getLogger(__name__)


class Canal(StrEnum):
    CALLBACK = "callback"
    POLLING = "polling"
    SANDBOX = "sandbox"
    ADMIN = "admin"


class ResultadoDaResposta(StrEnum):
    APLICADA = "aplicada"
    # O mesmo evento já tinha sido recebido: nada a fazer.
    DUPLICADA = "duplicada"
    # O pedido já não esperava esta resposta (expirou, foi recusado, é de outro tipo).
    OBSOLETA = "obsoleta"


def prazo_de_resposta(uow: UnidadeDeTrabalho, custodiante_id: str) -> timedelta:
    for info in uow.custodiante.custodiantes():
        if info.id == custodiante_id:
            return info.prazo_de_resposta
    raise CustodianteNaoConfigurado("Custodiante não configurado", custodiante_id=custodiante_id)


def prazos_dos_custodiantes(uow: UnidadeDeTrabalho) -> dict[str, timedelta]:
    return {info.id: info.prazo_de_resposta for info in uow.custodiante.custodiantes()}


def pedido_da_garantia(conta: Conta, garantia: Garantia) -> PedidoAoCustodiante:
    """Remonta o pedido em aberto. Mesmo id, mesmo pedido: reenviar é idempotente."""
    solicitacao = garantia.solicitacao
    if solicitacao is None or garantia.custodiante_id is None:
        raise SolicitacaoDivergente(
            "A garantia não tem pedido em aberto no custodiante", garantia_id=garantia.id
        )
    if solicitacao.tipo is TipoSolicitacao.GRAVAME:
        valor = garantia.valor_aplicado
    elif solicitacao.tipo is TipoSolicitacao.EXECUCAO:
        assert garantia.valor_em_execucao is not None  # noqa: S101 - invariante do estado
        valor = garantia.valor_em_execucao
    else:
        valor = conta.valor_de_mercado(garantia.id)
    return PedidoAoCustodiante(
        solicitacao_id=solicitacao.id,
        custodiante_id=garantia.custodiante_id,
        tipo=solicitacao.tipo,
        garantia_id=garantia.id,
        tipo_ativo=garantia.tipo_ativo,
        valor=valor,
        criado_em=solicitacao.criada_em,
    )


# ----------------------------------------------------------- aplicar resposta
def aplicar_resposta(
    uow: UnidadeDeTrabalho,
    custodiante_id: str,
    resposta: RespostaDoCustodiante,
    canal: Canal,
) -> tuple[ResultadoDaResposta, Conta, Garantia]:
    """Aplica uma resposta final (confirmada ou recusada). Não faz commit."""
    local = uow.contas.localizar_solicitacao(resposta.solicitacao_id)
    if local is None or local.custodiante_id != custodiante_id:
        # Mesma resposta para "não existe" e "é de outro custodiante".
        raise SolicitacaoNaoEncontrada("Pedido não encontrado para este custodiante")
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


def _aplicar_na_conta(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    conta: Conta,
    solicitacao_id: str,
    resposta: RespostaDoCustodiante,
    canal: Canal,
) -> tuple[ResultadoDaResposta, int]:
    """Devolve o resultado e quantos eventos foram gerados."""
    local = uow.contas.localizar_solicitacao(solicitacao_id)
    assert local is not None  # noqa: S101 - quem chama já achou o pedido
    if resposta.status is StatusDaResposta.PENDENTE or resposta.evento_id is None:
        raise ValorInvalido("Só respostas finais (confirmada ou recusada) são aplicadas")
    # Com a conta travada, a segunda cópia de uma resposta sempre enxerga a primeira.
    if uow.contas.resposta_ja_registrada(local.custodiante_id, resposta.evento_id):
        return ResultadoDaResposta.DUPLICADA, 0

    garantia = conta.garantia(local.garantia_id)
    agora, hoje = uow.relogio.agora(), uow.relogio.hoje()
    politica = parceiro.politica
    resultado = ResultadoDaResposta.APLICADA
    try:
        if resposta.status is StatusDaResposta.RECUSADA:
            conta.recusar_solicitacao(
                garantia.id,
                solicitacao_id,
                resposta.motivo or "recusado_sem_motivo",
                politica,
                agora,
                hoje,
            )
        elif local.tipo is TipoSolicitacao.GRAVAME:
            conta.confirmar_gravame(garantia.id, solicitacao_id, politica, agora, hoje)
        elif local.tipo is TipoSolicitacao.LIBERACAO:
            conta.confirmar_liberacao(garantia.id, solicitacao_id, politica, agora, hoje)
        else:
            if resposta.valor_obtido is None:
                raise ValorInvalido("Confirmação de execução precisa trazer o valor obtido")
            conta.confirmar_execucao(
                garantia.id, solicitacao_id, resposta.valor_obtido, politica, agora, hoje
            )
    except (SolicitacaoDivergente, TransicaoInvalida):
        # O domínio confere antes de mexer: nada mudou na conta.
        resultado = ResultadoDaResposta.OBSOLETA
        log.warning(
            "Resposta de custodiante para pedido que não está mais em aberto",
            extra={
                "custodiante_id": local.custodiante_id,
                "solicitacao_id": solicitacao_id,
                "estado_da_garantia": str(garantia.estado),
                "canal": str(canal),
            },
        )
    try:
        uow.contas.registrar_resposta(
            custodiante_id=local.custodiante_id,
            evento_id=resposta.evento_id,
            solicitacao_id=solicitacao_id,
            garantia_id=garantia.id,
            parceiro_id=parceiro.id,
            status=resposta.status,
            canal=canal.value,
            resultado=resultado.value,
            recebida_em=agora,
            valor_obtido=resposta.valor_obtido,
            motivo=resposta.motivo,
        )
    except RespostaDuplicada:
        # Só acontece numa corrida sem trava de linha (SQLite): a outra cópia venceu.
        conta.pendencias.limpar()
        return ResultadoDaResposta.DUPLICADA, 0
    if resultado is ResultadoDaResposta.APLICADA:
        return resultado, _salvar(uow, conta, parceiro)
    return resultado, 0


# ------------------------------------------------------------- envio e polling
def _enviar(uow: UnidadeDeTrabalho, pedido: PedidoAoCustodiante) -> RespostaDoCustodiante:
    enviar: Callable[[PedidoAoCustodiante], RespostaDoCustodiante] = {
        TipoSolicitacao.GRAVAME: uow.custodiante.solicitar_gravame,
        TipoSolicitacao.LIBERACAO: uow.custodiante.solicitar_liberacao,
        TipoSolicitacao.EXECUCAO: uow.custodiante.solicitar_execucao,
    }[pedido.tipo]
    return enviar(pedido)


def acompanhar_garantia(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    conta: Conta,
    garantia: Garantia,
    prazo_legado: timedelta,
) -> int | None:
    """Um passo da rotina para uma garantia que espera o custodiante.

    Envia o pedido se ainda não foi aceito, consulta se já foi, aplica a resposta que
    estiver pronta e expira o que passou do prazo. Devolve quantos eventos saíram, ou
    `None` se nada mudou (e não há o que gravar).
    """
    agora, hoje = uow.relogio.agora(), uow.relogio.hoje()
    if garantia.estado is EstadoGarantia.PENDENTE:
        # Modo legado de custódia assíncrona: sem pedido externo, só o prazo.
        if agora <= garantia.criada_em + prazo_legado:
            return None
        conta.expirar_solicitacao(garantia.id, parceiro.politica, agora, hoje)
        return _salvar(uow, conta, parceiro)

    solicitacao = garantia.solicitacao
    if solicitacao is None or not garantia.aguardando_custodiante:
        return None
    pedido = pedido_da_garantia(conta, garantia)
    resposta = (
        uow.custodiante.consultar(pedido)
        if solicitacao.enviada_em is not None
        else _enviar(uow, pedido)
    )
    if resposta.status is StatusDaResposta.PENDENTE:
        if solicitacao.vencida(agora):
            conta.expirar_solicitacao(garantia.id, parceiro.politica, agora, hoje)
            return _salvar(uow, conta, parceiro)
        if solicitacao.enviada_em is None:
            conta.aceitar_solicitacao(garantia.id, solicitacao.id, agora)
            return _salvar(uow, conta, parceiro)
        return None
    resultado, eventos = _aplicar_na_conta(
        uow, parceiro, conta, solicitacao.id, resposta, Canal.POLLING
    )
    # Duplicada ou obsoleta ainda precisa de commit: a resposta obsoleta fica registrada.
    return eventos if resultado is not ResultadoDaResposta.DUPLICADA else None


def processar_custodia(
    abrir_unidade: FabricaDeUnidades,
    prazo_legado: timedelta,
    parceiro_id: str | None = None,
) -> tuple[int, int, int]:
    """Rotina de reserva: (garantias processadas, com erro, eventos gerados).

    Uma garantia por transação, como as outras rotinas: a trava dura pouco e um
    custodiante fora do ar não impede os outros de serem processados.
    """
    with abrir_unidade() as uow:
        alvos = list(uow.contas.garantias_aguardando_custodiante(parceiro_id))

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
                "Falha ao acompanhar garantia no custodiante", extra={"garantia_id": garantia_id}
            )
    return processadas, erros, eventos


# ------------------------------------------------------------- atalhos
FabricaDeResposta = Callable[
    [PedidoAoCustodiante, StatusDaResposta, str | None], RespostaDoCustodiante
]


class AcaoDoSandbox(StrEnum):
    CONFIRMAR = "confirmar"
    RECUSAR = "recusar"
    NAO_RESPONDER = "nao_responder"


def decidir_pelo_custodiante(
    uow: UnidadeDeTrabalho,
    garantia_id: str,
    acao: AcaoDoSandbox,
    fabricar_resposta: FabricaDeResposta,
    canal: Canal,
    *,
    parceiro_id: str | None = None,
    valor_obtido: Dinheiro | None = None,
) -> tuple[ResultadoDaResposta, Conta, Garantia]:
    """Sandbox e confirmação administrativa: alguém decide no lugar do custodiante.

    Confirmar e recusar passam por `aplicar_resposta`, como um callback. A resposta é
    fabricada pelo próprio simulador, com o mesmo `evento_id` que ele daria: se a
    resposta "de verdade" chegar depois, é reconhecida como a mesma.
    "Não responder" faz o prazo do pedido vencer agora e expira.
    """
    conta = uow.contas.obter_pela_garantia_para_atualizar(garantia_id, parceiro_id)
    if conta is None:
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)
    garantia = conta.garantia(garantia_id)
    pedido = pedido_da_garantia(conta, garantia)
    if acao is AcaoDoSandbox.NAO_RESPONDER:
        solicitacao = garantia.solicitacao
        assert solicitacao is not None  # noqa: S101 - pedido_da_garantia já conferiu
        vencido = uow.relogio.agora() - timedelta(seconds=1)
        solicitacao.prazo_ate = min(solicitacao.prazo_ate, vencido)
        uow.contas.antecipar_prazo(solicitacao.id, solicitacao.prazo_ate)
        parceiro = uow.parceiros.obter(conta.parceiro_id)
        assert parceiro is not None  # noqa: S101 - a conta existe, o parceiro também
        conta.expirar_solicitacao(
            garantia.id, parceiro.politica, uow.relogio.agora(), uow.relogio.hoje()
        )
        uow.contas.salvar(conta, parceiro)
        return ResultadoDaResposta.APLICADA, conta, garantia

    if acao is AcaoDoSandbox.CONFIRMAR:
        resposta = fabricar_resposta(pedido, StatusDaResposta.CONFIRMADA, None)
        if valor_obtido is not None and pedido.tipo is TipoSolicitacao.EXECUCAO:
            resposta = replace(resposta, valor_obtido=valor_obtido)
    else:
        resposta = fabricar_resposta(pedido, StatusDaResposta.RECUSADA, f"recusa_pelo_{canal}")
    assert garantia.custodiante_id is not None  # noqa: S101 - garantia externa
    return aplicar_resposta(uow, garantia.custodiante_id, resposta, canal)
