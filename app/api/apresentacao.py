"""Conversão do domínio para o formato de resposta da API."""

from __future__ import annotations

from datetime import date, datetime

from app.api import esquemas
from app.dominio.conta import ChamadaDeMargem, Compra, Conta, Fatura, Pagamento
from app.dominio.conta import PosicaoDeLimite as PosicaoDoDominio
from app.dominio.dinheiro import Dinheiro
from app.dominio.garantia import ESTADOS_ATIVOS, EstadoGarantia, Garantia
from app.dominio.parceiro import Parceiro
from app.dominio.politica import PoliticaRisco, TipoAtivo
from app.dominio.stress import ResultadoDoStress
from app.infra.banco.modelos import (
    CompraModelo,
    EntregaDeWebhookModelo,
    EventoModelo,
    FaturaModelo,
    GarantiaModelo,
    LancamentoModelo,
    PartidaModelo,
)

_ESTADOS_ATIVOS = {estado.value for estado in ESTADOS_ATIVOS}


def posicao(valor: PosicaoDoDominio) -> esquemas.PosicaoDeLimite:
    return esquemas.PosicaoDeLimite.model_validate(valor.para_dados())


def fatura(valor: Fatura, saldo_em_aberto: Dinheiro, hoje: date) -> esquemas.FaturaResposta:
    return esquemas.FaturaResposta(
        id=valor.id,
        conta_id=valor.conta_id,
        valor_fechado=valor.valor_fechado.texto(),
        saldo_em_aberto=saldo_em_aberto.texto(),
        vencimento=valor.vencimento,
        estado=valor.estado.value,
        dias_em_atraso=valor.dias_em_atraso(hoje) if valor.em_aberto else 0,
        fechada_em=valor.fechada_em,
        encerrada_em=valor.encerrada_em,
    )


def chamada(valor: ChamadaDeMargem) -> esquemas.ChamadaDeMargemResposta:
    return esquemas.ChamadaDeMargemResposta(
        id=valor.id,
        estado=valor.estado.value,
        deficit_inicial=valor.deficit_inicial.texto(),
        aberta_em=valor.aberta_em,
        prazo_ate=valor.prazo_ate,
    )


def conta(valor: Conta, politica: PoliticaRisco, hoje: date) -> esquemas.ContaResposta:
    em_aberto = valor.fatura_em_aberto
    aberta = valor.chamada_aberta
    return esquemas.ContaResposta(
        id=valor.id,
        cliente_ref=valor.cliente_ref,
        estado=valor.estado.value,
        motivo_bloqueio=valor.motivo_bloqueio.value if valor.motivo_bloqueio else None,
        criada_em=valor.criada_em,
        limite=posicao(valor.posicao(politica)),
        fatura_em_aberto=fatura(em_aberto, valor.divida_faturada, hoje) if em_aberto else None,
        chamada_de_margem=chamada(aberta) if aberta else None,
    )


def _garantia(
    *,
    id: str,
    conta_id: str,
    tipo_ativo: TipoAtivo,
    estado: str,
    valor_aplicado: Dinheiro,
    valor_de_mercado: Dinheiro,
    marcada_em: date,
    criada_em: datetime,
    politica: PoliticaRisco,
) -> esquemas.GarantiaResposta:
    ltv = politica.ltv_por_ativo.get(tipo_ativo)
    gera_limite = ltv is not None and estado in _ESTADOS_ATIVOS
    return esquemas.GarantiaResposta(
        id=id,
        conta_id=conta_id,
        tipo_ativo=tipo_ativo,
        estado=estado,
        valor_aplicado=valor_aplicado.texto(),
        valor_de_mercado=valor_de_mercado.texto(),
        ltv=None if ltv is None else str(ltv),
        limite_gerado=(
            valor_de_mercado.aplicar_razao(ltv)
            if gera_limite and ltv is not None
            else Dinheiro.zero()
        ).texto(),
        marcada_em=marcada_em,
        criada_em=criada_em,
    )


def garantia(
    valor: Garantia, da_conta: Conta, politica: PoliticaRisco
) -> esquemas.GarantiaResposta:
    return _garantia(
        id=valor.id,
        conta_id=valor.conta_id,
        tipo_ativo=valor.tipo_ativo,
        estado=valor.estado.value,
        valor_aplicado=valor.valor_aplicado,
        valor_de_mercado=da_conta.valor_de_mercado(valor.id),
        marcada_em=valor.marcada_em,
        criada_em=valor.criada_em,
        politica=politica,
    )


def garantia_do_modelo(
    modelo: GarantiaModelo, valor_de_mercado: Dinheiro, politica: PoliticaRisco
) -> esquemas.GarantiaResposta:
    return _garantia(
        id=modelo.id,
        conta_id=modelo.conta_id,
        tipo_ativo=TipoAtivo(modelo.tipo_ativo),
        estado=modelo.estado,
        valor_aplicado=modelo.valor_aplicado,
        valor_de_mercado=valor_de_mercado,
        marcada_em=modelo.marcada_em,
        criada_em=modelo.criada_em,
        politica=politica,
    )


def garantia_pendente(estado: str) -> bool:
    return estado == EstadoGarantia.PENDENTE.value


def compra(valor: Compra) -> esquemas.CompraResposta:
    return esquemas.CompraResposta(
        id=valor.id,
        conta_id=valor.conta_id,
        valor=valor.valor.texto(),
        descricao=valor.descricao,
        estado=valor.estado.value,
        motivo_negacao=valor.motivo_negacao.value if valor.motivo_negacao else None,
        criada_em=valor.criada_em,
    )


def compra_do_modelo(modelo: CompraModelo) -> esquemas.CompraResposta:
    return esquemas.CompraResposta(
        id=modelo.id,
        conta_id=modelo.conta_id,
        valor=modelo.valor.texto(),
        descricao=modelo.descricao,
        estado=modelo.estado,
        motivo_negacao=modelo.motivo_negacao,
        criada_em=modelo.criada_em,
    )


def fatura_do_modelo(
    modelo: FaturaModelo, saldo_em_aberto: Dinheiro, hoje: date
) -> esquemas.FaturaResposta:
    em_aberto = modelo.estado in ("fechada", "em_atraso")
    return esquemas.FaturaResposta(
        id=modelo.id,
        conta_id=modelo.conta_id,
        valor_fechado=modelo.valor_fechado.texto(),
        saldo_em_aberto=(saldo_em_aberto if em_aberto else Dinheiro.zero()).texto(),
        vencimento=modelo.vencimento,
        estado=modelo.estado,
        dias_em_atraso=max((hoje - modelo.vencimento).days, 0) if em_aberto else 0,
        fechada_em=modelo.fechada_em,
        encerrada_em=modelo.encerrada_em,
    )


def pagamento(valor: Pagamento) -> esquemas.PagamentoResposta:
    return esquemas.PagamentoResposta(
        id=valor.id,
        conta_id=valor.conta_id,
        valor=valor.valor.texto(),
        valor_na_fatura=valor.valor_na_fatura.texto(),
        valor_antecipado=valor.valor_antecipado.texto(),
        fatura_id=valor.fatura_id,
        criado_em=valor.criado_em,
    )


def lancamento(
    modelo: LancamentoModelo, partidas: list[PartidaModelo]
) -> esquemas.LancamentoResposta:
    return esquemas.LancamentoResposta(
        id=modelo.id,
        tipo=modelo.tipo,
        descricao=modelo.descricao,
        referencia_id=modelo.referencia_id,
        criado_em=modelo.criado_em,
        partidas=[
            esquemas.PartidaResposta(
                conta_contabil=partida.conta_contabil,
                sentido=partida.sentido,
                valor=partida.valor.texto(),
                garantia_id=partida.garantia_id,
            )
            for partida in partidas
        ],
    )


def evento(modelo: EventoModelo) -> esquemas.EventoResposta:
    return esquemas.EventoResposta(
        id=modelo.id,
        tipo=modelo.tipo,
        conta_id=modelo.conta_id,
        dados=modelo.dados,
        ocorrido_em=modelo.ocorrido_em,
    )


def entrega(modelo: EntregaDeWebhookModelo, evento_tipo: str) -> esquemas.EntregaResposta:
    return esquemas.EntregaResposta(
        id=modelo.id,
        evento_id=modelo.evento_id,
        evento_tipo=evento_tipo,
        url=modelo.url,
        estado=modelo.estado,
        tentativas=modelo.tentativas,
        proxima_tentativa_em=modelo.proxima_tentativa_em if modelo.estado == "pendente" else None,
        ultimo_status_http=modelo.ultimo_status_http,
        ultimo_erro=modelo.ultimo_erro,
        entregue_em=modelo.entregue_em,
        criado_em=modelo.criado_em,
    )


def parceiro(valor: Parceiro) -> esquemas.ParceiroResposta:
    return esquemas.ParceiroResposta(
        id=valor.id,
        nome=valor.nome,
        ativo=valor.ativo,
        politica=valor.politica.para_dict(),
        webhook_url=valor.webhook_url,
        limite_de_requisicoes_por_minuto=valor.limite_de_requisicoes_por_minuto,
        criado_em=valor.criado_em,
    )


def stress(resultado: ResultadoDoStress, *, detalhar: bool) -> esquemas.ResultadoDoStress:
    contas = None
    if detalhar:
        contas = [
            esquemas.ContaNoStress(
                conta_id=c.conta_id,
                divida=c.divida.texto(),
                valor_das_garantias_antes=c.valor_das_garantias_antes.texto(),
                valor_das_garantias_depois=c.valor_das_garantias_depois.texto(),
                deficit_de_margem=c.deficit_de_margem.texto(),
                exposicao_descoberta=c.exposicao_descoberta.texto(),
                nova_chamada_de_margem=c.em_chamada_depois and not c.em_chamada_antes,
            )
            for c in resultado.contas
            if c.em_chamada_depois
        ]
    return esquemas.ResultadoDoStress(
        contas_analisadas=resultado.contas_analisadas,
        contas_em_chamada_antes=resultado.contas_em_chamada_antes,
        contas_em_chamada_depois=resultado.contas_em_chamada_depois,
        novas_chamadas_de_margem=resultado.novas_chamadas_de_margem,
        divida_total=resultado.divida_total.texto(),
        valor_das_garantias_antes=resultado.valor_das_garantias_antes.texto(),
        valor_das_garantias_depois=resultado.valor_das_garantias_depois.texto(),
        deficit_de_margem_total=resultado.deficit_de_margem_total.texto(),
        exposicao_descoberta_total=resultado.exposicao_descoberta_total.texto(),
        perda_esperada=resultado.perda_esperada.texto(),
        contas=contas,
    )
