"""Simulador de stress: e se os preços caírem amanhã?

Aplica um choque por tipo de ativo sobre a carteira de um parceiro e responde:
quantas contas entram em chamada de margem, de quanto é o déficit e quanto da dívida
ficaria sem cobertura mesmo vendendo toda a garantia ao preço estressado.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.politica import PoliticaRisco, TipoAtivo


@dataclass(frozen=True, slots=True)
class GarantiaNaCarteira:
    tipo_ativo: TipoAtivo
    valor_de_mercado: Dinheiro
    # None: dentro de casa. Senão, o custodiante externo que guarda o ativo.
    custodiante_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContaNaCarteira:
    conta_id: str
    divida: Dinheiro
    garantias: tuple[GarantiaNaCarteira, ...]


@dataclass(frozen=True, slots=True)
class ContaEstressada:
    conta_id: str
    divida: Dinheiro
    valor_das_garantias_antes: Dinheiro
    valor_das_garantias_depois: Dinheiro
    limite_garantido_antes: Dinheiro
    limite_garantido_depois: Dinheiro
    # Dívida acima do limite garantido estressado (chamada de margem).
    deficit_de_margem: Dinheiro
    # Dívida acima do valor de mercado estressado: nem vendendo tudo paga.
    exposicao_descoberta: Dinheiro
    em_chamada_antes: bool
    em_chamada_depois: bool


@dataclass(frozen=True, slots=True)
class ResultadoDoStress:
    contas_analisadas: int
    contas_em_chamada_antes: int
    contas_em_chamada_depois: int
    novas_chamadas_de_margem: int
    divida_total: Dinheiro
    valor_das_garantias_antes: Dinheiro
    valor_das_garantias_depois: Dinheiro
    deficit_de_margem_total: Dinheiro
    exposicao_descoberta_total: Dinheiro
    # exposição descoberta x probabilidade de inadimplência, arredondada para cima.
    perda_esperada: Dinheiro
    contas: tuple[ContaEstressada, ...]


def _fator_do_choque(choque: Decimal) -> Decimal:
    """Choque de -0.30 vira fator 0.70. Razão adimensional {1}."""
    if not isinstance(choque, Decimal) or not choque.is_finite() or not -1 <= choque <= 1:
        raise ValorInvalido("Cada choque precisa estar entre -1 (perda total) e 1 (+100%)")
    return Decimal(1) + choque


def _estressar(garantia: GarantiaNaCarteira, fatores: Mapping[TipoAtivo, Decimal]) -> Dinheiro:
    # {BRL} * fator {1} -> {BRL}; garantia sempre para baixo.
    return garantia.valor_de_mercado.aplicar_razao(fatores.get(garantia.tipo_ativo, Decimal(1)))


def simular_stress(
    carteira: Iterable[ContaNaCarteira],
    choques: Mapping[TipoAtivo, Decimal],
    politica: PoliticaRisco,
    probabilidade_de_inadimplencia: Decimal = Decimal(1),
) -> ResultadoDoStress:
    """Aplica os choques e consolida o efeito na carteira.

    A perda esperada parte do pior caso (probabilidade de inadimplência 1): toda conta
    descoberta deixa de pagar. Informe uma probabilidade menor para um cenário realista.
    """
    if not 0 <= probabilidade_de_inadimplencia <= 1:
        raise ValorInvalido("A probabilidade de inadimplência precisa estar entre 0 e 1")
    fatores = {tipo: _fator_do_choque(choque) for tipo, choque in choques.items()}

    contas: list[ContaEstressada] = []
    for conta in carteira:
        valor_antes = Dinheiro.zero()
        valor_depois = Dinheiro.zero()
        limite_antes = Dinheiro.zero()
        limite_depois = Dinheiro.zero()
        for garantia in conta.garantias:
            ltv = politica.ltv_por_ativo.get(garantia.tipo_ativo, Decimal(0))
            estressado = _estressar(garantia, fatores)
            valor_antes += garantia.valor_de_mercado
            valor_depois += estressado
            limite_antes += garantia.valor_de_mercado.aplicar_razao(ltv)
            limite_depois += estressado.aplicar_razao(ltv)
        contas.append(
            ContaEstressada(
                conta_id=conta.conta_id,
                divida=conta.divida,
                valor_das_garantias_antes=valor_antes,
                valor_das_garantias_depois=valor_depois,
                limite_garantido_antes=limite_antes,
                limite_garantido_depois=limite_depois,
                deficit_de_margem=(conta.divida - limite_depois).nao_negativo(),
                exposicao_descoberta=(conta.divida - valor_depois).nao_negativo(),
                em_chamada_antes=conta.divida > limite_antes,
                em_chamada_depois=conta.divida > limite_depois,
            )
        )

    descoberta = Dinheiro.somar(c.exposicao_descoberta for c in contas)
    return ResultadoDoStress(
        contas_analisadas=len(contas),
        contas_em_chamada_antes=sum(c.em_chamada_antes for c in contas),
        contas_em_chamada_depois=sum(c.em_chamada_depois for c in contas),
        novas_chamadas_de_margem=sum(
            c.em_chamada_depois and not c.em_chamada_antes for c in contas
        ),
        divida_total=Dinheiro.somar(c.divida for c in contas),
        valor_das_garantias_antes=Dinheiro.somar(c.valor_das_garantias_antes for c in contas),
        valor_das_garantias_depois=Dinheiro.somar(c.valor_das_garantias_depois for c in contas),
        deficit_de_margem_total=Dinheiro.somar(c.deficit_de_margem for c in contas),
        exposicao_descoberta_total=descoberta,
        # Estimativa de perda arredonda para cima: o lado seguro é o maior.
        perda_esperada=descoberta.aplicar_razao(probabilidade_de_inadimplencia, para_cima=True),
        contas=tuple(contas),
    )


# ============================================================ painel de risco
DIAS_EXTRA_MAXIMOS = 365
DESCONTO_DIARIO_MAXIMO = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class PremissasDaGarantiaExterna:
    """O que pode dar errado quando a garantia está em outro custodiante.

    - `custodiantes_indisponiveis`: não executam dentro do horizonte da análise. O que
      está com eles não recupera nada (premissa conservadora).
    - `dias_extra_de_execucao`: quanto a venda externa demora a mais que a interna.
      Em cada dia de espera o ativo perde `desconto_diario_por_ativo` do valor
      estressado (desconto linear, para baixo).
    """

    custodiantes_indisponiveis: frozenset[str] = frozenset()
    dias_extra_de_execucao: int = 0
    desconto_diario_por_ativo: Mapping[TipoAtivo, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.dias_extra_de_execucao <= DIAS_EXTRA_MAXIMOS:
            raise ValorInvalido(
                f"Dias extras de execução precisam estar entre 0 e {DIAS_EXTRA_MAXIMOS}"
            )
        for desconto in self.desconto_diario_por_ativo.values():
            if not 0 <= desconto <= DESCONTO_DIARIO_MAXIMO:
                raise ValorInvalido("Desconto diário precisa estar entre 0 e 0,10")

    def fator_de_recuperacao(self, garantia: GarantiaNaCarteira) -> Decimal:
        """Quanto do valor estressado a execução recupera. {1}, entre 0 e 1."""
        if garantia.custodiante_id is None:
            return Decimal(1)
        if garantia.custodiante_id in self.custodiantes_indisponiveis:
            return Decimal(0)
        desconto = self.desconto_diario_por_ativo.get(garantia.tipo_ativo, Decimal(0))
        # {1/dia} x {dia} -> {1}
        return max(Decimal(0), Decimal(1) - desconto * self.dias_extra_de_execucao)


@dataclass(frozen=True, slots=True)
class FotoDaCarteira:
    contas_em_chamada: int
    valor_das_garantias: Dinheiro
    # Soma de min(limite garantido, teto por cliente) de cada conta.
    limite_total: Dinheiro
    # O que está em uso: a dívida das contas.
    limite_utilizado: Dinheiro
    # Soma do que cada conta ainda pode usar (nunca negativo por conta).
    limite_disponivel: Dinheiro


@dataclass(frozen=True, slots=True)
class PerdaDoPainel:
    # Dívida total da carteira: o máximo que dá para perder.
    exposicao: Dinheiro
    # Dívida acima do que a venda imediata de todas as garantias rende.
    descoberta_com_execucao_imediata: Dinheiro
    # A mesma conta, com as premissas da garantia externa.
    descoberta_com_premissas_externas: Dinheiro
    # As perdas são a descoberta x taxa de quem não reforça, arredondadas para cima.
    perda_com_execucao_imediata: Dinheiro
    # O que a garantia externa acrescenta: custodiante fora do ar e prazo maior.
    perda_adicional_externa: Dinheiro
    perda_esperada_total: Dinheiro


@dataclass(frozen=True, slots=True)
class FatiaDeConcentracao:
    chave: str
    valor_antes: Dinheiro
    valor_depois: Dinheiro
    garantias: int
    contas: int


@dataclass(frozen=True, slots=True)
class ResultadoDoPainel:
    stress: ResultadoDoStress
    antes: FotoDaCarteira
    depois: FotoDaCarteira
    perda: PerdaDoPainel
    por_ativo: tuple[FatiaDeConcentracao, ...]
    por_custodiante: tuple[FatiaDeConcentracao, ...]


def simular_painel(
    carteira: Iterable[ContaNaCarteira],
    choques: Mapping[TipoAtivo, Decimal],
    politica: PoliticaRisco,
    taxa_sem_reforco: Decimal,
    premissas: PremissasDaGarantiaExterna,
) -> ResultadoDoPainel:
    """O simulador de stress, mais o que a garantia externa muda.

    Reaproveita `simular_stress` para chamada de margem e déficit, e acrescenta: limite
    antes e depois, perda separando o que é da execução imediata e o que é da garantia
    externa, e concentração por ativo e por custodiante.
    """
    contas = list(carteira)
    stress = simular_stress(contas, choques, politica, taxa_sem_reforco)
    fatores = {tipo: _fator_do_choque(choque) for tipo, choque in choques.items()}
    teto = politica.teto_limite_por_cliente

    descoberta_com_premissas = Dinheiro.zero()
    por_ativo: dict[str, _Acumulador] = {}
    por_custodiante: dict[str, _Acumulador] = {}
    for conta in contas:
        recuperado = Dinheiro.zero()
        for garantia in conta.garantias:
            estressado = _estressar(garantia, fatores)
            # {BRL} x {1} -> {BRL}, para baixo: recuperação nunca é otimista.
            recuperado += estressado.aplicar_razao(premissas.fator_de_recuperacao(garantia))
            for grupo, chave in (
                (por_ativo, str(garantia.tipo_ativo)),
                (por_custodiante, garantia.custodiante_id or "interna"),
            ):
                grupo.setdefault(chave, _Acumulador()).somar(
                    garantia.valor_de_mercado, estressado, conta.conta_id
                )
        descoberta_com_premissas += (conta.divida - recuperado).nao_negativo()

    descoberta_imediata = stress.exposicao_descoberta_total
    perda_imediata = descoberta_imediata.aplicar_razao(taxa_sem_reforco, para_cima=True)
    # A descoberta com premissas nunca é menor que a imediata (recupera menos ou igual),
    # então a diferença das perdas arredondadas para cima também não é negativa.
    perda_total = descoberta_com_premissas.aplicar_razao(taxa_sem_reforco, para_cima=True)

    def foto(depois: bool) -> FotoDaCarteira:
        total = utilizado = disponivel = valor = Dinheiro.zero()
        for c in stress.contas:
            limite = c.limite_garantido_depois if depois else c.limite_garantido_antes
            if teto is not None:
                limite = min(limite, teto)
            total += limite
            utilizado += c.divida
            disponivel += (limite - c.divida).nao_negativo()
            valor += c.valor_das_garantias_depois if depois else c.valor_das_garantias_antes
        return FotoDaCarteira(
            contas_em_chamada=stress.contas_em_chamada_depois
            if depois
            else stress.contas_em_chamada_antes,
            valor_das_garantias=valor,
            limite_total=total,
            limite_utilizado=utilizado,
            limite_disponivel=disponivel,
        )

    return ResultadoDoPainel(
        stress=stress,
        antes=foto(depois=False),
        depois=foto(depois=True),
        perda=PerdaDoPainel(
            exposicao=stress.divida_total,
            descoberta_com_execucao_imediata=descoberta_imediata,
            descoberta_com_premissas_externas=descoberta_com_premissas,
            perda_com_execucao_imediata=perda_imediata,
            perda_adicional_externa=perda_total - perda_imediata,
            perda_esperada_total=perda_total,
        ),
        por_ativo=_fatias(por_ativo),
        por_custodiante=_fatias(por_custodiante),
    )


@dataclass(slots=True)
class _Acumulador:
    valor_antes: Dinheiro = field(default_factory=Dinheiro.zero)
    valor_depois: Dinheiro = field(default_factory=Dinheiro.zero)
    garantias: int = 0
    contas: set[str] = field(default_factory=set)

    def somar(self, antes: Dinheiro, depois: Dinheiro, conta_id: str) -> None:
        self.valor_antes += antes
        self.valor_depois += depois
        self.garantias += 1
        self.contas.add(conta_id)


def _fatias(grupo: dict[str, _Acumulador]) -> tuple[FatiaDeConcentracao, ...]:
    fatias = [
        FatiaDeConcentracao(
            chave=chave,
            valor_antes=acumulado.valor_antes,
            valor_depois=acumulado.valor_depois,
            garantias=acumulado.garantias,
            contas=len(acumulado.contas),
        )
        for chave, acumulado in grupo.items()
    ]
    # Ordem estável: do maior para o menor, empate pela chave.
    return tuple(sorted(fatias, key=lambda f: (-f.valor_antes.centavos, f.chave)))
