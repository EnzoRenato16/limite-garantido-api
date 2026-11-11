"""Conta de um cliente do parceiro: o agregado onde moram as regras de crédito.

A conta não guarda saldo. Dívida e valor das garantias são lidos de `Saldos`, que por
sua vez é a soma das partidas do ledger. Toda operação que mexe com dinheiro cria um
`Lancamento` balanceado e o aplica nos saldos; quem persiste a conta grava os
lançamentos, os eventos e o que mais tiver mudado, tudo na mesma transação.

Este módulo não conhece banco, HTTP nem relógio: `agora` e `hoje` chegam de fora.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import (
    FaturaEmAberto,
    GarantiaCobrindoDivida,
    GarantiaNaoEncontrada,
    PagamentoExcedeDivida,
    SemSaldoParaFaturar,
    ValorInvalido,
    VencimentoInvalido,
)
from app.dominio.eventos import EventoDominio, TipoEvento
from app.dominio.garantia import EstadoGarantia, Garantia
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
from app.dominio.politica import PoliticaRisco, TipoAtivo


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
        """{dias} corridos depois do vencimento. Zero até o dia do vencimento."""
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
    """Foto do limite de uma conta. Tudo em {BRL}."""

    # Soma do valor de mercado das garantias bloqueadas.
    valor_das_garantias: Dinheiro
    # Soma de (valor de mercado x LTV) de cada garantia, cada parcela arredondada para baixo.
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
        return self.divida_a_faturar + self.divida_faturada

    @property
    def fatura_em_aberto(self) -> Fatura | None:
        return next((fatura for fatura in self.faturas if fatura.em_aberto), None)

    @property
    def chamada_aberta(self) -> ChamadaDeMargem | None:
        return next((c for c in self.chamadas if c.estado is EstadoChamada.ABERTA), None)

    @property
    def garantias_ativas(self) -> list[Garantia]:
        return [garantia for garantia in self.garantias if garantia.ativa]

    def garantia(self, garantia_id: str) -> Garantia:
        for garantia in self.garantias:
            if garantia.id == garantia_id:
                return garantia
        raise GarantiaNaoEncontrada("Garantia não encontrada", garantia_id=garantia_id)

    def valor_de_mercado(self, garantia_id: str) -> Dinheiro:
        return self.saldos.saldo(ContaContabil.GARANTIAS_CUSTODIADAS, garantia_id)

    def em_atraso(self, hoje: date) -> bool:
        fatura = self.fatura_em_aberto
        return fatura is not None and fatura.dias_em_atraso(hoje) > 0

    def posicao(self, politica: PoliticaRisco) -> PosicaoDeLimite:
        valor_das_garantias = Dinheiro.zero()
        limite_garantido = Dinheiro.zero()
        for garantia in self.garantias_ativas:
            valor = self.valor_de_mercado(garantia.id)
            valor_das_garantias += valor
            # {BRL} * LTV {1} -> {BRL}; cada parcela arredonda para baixo.
            limite_garantido += self._limite_da_garantia(garantia, valor, politica)

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
            deficit=(divida - limite_garantido).nao_negativo(),
        )

    @staticmethod
    def _limite_da_garantia(
        garantia: Garantia, valor: Dinheiro, politica: PoliticaRisco
    ) -> Dinheiro:
        # Se o parceiro deixou de aceitar o ativo, a garantia antiga para de gerar limite,
        # mas continua bloqueada e pode ser executada ou liberada.
        if not politica.aceita(garantia.tipo_ativo):
            return Dinheiro.zero()
        return politica.limite_da_garantia(garantia.tipo_ativo, valor)

    # ============================================================= garantias
    def registrar_garantia(
        self,
        tipo_ativo: TipoAtivo,
        valor: Dinheiro,
        politica: PoliticaRisco,
        agora: datetime,
        hoje: date,
    ) -> Garantia:
        """Registra o pedido. A garantia nasce `pendente`, sem efeito no limite."""
        if not valor.positivo:
            raise ValorInvalido("O valor da garantia precisa ser positivo")
        politica.ltv(tipo_ativo)  # levanta AtivoNaoAceito se o parceiro não aceita o ativo
        garantia = Garantia(
            id=novo_id("gar"),
            conta_id=self.id,
            tipo_ativo=tipo_ativo,
            valor_aplicado=valor,
            estado=EstadoGarantia.PENDENTE,
            criada_em=agora,
            marcada_em=hoje,
        )
        self.garantias.append(garantia)
        return garantia

    def confirmar_bloqueio(
        self, garantia_id: str, politica: PoliticaRisco, agora: datetime, hoje: date
    ) -> Garantia:
        """O custodiante confirmou o gravame: a garantia passa a valer e libera limite."""
        garantia = self.garantia(garantia_id)
        antes = self.posicao(politica)
        garantia.transicionar(EstadoGarantia.BLOQUEADA)
        garantia.marcada_em = hoje
        valor = garantia.valor_aplicado
        self._lancar(
            TipoLancamento.BLOQUEIO_GARANTIA,
            garantia.id,
            f"Bloqueio de {garantia.tipo_ativo} em garantia",
            agora,
            debito(ContaContabil.GARANTIAS_CUSTODIADAS, valor, garantia.id),
            credito(ContaContabil.GARANTIAS_A_DEVOLVER, valor, garantia.id),
        )
        self._emitir(
            TipoEvento.GARANTIA_BLOQUEADA,
            agora,
            garantia_id=garantia.id,
            tipo_ativo=str(garantia.tipo_ativo),
            valor_de_mercado=valor.texto(),
            ltv=str(politica.ltv_por_ativo.get(garantia.tipo_ativo, Decimal(0))),
            limite_gerado=self._limite_da_garantia(garantia, valor, politica).texto(),
        )
        self._reavaliar(politica, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, "garantia_bloqueada", agora)
        return garantia

    def remarcar(
        self,
        garantia_id: str,
        novo_valor: Dinheiro,
        politica: PoliticaRisco,
        agora: datetime,
        hoje: date,
    ) -> Garantia:
        """Marcação a mercado: ajusta o valor da garantia e reavalia a margem."""
        garantia = self.garantia(garantia_id)
        garantia.exigir_ativa("remarcar")
        if novo_valor.negativo:
            raise ValorInvalido("O valor de mercado não pode ser negativo")
        antes = self.posicao(politica)
        atual = self.valor_de_mercado(garantia.id)
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
        self._reavaliar(politica, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, "marcacao_a_mercado", agora)
        return garantia

    def liberar(
        self, garantia_id: str, politica: PoliticaRisco, agora: datetime, hoje: date
    ) -> Garantia:
        """Devolve a garantia ao cliente, desde que o resto ainda cubra a dívida."""
        garantia = self.garantia(garantia_id)
        garantia.exigir_ativa("liberar")
        antes = self.posicao(politica)
        valor = self.valor_de_mercado(garantia.id)
        limite_sem_ela = antes.limite_garantido - self._limite_da_garantia(
            garantia, valor, politica
        )
        if self.divida_total > limite_sem_ela:
            raise GarantiaCobrindoDivida(
                "A garantia está cobrindo dívida em aberto e não pode ser liberada",
                garantia_id=garantia.id,
                divida_total=self.divida_total.texto(),
                limite_garantido_sem_ela=limite_sem_ela.texto(),
            )
        garantia.transicionar(EstadoGarantia.LIBERADA)
        if valor.positivo:
            self._lancar(
                TipoLancamento.LIBERACAO_GARANTIA,
                garantia.id,
                "Liberação da garantia",
                agora,
                debito(ContaContabil.GARANTIAS_A_DEVOLVER, valor, garantia.id),
                credito(ContaContabil.GARANTIAS_CUSTODIADAS, valor, garantia.id),
            )
        self._emitir(
            TipoEvento.GARANTIA_LIBERADA,
            agora,
            garantia_id=garantia.id,
            tipo_ativo=str(garantia.tipo_ativo),
            valor_liberado=valor.texto(),
        )
        self._reavaliar(politica, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, "garantia_liberada", agora)
        return garantia

    # =============================================================== compras
    def autorizar_compra(
        self,
        valor: Dinheiro,
        descricao: str,
        politica: PoliticaRisco,
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
        antes = self.posicao(politica)
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
        self._emitir_limite_se_mudou(antes, politica, "compra_aprovada", agora)
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
        self, valor: Dinheiro, politica: PoliticaRisco, agora: datetime, hoje: date
    ) -> Pagamento:
        """Recebe um pagamento: abate primeiro a fatura fechada, depois antecipa o resto."""
        if not valor.positivo:
            raise ValorInvalido("O valor do pagamento precisa ser positivo")
        if valor > self.divida_total:
            raise PagamentoExcedeDivida(
                "O pagamento é maior que a dívida da conta",
                divida_total=self.divida_total.texto(),
            )
        antes = self.posicao(politica)
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
        self._reavaliar(politica, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, "pagamento", agora)
        return pagamento

    # ========================================================= virada do dia
    def virar_o_dia(self, politica: PoliticaRisco, agora: datetime, hoje: date) -> None:
        """Rotina diária da conta: atraso, execução e prazo da chamada de margem.

        Pode rodar mais de uma vez no mesmo dia sem repetir efeito.
        """
        antes = self.posicao(politica)
        self._tratar_atraso(politica, agora, hoje)
        self._tratar_prazo_da_chamada(politica, agora, hoje)
        self._reavaliar(politica, agora, hoje)
        self._emitir_limite_se_mudou(antes, politica, "virada_do_dia", agora)

    def _tratar_atraso(self, politica: PoliticaRisco, agora: datetime, hoje: date) -> None:
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
            self._executar_garantias(fatura, dias, politica, agora)

    def _executar_garantias(
        self, fatura: Fatura, dias_em_atraso: int, politica: PoliticaRisco, agora: datetime
    ) -> None:
        """Vende garantia para quitar a fatura em atraso, e só o necessário.

        Começa pelos ativos mais líquidos (ordem da política). O que sobra de uma
        garantia vendida em parte continua bloqueado.
        """
        devido = self.divida_faturada
        fila = sorted(
            self.garantias_ativas,
            key=lambda g: (politica.prioridade_de_execucao(g.tipo_ativo), g.criada_em, g.id),
        )
        for garantia in fila:
            if devido.nulo:
                break
            valor = self.valor_de_mercado(garantia.id)
            if valor.nulo:
                continue
            vendido = min(valor, devido)
            remanescente = valor - vendido
            self._lancar(
                TipoLancamento.EXECUCAO_GARANTIA,
                garantia.id,
                f"Execução da garantia para quitar a fatura {fatura.id}",
                agora,
                debito(ContaContabil.GARANTIAS_A_DEVOLVER, vendido, garantia.id),
                credito(ContaContabil.GARANTIAS_CUSTODIADAS, vendido, garantia.id),
                debito(ContaContabil.CAIXA_RECEBIMENTOS, vendido),
                credito(ContaContabil.RECEBIVEIS_FATURADOS, vendido),
            )
            garantia.transicionar(
                EstadoGarantia.EXECUTADA
                if remanescente.nulo
                else EstadoGarantia.PARCIALMENTE_EXECUTADA
            )
            devido -= vendido
            self._emitir(
                TipoEvento.GARANTIA_EXECUTADA,
                agora,
                garantia_id=garantia.id,
                tipo_ativo=str(garantia.tipo_ativo),
                valor_executado=vendido.texto(),
                valor_remanescente=remanescente.texto(),
                estado=str(garantia.estado),
                fatura_id=fatura.id,
                dias_em_atraso=dias_em_atraso,
                saldo_da_fatura_apos_execucao=devido.texto(),
            )
        if devido.nulo:
            fatura.estado = EstadoFatura.LIQUIDADA_POR_EXECUCAO
            fatura.encerrada_em = agora
        elif not self.garantias_com_valor():
            # A garantia acabou e ainda há fatura vencida: dívida sem cobertura.
            self._bloquear(MotivoBloqueio.DIVIDA_DESCOBERTA)

    def garantias_com_valor(self) -> list[Garantia]:
        return [g for g in self.garantias_ativas if self.valor_de_mercado(g.id).positivo]

    def _tratar_prazo_da_chamada(
        self, politica: PoliticaRisco, agora: datetime, hoje: date
    ) -> None:
        chamada = self.chamada_aberta
        if chamada is None or hoje <= chamada.prazo_ate:
            return
        deficit = self.posicao(politica).deficit
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

    def _reavaliar(self, politica: PoliticaRisco, agora: datetime, hoje: date) -> None:
        """Depois de qualquer mudança em dívida ou garantia: abre, atende ou destrava."""
        deficit = self.posicao(politica).deficit
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
        self, antes: PosicaoDeLimite, politica: PoliticaRisco, motivo: str, agora: datetime
    ) -> None:
        depois = self.posicao(politica)
        if depois == antes:
            return
        self._emitir(
            TipoEvento.LIMITE_ATUALIZADO,
            agora,
            motivo=motivo,
            conta_estado=str(self.estado),
            **depois.para_dados(),
        )
