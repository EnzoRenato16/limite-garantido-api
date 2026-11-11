"""Formato das requisições e respostas da API.

Dinheiro entra e sai como texto decimal com duas casas (`"4250.00"`). Número JSON também
é aceito na entrada, mas texto é o recomendado: não passa por ponto flutuante em nenhum
cliente.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.dominio.politica import TipoAtivo

ValorDeEntrada = Annotated[
    Decimal,
    Field(
        gt=0,
        max_digits=14,
        decimal_places=2,
        description="Valor em reais, com até duas casas decimais",
        examples=["5000.00"],
    ),
]
ValorDeSaida = Annotated[
    str,
    Field(pattern=r"^-?\d+\.\d{2}$", description="Valor em reais", examples=["4250.00"]),
]


class Esquema(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------- contas
class NovaConta(Esquema):
    cliente_ref: str = Field(
        min_length=1,
        max_length=120,
        description="Identificador do cliente no sistema do parceiro. Não envie CPF nem nome.",
        examples=["cliente-8f3a"],
    )


class PosicaoDeLimite(BaseModel):
    valor_das_garantias: ValorDeSaida = Field(
        description="Valor de mercado somado das garantias bloqueadas"
    )
    limite_garantido: ValorDeSaida = Field(
        description="Soma de valor de mercado x LTV de cada garantia, arredondada para baixo"
    )
    limite_total: ValorDeSaida = Field(
        description="Limite da conta: o menor entre o limite garantido e o teto por cliente"
    )
    divida_total: ValorDeSaida = Field(
        description="Saldo devedor: fatura fechada + compras a faturar"
    )
    divida_faturada: ValorDeSaida = Field(
        description="Parte da dívida que já está em fatura fechada"
    )
    limite_disponivel: ValorDeSaida = Field(
        description="Limite ainda não tomado por dívida. Nunca negativo; zero com a conta "
        "bloqueada. Com fatura em atraso as compras também ficam suspensas, mesmo havendo saldo"
    )
    deficit: ValorDeSaida = Field(
        description="Quanto a dívida passa do limite garantido. Maior que zero = chamada de margem"
    )


class FaturaResposta(BaseModel):
    id: str
    conta_id: str
    valor_fechado: ValorDeSaida = Field(description="Valor da fatura no fechamento")
    saldo_em_aberto: ValorDeSaida = Field(description="Quanto falta pagar desta fatura")
    vencimento: date
    estado: str = Field(
        examples=["fechada"], description="fechada, em_atraso, paga ou liquidada_por_execucao"
    )
    dias_em_atraso: int
    fechada_em: datetime
    encerrada_em: datetime | None


class ChamadaDeMargemResposta(BaseModel):
    id: str
    estado: str = Field(examples=["aberta"], description="aberta, atendida ou vencida")
    deficit_inicial: ValorDeSaida
    aberta_em: datetime
    prazo_ate: date = Field(description="Último dia para reforçar a garantia ou pagar")


class ContaResposta(BaseModel):
    id: str = Field(examples=["cta_01JCD3N8X5T9Q0W2R4Y6U8I0PA"])
    cliente_ref: str
    estado: str = Field(examples=["ativa"], description="ativa ou bloqueada")
    motivo_bloqueio: str | None = Field(
        description="chamada_de_margem_vencida ou divida_descoberta, quando bloqueada"
    )
    criada_em: datetime
    limite: PosicaoDeLimite
    fatura_em_aberto: FaturaResposta | None
    chamada_de_margem: ChamadaDeMargemResposta | None


# ---------------------------------------------------------------- garantias
IdDeCustodiante = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9_]{1,40}$",
        description="Id do custodiante externo (veja GET /v1/custodiantes)",
        examples=["custodiante_agil"],
    ),
]


class NovaGarantia(Esquema):
    tipo_ativo: TipoAtivo = Field(description="Tipo do investimento que vira garantia")
    valor: ValorDeEntrada
    custodiante_id: IdDeCustodiante | None = Field(
        default=None,
        description="Só para investimento em outra instituição (fase 2, simulada). Sem ele, "
        "o investimento fica dentro de casa e o bloqueio é imediato.",
    )

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"example": {"tipo_ativo": "cdb", "valor": "5000.00"}}
    )


class SolicitacaoResposta(BaseModel):
    """Pedido em aberto no custodiante externo."""

    id: str = Field(examples=["sol_01JCD3N8X5T9Q0W2R4Y6U8I0PZ"])
    tipo: str = Field(description="gravame, liberacao ou execucao")
    criada_em: datetime
    prazo_ate: datetime = Field(description="Sem resposta até aqui, o pedido expira")
    enviada_em: datetime | None = Field(
        description="Quando o custodiante aceitou o pedido. Nulo enquanto não foi enviado."
    )


class GarantiaResposta(BaseModel):
    id: str = Field(examples=["gar_01JCD3N8X5T9Q0W2R4Y6U8I0PB"])
    conta_id: str
    tipo_ativo: TipoAtivo
    estado: str = Field(
        examples=["bloqueada"],
        description="Interna: pendente, bloqueada, parcialmente_executada, liberada ou "
        "executada. Externa também: solicitada, gravame_pendente, gravame_confirmado, "
        "gravame_recusado, gravame_expirado, liberacao_pendente e execucao_solicitada.",
    )
    valor_aplicado: ValorDeSaida = Field(description="Quanto o cliente aplicou")
    valor_de_mercado: ValorDeSaida = Field(description="Quanto a garantia vale hoje")
    ltv: str | None = Field(examples=["0.85"], description="LTV do ativo na política do parceiro")
    limite_gerado: ValorDeSaida = Field(description="Limite que esta garantia libera hoje")
    marcada_em: date = Field(description="Data da última marcação a mercado")
    criada_em: datetime
    origem: str = Field(
        default="interna",
        description="interna (fase 1) ou externa (custodiante externo, simulado)",
    )
    custodiante_id: str | None = Field(default=None, description="Só na garantia externa")
    solicitacao: SolicitacaoResposta | None = Field(
        default=None, description="Pedido em aberto no custodiante externo, se houver"
    )
    valor_em_execucao: ValorDeSaida | None = Field(
        default=None, description="Quanto foi mandado vender e ainda não teve confirmação"
    )
    motivo: str | None = Field(
        default=None, description="Motivo da última recusa ou falha do custodiante"
    )


class GarantiaComLimite(BaseModel):
    garantia: GarantiaResposta
    limite: PosicaoDeLimite


# ------------------------------------------------------------------ compras
class NovaCompra(Esquema):
    valor: ValorDeEntrada
    descricao: str = Field(
        min_length=1, max_length=200, description="Estabelecimento ou descrição da compra"
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"valor": "1200.00", "descricao": "Mercado Exemplo"}},
    )


class CompraResposta(BaseModel):
    id: str = Field(examples=["cmp_01JCD3N8X5T9Q0W2R4Y6U8I0PC"])
    conta_id: str
    valor: ValorDeSaida
    descricao: str
    estado: str = Field(examples=["aprovada"], description="aprovada ou negada")
    motivo_negacao: str | None
    criada_em: datetime


class CompraComLimite(BaseModel):
    compra: CompraResposta
    limite: PosicaoDeLimite


# ------------------------------------------------------ faturas e pagamentos
class FechamentoDeFatura(Esquema):
    vencimento: date | None = Field(
        default=None,
        description="Data de vencimento. Se omitida, vence em 10 dias corridos.",
        examples=["2026-12-10"],
    )


class NovoPagamento(Esquema):
    valor: ValorDeEntrada

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"valor": "1200.00"}})


class PagamentoResposta(BaseModel):
    id: str = Field(examples=["pag_01JCD3N8X5T9Q0W2R4Y6U8I0PD"])
    conta_id: str
    valor: ValorDeSaida
    valor_na_fatura: ValorDeSaida = Field(description="Parte que abateu a fatura fechada")
    valor_antecipado: ValorDeSaida = Field(description="Parte que antecipou compras a faturar")
    fatura_id: str | None
    criado_em: datetime


class PagamentoComLimite(BaseModel):
    pagamento: PagamentoResposta
    limite: PosicaoDeLimite


# ------------------------------------------------------------------- ledger
class PartidaResposta(BaseModel):
    conta_contabil: str = Field(examples=["recebiveis_a_faturar"])
    sentido: str = Field(examples=["D"], description="D (débito) ou C (crédito)")
    valor: ValorDeSaida
    garantia_id: str | None


class LancamentoResposta(BaseModel):
    id: str = Field(examples=["lan_01JCD3N8X5T9Q0W2R4Y6U8I0PE"])
    tipo: str = Field(examples=["compra"])
    descricao: str
    referencia_id: str = Field(description="Id da garantia, compra, pagamento ou fatura de origem")
    criado_em: datetime
    partidas: list[PartidaResposta]


# ------------------------------------------------------- eventos e webhooks
class EventoResposta(BaseModel):
    id: str = Field(examples=["evt_01JCD3N8X5T9Q0W2R4Y6U8I0PF"])
    tipo: str = Field(examples=["compra.aprovada"])
    conta_id: str
    dados: dict[str, Any]
    ocorrido_em: datetime


class ConfiguracaoDeWebhook(Esquema):
    url: HttpUrl = Field(description="Para onde os eventos serão enviados (POST)")
    rotacionar_segredo: bool = Field(
        default=False, description="Gera um segredo de assinatura novo e invalida o anterior"
    )


class WebhookResposta(BaseModel):
    url: str
    segredo: str = Field(
        description="Segredo HMAC para conferir a assinatura. Guarde: é com ele que você valida."
    )


class EntregaResposta(BaseModel):
    id: str = Field(examples=["ent_01JCD3N8X5T9Q0W2R4Y6U8I0PG"])
    evento_id: str
    evento_tipo: str
    url: str
    estado: str = Field(examples=["entregue"], description="pendente, entregue ou morta")
    tentativas: int
    proxima_tentativa_em: datetime | None
    ultimo_status_http: int | None
    ultimo_erro: str | None
    entregue_em: datetime | None
    criado_em: datetime


# -------------------------------------------------------------------- risco
class PoliticaDeRisco(Esquema):
    ltv_por_ativo: dict[TipoAtivo, Decimal] = Field(
        description="LTV de cada ativo aceito, entre 0 (exclusivo) e 1",
        examples=[{"cdb": "0.85", "tesouro_selic": "0.95"}],
    )
    ordem_de_execucao: list[TipoAtivo] = Field(
        description="Ordem de venda na execução: os mais líquidos primeiro"
    )
    dias_para_execucao: int = Field(ge=1, examples=[7])
    prazo_chamada_de_margem_dias: int = Field(ge=1, examples=[5])
    teto_limite_por_cliente: Decimal | None = Field(default=None, examples=["50000.00"])
    teto_limite_do_parceiro: Decimal | None = Field(default=None, examples=["5000000.00"])


class ParceiroResposta(BaseModel):
    id: str
    nome: str
    ativo: bool
    politica: dict[str, Any]
    webhook_url: str | None
    limite_de_requisicoes_por_minuto: int
    criado_em: datetime


class NovoParceiro(Esquema):
    nome: str = Field(min_length=1, max_length=120, examples=["Fintech Exemplo"])
    politica: PoliticaDeRisco | None = Field(
        default=None, description="Se omitida, usa a política de referência da plataforma"
    )
    limite_de_requisicoes_por_minuto: int | None = Field(default=None, ge=1)


class ParceiroCriado(BaseModel):
    parceiro: ParceiroResposta
    api_key: str = Field(
        description="Aparece só nesta resposta. A plataforma guarda apenas o hash."
    )


class SimulacaoDeStress(Esquema):
    choques: dict[TipoAtivo, Decimal] = Field(
        description="Variação de preço por tipo de ativo: -0.30 é queda de 30%",
        examples=[{"acoes": "-0.30", "tesouro_prefixado": "-0.10"}],
    )
    probabilidade_de_inadimplencia: Decimal = Field(
        default=Decimal(1),
        ge=0,
        le=1,
        description="Fração das contas descobertas que deixariam de pagar. 1 é o pior caso.",
    )
    detalhar_contas: bool = Field(default=False, description="Inclui as contas afetadas")


class ContaNoStress(BaseModel):
    conta_id: str
    divida: ValorDeSaida
    valor_das_garantias_antes: ValorDeSaida
    valor_das_garantias_depois: ValorDeSaida
    deficit_de_margem: ValorDeSaida
    exposicao_descoberta: ValorDeSaida
    nova_chamada_de_margem: bool


class ResultadoDoStress(BaseModel):
    contas_analisadas: int
    contas_em_chamada_antes: int
    contas_em_chamada_depois: int
    novas_chamadas_de_margem: int
    divida_total: ValorDeSaida
    valor_das_garantias_antes: ValorDeSaida
    valor_das_garantias_depois: ValorDeSaida
    deficit_de_margem_total: ValorDeSaida = Field(
        description="Quanto de garantia ou pagamento seria pedido nas chamadas de margem"
    )
    exposicao_descoberta_total: ValorDeSaida = Field(
        description="Dívida que não seria paga nem vendendo toda a garantia ao preço estressado"
    )
    perda_esperada: ValorDeSaida = Field(
        description="Exposição descoberta x probabilidade de inadimplência"
    )
    contas: list[ContaNoStress] | None = None


class ResumoDaRotina(BaseModel):
    contas_processadas: int
    contas_com_erro: int
    eventos_gerados: int


class VerificacaoDoLedger(BaseModel):
    fecha: bool = Field(description="Verdadeiro quando débitos e créditos somam o mesmo valor")
    total_de_debitos: ValorDeSaida
    total_de_creditos: ValorDeSaida
    lancamentos: int
    lancamentos_desbalanceados: int


# ------------------------------------------------------------------ sandbox
class RemarcacaoNoSandbox(Esquema):
    variacao_percentual: Decimal | None = Field(
        default=None, ge=-100, le=1000, description="-10 derruba o ativo em 10%", examples=["-10"]
    )
    valor_de_mercado: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2, examples=["4500.00"]
    )

    @model_validator(mode="after")
    def _exatamente_um(self) -> RemarcacaoNoSandbox:
        if (self.variacao_percentual is None) == (self.valor_de_mercado is None):
            raise ValueError("Informe variacao_percentual ou valor_de_mercado, um dos dois")
        return self


class AvancoDeDias(Esquema):
    dias: int = Field(ge=1, le=365, description="Quantos dias fazer passar para esta conta")


class CatalogoDeErro(BaseModel):
    codigo: str
    status: int
    titulo: str
    descricao: str


# -------------------------------------------------------------- custódia
class CustodianteResposta(BaseModel):
    id: str = Field(examples=["custodiante_agil"])
    nome: str = Field(examples=["Custodiante Ágil (simulador)"])
    descricao: str
    simulado: bool = Field(
        description="Sempre true neste projeto: não há integração com custodiante real"
    )
    prazo_de_resposta_minutos: int = Field(
        description="Sem resposta neste prazo, o pedido expira e o parceiro é avisado"
    )


class CallbackDoCustodiante(Esquema):
    """Resposta do custodiante a um pedido. Chega assinada (HMAC-SHA256)."""

    evento_id: str = Field(
        pattern=r"^[A-Za-z0-9_.:-]{1,80}$",
        description="Id do evento do lado do custodiante. A mesma resposta enviada de novo "
        "(ou já obtida por consulta) tem o mesmo id e não é aplicada duas vezes.",
    )
    solicitacao_id: str = Field(
        pattern=r"^[A-Za-z0-9_]{1,40}$", description="Id do pedido que está sendo respondido"
    )
    status: Literal["confirmada", "recusada"]
    valor_obtido: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=14,
        decimal_places=2,
        description="Só na confirmação de execução: quanto a venda rendeu",
    )
    motivo: str | None = Field(default=None, max_length=200, description="Motivo da recusa")

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "evento_id": "evc_3f9a1c7d2b8e4a6f0c1d2e3f4a",
                "solicitacao_id": "sol_01JCD3N8X5T9Q0W2R4Y6U8I0PZ",
                "status": "confirmada",
            }
        },
    )


class ResultadoDoCallback(BaseModel):
    resultado: Literal["aplicada", "duplicada", "obsoleta"] = Field(
        description="aplicada: mudou a garantia. duplicada: já recebida antes. obsoleta: o "
        "pedido não esperava mais esta resposta (expirou ou foi encerrado); nada mudou."
    )


class DecisaoNoSandbox(Esquema):
    acao: Literal["confirmar", "recusar", "nao_responder"] = Field(
        description="O que o custodiante simulado faz com o pedido em aberto. "
        "nao_responder vence o prazo agora e expira o pedido."
    )
    valor_obtido: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=14,
        decimal_places=2,
        description="Só ao confirmar uma execução: quanto a venda rendeu. Sem ele, usa o "
        "preço do perfil do custodiante.",
    )
