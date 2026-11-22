"""Conversão do domínio para o formato de resposta da API."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

from app.api import esquemas
from app.aplicacao.conciliacao import Divergencia
from app.dominio.conta import ChamadaDeMargem, Compra, Conta, Fatura, Pagamento
from app.dominio.conta import PosicaoDeLimite as PosicaoDoDominio
from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.analises import ResultadoEconomico
from app.dominio.economia.calculo import Visao
from app.dominio.economia.premissas import EsquemaDaPremissa, FonteDeReceita, NomeDaPremissa
from app.dominio.emprestimo import Emprestimo
from app.dominio.garantia import Garantia
from app.dominio.ltv import ltv_efetivo
from app.dominio.parceiro import Parceiro
from app.dominio.politica import PoliticaRisco, TipoAtivo
from app.dominio.regulacao import CatalogoRegulatorio, Instrumento
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
    LancamentoModelo,
    PartidaModelo,
)
from app.infra.carteira_sintetica import Cenario
from app.infra.economia import ParametrosDaEconomia


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


def conta(
    valor: Conta, politica: PoliticaRisco, catalogo: CatalogoRegulatorio, hoje: date
) -> esquemas.ContaResposta:
    em_aberto = valor.fatura_em_aberto
    aberta = valor.chamada_aberta
    return esquemas.ContaResposta(
        id=valor.id,
        cliente_ref=valor.cliente_ref,
        estado=valor.estado.value,
        motivo_bloqueio=valor.motivo_bloqueio.value if valor.motivo_bloqueio else None,
        criada_em=valor.criada_em,
        limite=posicao(valor.posicao(politica, catalogo, hoje)),
        fatura_em_aberto=fatura(em_aberto, valor.divida_faturada, hoje) if em_aberto else None,
        chamada_de_margem=chamada(aberta) if aberta else None,
    )


ALERTA_DO_PENHOR = (
    "Penhor de aplicação financeira de tomador PJ ou MEI em recuperação judicial vai para "
    "conta vinculada (Lei 11.101/2005, art. 49, §5º); a forma fiduciária fica fora da "
    "recuperação. Repasse ao cliente."
)


def _alertas(instrumento: Instrumento) -> list[str]:
    return [ALERTA_DO_PENHOR] if instrumento is Instrumento.PENHOR else []


def _solicitacao(
    id: str,
    tipo: str,
    criada_em: datetime,
    prazo_ate: datetime,
    enviada_em: datetime | None,
    prazo_regulatorio_vencido_em: datetime | None,
) -> esquemas.SolicitacaoResposta:
    return esquemas.SolicitacaoResposta(
        id=id,
        tipo=tipo,
        criada_em=criada_em,
        prazo_ate=prazo_ate,
        enviada_em=enviada_em,
        prazo_regulatorio_vencido=prazo_regulatorio_vencido_em is not None,
    )


def garantia(
    valor: Garantia,
    da_conta: Conta,
    politica: PoliticaRisco,
    catalogo: CatalogoRegulatorio,
    hoje: date,
) -> esquemas.GarantiaResposta:
    gravame = valor.gravame
    solicitacao = valor.solicitacao
    informacoes = valor.informacoes
    valor_de_mercado = da_conta.valor_de_mercado(valor.id)
    ltv = ltv_efetivo(valor, politica, catalogo, hoje)
    return esquemas.GarantiaResposta(
        id=valor.id,
        conta_id=valor.conta_id,
        tipo_ativo=valor.tipo_ativo,
        estado=valor.estado.value,
        produto=valor.produto.value,
        valor_aplicado=valor.valor_aplicado.texto(),
        valor_de_mercado=valor_de_mercado.texto(),
        ltv=str(ltv) if politica.aceita(valor.tipo_ativo) else None,
        limite_gerado=(
            valor_de_mercado.aplicar_razao(ltv) if valor.gera_limite else Dinheiro.zero()
        ).texto(),
        marcada_em=valor.marcada_em,
        criada_em=valor.criada_em,
        gravame=esquemas.GravameResposta(
            estado=gravame.estado.value,
            infraestrutura=gravame.infraestrutura.value,
            instrumento=gravame.instrumento.value,
            trilho=gravame.trilho.value,
            protocolo=gravame.protocolo,
            baixa_prazo=gravame.baixa_prazo,
            motivo=gravame.motivo,
        ),
        contraparte_id=valor.contraparte_id,
        solicitacao=None
        if solicitacao is None
        else _solicitacao(
            solicitacao.id,
            solicitacao.tipo.value,
            solicitacao.criada_em,
            solicitacao.prazo_ate,
            solicitacao.enviada_em,
            solicitacao.prazo_regulatorio_vencido_em,
        ),
        emprestimo_id=valor.emprestimo_id,
        plano=valor.plano,
        fim_da_acumulacao=valor.fim_da_acumulacao,
        informacoes=None
        if informacoes is None
        else esquemas.InformacoesDaOperadoraResposta(
            valor_disponivel=informacoes.valor_disponivel.texto(),
            tributos_informados=informacoes.tributos_informados.texto(),
            valor_liquido=informacoes.valor_liquido.texto(),
            recebidas_em=informacoes.recebidas_em,
        ),
        alertas=_alertas(gravame.instrumento),
    )


def emprestimo(valor: Emprestimo, hoje: date) -> esquemas.EmprestimoResposta:
    return esquemas.EmprestimoResposta(
        id=valor.id,
        conta_id=valor.conta_id,
        estado=valor.estado.value,
        principal=valor.principal.texto(),
        taxa_ao_mes=str(valor.taxa_ao_mes),
        saldo_devedor=(Dinheiro.zero() if valor.encerrado else valor.saldo_devedor(hoje)).texto(),
        contratado_em=valor.contratado_em,
        garantia_ids=list(valor.garantia_ids),
        dias_minimos_execucao=valor.dias_minimos_execucao,
        dias_em_atraso=0 if valor.encerrado else valor.dias_em_atraso(hoje),
        parcelas=[
            esquemas.ParcelaResposta(
                numero=p.numero,
                vencimento=p.vencimento,
                valor=p.valor.texto(),
                juros=p.juros.texto(),
                amortizacao=p.amortizacao.texto(),
                paga_em=p.paga_em,
            )
            for p in valor.parcelas
        ],
    )


def divergencia(valor: Divergencia) -> esquemas.DivergenciaResposta:
    return esquemas.DivergenciaResposta(
        tipo=valor.tipo.value,
        conta_id=valor.conta_id,
        garantia_id=valor.garantia_id,
        emprestimo_id=valor.emprestimo_id,
        detalhe=valor.detalhe,
    )


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
AVISO_SINTETICO = (
    "Os clientes deste painel são sintéticos: foram gerados por sorteio a partir de uma "
    "semente. Não são clientes reais de nenhum parceiro, e os números não estimam risco real."
)
MARCACAO_A_MERCADO = (
    "Todo dia o limite disponível é recalculado com o desconto do ativo. Se o valor da "
    "garantia cair abaixo do usado, as compras novas são bloqueadas e o cliente é chamado a "
    "reforçar a garantia ou pagar antes; se a fatura vencer sem pagamento, a garantia é "
    "executada depois do prazo do contrato (interpretação)."
)
AVISOS_DE_RISCO = (
    "A garantia reduz a perda, não a elimina: continuam o risco do emissor, o de preço, o "
    "de liquidez e o de execução."
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
    regulatorio = resultado.regulatorio
    return esquemas.ResultadoDoPainel(
        aviso_sintetico=AVISO_SINTETICO,
        carteira=esquemas.CarteiraSinteticaResposta(
            sintetica=True, tamanho=tamanho, semente=semente, aviso=AVISO_DA_CARTEIRA_SINTETICA
        ),
        cenario=cenario,
        premissas=esquemas.PremissasResposta(
            choques={tipo: _decimal(valor) for tipo, valor in choques.items()},
            taxa_sem_reforco=_decimal(taxa_sem_reforco),
            custodiantes_indisponiveis=sorted(premissas.custodiantes_indisponiveis),
            atraso_por_onde_esta=dict(sorted(premissas.atraso_por_onde_esta.items())),
            emissores_em_quebra=sorted(premissas.emissores_em_quebra),
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
        concentracao_por_onde_esta=_fatias(resultado.por_custodiante, total),
        regulatorio=esquemas.PainelRegulatorioResposta(
            haircut_por_classe_e_prazo=[
                esquemas.HaircutDaFaixaResposta(
                    tipo_ativo=h.tipo_ativo,
                    ate_anos=h.ate_anos,
                    haircut_minimo=_decimal(h.haircut_minimo),
                    haircut_maximo=_decimal(h.haircut_maximo),
                )
                for h in regulatorio.haircut_por_classe_e_prazo
            ],
            teto_do_fgc=regulatorio.teto_do_fgc.texto(),
            concentracao_por_conglomerado=[
                esquemas.ConcentracaoDeConglomeradoResposta(
                    conglomerado=c.conglomerado,
                    valor=c.valor.texto(),
                    clientes=c.clientes,
                    clientes_acima_do_teto=c.clientes_acima_do_teto,
                    excesso_total=c.excesso_total.texto(),
                )
                for c in regulatorio.concentracao_por_conglomerado
            ],
            carteiras_de_provisao=[
                esquemas.ProvisaoDaCarteiraResposta(
                    carteira=str(p.carteira),
                    contas=p.contas,
                    saldo=p.saldo.texto(),
                    provisao_primeiro_mes=p.provisao_primeiro_mes.texto(),
                    provisao_decimo_terceiro_mes=p.provisao_decimo_terceiro_mes.texto(),
                )
                for p in regulatorio.carteiras_de_provisao
            ],
            marcacao_a_mercado=MARCACAO_A_MERCADO,
            avisos_de_risco=AVISOS_DE_RISCO,
        ),
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
        atraso_por_onde_esta=dict(sorted(valor.premissas.atraso_por_onde_esta.items())),
        emissores_em_quebra=sorted(valor.premissas.emissores_em_quebra),
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
