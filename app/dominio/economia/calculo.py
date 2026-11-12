"""Linhas de receita e custo de um cliente ativo médio por mês, e as visões por parte.

Cada linha tem origem e destino. O valor exato é calculado em `Decimal`, em centavos,
com 34 dígitos, e arredondado uma única vez: receita para baixo, custo para cima. As
transferências entre plataforma e parceiro (repasse do intercâmbio e os dois fees)
arredondam para baixo, e o mesmo valor aparece nos dois lados.

As visões:
- plataforma e parceiro: receita é o que tem a parte como destino, custo é o que tem a
  parte como origem;
- arranjo (plataforma e parceiro juntos): ignora as linhas que vão de um para o outro.
  Por construção, plataforma + parceiro = arranjo.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.premissas import FonteDeReceita, NomeDaPremissa, Premissas
from app.dominio.erros import ValorInvalido
from app.dominio.taxas import (
    DIAS_CORRIDOS_NO_ANO,
    MESES_NO_ANO,
    Fracao,
    FrequenciaAoAno,
    TaxaAoAno,
    TaxaAoMes,
)

N = NomeDaPremissa

_PRECISAO = 34

# Lei 14.690/2023, regulamentada pela Resolução CMN 5.112/2023: juros e encargos do
# rotativo nunca passam de 100% do valor original da dívida. {1}
TETO_DE_JUROS_E_ENCARGOS = Decimal(1)

ARRANJO = "arranjo"  # nome da visão de plataforma e parceiro juntos


class Parte(StrEnum):
    cliente = "cliente"
    lojista = "lojista"
    parceiro = "parceiro"
    plataforma = "plataforma"
    fidc = "fidc"
    custodiante = "custodiante"
    fornecedores = "fornecedores"
    financiadores = "financiadores"
    governo = "governo"
    inadimplencia = "inadimplencia"  # o que não volta


_DO_ARRANJO = frozenset({Parte.plataforma, Parte.parceiro})


@dataclass(frozen=True, slots=True)
class TaxasDePerda:
    """Perda condicional a um choque do cenário, medida com toda garantia interna e
    com toda externa: perda esperada ÷ exposição, {1} por evento."""

    cenario_id: str
    nome: str
    condicional_interna: Fracao
    condicional_externa: Fracao


@dataclass(frozen=True, slots=True)
class Linha:
    """Um fluxo de dinheiro por cliente por mês, de `origem` para `destino`."""

    id: str
    nome: str
    fonte: FonteDeReceita | None  # a receita que liga ou desliga a linha; custo é None
    origem: Parte
    destino: Parte
    valor: Dinheiro
    formula: str
    premissas: tuple[NomeDaPremissa, ...]


@dataclass(frozen=True, slots=True)
class Visao:
    """Receita, custo e margem de uma parte. `parte` é uma `Parte` ou `ARRANJO`."""

    parte: str
    receita: Dinheiro
    custo: Dinheiro
    margem: Dinheiro

    def __post_init__(self) -> None:
        if self.margem != self.receita - self.custo:
            raise ValorInvalido("Margem precisa ser receita menos custo", parte=self.parte)

    @classmethod
    def de(cls, parte: str, receita: Dinheiro, custo: Dinheiro) -> Visao:
        return cls(parte, receita, custo, receita - custo)


@dataclass(frozen=True, slots=True)
class ContaDaPerda:
    """A conta inteira da perda anual, para a tela mostrar de onde ela vem."""

    taxa_base_ao_ano: TaxaAoAno
    frequencia_ao_ano: FrequenciaAoAno
    condicional_interna: Fracao
    condicional_externa: Fracao
    condicional_mista: Fracao  # fi × interna + fe × externa
    taxa_ao_ano: TaxaAoAno  # base + frequência × mista
    taxa_ao_mes: TaxaAoMes
    saldo_financiado: Dinheiro  # arredondado para cima; as linhas usam o valor exato


@dataclass(frozen=True, slots=True)
class ResultadoDoMes:
    linhas: tuple[Linha, ...]
    plataforma: Visao
    parceiro: Visao
    arranjo: Visao
    perda: ContaDaPerda
    rotativo_no_teto: bool


# --------------------------------------------------------------------- texto
def _br(texto: str) -> str:
    """Troca a notação inglesa pela portuguesa: milhar com ponto, decimal com vírgula."""
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def _numero(valor: Decimal) -> str:
    """Quantidade sem zeros à direita: 1,5 e 0,1."""
    return _br(f"{valor.normalize():,f}") if valor != 0 else "0"


def _percentual(valor: Decimal) -> str:
    """Fração como percentual: 0,012 -> "1,20%". Taxas miúdas ganham até 4 casas."""
    with localcontext() as contexto:
        contexto.prec = _PRECISAO
        cem_vezes = valor * 100
        casas = 2
        while casas < 4 and cem_vezes != cem_vezes.quantize(Decimal(1).scaleb(-casas)):
            casas += 1
        arredondado = cem_vezes.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_EVEN)
    return _br(f"{arredondado:,f}") + "%"


def _reais(dinheiro: Dinheiro) -> str:
    return dinheiro.formatado()


# --------------------------------------------------------------- arredondar
def _para_baixo(centavos_exatos: Decimal) -> Dinheiro:
    """Receita e transferência: {centavos} exatos -> {BRL}, para baixo."""
    return Dinheiro(int(centavos_exatos.to_integral_value(rounding=ROUND_FLOOR)))


def _para_cima(centavos_exatos: Decimal) -> Dinheiro:
    """Custo: {centavos} exatos -> {BRL}, para cima."""
    return Dinheiro(int(centavos_exatos.to_integral_value(rounding=ROUND_CEILING)))


def _centavos(dinheiro: Dinheiro) -> Decimal:
    return Decimal(dinheiro.centavos)


# ------------------------------------------------------------------- cálculo
def calcular_mes(premissas: Premissas, perdas: TaxasDePerda) -> ResultadoDoMes:
    """As 16 linhas de um cliente ativo médio por mês e as três visões."""
    with localcontext() as contexto:
        contexto.prec = _PRECISAO
        return _calcular(premissas, perdas)


def _calcular(p: Premissas, perdas: TaxasDePerda) -> ResultadoDoMes:
    ligadas = p.receitas_ligadas
    g = _centavos(p.gasto_mensal)  # {centavos/mês}
    fi = p.fracao_interna.valor
    fe = p.fracao_externa.valor
    fc = p.fracao_cedida_ao_fidc.valor
    linhas: list[Linha] = []

    # Rotativo: R vai para o rotativo a cada mês e fica n meses. Sem o produto, R = 0.
    tem_rotativo = FonteDeReceita.juros_rotativo in ligadas
    fcr = p.fracao_de_clientes_no_rotativo.valor
    ffr = p.fracao_da_fatura_rolada.valor
    rolado = g * fcr * ffr if tem_rotativo else Decimal(0)  # {centavos/mês}
    n = p.meses_no_rotativo  # {mês}

    # Saldo financiado médio: G × dias ÷ (365/12) + R × n. {centavos}
    # A divisão vem por último: 12/365 é dízima, e dividir primeiro deixa um fio acima ou
    # abaixo do inteiro quando a conta é exata, o que move o arredondamento em 1 centavo.
    # {centavos/mês} × {dia} × {mês/ano} ÷ {dia/ano} -> {centavos}
    dias = g * p.dias_financiados * MESES_NO_ANO / DIAS_CORRIDOS_NO_ANO
    saldo = dias + rolado * n
    saldo_arredondado = _para_cima(saldo)
    premissas_do_saldo: tuple[NomeDaPremissa, ...] = (N.gasto_mensal, N.dias_financiados)
    if tem_rotativo:
        premissas_do_saldo += (
            N.fracao_de_clientes_no_rotativo,
            N.fracao_da_fatura_rolada,
            N.meses_no_rotativo,
        )
    texto_do_saldo = f"saldo financiado de {_reais(saldo_arredondado)}"

    # ------------------------------------------------------------ receitas
    if FonteDeReceita.intercambio in ligadas:
        taxa = p.taxa_de_intercambio.valor
        da_plataforma = p.fracao_do_intercambio_da_plataforma.valor
        linhas.append(
            Linha(
                "intercambio",
                "Intercâmbio",
                FonteDeReceita.intercambio,
                Parte.lojista,
                Parte.plataforma,
                _para_baixo(g * taxa),
                f"{_reais(p.gasto_mensal)} × {_percentual(taxa)}",
                (N.gasto_mensal, N.taxa_de_intercambio),
            )
        )
        linhas.append(
            Linha(
                "repasse_intercambio",
                "Repasse do intercâmbio ao parceiro",
                FonteDeReceita.intercambio,
                Parte.plataforma,
                Parte.parceiro,
                _para_baixo(g * taxa * (1 - da_plataforma)),
                f"{_reais(p.gasto_mensal)} × {_percentual(taxa)} × "
                f"(1 − {_percentual(da_plataforma)} da plataforma)",
                (N.gasto_mensal, N.taxa_de_intercambio, N.fracao_do_intercambio_da_plataforma),
            )
        )

    if FonteDeReceita.fee_garantia_ativa in ligadas:
        linhas.append(
            Linha(
                "fee_garantia_ativa",
                "Fee por garantia ativa",
                FonteDeReceita.fee_garantia_ativa,
                Parte.parceiro,
                Parte.plataforma,
                _para_baixo(p.garantias_por_cliente * _centavos(p.fee_por_garantia_ativa)),
                f"{_numero(p.garantias_por_cliente)} garantias × "
                f"{_reais(p.fee_por_garantia_ativa)}",
                (N.garantias_por_cliente, N.fee_por_garantia_ativa),
            )
        )

    if FonteDeReceita.fee_gravame in ligadas:
        fee_medio = fi * _centavos(p.fee_gravame_interna) + fe * _centavos(p.fee_gravame_externa)
        linhas.append(
            Linha(
                "fee_gravame",
                "Fee de gravame",
                FonteDeReceita.fee_gravame,
                Parte.parceiro,
                Parte.plataforma,
                _para_baixo(p.gravames_ao_mes * fee_medio),
                f"{_numero(p.gravames_ao_mes)} gravames × ({_percentual(fi)} × "
                f"{_reais(p.fee_gravame_interna)} + {_percentual(fe)} × "
                f"{_reais(p.fee_gravame_externa)})",
                (
                    N.gravames_ao_mes,
                    N.fracao_interna,
                    N.fee_gravame_interna,
                    N.fracao_externa,
                    N.fee_gravame_externa,
                ),
            )
        )

    if FonteDeReceita.receita_sobre_investimento in ligadas:
        ao_ano = p.taxa_sobre_investimento_ao_ano
        ao_mes = ao_ano.ao_mes().valor
        linhas.append(
            Linha(
                "receita_sobre_investimento",
                "Receita sobre o investimento em garantia",
                FonteDeReceita.receita_sobre_investimento,
                Parte.cliente,
                Parte.plataforma,
                _para_baixo(_centavos(p.saldo_em_garantia) * fi * ao_mes),
                f"{_reais(p.saldo_em_garantia)} × {_percentual(fi)} interna × "
                f"{_percentual(ao_ano.valor)} a.a. ({_percentual(ao_mes)} a.m.)",
                (N.saldo_em_garantia, N.fracao_interna, N.taxa_sobre_investimento_ao_ano),
            )
        )

    rotativo_no_teto = False
    if tem_rotativo:
        i = p.taxa_do_rotativo_ao_mes.valor
        composto = (1 + i) ** n - 1  # {1}: juros compostos de n meses
        rotativo_no_teto = composto > TETO_DE_JUROS_E_ENCARGOS
        fator = min(composto, TETO_DE_JUROS_E_ENCARGOS)
        rolado_texto = (
            f"{_reais(p.gasto_mensal)} × {_percentual(fcr)} no rotativo × "
            f"{_percentual(ffr)} da fatura"
        )
        if rotativo_no_teto:
            formula = (
                f"{rolado_texto} × 100% (teto de 100% da Lei 14.690/2023: "
                f"(1 + {_percentual(i)})^{n} − 1 = {_percentual(composto)} passa do teto)"
            )
        else:
            formula = f"{rolado_texto} × ((1 + {_percentual(i)})^{n} − 1)"
        linhas.append(
            Linha(
                "juros_rotativo",
                "Juros do rotativo",
                FonteDeReceita.juros_rotativo,
                Parte.cliente,
                Parte.plataforma,
                _para_baixo(rolado * fator),
                formula,
                (
                    N.gasto_mensal,
                    N.fracao_de_clientes_no_rotativo,
                    N.fracao_da_fatura_rolada,
                    N.taxa_do_rotativo_ao_mes,
                    N.meses_no_rotativo,
                ),
            )
        )

    if FonteDeReceita.taxa_fidc in ligadas:
        ao_ano = p.taxa_fidc_ao_ano
        ao_mes = ao_ano.ao_mes().valor
        linhas.append(
            Linha(
                "taxa_fidc",
                "Taxa do FIDC",
                FonteDeReceita.taxa_fidc,
                Parte.fidc,
                Parte.plataforma,
                _para_baixo(saldo * fc * ao_mes),
                f"{texto_do_saldo} × {_percentual(fc)} cedida × "
                f"{_percentual(ao_ano.valor)} a.a. ({_percentual(ao_mes)} a.m.)",
                (*premissas_do_saldo, N.fracao_cedida_ao_fidc, N.taxa_fidc_ao_ano),
            )
        )

    # -------------------------------------------------------------- custos
    cdi = p.cdi_ao_ano
    for id_, nome, destino, fracao, onde, spread, nome_do_spread, campo_do_spread in (
        (
            "funding_fidc",
            "Funding do FIDC",
            Parte.fidc,
            fc,
            "cedida ao FIDC",
            p.spread_fidc_ao_ano,
            "spread do FIDC",
            N.spread_fidc_ao_ano,
        ),
        (
            "funding_balanco",
            "Funding de balanço",
            Parte.financiadores,
            1 - fc,
            "no balanço",
            p.spread_balanco_ao_ano,
            "spread de balanço",
            N.spread_balanco_ao_ano,
        ),
    ):
        ao_mes = cdi.composta_com(spread).ao_mes().valor
        linhas.append(
            Linha(
                id_,
                nome,
                None,
                Parte.plataforma,
                destino,
                _para_cima(saldo * fracao * ao_mes),
                f"{texto_do_saldo} × {_percentual(fracao)} {onde} × {_percentual(ao_mes)} a.m. "
                f"(CDI {_percentual(cdi.valor)} ⊕ {nome_do_spread} "
                f"{_percentual(spread.valor)} a.a.)",
                (*premissas_do_saldo, N.fracao_cedida_ao_fidc, N.cdi_ao_ano, campo_do_spread),
            )
        )

    conta = _conta_da_perda(p, perdas, saldo_arredondado)
    linhas.append(
        Linha(
            "perda_esperada",
            "Perda esperada",
            None,
            Parte.plataforma,
            Parte.inadimplencia,
            _para_cima(saldo * conta.taxa_ao_mes.valor),
            f"{texto_do_saldo} × {_percentual(conta.taxa_ao_mes.valor)} a.m. "
            f"({_percentual(conta.taxa_base_ao_ano.valor)} a.a. + "
            f"{_numero(conta.frequencia_ao_ano.valor)} evento/ano × "
            f"{_percentual(conta.condicional_mista.valor)} por evento = "
            f"{_percentual(conta.taxa_ao_ano.valor)} a.a.)",
            (
                *premissas_do_saldo,
                N.taxa_base_de_perda_ao_ano,
                N.frequencia_do_cenario_ao_ano,
                N.fracao_interna,
                N.fracao_externa,
            ),
        )
    )

    linhas.append(
        Linha(
            "processamento",
            "Processamento do cartão",
            None,
            Parte.plataforma,
            Parte.fornecedores,
            _para_cima(_centavos(p.custo_de_processamento_por_cartao)),
            f"1 cartão × {_reais(p.custo_de_processamento_por_cartao)}",
            (N.custo_de_processamento_por_cartao,),
        )
    )
    linhas.append(
        Linha(
            "gravame_custodiante",
            "Gravame no custodiante",
            None,
            Parte.plataforma,
            Parte.custodiante,
            _para_cima(p.gravames_ao_mes * fe * _centavos(p.custo_do_gravame_externo)),
            f"{_numero(p.gravames_ao_mes)} gravames × {_percentual(fe)} externa × "
            f"{_reais(p.custo_do_gravame_externo)}",
            (N.gravames_ao_mes, N.fracao_externa, N.custo_do_gravame_externo),
        )
    )
    # Execuções por ano viram mês dividindo por 12 (frequência não se compõe). A divisão
    # vem por último para não criar dízima onde a conta é exata.
    for id_, nome, destino, fracao, custo, nome_da_fracao, campo_da_fracao, campo_do_custo in (
        (
            "execucao_interna",
            "Execução de garantia interna",
            Parte.fornecedores,
            fi,
            p.custo_de_execucao_interna,
            "interna",
            N.fracao_interna,
            N.custo_de_execucao_interna,
        ),
        (
            "execucao_externa",
            "Execução de garantia externa",
            Parte.custodiante,
            fe,
            p.custo_de_execucao_externa,
            "externa",
            N.fracao_externa,
            N.custo_de_execucao_externa,
        ),
    ):
        linhas.append(
            Linha(
                id_,
                nome,
                None,
                Parte.plataforma,
                destino,
                _para_cima(p.execucoes_ao_ano * fracao * _centavos(custo) / MESES_NO_ANO),
                f"{_numero(p.execucoes_ao_ano)} execuções/ano ÷ 12 × {_percentual(fracao)} "
                f"{nome_da_fracao} × {_reais(custo)}",
                (N.execucoes_ao_ano, campo_da_fracao, campo_do_custo),
            )
        )

    aliquota = p.aliquota_sobre_receita.valor
    for parte, id_, nome, de_quem in (
        (Parte.plataforma, "tributos_plataforma", "Tributos da plataforma", "da plataforma"),
        (Parte.parceiro, "tributos_parceiro", "Tributos do parceiro", "do parceiro"),
    ):
        receita = Dinheiro.somar(linha.valor for linha in linhas if linha.destino == parte)
        linhas.append(
            Linha(
                id_,
                nome,
                None,
                parte,
                Parte.governo,
                _para_cima(aliquota * _centavos(receita)),
                f"{_percentual(aliquota)} × {_reais(receita)} de receita {de_quem}",
                (N.aliquota_sobre_receita,),
            )
        )

    return ResultadoDoMes(
        linhas=tuple(linhas),
        plataforma=_visao_da_parte(Parte.plataforma, linhas),
        parceiro=_visao_da_parte(Parte.parceiro, linhas),
        arranjo=_visao_do_arranjo(linhas),
        perda=conta,
        rotativo_no_teto=rotativo_no_teto,
    )


def _conta_da_perda(p: Premissas, perdas: TaxasDePerda, saldo: Dinheiro) -> ContaDaPerda:
    """Taxa anual = base + frequência × (fi × interna + fe × externa)."""
    fi = p.fracao_interna.valor
    fe = p.fracao_externa.valor
    # A mistura de duas frações com pesos que somam 1 fica em [0, 1]; o min só protege
    # do último dígito quando as condicionais vêm com 34 dígitos.
    mista = min(
        fi * perdas.condicional_interna.valor + fe * perdas.condicional_externa.valor,
        Decimal(1),
    )
    # {1/ano} + {evento/ano} × {1/evento} -> {1/ano}
    ao_ano = TaxaAoAno(
        p.taxa_base_de_perda_ao_ano.valor + p.frequencia_do_cenario_ao_ano.valor * mista
    )
    return ContaDaPerda(
        taxa_base_ao_ano=p.taxa_base_de_perda_ao_ano,
        frequencia_ao_ano=p.frequencia_do_cenario_ao_ano,
        condicional_interna=perdas.condicional_interna,
        condicional_externa=perdas.condicional_externa,
        condicional_mista=Fracao(mista),
        taxa_ao_ano=ao_ano,
        taxa_ao_mes=ao_ano.ao_mes(),
        saldo_financiado=saldo,
    )


def _visao_da_parte(parte: Parte, linhas: list[Linha]) -> Visao:
    receita = Dinheiro.somar(linha.valor for linha in linhas if linha.destino == parte)
    custo = Dinheiro.somar(linha.valor for linha in linhas if linha.origem == parte)
    return Visao.de(parte, receita, custo)


def _visao_do_arranjo(linhas: list[Linha]) -> Visao:
    """Plataforma e parceiro juntos: as linhas entre os dois não entram."""
    receita = Dinheiro.somar(
        linha.valor
        for linha in linhas
        if linha.destino in _DO_ARRANJO and linha.origem not in _DO_ARRANJO
    )
    custo = Dinheiro.somar(
        linha.valor
        for linha in linhas
        if linha.origem in _DO_ARRANJO and linha.destino not in _DO_ARRANJO
    )
    return Visao.de(ARRANJO, receita, custo)
