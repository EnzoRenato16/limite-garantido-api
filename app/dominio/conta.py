"""Conta de um cliente do parceiro: o agregado onde moram as regras de crédito.

A conta não guarda saldo. Dívida e valor das garantias são lidos de `Saldos`, que por
sua vez é a soma das partidas do ledger. Toda operação que mexe com dinheiro cria um
`Lancamento` balanceado e o aplica nos saldos; quem persiste a conta grava os
lançamentos, os eventos e o que mais tiver mudado, tudo na mesma transação.

O limite do cartão só vem de garantia de produto cartão com gravame `registrado` e sem
baixa pedida. O LTV de cada garantia é o menor entre o da política do parceiro e o teto
do catálogo regulatório para o ativo e o prazo até o vencimento.

Este módulo não conhece banco, HTTP, relógio nem calendário: `agora`, `hoje`, o catálogo
regulatório e os prazos já calculados em dias úteis (`prazo_ate`, `prazo_da_baixa`)
chegam de fora.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.emprestimo import (
    Emprestimo,
    EstadoEmprestimo,
    cronograma_price,
    valor_maximo_do_emprestimo,
)
from app.dominio.erros import (
    CombinacaoNaoPermitida,
    EmprestimoNaoEncontrado,
    EmprestimoNegado,
    FaturaEmAberto,
    GarantiaCobrindoDivida,
    GarantiaNaoEncontrada,
    PagamentoExcedeDivida,
    SemSaldoParaFaturar,
    TransicaoInvalida,
    ValorInvalido,
    VencimentoInvalido,
)
from app.dominio.eventos import EventoDominio, TipoEvento
from app.dominio.garantia import (
    Garantia,
    InformacoesDaOperadora,
    Solicitacao,
    TipoSolicitacao,
)
from app.dominio.gravame import EstadoGravame, Gravame
from app.dominio.ids import novo_id
from app.dominio.ledger import (
    ContaContabil,
    Lancamento,
    Partida,
    Saldos,
    TipoLancamento,
    credito,
    debito,
)
from app.dominio.ltv import ltv_efetivo
from app.dominio.politica import PoliticaRisco, TipoAtivo
from app.dominio.regulacao import CatalogoRegulatorio, Instrumento, Produto, Trilho
from app.dominio.tributos import ResultadoDaExecucao, retencao_na_execucao

# Res. Conjunta 12, art. 6º, §2º, IV: aviso aos 45 dias de atraso e 10 dias antes da
# liquidação. {dia_corrido}
_AVISO_INICIAL = 45
_AVISO_FINAL = 10

TIPOS_DO_TESOURO = frozenset(
    {TipoAtivo.TESOURO_SELIC, TipoAtivo.TESOURO_PREFIXADO, TipoAtivo.TESOURO_IPCA}
)

_EVENTO_DE_ENTRADA_SOLICITADA = {
    Trilho.PORTABILIDADE_CVM_210: TipoEvento.PORTABILIDADE_SOLICITADA,
    Trilho.TRANSFERENCIA_DE_CUSTODIA: TipoEvento.TRANSFERENCIA_SOLICITADA,
}
_EVENTO_DE_ENTRADA_CONCLUIDA = {
    Trilho.PORTABILIDADE_CVM_210: TipoEvento.PORTABILIDADE_CONCLUIDA,
    Trilho.TRANSFERENCIA_DE_CUSTODIA: TipoEvento.TRANSFERENCIA_CONCLUIDA,
}


class EstadoConta(StrEnum):
    ATIVA = "ativa"
    BLOQUEADA = "bloqueada"


class MotivoBloqueio(StrEnum):
    CHAMADA_DE_MARGEM_VENCIDA = "chamada_de_margem_vencida"
    DIVIDA_DESCOBERTA = "divida_descoberta"


class EstadoFatura(StrEnum):
    FECHADA = "fechada"
    EM_ATRASO = "em_atraso"
    PAGA = "paga"
    LIQUIDADA_POR_EXECUCAO = "liquidada_por_execucao"


class EstadoChamada(StrEnum):
    ABERTA = "aberta"
    ATENDIDA = "atendida"
    VENCIDA = "vencida"


class EstadoCompra(StrEnum):
    APROVADA = "aprovada"
    NEGADA = "negada"


class MotivoNegacao(StrEnum):
    LIMITE_INSUFICIENTE = "limite_insuficiente"
    CONTA_BLOQUEADA = "conta_bloqueada"
    FATURA_EM_ATRASO = "fatura_em_atraso"
    TETO_DO_PARCEIRO_EXCEDIDO = "teto_do_parceiro_excedido"


@dataclass(slots=True)
class Fatura:
    id: str
    conta_id: str
    # Valor no fechamento. É documento, não saldo: o saldo em aberto vem do ledger.
    valor_fechado: Dinheiro
    fechada_em: datetime
    vencimento: date
    estado: EstadoFatura = EstadoFatura.FECHADA
    encerrada_em: datetime | None = None

    @property
    def em_aberto(self) -> bool:
        return self.estado in (EstadoFatura.FECHADA, EstadoFatura.EM_ATRASO)

    def dias_em_atraso(self, hoje: date) -> int:
        """{dia_corrido} depois do vencimento. Zero até o dia do vencimento."""
        return max((hoje - self.vencimento).days, 0)


@dataclass(slots=True)
class ChamadaDeMargem:
    id: str
    conta_id: str
    deficit_inicial: Dinheiro
    aberta_em: datetime
    # Último dia para reforçar a garantia ou pagar.
    prazo_ate: date
    estado: EstadoChamada = EstadoChamada.ABERTA
    encerrada_em: datetime | None = None


@dataclass(frozen=True, slots=True)
class Compra:
    id: str
    conta_id: str
    valor: Dinheiro
    descricao: str
    estado: EstadoCompra
    motivo_negacao: MotivoNegacao | None
    criada_em: datetime

    @property
    def aprovada(self) -> bool:
        return self.estado is EstadoCompra.APROVADA


@dataclass(frozen=True, slots=True)
class Pagamento:
    id: str
    conta_id: str
    valor: Dinheiro
    # Parte que abateu a fatura fechada e parte que antecipou compras ainda não faturadas.
    valor_na_fatura: Dinheiro
    valor_antecipado: Dinheiro
    fatura_id: str | None
    criado_em: datetime


@dataclass(frozen=True, slots=True)
class PosicaoDeLimite:
    """Foto do limite do cartão de uma conta. Tudo em {BRL}."""

    # Soma do valor de mercado (título + cupons vinculados) das garantias do cartão
    # ainda oneradas.
    valor_das_garantias: Dinheiro
    # Soma de (valor de mercado x LTV efetivo) de cada garantia vigente, cada parcela
    # arredondada para baixo.
    limite_garantido: Dinheiro
    # O menor entre o limite garantido e o teto por cliente.
    limite_total: Dinheiro
    divida_total: Dinheiro
    divida_faturada: Dinheiro
    # Quanto ainda dá para comprar. Nunca negativo; zero se a conta estiver bloqueada.
    limite_disponivel: Dinheiro
    # Quanto a dívida passa do limite garantido. Maior que zero = chamada de margem.
    deficit: Dinheiro

    def para_dados(self) -> dict[str, object]:
        return {
            "valor_das_garantias": self.valor_das_garantias.texto(),
            "limite_garantido": self.limite_garantido.texto(),
            "limite_total": self.limite_total.texto(),
            "divida_total": self.divida_total.texto(),
            "divida_faturada": self.divida_faturada.texto(),
            "limite_disponivel": self.limite_disponivel.texto(),
            "deficit": self.deficit.texto(),
        }


@dataclass(slots=True)
class Pendencias:
    """O que a conta produziu e ainda não foi persistido."""

    lancamentos: list[Lancamento] = field(default_factory=list)
    eventos: list[EventoDominio] = field(default_factory=list)
    compras: list[Compra] = field(default_factory=list)
    pagamentos: list[Pagamento] = field(default_factory=list)

    def limpar(self) -> None:
        self.lancamentos.clear()
        self.eventos.clear()
        self.compras.clear()
        self.pagamentos.clear()


class Conta:
    def __init__(
        self,
        *,
        id: str,
        parceiro_id: str,
        cliente_ref: str,
        criada_em: datetime,
        estado: EstadoConta = EstadoConta.ATIVA,
        motivo_bloqueio: MotivoBloqueio | None = None,
        garantias: Iterable[Garantia] = (),
        faturas: Iterable[Fatura] = (),
        chamadas: Iterable[ChamadaDeMargem] = (),
        emprestimos: Iterable[Emprestimo] = (),
        saldos: Saldos | None = None,
        versao: int = 0,
    ) -> None:
        self.id = id
        self.parceiro_id = parceiro_id
        self.cliente_ref = cliente_ref
        self.criada_em = criada_em
        self.estado = estado
        self.motivo_bloqueio = motivo_bloqueio
        self.garantias: list[Garantia] = list(garantias)
        self.faturas: list[Fatura] = list(faturas)
        self.chamadas: list[ChamadaDeMargem] = list(chamadas)
        self.emprestimos: list[Emprestimo] = list(emprestimos)
        self.saldos = saldos or Saldos()
        self.versao = versao
        self.pendencias = Pendencias()

    @classmethod
    def abrir(cls, *, parceiro_id: str, cliente_ref: str, agora: datetime) -> Conta:
        return cls(
            id=novo_id("cta"), parceiro_id=parceiro_id, cliente_ref=cliente_ref, criada_em=agora
        )

    # ============================================================= consultas
    @property
    def divida_a_faturar(self) -> Dinheiro:
        return self.saldos.saldo(ContaContabil.RECEBIVEIS_A_FATURAR)

    @property
    def divida_faturada(self) -> Dinheiro:
        return self.saldos.saldo(ContaContabil.RECEBIVEIS_FATURADOS)

    @property
    def divida_total(self) -> Dinheiro:
        """Dívida do cartão {BRL}."""
        return self.divida_a_faturar + self.divida_faturada

    @property
    def fatura_em_aberto(self) -> Fatura | None:
        return next((fatura for fatura in self.faturas if fatura.em_aberto), None)

    @property
    def chamada_aberta(self) -> ChamadaDeMargem | None:
        return next((c for c in self.chamadas if c.estado is EstadoChamada.ABERTA), None)

    @property
    def garantias_vigentes(self) -> list[Garantia]:
        """Gravame registrado e sem baixa pedida, de qualquer produto."""
        return [garantia for garantia in self.garantias if garantia.gravame.vigente]

    def gravames_com_baixa_pendente(self) -> list[Garantia]:
        """Baixa pedida e ainda não confirmada: o worker a faz na infraestrutura simulada."""
        return [garantia for garantia in self.garantias if garantia.gravame.baixa_pendente]

    def garantia(self, garantia_id: str) -> Garantia:
        for garantia in self.garantias:
            if garantia.id == garantia_id:
                return garantia
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)

    def emprestimo(self, emprestimo_id: str) -> Emprestimo:
        for emprestimo in self.emprestimos:
            if emprestimo.id == emprestimo_id:
                return emprestimo
        raise EmprestimoNaoEncontrado("Empréstimo não encontrado", emprestimo_id=emprestimo_id)

    def valor_maximo_do_emprestimo(
        self, politica: PoliticaRisco, catalogo: CatalogoRegulatorio, hoje: date
    ) -> Dinheiro:
        """Quanto as garantias disponíveis sustentam de novo empréstimo {BRL}."""
        return valor_maximo_do_emprestimo(
            self.garantias, politica, catalogo, hoje, valor_de=lambda g: self.valor_de_mercado(g.id)
        )

    def valor_do_titulo(self, garantia_id: str) -> Dinheiro:
        """Valor de mercado do ativo em si {BRL}."""
        return self.saldos.saldo(ContaContabil.GARANTIAS_CUSTODIADAS, garantia_id)

    def cupons_vinculados(self, garantia_id: str) -> Dinheiro:
        return self.saldos.saldo(ContaContabil.CUPONS_VINCULADOS, garantia_id)

    def valor_de_mercado(self, garantia_id: str) -> Dinheiro:
        """Valor da garantia {BRL}: o título mais os cupons vinculados a ele."""
        return self.valor_do_titulo(garantia_id) + self.cupons_vinculados(garantia_id)

    def em_atraso(self, hoje: date) -> bool:
        fatura = self.fatura_em_aberto
        return fatura is not None and fatura.dias_em_atraso(hoje) > 0

    def posicao(
        self, politica: PoliticaRisco, catalogo: CatalogoRegulatorio, hoje: date
    ) -> PosicaoDeLimite:
        valor_das_garantias = Dinheiro.zero()
        limite_garantido = Dinheiro.zero()
        # Garantia liberada para excussão: não gera limite, mas ainda está onerada e
        # cobre a dívida enquanto a venda não termina. Sem isso, a excussão abriria uma
        # chamada de margem contra o próprio cliente que está sendo executado.
        cobertura_em_execucao = Dinheiro.zero()
        for garantia in self.garantias:
            if garantia.produto is not Produto.CARTAO or not garantia.cobre_divida:
                continue
            valor = self.valor_de_mercado(garantia.id)
            valor_das_garantias += valor
            # {BRL} x LTV {1} -> {BRL}; cada parcela arredonda para baixo.
            limite = valor.aplicar_razao(ltv_efetivo(garantia, politica, catalogo, hoje))
            if garantia.gera_limite:
                limite_garantido += limite
            else:
                cobertura_em_execucao += limite

        limite_total = limite_garantido
        if politica.teto_limite_por_cliente is not None:
            limite_total = min(limite_total, politica.teto_limite_por_cliente)

        divida = self.divida_total
        disponivel = (limite_total - divida).nao_negativo()
        if self.estado is EstadoConta.BLOQUEADA:
            disponivel = Dinheiro.zero()
        return PosicaoDeLimite(
            valor_das_garantias=valor_das_garantias,
            limite_garantido=limite_garantido,
            limite_total=limite_total,
            divida_total=divida,
            divida_faturada=self.divida_faturada,
            limite_disponivel=disponivel,
            deficit=(divida - limite_garantido - cobertura_em_execucao).nao_negativo(),
        )

    # ======================================================= entrada de garantia
    def custodiar_na_casa(
        self,
        *,
        tipo: TipoAtivo,
        valor: Dinheiro,
        produto: Produto,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        instrumento: Instrumento | None = None,
        emissor: str | None = None,
        vencimento: date | None = None,
        liquidez_dias: int | None = None,
        protocolo: str | None = None,
        plano: str | None = None,
        fim_da_acumulacao: date | None = None,
        aplicado_em: date | None = None,
    ) -> Garantia:
        """Ativo custodiado na CTVM do grupo (ou cotas da DTVM do grupo).

        Com `protocolo`, o gravame já foi registrado na infraestrutura (simulada) e a
        garantia gera limite na hora. Sem ele (modo assíncrono), fica `solicitado`, sem
        limite, até `concluir_entrada`.
        """
        if protocolo is not None and not protocolo.strip():
            raise ValorInvalido("O registro do gravame exige protocolo")
        garantia = self._nova_garantia(
            trilho=Trilho.CASA,
            tipo=tipo,
            valor=valor,
            produto=produto,
            politica=politica,
            catalogo=catalogo,
            agora=agora,
            hoje=hoje,
            instrumento=instrumento,
            emissor=emissor,
            vencimento=vencimento,
            liquidez_dias=liquidez_dias,
            plano=plano,
            fim_da_acumulacao=fim_da_acumulacao,
            contraparte_id=None,
            ja_em_garantia=False,
            aplicado_em=aplicado_em,
        )
        if protocolo is not None:
            try:
                self._registrar(garantia, protocolo, politica, catalogo, agora, hoje)
            except Exception:
                # Nenhuma operação que falha deixa o agregado alterado.
                self.garantias.remove(garantia)
                raise
        else:
            self._emitir(TipoEvento.GRAVAME_SOLICITADO, agora, **self._dados(garantia))
        return garantia

    def receber_de_fora(
        self,
        *,
        trilho: Trilho,
        tipo: TipoAtivo,
        valor: Dinheiro,
        produto: Produto,
        contraparte_id: str,
        prazo_ate: datetime,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        instrumento: Instrumento | None = None,
        emissor: str | None = None,
        vencimento: date | None = None,
        liquidez_dias: int | None = None,
        plano: str | None = None,
        fim_da_acumulacao: date | None = None,
        aplicado_em: date | None = None,
        ja_em_garantia: bool = False,
    ) -> Garantia:
        """Pede a entrada por um trilho de fora; a garantia nasce `solicitada`, sem limite.

        `prazo_ate` é o prazo do trilho, já contado em dias úteis por quem chama.
        """
        if trilho is Trilho.CASA:
            raise ValorInvalido("Custódia na casa não é trilho de fora: use custodiar_na_casa")
        if prazo_ate <= agora:
            raise ValorInvalido("O prazo da contraparte precisa ficar no futuro")
        garantia = self._nova_garantia(
            trilho=trilho,
            tipo=tipo,
            valor=valor,
            produto=produto,
            politica=politica,
            catalogo=catalogo,
            agora=agora,
            hoje=hoje,
            instrumento=instrumento,
            emissor=emissor,
            vencimento=vencimento,
            liquidez_dias=liquidez_dias,
            plano=plano,
            fim_da_acumulacao=fim_da_acumulacao,
            contraparte_id=contraparte_id,
            ja_em_garantia=ja_em_garantia,
            aplicado_em=aplicado_em,
        )
        garantia.solicitacao = Solicitacao(
            id=novo_id("sol"), tipo=TipoSolicitacao.ENTRADA, criada_em=agora, prazo_ate=prazo_ate
        )
        evento = _EVENTO_DE_ENTRADA_SOLICITADA.get(trilho, TipoEvento.GRAVAME_SOLICITADO)
        self._emitir(evento, agora, **self._dados(garantia))
        return garantia

    def _nova_garantia(
        self,
        *,
        trilho: Trilho,
        tipo: TipoAtivo,
        valor: Dinheiro,
        produto: Produto,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        instrumento: Instrumento | None,
        emissor: str | None,
        vencimento: date | None,
        liquidez_dias: int | None,
        plano: str | None,
        fim_da_acumulacao: date | None,
        contraparte_id: str | None,
        ja_em_garantia: bool,
        aplicado_em: date | None,
    ) -> Garantia:
        if not valor.positivo:
            raise ValorInvalido("O valor da garantia precisa ser positivo")
        if aplicado_em is not None and aplicado_em > hoje:
            raise ValorInvalido("A data da aplicação não pode ficar no futuro")
        # Antes de tudo, o catálogo: previdência, LCI ou ações no cartão param aqui.
        catalogo.exigir_combinacao(
            tipo,
            trilho,
            produto,
            liquidez_dias=liquidez_dias,
            plano=plano,
            ja_em_garantia=ja_em_garantia,
        )
        politica.ltv(tipo)  # levanta AtivoNaoAceito se o parceiro não aceita o ativo
        regra = catalogo.regra(tipo)
        escolhido = instrumento or regra.instrumentos[0]
        if escolhido not in regra.instrumentos:
            aceitos = ", ".join(regra.instrumentos)
            raise ValorInvalido(
                f"{regra.nome} não aceita {escolhido}; aceita {aceitos}",
                instrumento=str(escolhido),
                tipo_ativo=str(tipo),
            )
        garantia = Garantia(
            id=novo_id("gar"),
            conta_id=self.id,
            tipo_ativo=tipo,
            valor_aplicado=valor,
            criada_em=agora,
            marcada_em=hoje,
            produto=produto,
            gravame=Gravame.solicitar(
                infraestrutura=regra.infraestrutura,
                instrumento=escolhido,
                trilho=trilho,
                agora=agora,
            ),
            emissor=emissor,
            vencimento=vencimento,
            liquidez_dias=liquidez_dias,
            contraparte_id=contraparte_id,
            plano=plano,
            fim_da_acumulacao=fim_da_acumulacao,
            aplicado_em=aplicado_em,
        )
        self.garantias.append(garantia)
        return garantia

    def concluir_entrada(
        self,
        garantia_id: str,
        protocolo: str,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
    ) -> Garantia:
        """O ativo chegou (ou a casa confirmou) e o gravame foi registrado: gera limite."""
        garantia = self.garantia(garantia_id)
        if garantia.estado is not EstadoGravame.SOLICITADO:
            raise TransicaoInvalida(
                f"Garantia {garantia.id} está {garantia.estado}: não há entrada a concluir",
                garantia_id=garantia.id,
                estado_atual=str(garantia.estado),
            )
        if garantia.trilho is Trilho.RES_CONJUNTA_12 and garantia.informacoes is None:
            raise TransicaoInvalida(
                "Previdência só registra o bloqueio depois das informações da operadora",
                garantia_id=garantia.id,
            )
        solicitacao = garantia.solicitacao
        self._registrar(garantia, protocolo, politica, catalogo, agora, hoje)
        # O evento de conclusão só sai depois do registro dar certo, com o estado novo.
        evento = _EVENTO_DE_ENTRADA_CONCLUIDA.get(garantia.trilho)
        if evento is not None:
            self._emitir(evento, agora, **self._dados(garantia, solicitacao))
        return garantia

    def recusar_entrada(
        self, garantia_id: str, motivo: str, catalogo: CatalogoRegulatorio, agora: datetime
    ) -> Garantia:
        """A contraparte recusou: `recusado` no TD Garantia, `cancelado` nos demais.

        Trilho que não pode recusar (casa, Res. Conjunta 12, art. 18) levanta
        `TransicaoInvalida`.
        """
        garantia = self.garantia(garantia_id)
        if not catalogo.trilhos[garantia.trilho].pode_recusar:
            raise TransicaoInvalida(
                f"O trilho {garantia.trilho} não admite recusa",
                garantia_id=garantia.id,
                trilho=str(garantia.trilho),
            )
        solicitacao = garantia.solicitacao
        if garantia.trilho is Trilho.TD_GARANTIA:
            garantia.gravame.recusar(agora, motivo)
            tipo = TipoEvento.GRAVAME_RECUSADO
        else:
            garantia.gravame.cancelar(agora, motivo)
            tipo = TipoEvento.GRAVAME_CANCELADO
        garantia.solicitacao = None
        self._emitir(tipo, agora, motivo=motivo, **self._dados(garantia, solicitacao))
        return garantia

    def registrar_envio_do_pedido(self, garantia_id: str, agora: datetime) -> Garantia:
        """A contraparte aceitou o pedido de entrada (a resposta ainda vem depois)."""
        garantia = self.garantia(garantia_id)
        solicitacao = garantia.solicitacao
        if solicitacao is None:
            raise TransicaoInvalida("Não há pedido de entrada em aberto", garantia_id=garantia.id)
        if solicitacao.enviada_em is None:
            solicitacao.enviada_em = agora
        return garantia

    def marcar_prazo_regulatorio_vencido(self, garantia_id: str, agora: datetime) -> Garantia:
        """O trilho tem prazo regulatório (portabilidade, Res. Conj. 12) e passou sem resposta.

        Não cancela: a obrigação continua com a contraparte, e o pedido segue em aberto,
        marcado. Marcar de novo não repete o evento.
        """
        garantia = self.garantia(garantia_id)
        solicitacao = garantia.solicitacao
        if solicitacao is None or garantia.estado is not EstadoGravame.SOLICITADO:
            raise TransicaoInvalida(
                "Não há pedido de entrada em aberto para marcar",
                garantia_id=garantia.id,
                estado_atual=str(garantia.estado),
            )
        if solicitacao.prazo_regulatorio_vencido:
            return garantia
        solicitacao.prazo_regulatorio_vencido_em = agora
        self._emitir(TipoEvento.PRAZO_REGULATORIO_VENCIDO, agora, **self._dados(garantia))
        return garantia

    def cancelar_pedido(self, garantia_id: str, motivo: str, agora: datetime) -> Garantia:
        """Desistência do cliente ou prazo vencido: o pedido de entrada é encerrado."""
        garantia = self.garantia(garantia_id)
        solicitacao = garantia.solicitacao
        garantia.gravame.cancelar(agora, motivo)
        garantia.solicitacao = None
        self._emitir(
            TipoEvento.GRAVAME_CANCELADO, agora, motivo=motivo, **self._dados(garantia, solicitacao)
        )
        return garantia

    def registrar_informacoes_da_operadora(
        self, garantia_id: str, informacoes: InformacoesDaOperadora, agora: datetime
    ) -> Garantia:
        garantia = self.garantia(garantia_id)
        if garantia.trilho is not Trilho.RES_CONJUNTA_12:
            raise TransicaoInvalida(
                "Informações da operadora só existem no trilho da Res. Conjunta 12",
                garantia_id=garantia.id,
            )
        if garantia.estado is not EstadoGravame.SOLICITADO:
            raise TransicaoInvalida(
                f"Garantia {garantia.id} está {garantia.estado}",
                garantia_id=garantia.id,
                estado_atual=str(garantia.estado),
            )
        garantia.informacoes = informacoes
        self._emitir(
            TipoEvento.PREVIDENCIA_INFORMACOES_RECEBIDAS,
            agora,
            valor_disponivel=informacoes.valor_disponivel.texto(),
            tributos_informados=informacoes.tributos_informados.texto(),
            **self._dados(garantia),
        )
        return garantia

    def _registrar(
        self,
        garantia: Garantia,
        protocolo: str,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
    ) -> None:
        antes = self.posicao(politica, catalogo, hoje)
        garantia.gravame.registrar(agora, protocolo)
        garantia.solicitacao = None
        garantia.marcada_em = hoje
        # Na previdência, vale o que a operadora informou, líquido dos tributos que ela
        # informou (o lado seguro); nos demais, o aplicado.
        informacoes = garantia.informacoes
        valor = informacoes.valor_liquido if informacoes else garantia.valor_aplicado
        if valor.positivo:
            self._lancar(
                TipoLancamento.BLOQUEIO_GARANTIA,
                garantia.id,
                f"Gravame de {garantia.tipo_ativo} registrado ({protocolo})",
                agora,
                debito(ContaContabil.GARANTIAS_CUSTODIADAS, valor, garantia.id),
                credito(ContaContabil.GARANTIAS_A_DEVOLVER, valor, garantia.id),
            )
        ltv = ltv_efetivo(garantia, politica, catalogo, hoje)
        limite = valor.aplicar_razao(ltv) if garantia.produto is Produto.CARTAO else Dinheiro(0)
        self._emitir(
            TipoEvento.GRAVAME_REGISTRADO,
            agora,
            protocolo=protocolo,
            infraestrutura=str(garantia.gravame.infraestrutura),
            instrumento=str(garantia.gravame.instrumento),
            valor_de_mercado=valor.texto(),
            ltv=str(ltv),
            limite_gerado=limite.texto(),
            **self._dados(garantia),
        )
        self._reavaliar(politica, catalogo, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, catalogo, hoje, "gravame_registrado", agora)

    # ========================================================= vida da garantia
    def remarcar(
        self,
        garantia_id: str,
        novo_valor: Dinheiro,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
    ) -> Garantia:
        """Marcação a mercado do título: ajusta o valor e reavalia a margem."""
        garantia = self.garantia(garantia_id)
        self._exigir_vigente(garantia, "remarcar")
        if novo_valor.negativo:
            raise ValorInvalido("O valor de mercado não pode ser negativo")
        antes = self.posicao(politica, catalogo, hoje)
        atual = self.valor_do_titulo(garantia.id)
        garantia.marcada_em = hoje
        if novo_valor > atual:
            alta = novo_valor - atual
            self._lancar(
                TipoLancamento.AJUSTE_MARCACAO,
                garantia.id,
                "Marcação a mercado: valorização",
                agora,
                debito(ContaContabil.GARANTIAS_CUSTODIADAS, alta, garantia.id),
                credito(ContaContabil.GARANTIAS_A_DEVOLVER, alta, garantia.id),
            )
        elif novo_valor < atual:
            queda = atual - novo_valor
            self._lancar(
                TipoLancamento.AJUSTE_MARCACAO,
                garantia.id,
                "Marcação a mercado: desvalorização",
                agora,
                debito(ContaContabil.GARANTIAS_A_DEVOLVER, queda, garantia.id),
                credito(ContaContabil.GARANTIAS_CUSTODIADAS, queda, garantia.id),
            )
        self._reavaliar(politica, catalogo, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, catalogo, hoje, "marcacao_a_mercado", agora)
        return garantia

    def receber_evento_de_custodia(
        self, garantia_id: str, valor: Dinheiro, agora: datetime
    ) -> Garantia:
        """Cupom ou amortização de Tesouro gravado, pago ao agente do garantido (item 127).

        O valor sai do título e fica vinculado à garantia: o valor da garantia (e o
        limite) não muda, e a execução vende os dois.
        """
        garantia = self.garantia(garantia_id)
        if garantia.tipo_ativo not in TIPOS_DO_TESOURO:
            raise ValorInvalido(
                "Só título do Tesouro gravado tem cupom vinculado",
                garantia_id=garantia.id,
                tipo_ativo=str(garantia.tipo_ativo),
            )
        self._exigir_vigente(garantia, "vincular cupom a")
        if not valor.positivo:
            raise ValorInvalido("O cupom precisa ser positivo")
        titulo = self.valor_do_titulo(garantia.id)
        if valor > titulo:
            raise ValorInvalido(
                "O cupom não pode passar do valor do título",
                valor=valor.texto(),
                valor_do_titulo=titulo.texto(),
            )
        self._lancar(
            TipoLancamento.VINCULACAO_DE_CUPOM,
            garantia.id,
            "Cupom ou amortização vinculado à garantia",
            agora,
            debito(ContaContabil.CUPONS_VINCULADOS, valor, garantia.id),
            credito(ContaContabil.GARANTIAS_CUSTODIADAS, valor, garantia.id),
        )
        return garantia

    def liberar(
        self,
        garantia_id: str,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
    ) -> Garantia:
        """Devolve a garantia ao cliente, se o resto ainda cobre a dívida, e pede a baixa.

        O limite cai na hora; a baixa na infraestrutura fica pedida até `prazo_da_baixa`.
        """
        garantia = self.garantia(garantia_id)
        self._exigir_vigente(garantia, "liberar")
        if garantia.emprestimo_id is not None:
            raise GarantiaCobrindoDivida(
                "A garantia cobre um empréstimo em aberto e não pode ser liberada",
                garantia_id=garantia.id,
                emprestimo_id=garantia.emprestimo_id,
            )
        antes = self.posicao(politica, catalogo, hoje)
        if garantia.produto is Produto.CARTAO:
            self._exigir_que_o_resto_cubra(garantia, antes, politica, catalogo, hoje)
        self._lancar_liberacao(garantia, agora)
        self._pedir_baixa(garantia, agora, prazo_da_baixa)
        self._reavaliar(politica, catalogo, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, catalogo, hoje, "garantia_liberada", agora)
        return garantia

    def confirmar_baixa(self, garantia_id: str, agora: datetime) -> Garantia:
        """A infraestrutura (simulada) baixou o gravame."""
        garantia = self.garantia(garantia_id)
        garantia.gravame.confirmar_baixa(agora)
        self._emitir(
            TipoEvento.GRAVAME_BAIXADO,
            agora,
            protocolo=garantia.gravame.protocolo,
            infraestrutura=str(garantia.gravame.infraestrutura),
            **self._dados(garantia),
        )
        return garantia

    def _exigir_vigente(self, garantia: Garantia, operacao: str) -> None:
        if not garantia.gravame.vigente:
            raise TransicaoInvalida(
                f"Não dá para {operacao} uma garantia sem gravame vigente ({garantia.estado})",
                garantia_id=garantia.id,
                estado_atual=str(garantia.estado),
                baixa_pedida=garantia.gravame.baixa_pedida_em is not None,
            )

    def _exigir_que_o_resto_cubra(
        self,
        garantia: Garantia,
        antes: PosicaoDeLimite,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        hoje: date,
    ) -> None:
        valor = self.valor_de_mercado(garantia.id)
        limite_dela = valor.aplicar_razao(ltv_efetivo(garantia, politica, catalogo, hoje))
        limite_sem_ela = antes.limite_garantido - limite_dela
        if self.divida_total > limite_sem_ela:
            raise GarantiaCobrindoDivida(
                "A garantia está cobrindo dívida em aberto e não pode ser liberada",
                garantia_id=garantia.id,
                divida_total=self.divida_total.texto(),
                limite_garantido_sem_ela=limite_sem_ela.texto(),
            )

    def _partidas_de_saida(self, garantia: Garantia) -> tuple[Dinheiro, list[Partida]]:
        """Zera a compensação da garantia: título e cupons contra a contrapartida."""
        titulo = self.valor_do_titulo(garantia.id)
        cupons = self.cupons_vinculados(garantia.id)
        total = titulo + cupons
        partidas: list[Partida] = []
        if total.positivo:
            partidas.append(debito(ContaContabil.GARANTIAS_A_DEVOLVER, total, garantia.id))
        if titulo.positivo:
            partidas.append(credito(ContaContabil.GARANTIAS_CUSTODIADAS, titulo, garantia.id))
        if cupons.positivo:
            partidas.append(credito(ContaContabil.CUPONS_VINCULADOS, cupons, garantia.id))
        return total, partidas

    def _lancar_liberacao(self, garantia: Garantia, agora: datetime) -> None:
        valor, partidas = self._partidas_de_saida(garantia)
        if valor.positivo:
            self._lancar(
                TipoLancamento.LIBERACAO_GARANTIA,
                garantia.id,
                "Liberação da garantia",
                agora,
                *partidas,
            )
        self._emitir(
            TipoEvento.GARANTIA_LIBERADA,
            agora,
            valor_liberado=valor.texto(),
            **self._dados(garantia),
        )

    def _pedir_baixa(self, garantia: Garantia, agora: datetime, prazo: date) -> None:
        garantia.gravame.pedir_baixa(agora, prazo)
        self._emitir(
            TipoEvento.GRAVAME_BAIXA_PEDIDA,
            agora,
            baixa_prazo=prazo.isoformat(),
            protocolo=garantia.gravame.protocolo,
            **self._dados(garantia),
        )

    def _executar_por_inteiro(
        self,
        garantia: Garantia,
        devido: Dinheiro,
        abater: Callable[[Dinheiro], list[Partida]],
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
        descricao: str,
        **dados: object,
    ) -> ResultadoDaExecucao:
        """Vende ou resgata a garantia inteira (Lei 4.728/1965, art. 66-B, §3º).

        Quem paga retém o tributo; o líquido abate até `devido` (pelas partidas que
        `abater` devolve, que somam o valor pedido) e o excedente volta ao cliente.
        Depois pede a baixa do gravame. Serve ao cartão e, na Tarefa 5, ao empréstimo.
        """
        valor, partidas = self._partidas_de_saida(garantia)
        retencao = retencao_na_execucao(garantia, valor, hoje, catalogo)
        resultado = ResultadoDaExecucao.demonstrar(valor, retencao, devido)
        if garantia.estado is not EstadoGravame.LIBERADO_PARA_EXCUSSAO:
            # Gravame migrado já liberado para excussão (migração 0003) só termina a venda.
            garantia.gravame.liberar_para_excussao(agora)
            self._emitir(TipoEvento.GRAVAME_LIBERADO_PARA_EXCUSSAO, agora, **self._dados(garantia))

        if resultado.liquido.positivo:
            partidas.append(debito(ContaContabil.CAIXA_RECEBIMENTOS, resultado.liquido))
        if resultado.divida_abatida.positivo:
            partidas.extend(abater(resultado.divida_abatida))
        excedente = resultado.excedente_devolvido
        if excedente.positivo:
            partidas.append(credito(ContaContabil.EXCEDENTE_A_DEVOLVER, excedente))
        if partidas:
            self._lancar(TipoLancamento.EXECUCAO_GARANTIA, garantia.id, descricao, agora, *partidas)
        if excedente.positivo:
            self._lancar(
                TipoLancamento.DEVOLUCAO_DE_EXCEDENTE,
                garantia.id,
                "Excedente da execução devolvido ao cliente",
                agora,
                debito(ContaContabil.EXCEDENTE_A_DEVOLVER, excedente),
                credito(ContaContabil.CAIXA_RECEBIMENTOS, excedente),
            )
        garantia.gravame.marcar_executado(agora)
        regra = catalogo.regra(garantia.tipo_ativo).retencao
        self._emitir(
            TipoEvento.GARANTIA_EXECUTADA,
            agora,
            demonstrativo=resultado.para_dados(),
            regra_de_retencao=str(regra.regra) if regra else None,
            **dados,
            **self._dados(garantia),
        )
        self._pedir_baixa(garantia, agora, prazo_da_baixa)
        return resultado

    def _dados(
        self, garantia: Garantia, solicitacao: Solicitacao | None = None
    ) -> dict[str, object]:
        solicitacao = solicitacao or garantia.solicitacao
        return {
            "garantia_id": garantia.id,
            "tipo_ativo": str(garantia.tipo_ativo),
            "produto": str(garantia.produto),
            "trilho": str(garantia.trilho),
            "estado_do_gravame": str(garantia.estado),
            "contraparte_id": garantia.contraparte_id,
            "solicitacao_id": solicitacao.id if solicitacao else None,
            "prazo_ate": solicitacao.prazo_ate.isoformat() if solicitacao else None,
        }

    # =============================================================== compras
    def autorizar_compra(
        self,
        valor: Dinheiro,
        descricao: str,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        carteira_do_parceiro: Dinheiro | None = None,
    ) -> Compra:
        """Aprova ou nega uma compra. Negar é resultado de negócio, não exceção.

        `carteira_do_parceiro` é a soma dos saldos devedores de todas as contas do
        parceiro; só é necessária quando a política tem teto para o parceiro.
        """
        if not valor.positivo:
            raise ValorInvalido("O valor da compra precisa ser positivo")
        antes = self.posicao(politica, catalogo, hoje)
        motivo = self._motivo_para_negar(valor, antes, politica, hoje, carteira_do_parceiro)
        compra = Compra(
            id=novo_id("cmp"),
            conta_id=self.id,
            valor=valor,
            descricao=descricao,
            estado=EstadoCompra.NEGADA if motivo else EstadoCompra.APROVADA,
            motivo_negacao=motivo,
            criada_em=agora,
        )
        self.pendencias.compras.append(compra)
        if motivo is not None:
            self._emitir(
                TipoEvento.COMPRA_NEGADA,
                agora,
                compra_id=compra.id,
                valor=valor.texto(),
                descricao=descricao,
                motivo=str(motivo),
                limite_disponivel=antes.limite_disponivel.texto(),
            )
            return compra
        self._lancar(
            TipoLancamento.COMPRA,
            compra.id,
            f"Compra: {descricao}",
            agora,
            debito(ContaContabil.RECEBIVEIS_A_FATURAR, valor),
            credito(ContaContabil.LIQUIDACAO_A_PAGAR, valor),
        )
        self._emitir(
            TipoEvento.COMPRA_APROVADA,
            agora,
            compra_id=compra.id,
            valor=valor.texto(),
            descricao=descricao,
        )
        self._emitir_limite_se_mudou(antes, politica, catalogo, hoje, "compra_aprovada", agora)
        return compra

    def _motivo_para_negar(
        self,
        valor: Dinheiro,
        posicao: PosicaoDeLimite,
        politica: PoliticaRisco,
        hoje: date,
        carteira_do_parceiro: Dinheiro | None,
    ) -> MotivoNegacao | None:
        if self.estado is EstadoConta.BLOQUEADA:
            return MotivoNegacao.CONTA_BLOQUEADA
        if self.em_atraso(hoje):
            return MotivoNegacao.FATURA_EM_ATRASO
        if valor > posicao.limite_disponivel:
            return MotivoNegacao.LIMITE_INSUFICIENTE
        teto = politica.teto_limite_do_parceiro
        if teto is not None:
            if carteira_do_parceiro is None:
                # Falha fechada: sem saber a carteira não dá para garantir o teto.
                raise ValueError("A política tem teto do parceiro: informe a carteira atual")
            if carteira_do_parceiro + valor > teto:
                return MotivoNegacao.TETO_DO_PARCEIRO_EXCEDIDO
        return None

    # ================================================== faturas e pagamentos
    def fechar_fatura(self, vencimento: date, agora: datetime, hoje: date) -> Fatura:
        """Fecha o ciclo: o que estava a faturar passa a ter data para vencer."""
        if self.fatura_em_aberto is not None:
            raise FaturaEmAberto(
                "Já existe uma fatura fechada aguardando pagamento",
                fatura_id=self.fatura_em_aberto.id,
            )
        valor = self.divida_a_faturar
        if not valor.positivo:
            raise SemSaldoParaFaturar("Não há compras a faturar nesta conta")
        if vencimento < hoje:
            raise VencimentoInvalido("O vencimento não pode ficar no passado")
        fatura = Fatura(
            id=novo_id("fat"),
            conta_id=self.id,
            valor_fechado=valor,
            fechada_em=agora,
            vencimento=vencimento,
        )
        self.faturas.append(fatura)
        self._lancar(
            TipoLancamento.FECHAMENTO_FATURA,
            fatura.id,
            f"Fechamento da fatura com vencimento em {vencimento.isoformat()}",
            agora,
            debito(ContaContabil.RECEBIVEIS_FATURADOS, valor),
            credito(ContaContabil.RECEBIVEIS_A_FATURAR, valor),
        )
        return fatura

    def pagar(
        self,
        valor: Dinheiro,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
    ) -> Pagamento:
        """Recebe um pagamento: abate primeiro a fatura fechada, depois antecipa o resto."""
        if not valor.positivo:
            raise ValorInvalido("O valor do pagamento precisa ser positivo")
        if valor > self.divida_total:
            raise PagamentoExcedeDivida(
                "O pagamento é maior que a dívida da conta",
                divida_total=self.divida_total.texto(),
            )
        antes = self.posicao(politica, catalogo, hoje)
        fatura = self.fatura_em_aberto
        na_fatura = min(valor, self.divida_faturada)
        antecipado = valor - na_fatura
        partidas = [debito(ContaContabil.CAIXA_RECEBIMENTOS, valor)]
        if na_fatura.positivo:
            partidas.append(credito(ContaContabil.RECEBIVEIS_FATURADOS, na_fatura))
        if antecipado.positivo:
            partidas.append(credito(ContaContabil.RECEBIVEIS_A_FATURAR, antecipado))
        pagamento = Pagamento(
            id=novo_id("pag"),
            conta_id=self.id,
            valor=valor,
            valor_na_fatura=na_fatura,
            valor_antecipado=antecipado,
            fatura_id=fatura.id if fatura and na_fatura.positivo else None,
            criado_em=agora,
        )
        self.pendencias.pagamentos.append(pagamento)
        self._lancar(TipoLancamento.PAGAMENTO, pagamento.id, "Pagamento", agora, *partidas)
        if fatura is not None and self.divida_faturada.nulo:
            fatura.estado = EstadoFatura.PAGA
            fatura.encerrada_em = agora
            self._emitir(
                TipoEvento.FATURA_PAGA,
                agora,
                fatura_id=fatura.id,
                valor_fechado=fatura.valor_fechado.texto(),
                vencimento=fatura.vencimento.isoformat(),
            )
        self._reavaliar(politica, catalogo, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, catalogo, hoje, "pagamento", agora)
        return pagamento

    # ============================================================ empréstimo
    def contratar_emprestimo(
        self,
        *,
        valor: Dinheiro,
        parcelas: int,
        garantia_ids: Iterable[str],
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        primeiro_vencimento: date,
        agora: datetime,
        hoje: date,
        protocolos_da_previdencia: Mapping[str, str],
        carteira_do_parceiro: Dinheiro | None = None,
    ) -> Emprestimo:
        """Contrata um empréstimo parcelado coberto por garantias de produto empréstimo.

        Valida tudo antes de mexer em qualquer coisa. Garantia de previdência ainda
        `solicitado`, com as informações da operadora, é formalizada no mesmo passo: o
        gravame é registrado com o protocolo de `protocolos_da_previdencia` (Res.
        Conjunta 12, art. 8º). A última parcela não pode passar do fim da acumulação nem
        do vencimento do título.

        Como na compra, a conta precisa estar apta: nada de empréstimo com a conta
        bloqueada, a fatura do cartão em atraso ou outro empréstimo em atraso. Com teto
        do parceiro na política, `carteira_do_parceiro` é obrigatória (falha fechada).
        """
        ids = tuple(garantia_ids)
        if not valor.positivo:
            raise ValorInvalido("O valor do empréstimo precisa ser positivo")
        self._exigir_conta_apta_para_emprestimo(valor, politica, hoje, carteira_do_parceiro)
        if primeiro_vencimento <= hoje:
            raise VencimentoInvalido("O primeiro vencimento precisa ficar no futuro")
        if not ids or len(set(ids)) != len(ids):
            raise ValorInvalido("Informe uma ou mais garantias, sem repetir")
        cronograma = cronograma_price(
            valor, politica.taxa_do_emprestimo_ao_mes, parcelas, primeiro_vencimento, hoje
        )
        ultimo_vencimento = cronograma[-1].vencimento

        garantias = [self.garantia(garantia_id) for garantia_id in ids]
        maximo = Dinheiro.zero()
        a_formalizar: list[tuple[Garantia, str]] = []
        for garantia in garantias:
            self._exigir_que_serve_ao_emprestimo(garantia, ultimo_vencimento)
            if garantia.gravame.vigente:
                valor_da = self.valor_de_mercado(garantia.id)
            else:
                protocolo = protocolos_da_previdencia.get(garantia.id, "")
                if not protocolo.strip():
                    raise ValorInvalido(
                        "A formalização da previdência exige o protocolo do gravame",
                        garantia_id=garantia.id,
                    )
                assert garantia.informacoes is not None  # noqa: S101 - conferido acima
                valor_da = garantia.informacoes.valor_liquido
                a_formalizar.append((garantia, protocolo))
            maximo += valor_da.aplicar_razao(ltv_efetivo(garantia, politica, catalogo, hoje))
        if valor > maximo:
            raise ValorInvalido(
                "O valor pedido passa do máximo que as garantias sustentam",
                valor=valor.texto(),
                valor_maximo=maximo.texto(),
            )

        for garantia, protocolo in a_formalizar:
            self._registrar(garantia, protocolo, politica, catalogo, agora, hoje)

        emprestimo = Emprestimo(
            id=novo_id("emp"),
            conta_id=self.id,
            principal=valor,
            taxa_ao_mes=politica.taxa_do_emprestimo_ao_mes,
            parcelas=cronograma,
            garantia_ids=ids,
            contratado_em=agora,
            dias_minimos_execucao=self._primeiro_dia_de_execucao(garantias, politica, catalogo),
        )
        self.emprestimos.append(emprestimo)
        for garantia in garantias:
            garantia.emprestimo_id = emprestimo.id
        self._lancar(
            TipoLancamento.DESEMBOLSO,
            emprestimo.id,
            "Desembolso do empréstimo",
            agora,
            debito(ContaContabil.EMPRESTIMOS_A_RECEBER, valor),
            credito(ContaContabil.DESEMBOLSOS_A_LIQUIDAR, valor),
        )
        self._emitir(
            TipoEvento.EMPRESTIMO_CONTRATADO,
            agora,
            emprestimo_id=emprestimo.id,
            principal=valor.texto(),
            taxa_ao_mes=str(emprestimo.taxa_ao_mes),
            parcelas=parcelas,
            primeiro_vencimento=cronograma[0].vencimento.isoformat(),
            ultimo_vencimento=ultimo_vencimento.isoformat(),
            valor_da_parcela=cronograma[0].valor.texto(),
            garantia_ids=list(ids),
            dias_minimos_execucao=emprestimo.dias_minimos_execucao,
        )
        return emprestimo

    def _exigir_conta_apta_para_emprestimo(
        self,
        valor: Dinheiro,
        politica: PoliticaRisco,
        hoje: date,
        carteira_do_parceiro: Dinheiro | None,
    ) -> None:
        if self.estado is EstadoConta.BLOQUEADA:
            raise EmprestimoNegado(
                "emprestimo_conta_bloqueada",
                "A conta está bloqueada: regularize antes de contratar empréstimo",
                conta_id=self.id,
            )
        if self.em_atraso(hoje):
            raise EmprestimoNegado(
                "emprestimo_fatura_em_atraso",
                "Há fatura do cartão vencida e não paga",
                conta_id=self.id,
            )
        if any(e.estado is EstadoEmprestimo.EM_ATRASO for e in self.emprestimos):
            raise EmprestimoNegado(
                "emprestimo_em_atraso_na_conta",
                "Há empréstimo em atraso nesta conta",
                conta_id=self.id,
            )
        teto = politica.teto_limite_do_parceiro
        if teto is not None:
            if carteira_do_parceiro is None:
                # Falha fechada: sem saber a carteira não dá para garantir o teto.
                raise EmprestimoNegado(
                    "emprestimo_carteira_nao_informada",
                    "A política tem teto do parceiro e a carteira atual não foi informada",
                    conta_id=self.id,
                )
            if carteira_do_parceiro + valor > teto:
                raise EmprestimoNegado(
                    "emprestimo_teto_do_parceiro_excedido",
                    "O empréstimo passaria do teto da carteira do parceiro",
                    teto=teto.texto(),
                    carteira=carteira_do_parceiro.texto(),
                )

    def _exigir_que_serve_ao_emprestimo(self, garantia: Garantia, ultimo_vencimento: date) -> None:
        if garantia.produto is not Produto.EMPRESTIMO:
            raise ValorInvalido(
                "Garantia do cartão não entra no empréstimo",
                garantia_id=garantia.id,
                produto=str(garantia.produto),
            )
        if garantia.emprestimo_id is not None:
            raise ValorInvalido(
                "A garantia já cobre outro empréstimo",
                garantia_id=garantia.id,
                emprestimo_id=garantia.emprestimo_id,
            )
        previdencia = garantia.tipo_ativo is TipoAtivo.PREVIDENCIA
        if not garantia.gravame.vigente:
            formalizavel = (
                previdencia
                and garantia.estado is EstadoGravame.SOLICITADO
                and garantia.trilho is Trilho.RES_CONJUNTA_12
            )
            if not formalizavel:
                raise ValorInvalido(
                    f"Garantia {garantia.estado} não entra no empréstimo: só gravame registrado",
                    garantia_id=garantia.id,
                    estado_atual=str(garantia.estado),
                )
            if garantia.informacoes is None:
                raise TransicaoInvalida(
                    "Previdência só formaliza depois das informações da operadora",
                    garantia_id=garantia.id,
                )
        if previdencia:
            if garantia.fim_da_acumulacao is None:
                raise ValorInvalido(
                    "Previdência sem fim da acumulação informado", garantia_id=garantia.id
                )
            if ultimo_vencimento > garantia.fim_da_acumulacao:
                raise CombinacaoNaoPermitida(
                    "prazo_alem_da_acumulacao",
                    "O prazo do crédito não pode passar do fim da acumulação "
                    "(Res. Conjunta 12, art. 6º, §3º)",
                    sugestao="Reduza o número de parcelas até o fim da acumulação do plano.",
                    garantia_id=garantia.id,
                    ultimo_vencimento=ultimo_vencimento.isoformat(),
                    fim_da_acumulacao=garantia.fim_da_acumulacao.isoformat(),
                )
        if garantia.vencimento is not None and ultimo_vencimento > garantia.vencimento:
            raise CombinacaoNaoPermitida(
                "vencimento_nao_casado",
                "A última parcela precisa vencer até o vencimento do título",
                sugestao="Reduza o número de parcelas ou use um título com vencimento maior.",
                garantia_id=garantia.id,
                ultimo_vencimento=ultimo_vencimento.isoformat(),
                vencimento_do_titulo=garantia.vencimento.isoformat(),
            )

    @staticmethod
    def _primeiro_dia_de_execucao(
        garantias: Iterable[Garantia], politica: PoliticaRisco, catalogo: CatalogoRegulatorio
    ) -> int:
        """Primeiro dia de atraso {dia_corrido} em que o empréstimo pode ser executado.

        Previdência: só depois do piso do catálogo (mais de 90 dias, art. 12), então o
        dia seguinte. Os demais: o piso do catálogo, se houver, ou o prazo contratual da
        política. Com várias garantias vale a mais restritiva.
        """
        dias = 0
        for garantia in garantias:
            piso = catalogo.regra(garantia.tipo_ativo).dias_minimos_execucao
            if garantia.tipo_ativo is TipoAtivo.PREVIDENCIA and piso is not None:
                primeiro = piso + 1
            else:
                primeiro = piso if piso is not None else politica.dias_para_execucao_emprestimo
            dias = max(dias, primeiro)
        return dias

    def _tem_previdencia(self, emprestimo: Emprestimo) -> bool:
        return any(
            self.garantia(garantia_id).tipo_ativo is TipoAtivo.PREVIDENCIA
            for garantia_id in emprestimo.garantia_ids
        )

    def pagar_parcela(
        self,
        emprestimo_id: str,
        valor: Dinheiro,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
    ) -> Emprestimo:
        """Recebe um pagamento do empréstimo: abate as parcelas em ordem, juros primeiro.

        Parcela vincenda é paga só pela amortização (sem juros futuros). Quitado o
        empréstimo, pede a baixa de todas as garantias (`prazo_da_baixa`).
        """
        emprestimo = self.emprestimo(emprestimo_id)
        if emprestimo.encerrado:
            raise TransicaoInvalida(
                f"O empréstimo {emprestimo.id} está {emprestimo.estado}",
                emprestimo_id=emprestimo.id,
                estado_atual=str(emprestimo.estado),
            )
        if not valor.positivo:
            raise ValorInvalido("O valor do pagamento precisa ser positivo")
        saldo = emprestimo.saldo_devedor(hoje)
        if valor > saldo:
            raise PagamentoExcedeDivida(
                "O pagamento é maior que o saldo devedor do empréstimo",
                saldo_devedor=saldo.texto(),
            )
        abertas_antes = {p.numero for p in emprestimo.parcelas_em_aberto}
        amortizacao, juros = emprestimo.aplicar_pagamento(valor, hoje, agora)
        partidas = [debito(ContaContabil.CAIXA_RECEBIMENTOS, valor)]
        if amortizacao.positivo:
            partidas.append(credito(ContaContabil.EMPRESTIMOS_A_RECEBER, amortizacao))
        if juros.positivo:
            partidas.append(credito(ContaContabil.RECEITA_DE_JUROS, juros))
        self._lancar(
            TipoLancamento.PAGAMENTO_DE_PARCELA, emprestimo.id, "Pagamento do empréstimo", agora,
            *partidas,
        )  # fmt: skip
        quitadas = sorted(abertas_antes - {p.numero for p in emprestimo.parcelas_em_aberto})
        saldo_depois = emprestimo.saldo_devedor(hoje)
        self._emitir(
            TipoEvento.EMPRESTIMO_PARCELA_PAGA,
            agora,
            emprestimo_id=emprestimo.id,
            valor=valor.texto(),
            amortizacao=amortizacao.texto(),
            juros=juros.texto(),
            parcelas_quitadas=quitadas,
            saldo_devedor=saldo_depois.texto(),
        )
        if saldo_depois.nulo:
            emprestimo.estado = EstadoEmprestimo.QUITADO
            self._liberar_garantias_do_emprestimo(emprestimo, agora, prazo_da_baixa)
            self._emitir(
                TipoEvento.EMPRESTIMO_QUITADO,
                agora,
                emprestimo_id=emprestimo.id,
                forma="pagamento",
            )
        elif emprestimo.dias_em_atraso(hoje) == 0:
            self._sair_do_atraso(emprestimo)
        return emprestimo

    def _sair_do_atraso(self, emprestimo: Emprestimo) -> None:
        emprestimo.estado = EstadoEmprestimo.ATIVO
        emprestimo.aviso_45_em = None
        emprestimo.aviso_10_em = None

    def _liberar_garantias_do_emprestimo(
        self, emprestimo: Emprestimo, agora: datetime, prazo_da_baixa: date
    ) -> None:
        """Quitado o empréstimo, as garantias que sobram saem e a baixa é pedida."""
        for garantia_id in emprestimo.garantia_ids:
            garantia = self.garantia(garantia_id)
            if garantia.gravame.vigente:
                self._lancar_liberacao(garantia, agora)
                self._pedir_baixa(garantia, agora, prazo_da_baixa)

    def _tratar_emprestimos(
        self,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
    ) -> None:
        for emprestimo in self.emprestimos:
            if not emprestimo.encerrado:
                self._tratar_emprestimo(emprestimo, politica, catalogo, agora, hoje, prazo_da_baixa)

    def _tratar_emprestimo(
        self,
        emprestimo: Emprestimo,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
    ) -> None:
        dias = emprestimo.dias_em_atraso(hoje)
        if dias == 0:
            if emprestimo.estado is EstadoEmprestimo.EM_ATRASO:
                self._sair_do_atraso(emprestimo)
            return
        if emprestimo.estado is EstadoEmprestimo.ATIVO:
            emprestimo.estado = EstadoEmprestimo.EM_ATRASO
            self._emitir(
                TipoEvento.EMPRESTIMO_EM_ATRASO,
                agora,
                emprestimo_id=emprestimo.id,
                dias_em_atraso=dias,
                saldo_devedor=emprestimo.saldo_devedor(hoje).texto(),
                execucao_a_partir_do_dia=emprestimo.dias_minimos_execucao,
            )
        previdencia = self._tem_previdencia(emprestimo)
        if previdencia:
            self._avisar_previdencia(emprestimo, dias, agora, hoje)
        pode_executar = dias >= emprestimo.dias_minimos_execucao
        if previdencia:
            aviso = emprestimo.aviso_10_em
            pode_executar = (
                pode_executar and aviso is not None and hoje >= aviso + timedelta(days=_AVISO_FINAL)
            )
        if pode_executar:
            self._executar_emprestimo(emprestimo, politica, catalogo, agora, hoje, prazo_da_baixa)

    def _avisar_previdencia(
        self, emprestimo: Emprestimo, dias: int, agora: datetime, hoje: date
    ) -> None:
        """Avisos da Res. Conjunta 12 (art. 6º, §2º, IV): 45 dias de atraso e 10 dias antes."""
        dia_do_aviso_final = max(emprestimo.dias_minimos_execucao - _AVISO_FINAL, 1)
        if dias < _AVISO_INICIAL:
            emprestimo.aviso_45_em = None
        elif emprestimo.aviso_45_em is None:
            emprestimo.aviso_45_em = hoje
            self._emitir(
                TipoEvento.PREVIDENCIA_AVISO_45_DIAS,
                agora,
                emprestimo_id=emprestimo.id,
                dias_em_atraso=dias,
                saldo_devedor=emprestimo.saldo_devedor(hoje).texto(),
            )
        if dias < dia_do_aviso_final:
            emprestimo.aviso_10_em = None
        elif emprestimo.aviso_10_em is None:
            emprestimo.aviso_10_em = hoje
            self._emitir(
                TipoEvento.PREVIDENCIA_AVISO_10_DIAS,
                agora,
                emprestimo_id=emprestimo.id,
                dias_em_atraso=dias,
                liquidacao_a_partir_de=(hoje + timedelta(days=_AVISO_FINAL)).isoformat(),
                saldo_devedor=emprestimo.saldo_devedor(hoje).texto(),
            )

    def _executar_emprestimo(
        self,
        emprestimo: Emprestimo,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
    ) -> None:
        """Vende ou resgata as garantias inteiras até cobrir o saldo devedor.

        O líquido abate o saldo (juros vencidos e amortização) e o excedente volta ao
        cliente. Liquidado o saldo, as garantias que não foram executadas são liberadas.
        """

        def abater(valor: Dinheiro) -> list[Partida]:
            amortizacao, juros = emprestimo.aplicar_pagamento(valor, hoje, agora)
            partidas: list[Partida] = []
            if amortizacao.positivo:
                partidas.append(credito(ContaContabil.EMPRESTIMOS_A_RECEBER, amortizacao))
            if juros.positivo:
                partidas.append(credito(ContaContabil.RECEITA_DE_JUROS, juros))
            return partidas

        fila = sorted(
            (g for g in map(self.garantia, emprestimo.garantia_ids) if g.gravame.vigente),
            key=lambda g: (politica.prioridade_de_execucao(g.tipo_ativo), g.criada_em, g.id),
        )
        devido = emprestimo.saldo_devedor(hoje)
        for garantia in fila:
            if devido.nulo:
                break
            resultado = self._executar_por_inteiro(
                garantia,
                devido,
                abater,
                catalogo,
                agora,
                hoje,
                prazo_da_baixa,
                f"Execução da garantia do empréstimo {emprestimo.id}",
                emprestimo_id=emprestimo.id,
                dias_em_atraso=emprestimo.dias_em_atraso(hoje),
                saldo_devedor_antes=devido.texto(),
            )
            devido -= resultado.divida_abatida
        if devido.nulo:
            emprestimo.estado = EstadoEmprestimo.LIQUIDADO_POR_EXECUCAO
            self._liberar_garantias_do_emprestimo(emprestimo, agora, prazo_da_baixa)
            self._emitir(
                TipoEvento.EMPRESTIMO_QUITADO,
                agora,
                emprestimo_id=emprestimo.id,
                forma="execucao",
            )

    # ========================================================= virada do dia
    def virar_o_dia(
        self,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
    ) -> None:
        """Rotina diária da conta: atraso, execução e prazo da chamada de margem.

        Pode rodar mais de uma vez no mesmo dia sem repetir efeito. `prazo_da_baixa` é
        hoje mais o prazo da baixa do catálogo, em dias úteis, calculado por quem chama.
        """
        antes = self.posicao(politica, catalogo, hoje)
        self._concluir_excussoes_pendentes(catalogo, agora, hoje, prazo_da_baixa)
        self._tratar_atraso(politica, catalogo, agora, hoje, prazo_da_baixa)
        self._tratar_emprestimos(politica, catalogo, agora, hoje, prazo_da_baixa)
        self._tratar_prazo_da_chamada(politica, catalogo, agora, hoje)
        self._reavaliar(politica, catalogo, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, catalogo, hoje, "virada_do_dia", agora)

    def _concluir_excussoes_pendentes(
        self, catalogo: CatalogoRegulatorio, agora: datetime, hoje: date, prazo_da_baixa: date
    ) -> None:
        """Termina a venda de gravame que ficou em `liberado_para_excussao`.

        Só existe por causa da migração 0003 (o estado antigo `execucao_solicitada` migra
        para ele): o domínio novo atravessa esse estado dentro de uma única execução. A
        venda vale o valor de mercado da garantia (do cartão); o líquido abate a fatura vencida
        e o excedente volta ao cliente.
        """
        for garantia in self.garantias:
            if garantia.estado is not EstadoGravame.LIBERADO_PARA_EXCUSSAO:
                continue
            devido = self.divida_faturada

            def abater(valor: Dinheiro) -> list[Partida]:
                return [credito(ContaContabil.RECEBIVEIS_FATURADOS, valor)]

            self._executar_por_inteiro(
                garantia,
                devido,
                abater,
                catalogo,
                agora,
                hoje,
                prazo_da_baixa,
                "Conclusão da excussão da garantia (gravame migrado)",
                excussao_concluida_pela_rotina=True,
            )

    def _tratar_atraso(
        self,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
    ) -> None:
        fatura = self.fatura_em_aberto
        if fatura is None:
            return
        dias = fatura.dias_em_atraso(hoje)
        if dias == 0:
            return
        if fatura.estado is EstadoFatura.FECHADA:
            fatura.estado = EstadoFatura.EM_ATRASO
            self._emitir(
                TipoEvento.FATURA_EM_ATRASO,
                agora,
                fatura_id=fatura.id,
                dias_em_atraso=dias,
                saldo_em_atraso=self.divida_faturada.texto(),
                vencimento=fatura.vencimento.isoformat(),
                execucao_a_partir_de=(
                    fatura.vencimento + timedelta(days=politica.dias_para_execucao)
                ).isoformat(),
            )
        if dias >= politica.dias_para_execucao:
            self._executar_garantias(fatura, dias, politica, catalogo, agora, hoje, prazo_da_baixa)

    def _executar_garantias(
        self,
        fatura: Fatura,
        dias_em_atraso: int,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        agora: datetime,
        hoje: date,
        prazo_da_baixa: date,
    ) -> None:
        """Executa garantias inteiras, até cobrir a fatura vencida.

        O alvo é a fatura vencida (`divida_faturada`), não as compras ainda a faturar.
        A fila segue a ordem de execução da política (os mais líquidos primeiro); cada
        garantia escolhida é vendida por inteiro, e para quando a fatura está coberta.
        Ativo com atraso mínimo próprio no catálogo espera esse prazo.
        """
        devido = self.divida_faturada

        def abater(valor: Dinheiro) -> list[Partida]:
            return [credito(ContaContabil.RECEBIVEIS_FATURADOS, valor)]

        fila = sorted(
            (
                g
                for g in self._garantias_do_cartao_com_valor()
                if dias_em_atraso >= (catalogo.regra(g.tipo_ativo).dias_minimos_execucao or 0)
            ),
            key=lambda g: (politica.prioridade_de_execucao(g.tipo_ativo), g.criada_em, g.id),
        )
        for garantia in fila:
            if devido.nulo:
                break
            resultado = self._executar_por_inteiro(
                garantia,
                devido,
                abater,
                catalogo,
                agora,
                hoje,
                prazo_da_baixa,
                f"Execução da garantia para quitar a fatura {fatura.id}",
                fatura_id=fatura.id,
                dias_em_atraso=dias_em_atraso,
                saldo_da_fatura_antes=devido.texto(),
            )
            devido -= resultado.divida_abatida
        if self.divida_faturada.nulo:
            fatura.estado = EstadoFatura.LIQUIDADA_POR_EXECUCAO
            fatura.encerrada_em = agora
        elif devido.positivo and not self._garantias_do_cartao_com_valor():
            # A garantia acabou e ainda há fatura vencida: dívida sem cobertura.
            self._bloquear(MotivoBloqueio.DIVIDA_DESCOBERTA)

    def _garantias_do_cartao_com_valor(self) -> list[Garantia]:
        return [g for g in self.garantias if g.gera_limite and self.valor_de_mercado(g.id).positivo]

    def _tratar_prazo_da_chamada(
        self, politica: PoliticaRisco, catalogo: CatalogoRegulatorio, agora: datetime, hoje: date
    ) -> None:
        chamada = self.chamada_aberta
        if chamada is None or hoje <= chamada.prazo_ate:
            return
        deficit = self.posicao(politica, catalogo, hoje).deficit
        if deficit.nulo:
            return  # _reavaliar marca como atendida
        chamada.estado = EstadoChamada.VENCIDA
        chamada.encerrada_em = agora
        self._bloquear(MotivoBloqueio.CHAMADA_DE_MARGEM_VENCIDA)
        self._emitir(
            TipoEvento.CHAMADA_DE_MARGEM,
            agora,
            chamada_id=chamada.id,
            situacao=str(EstadoChamada.VENCIDA),
            deficit=deficit.texto(),
            prazo_ate=chamada.prazo_ate.isoformat(),
            consequencia="conta_bloqueada_para_novas_compras",
        )

    # ============================================================== internos
    def _bloquear(self, motivo: MotivoBloqueio) -> None:
        self.estado = EstadoConta.BLOQUEADA
        self.motivo_bloqueio = motivo

    def _reavaliar(
        self, politica: PoliticaRisco, catalogo: CatalogoRegulatorio, agora: datetime, hoje: date
    ) -> None:
        """Depois de qualquer mudança em dívida ou garantia: abre, atende ou destrava."""
        deficit = self.posicao(politica, catalogo, hoje).deficit
        chamada = self.chamada_aberta
        if deficit.positivo:
            if chamada is None and self.estado is EstadoConta.ATIVA:
                self._abrir_chamada(deficit, politica, agora, hoje)
            return
        if chamada is not None:
            chamada.estado = EstadoChamada.ATENDIDA
            chamada.encerrada_em = agora
            self._emitir(
                TipoEvento.CHAMADA_DE_MARGEM,
                agora,
                chamada_id=chamada.id,
                situacao=str(EstadoChamada.ATENDIDA),
                deficit="0.00",
                prazo_ate=chamada.prazo_ate.isoformat(),
            )
        if self.estado is EstadoConta.BLOQUEADA and not self.em_atraso(hoje):
            self.estado = EstadoConta.ATIVA
            self.motivo_bloqueio = None

    def _abrir_chamada(
        self, deficit: Dinheiro, politica: PoliticaRisco, agora: datetime, hoje: date
    ) -> None:
        chamada = ChamadaDeMargem(
            id=novo_id("chm"),
            conta_id=self.id,
            deficit_inicial=deficit,
            aberta_em=agora,
            prazo_ate=hoje + timedelta(days=politica.prazo_chamada_de_margem_dias),
        )
        self.chamadas.append(chamada)
        self._emitir(
            TipoEvento.CHAMADA_DE_MARGEM,
            agora,
            chamada_id=chamada.id,
            situacao=str(EstadoChamada.ABERTA),
            deficit=deficit.texto(),
            prazo_ate=chamada.prazo_ate.isoformat(),
            como_regularizar="reforce a garantia ou pague pelo menos o valor do déficit",
        )

    def _lancar(
        self,
        tipo: TipoLancamento,
        referencia_id: str,
        descricao: str,
        agora: datetime,
        *partidas: Partida,
    ) -> None:
        lancamento = Lancamento(
            id=novo_id("lan"),
            tipo=tipo,
            partidas=tuple(partidas),
            referencia_id=referencia_id,
            descricao=descricao,
            criado_em=agora,
        )
        self.saldos.aplicar(lancamento)
        self.pendencias.lancamentos.append(lancamento)

    def _emitir(self, tipo: TipoEvento, agora: datetime, **dados: object) -> None:
        self.pendencias.eventos.append(
            EventoDominio(
                id=novo_id("evt"),
                tipo=tipo,
                conta_id=self.id,
                dados={"conta_id": self.id, "cliente_ref": self.cliente_ref, **dados},
                ocorrido_em=agora,
            )
        )

    def _emitir_limite_se_mudou(
        self,
        antes: PosicaoDeLimite,
        politica: PoliticaRisco,
        catalogo: CatalogoRegulatorio,
        hoje: date,
        motivo: str,
        agora: datetime,
    ) -> None:
        depois = self.posicao(politica, catalogo, hoje)
        if depois == antes:
            return
        self._emitir(
            TipoEvento.LIMITE_ATUALIZADO,
            agora,
            motivo=motivo,
            conta_estado=str(self.estado),
            **depois.para_dados(),
        )
