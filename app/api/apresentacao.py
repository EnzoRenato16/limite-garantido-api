"""Conversão do domínio para o formato de resposta da API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

from app.api import esquemas
from app.dominio.conta import ChamadaDeMargem, Compra, Conta, Fatura, Pagamento
from app.dominio.conta import PosicaoDeLimite as PosicaoDoDominio
from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.analises import ResultadoEconomico
from app.dominio.economia.calculo import Visao
from app.dominio.economia.premissas import EsquemaDaPremissa, FonteDeReceita, NomeDaPremissa
from app.dominio.garantia import (
    ESTADOS_AGUARDANDO_CUSTODIANTE,
    ESTADOS_ATIVOS,
    EstadoGarantia,
    Garantia,
)
from app.dominio.parceiro import Parceiro
from app.dominio.politica import PoliticaRisco, TipoAtivo
from app.dominio.stress import (
    FatiaDeConcentracao,
    FotoDaCarteira,
    PremissasDaGarantiaExterna,
    ResultadoDoPainel,
    ResultadoDoStress,
)
from app.infra.banco.modelos import (
    CompraModelo,
    EntregaDeWebhookModelo,
    EventoModelo,
    FaturaModelo,
    GarantiaModelo,
    LancamentoModelo,
    PartidaModelo,
    SolicitacaoDeCustodiaModelo,
)
from app.infra.carteira_sintetica import Cenario
from app.infra.economia import ParametrosDaEconomia

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
    origem: str = "interna",
    custodiante_id: str | None = None,
    solicitacao: esquemas.SolicitacaoResposta | None = None,
    valor_em_execucao: Dinheiro | None = None,
    motivo: str | None = None,
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
        origem=origem,
        custodiante_id=custodiante_id,
        solicitacao=solicitacao,
        valor_em_execucao=None if valor_em_execucao is None else valor_em_execucao.texto(),
        motivo=motivo,
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
        origem=valor.origem.value,
        custodiante_id=valor.custodiante_id,
        solicitacao=None
        if valor.solicitacao is None
        else esquemas.SolicitacaoResposta(
            id=valor.solicitacao.id,
            tipo=valor.solicitacao.tipo.value,
            criada_em=valor.solicitacao.criada_em,
            prazo_ate=valor.solicitacao.prazo_ate,
            enviada_em=valor.solicitacao.enviada_em,
        ),
        valor_em_execucao=valor.valor_em_execucao,
        motivo=valor.motivo,
    )


def garantia_do_modelo(
    modelo: GarantiaModelo,
    valor_de_mercado: Dinheiro,
    politica: PoliticaRisco,
    pedido: SolicitacaoDeCustodiaModelo | None = None,
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
        origem=modelo.origem,
        custodiante_id=modelo.custodiante_id,
        solicitacao=None
        if pedido is None
        else esquemas.SolicitacaoResposta(
            id=pedido.id,
            tipo=pedido.tipo,
            criada_em=pedido.criada_em,
            prazo_ate=pedido.prazo_ate,
            enviada_em=modelo.solicitacao_enviada_em,
        ),
        valor_em_execucao=modelo.valor_em_execucao,
        motivo=modelo.motivo,
    )


_ESPERANDO_CUSTODIA = {e.value for e in ESTADOS_AGUARDANDO_CUSTODIANTE | {EstadoGarantia.PENDENTE}}


def garantia_pendente(estado: str) -> bool:
    """Pedido aceito e ainda sem resposta da custódia: a API responde 202."""
    return estado in _ESPERANDO_CUSTODIA


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


# ------------------------------------------------------------- painel de risco
AVISO_DA_CARTEIRA_SINTETICA = (
    "Carteira sintética, gerada em memória a partir da semente. Os parâmetros e as "
    "premissas são ilustrativos: não são dados de mercado nem de nenhum parceiro."
)


def _percentual(parte: Dinheiro, total: Dinheiro) -> str:
    """Percentual com duas casas, truncado. Não é dinheiro: é proporção para exibir."""
    if not total.positivo:
        return "0.00"
    centesimos = parte.centavos * 10_000 // total.centavos
    return f"{centesimos // 100}.{centesimos % 100:02d}"


def _foto(valor: FotoDaCarteira) -> esquemas.FotoDaCarteiraResposta:
    return esquemas.FotoDaCarteiraResposta(
        contas_em_chamada=valor.contas_em_chamada,
        valor_das_garantias=valor.valor_das_garantias.texto(),
        limite_total=valor.limite_total.texto(),
        limite_utilizado=valor.limite_utilizado.texto(),
        limite_disponivel=valor.limite_disponivel.texto(),
        utilizacao_percentual=_percentual(valor.limite_utilizado, valor.limite_total),
    )


def _fatias(
    fatias: tuple[FatiaDeConcentracao, ...], total: Dinheiro
) -> list[esquemas.FatiaDeConcentracaoResposta]:
    return [
        esquemas.FatiaDeConcentracaoResposta(
            chave=fatia.chave,
            valor_antes=fatia.valor_antes.texto(),
            valor_depois=fatia.valor_depois.texto(),
            percentual_antes=_percentual(fatia.valor_antes, total),
            garantias=fatia.garantias,
            contas=fatia.contas,
        )
        for fatia in fatias
    ]


def painel(
    resultado: ResultadoDoPainel,
    *,
    tamanho: int,
    semente: int,
    cenario: str | None,
    choques: dict[TipoAtivo, Decimal],
    taxa_sem_reforco: Decimal,
    premissas: PremissasDaGarantiaExterna,
) -> esquemas.ResultadoDoPainel:
    perda = resultado.perda
    total = resultado.antes.valor_das_garantias
    return esquemas.ResultadoDoPainel(
        carteira=esquemas.CarteiraSinteticaResposta(
            sintetica=True, tamanho=tamanho, semente=semente, aviso=AVISO_DA_CARTEIRA_SINTETICA
        ),
        cenario=cenario,
        premissas=esquemas.PremissasResposta(
            choques={tipo: _decimal(valor) for tipo, valor in choques.items()},
            taxa_sem_reforco=_decimal(taxa_sem_reforco),
            custodiantes_indisponiveis=sorted(premissas.custodiantes_indisponiveis),
            dias_extra_de_execucao=premissas.dias_extra_de_execucao,
            desconto_diario_por_ativo={
                tipo: _decimal(valor) for tipo, valor in premissas.desconto_diario_por_ativo.items()
            },
        ),
        antes=_foto(resultado.antes),
        depois=_foto(resultado.depois),
        perda=esquemas.PerdaDoPainelResposta(
            exposicao=perda.exposicao.texto(),
            descoberta_com_execucao_imediata=perda.descoberta_com_execucao_imediata.texto(),
            descoberta_com_premissas_externas=perda.descoberta_com_premissas_externas.texto(),
            perda_com_execucao_imediata=perda.perda_com_execucao_imediata.texto(),
            perda_adicional_externa=perda.perda_adicional_externa.texto(),
            perda_esperada_total=perda.perda_esperada_total.texto(),
        ),
        concentracao_por_ativo=_fatias(resultado.por_ativo, total),
        concentracao_por_custodiante=_fatias(resultado.por_custodiante, total),
    )


def _decimal(valor: Decimal) -> str:
    """Decimal como texto, sem notação científica e com pelo menos duas casas."""
    texto = format(valor.normalize(), "f")
    inteiro, _, casas = texto.partition(".")
    return f"{inteiro}.{casas.ljust(2, '0')}"


def cenario(valor: Cenario) -> esquemas.CenarioDoPainel:
    return esquemas.CenarioDoPainel(
        id=valor.id,
        nome=valor.nome,
        descricao=valor.descricao,
        choques={tipo: _decimal(choque) for tipo, choque in valor.choques.items()},
        taxa_sem_reforco=_decimal(valor.taxa_sem_reforco),
        dias_extra_de_execucao=valor.premissas.dias_extra_de_execucao,
        custodiantes_indisponiveis=sorted(valor.premissas.custodiantes_indisponiveis),
    )


# ------------------------------------------------------------------- negócio
def _decimal_texto(valor: Decimal) -> str:
    """Decimal sem notação científica: `"0.1365"`, nunca `"1.365E-1"`."""
    return format(valor, "f")


def catalogo_do_negocio(
    parametros: ParametrosDaEconomia, cenarios: list[tuple[str, str]]
) -> esquemas.CatalogoDoNegocio:
    return esquemas.CatalogoDoNegocio(
        premissas=[
            esquemas.EsquemaDaPremissaResposta(
                nome=e.nome.value,
                nome_exibicao=e.nome_exibicao,
                tipo=e.tipo.value,
                unidade=e.unidade,
                minimo=_decimal_texto(e.minimo),
                maximo=_decimal_texto(e.maximo),
                razoavel_minimo=None
                if e.razoavel_minimo is None
                else _decimal_texto(e.razoavel_minimo),
                razoavel_maximo=None
                if e.razoavel_maximo is None
                else _decimal_texto(e.razoavel_maximo),
                origem=e.origem,
                fonte=e.fonte,
            )
            for e in parametros.esquema.values()
        ],
        conjuntos=[
            esquemas.ConjuntoResposta(
                id=c.id,
                nome=c.nome,
                descricao=c.descricao,
                cenario_de_risco=c.cenario_de_risco,
                receitas_ligadas=sorted(f.value for f in c.receitas_ligadas),
                valores={n.value: _decimal_texto(v) for n, v in c.valores.items()},
            )
            for c in parametros.conjuntos.values()
        ],
        cenarios_de_risco=[esquemas.CenarioDeRiscoResposta(id=i, nome=n) for i, n in cenarios],
        fontes_de_receita=[f.value for f in FonteDeReceita],
    )


def _visao(visao: Visao) -> esquemas.VisaoResposta:
    return esquemas.VisaoResposta(
        receita=visao.receita.texto(), custo=visao.custo.texto(), margem=visao.margem.texto()
    )


def _opcional(valor: Dinheiro | None) -> str | None:
    return None if valor is None else valor.texto()


def resultado_do_negocio(
    r: ResultadoEconomico,
    esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa],
    cenarios: list[tuple[str, str]],
) -> esquemas.ResultadoDoNegocio:
    p = r.premissas
    base = p.valores()
    perda = r.mes.perda
    nome_do_cenario = dict(cenarios).get(p.cenario_de_risco, p.cenario_de_risco)
    ix = r.interna_x_externa
    return esquemas.ResultadoDoNegocio(
        premissas=esquemas.PremissasUsadas(
            valores={n.value: _decimal_texto(v) for n, v in base.items()},
            receitas_ligadas=sorted(f.value for f in p.receitas_ligadas),
            cenario_de_risco=p.cenario_de_risco,
        ),
        linhas=[
            esquemas.LinhaResposta(
                id=x.id,
                nome=x.nome,
                fonte=None if x.fonte is None else x.fonte.value,
                origem=str(x.origem),
                destino=str(x.destino),
                valor=x.valor.texto(),
                formula=x.formula,
                premissas=[n.value for n in x.premissas],
            )
            for x in r.mes.linhas
        ],
        por_cliente_mes=esquemas.VisoesResposta(
            plataforma=_visao(r.mes.plataforma),
            parceiro=_visao(r.mes.parceiro),
            arranjo=_visao(r.mes.arranjo),
        ),
        por_cliente_ano=esquemas.VisoesResposta(
            plataforma=_visao(r.ano["plataforma"]),
            parceiro=_visao(r.ano["parceiro"]),
            arranjo=_visao(r.ano["arranjo"]),
        ),
        carteira=esquemas.CarteiraDoNegocioResposta(
            clientes_ativos=r.carteira.clientes_ativos,
            plataforma_mes=r.carteira.plataforma_mes.texto(),
            plataforma_ano=r.carteira.plataforma_ano.texto(),
            parceiro_por_parceiro_mes=r.carteira.parceiro_por_parceiro_mes.texto(),
            parceiro_por_parceiro_ano=r.carteira.parceiro_por_parceiro_ano.texto(),
            arranjo_mes=r.carteira.arranjo_mes.texto(),
        ),
        ponto_de_equilibrio=esquemas.PontoDeEquilibrioResposta(
            clientes=r.equilibrio.clientes, motivo=r.equilibrio.motivo
        ),
        perda=esquemas.PerdaDoNegocioResposta(
            cenario=esquemas.CenarioDeRiscoResposta(id=p.cenario_de_risco, nome=nome_do_cenario),
            taxa_base_ao_ano=_decimal_texto(perda.taxa_base_ao_ano.valor),
            frequencia_ao_ano=_decimal_texto(perda.frequencia_ao_ano.valor),
            condicional_interna=_decimal_texto(perda.condicional_interna.valor),
            condicional_externa=_decimal_texto(perda.condicional_externa.valor),
            condicional_mista=_decimal_texto(perda.condicional_mista.valor),
            taxa_ao_ano=_decimal_texto(perda.taxa_ao_ano.valor),
            taxa_ao_mes=_decimal_texto(perda.taxa_ao_mes.valor),
            saldo_financiado=perda.saldo_financiado.texto(),
        ),
        rotativo_no_teto=r.mes.rotativo_no_teto,
        interna_x_externa=esquemas.InternaXExternaResposta(
            margem_interna=ix.margem_interna.texto(),
            margem_externa=ix.margem_externa.texto(),
            custo_a_mais_da_externa=ix.custo_a_mais_da_externa.texto(),
            fee_de_gravame_externo_para_empatar=_opcional(ix.fee_de_gravame_externo_para_empatar),
            motivo_fee=ix.motivo_fee,
            gasto_para_empatar=_opcional(ix.gasto_para_empatar),
            motivo_gasto=ix.motivo_gasto,
            gasto_cabe_no_limite=ix.gasto_cabe_no_limite,
            parceiro_interna=ix.parceiro_interna.texto(),
            parceiro_externa=ix.parceiro_externa.texto(),
        ),
        tornado=[
            esquemas.BarraDoTornadoResposta(
                premissa=b.premissa.value,
                nome_exibicao=esquema[b.premissa].nome_exibicao,
                unidade=esquema[b.premissa].unidade,
                valor_baixo=_decimal_texto(b.valor_baixo),
                valor_alto=_decimal_texto(b.valor_alto),
                valor_base=_decimal_texto(base[b.premissa]),
                margem_no_baixo=b.margem_no_baixo.texto(),
                margem_no_alto=b.margem_no_alto.texto(),
                margem_base=r.mes.plataforma.margem.texto(),
                minimo=b.minimo.texto(),
                maximo=b.maximo.texto(),
                amplitude=b.amplitude.texto(),
                cortado_pela_coerencia=b.cortado_pela_coerencia,
            )
            for b in r.tornado
        ],
        aviso=esquemas.AVISO_DO_NEGOCIO,
    )
