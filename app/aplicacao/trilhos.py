"""Casos de uso dos trilhos de entrada da garantia.

Cinco trilhos levam o ativo à condição de garantia da SCD do grupo: a custódia na casa e
quatro de fora (portabilidade da Res. CVM 210, transferência de custódia, TD Garantia e
Res. Conjunta 12). O catálogo regulatório diz o que cada ativo aceita; o domínio confere a
combinação antes de criar a garantia.

Os prazos nascem aqui, nunca no domínio: `prazo_ate` (prazo do trilho, em dias úteis, do
catálogo e do calendário) e `prazo_da_baixa` (2 dias úteis). Nenhuma função faz commit.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.aplicacao.contrapartes import exigir_contraparte
from app.aplicacao.erros import ContaNaoEncontrada, ContraparteNaoAtendeOTrilho
from app.aplicacao.portas import UnidadeDeTrabalho
from app.dominio.calendario import DiasUteis
from app.dominio.conta import Conta
from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import CombinacaoNaoPermitida, GarantiaNaoEncontrada
from app.dominio.garantia import Garantia
from app.dominio.parceiro import Parceiro
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Instrumento, Produto, Trilho


def prazo_da_baixa(uow: UnidadeDeTrabalho) -> date:
    """Quando o gravame precisa ser baixado: hoje mais o prazo do catálogo em dias úteis."""
    return uow.calendario.somar(
        uow.relogio.hoje(), DiasUteis(uow.catalogo.prazo_da_baixa_dias_uteis)
    )


def prazo_do_trilho(uow: UnidadeDeTrabalho, trilho: Trilho, tipo: TipoAtivo) -> datetime | None:
    """O instante em que o prazo do trilho para o ativo termina; None se o trilho não tem
    prazo (custódia na casa).

    É hoje mais os dias úteis do catálogo, no mesmo horário de agora: a data de negócio do
    prazo é a do calendário, e o horário só mantém a ordem dos instantes.
    """
    dias = uow.catalogo.prazo_do_trilho(trilho, tipo)
    if dias is None:
        return None
    hoje = uow.relogio.hoje()
    limite = uow.calendario.somar(hoje, DiasUteis(dias))
    return uow.relogio.agora() + timedelta(days=(limite - hoje).days)


def _conta_para_atualizar(uow: UnidadeDeTrabalho, parceiro: Parceiro, conta_id: str) -> Conta:
    conta = uow.contas.obter_para_atualizar(parceiro.id, conta_id)
    if conta is None:
        # Conta de outro parceiro também cai aqui: para quem pergunta, ela não existe.
        raise ContaNaoEncontrada("Conta não encontrada", conta_id=conta_id)
    return conta


def _conta_da_garantia(uow: UnidadeDeTrabalho, garantia_id: str, parceiro_id: str | None) -> Conta:
    conta = uow.contas.obter_pela_garantia_para_atualizar(garantia_id, parceiro_id)
    if conta is None:
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)
    return conta


def custodiar_na_casa(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    conta_id: str,
    *,
    tipo: TipoAtivo,
    valor: Dinheiro,
    produto: Produto,
    instrumento: Instrumento | None = None,
    emissor: str | None = None,
    vencimento: date | None = None,
    liquidez_dias: int | None = None,
    aplicado_em: date | None = None,
) -> tuple[Conta, Garantia]:
    """Ativo custodiado na CTVM do grupo (ou cotas da DTVM do grupo): o gravame é registrado
    na infraestrutura (simulada) no mesmo ato, e só então a garantia gera limite."""
    conta = _conta_para_atualizar(uow, parceiro, conta_id)
    agora, hoje = uow.relogio.agora(), uow.relogio.hoje()
    garantia = conta.custodiar_na_casa(
        tipo=tipo,
        valor=valor,
        produto=produto,
        politica=parceiro.politica,
        catalogo=uow.catalogo,
        agora=agora,
        hoje=hoje,
        instrumento=instrumento,
        emissor=emissor,
        vencimento=vencimento,
        liquidez_dias=liquidez_dias,
        aplicado_em=aplicado_em,
    )
    if uow.infraestrutura.confirma_na_hora:
        conta.concluir_entrada(
            garantia.id,
            uow.infraestrutura.registrar(garantia),
            parceiro.politica,
            uow.catalogo,
            agora,
            hoje,
        )
    uow.contas.salvar(conta, parceiro)
    return conta, garantia


def receber_de_fora(
    uow: UnidadeDeTrabalho,
    parceiro: Parceiro,
    conta_id: str,
    *,
    trilho: Trilho,
    tipo: TipoAtivo,
    valor: Dinheiro,
    produto: Produto,
    contraparte_id: str,
    instrumento: Instrumento | None = None,
    emissor: str | None = None,
    vencimento: date | None = None,
    liquidez_dias: int | None = None,
    plano: str | None = None,
    fim_da_acumulacao: date | None = None,
    aplicado_em: date | None = None,
    ja_em_garantia: bool = False,
) -> tuple[Conta, Garantia]:
    """Pede a entrada por um trilho de fora. A garantia nasce com o gravame `solicitado`,
    sem limite; o pedido é enviado pela rotina das contrapartes (o worker), depois do
    commit. Enviar dentro da transação arriscaria um pedido na contraparte sem registro do
    nosso lado, se o commit falhasse."""
    conta = _conta_para_atualizar(uow, parceiro, conta_id)
    # O catálogo primeiro: combinação que ele não aceita nunca chega à contraparte, e o erro
    # certo (rota e sugestão) vem antes de "contraparte não atende".
    uow.catalogo.exigir_combinacao(
        tipo,
        trilho,
        produto,
        liquidez_dias=liquidez_dias,
        plano=plano,
        ja_em_garantia=ja_em_garantia,
    )
    prazo_ate = prazo_do_trilho(uow, trilho, tipo)
    if prazo_ate is None:
        raise CombinacaoNaoPermitida(
            "trilho_nao_atende_o_ativo",
            f"O trilho {trilho} não é de fora",
            sugestao=f"Use {uow.catalogo.trilhos[Trilho.CASA].rota}.",
        )
    exigir_contraparte(uow, contraparte_id)
    atendidos = next(i.trilhos for i in uow.contraparte.contrapartes() if i.id == contraparte_id)
    if trilho not in atendidos:
        raise ContraparteNaoAtendeOTrilho(
            f"A contraparte {contraparte_id} não atende o trilho {trilho}",
            contraparte_id=contraparte_id,
            trilho=str(trilho),
        )
    garantia = conta.receber_de_fora(
        trilho=trilho,
        tipo=tipo,
        valor=valor,
        produto=produto,
        contraparte_id=contraparte_id,
        prazo_ate=prazo_ate,
        politica=parceiro.politica,
        catalogo=uow.catalogo,
        agora=uow.relogio.agora(),
        hoje=uow.relogio.hoje(),
        instrumento=instrumento,
        emissor=emissor,
        vencimento=vencimento,
        liquidez_dias=liquidez_dias,
        plano=plano,
        fim_da_acumulacao=fim_da_acumulacao,
        aplicado_em=aplicado_em,
        ja_em_garantia=ja_em_garantia,
    )
    uow.contas.salvar(conta, parceiro)
    return conta, garantia


def liberar_garantia(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, garantia_id: str
) -> tuple[Conta, Garantia]:
    """Devolve a garantia ao cliente: o limite cai na hora e a baixa do gravame fica
    pedida, com o prazo do catálogo em dias úteis."""
    conta = _conta_da_garantia(uow, garantia_id, parceiro.id)
    garantia = conta.liberar(
        garantia_id,
        parceiro.politica,
        uow.catalogo,
        uow.relogio.agora(),
        uow.relogio.hoje(),
        prazo_da_baixa(uow),
    )
    uow.contas.salvar(conta, parceiro)
    return conta, garantia


def confirmar_registro(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, garantia_id: str
) -> tuple[Conta, Garantia]:
    """Modo legado (`CUSTODIA_MODO=assincrona`): a administração confirma o registro de
    uma garantia da casa que ficou `solicitada`."""
    conta = _conta_da_garantia(uow, garantia_id, parceiro.id)
    garantia = conta.garantia(garantia_id)
    conta.concluir_entrada(
        garantia_id,
        uow.infraestrutura.registrar(garantia),
        parceiro.politica,
        uow.catalogo,
        uow.relogio.agora(),
        uow.relogio.hoje(),
    )
    uow.contas.salvar(conta, parceiro)
    return conta, garantia


def receber_evento_de_custodia(
    uow: UnidadeDeTrabalho, parceiro: Parceiro, garantia_id: str, valor: Dinheiro
) -> tuple[Conta, Garantia]:
    """Cupom ou amortização do Tesouro: vai ao agente do garantido e fica vinculado."""
    conta = _conta_da_garantia(uow, garantia_id, parceiro.id)
    garantia = conta.receber_evento_de_custodia(garantia_id, valor, uow.relogio.agora())
    uow.contas.salvar(conta, parceiro)
    return conta, garantia
