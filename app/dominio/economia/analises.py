"""Análises sobre o mês de um cliente ativo médio: ano, carteira, ponto de equilíbrio,
interna x externa e tornado.

Tudo sai das linhas mensais já arredondadas. O ano é o mês vezes 12, o parceiro médio é
o mês vezes os clientes por parceiro e a carteira é o mês vezes o total de clientes:
multiplicar por inteiros mantém a conta fechando centavo por centavo.

- **Ponto de equilíbrio**: N = ⌈custo fixo mensal ÷ margem da plataforma por cliente⌉,
  em centavos inteiros. Sem margem positiva não há N.
- **Interna x externa**: a margem da plataforma por cliente com toda garantia interna e
  com toda externa. Se a externa rende menos, procura em centavos o fee de gravame
  externo e o gasto mensal que empatam, na direção em que a margem da externa sobe.
  Quando não há valor, o motivo diz por quê.
- **Tornado**: cada premissa com faixa razoável vai aos dois extremos dela, cortados
  para respeitar a coerência, e a barra vai do mínimo ao máximo de {baixo, base, alto}.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, localcontext
from enum import Enum
from fractions import Fraction

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.calculo import (
    ResultadoDoMes,
    TaxasDePerda,
    Visao,
    calcular_mes,
)
from app.dominio.economia.premissas import (
    LIMITE_DE_CLIENTES_NA_CARTEIRA,
    EsquemaDaPremissa,
    FonteDeReceita,
    NomeDaPremissa,
    Premissas,
    converter_para_o_campo,
)
from app.dominio.erros import ValorInvalido
from app.dominio.taxas import MESES_NO_ANO, Fracao

N = NomeDaPremissa

MOTIVO_SEM_EQUILIBRIO = (
    "Não há ponto de equilíbrio: a margem da plataforma por cliente é zero ou negativa, "
    "então nenhum número de clientes paga o custo fixo."
)
MOTIVO_SEM_EQUILIBRIO_SEM_CUSTO_FIXO = (
    "Não há ponto de equilíbrio: o custo fixo é zero, mas a margem da plataforma por "
    "cliente é zero ou negativa, então nenhum cliente dá lucro."
)

# Por que não há fee ou gasto que empata a externa com a interna.
MOTIVO_EXTERNA_NAO_CUSTA_MAIS = (
    "A garantia externa não custa mais que a interna: não há diferença para empatar."
)
MOTIVO_SEM_GRAVAME = (
    "Sem gravame por cliente (0 por mês), o fee de gravame externo não mexe na margem."
)
MOTIVO_FEE_DE_GRAVAME_DESLIGADO = (
    "A receita de fee de gravame está desligada: o fee de gravame externo não entra na conta."
)

# Busca do valor que empata: passos de um centavo a partir da estimativa, no máximo
# estes; se acabarem, a bissecção fecha a conta. Poucos passos limitam o pior caso.
MAXIMO_DE_PASSOS = 50
# Distância entre os dois pontos que medem a inclinação. O fee mexe só em uma linha e
# nos tributos, então R$ 1,00 basta. O gasto mexe em oito linhas arredondadas e cada
# real dele vale menos de um centavo de margem: com R$ 1,00 a inclinação seria só o
# ruído do arredondamento, por isso R$ 100,00.
PASSO_DO_FEE = Dinheiro(100)
PASSO_DO_GASTO = Dinheiro(10000)
# Correções pela secante entre o início e a estimativa, antes de andar centavo a centavo.
_CORRECOES = 6

_ZERO = Fracao(Decimal(0))
_UM = Fracao(Decimal(1))


# ------------------------------------------------------------------ resultados
@dataclass(frozen=True, slots=True)
class PontoDeEquilibrio:
    """Clientes ativos que pagam o custo fixo, ou None com o motivo. Um dos dois, nunca
    os dois nem nenhum."""

    clientes: int | None
    motivo: str | None

    def __post_init__(self) -> None:
        if (self.clientes is None) == (self.motivo is None):
            raise ValorInvalido("Ponto de equilíbrio tem clientes ou motivo, só um dos dois")


@dataclass(frozen=True, slots=True)
class ResultadoDaCarteira:
    """O mês por cliente multiplicado pela carteira. O custo fixo sai da plataforma."""

    clientes_ativos: int  # parceiros × clientes por parceiro
    plataforma_mes: Dinheiro  # margem × clientes − custo fixo
    plataforma_ano: Dinheiro
    parceiro_por_parceiro_mes: Dinheiro  # margem do parceiro × clientes por parceiro
    parceiro_por_parceiro_ano: Dinheiro
    arranjo_mes: Dinheiro  # margem do arranjo × clientes − custo fixo


@dataclass(frozen=True, slots=True)
class ComparacaoInternaExterna:
    """Margem por cliente por mês com toda garantia interna e com toda externa.

    `custo_a_mais_da_externa` é da plataforma e pode ser negativo (a externa rende
    mais). Os valores de empate só existem quando a externa custa mais. Cada valor vem
    com um motivo, e só um dos dois existe: o valor, ou o porquê de não haver valor (a
    externa não custa mais; sem gravame; fonte desligada; nenhum valor na faixa aceita
    empata).
    """

    margem_interna: Dinheiro
    margem_externa: Dinheiro
    custo_a_mais_da_externa: Dinheiro
    fee_de_gravame_externo_para_empatar: Dinheiro | None
    motivo_fee: str | None
    # Valor de fronteira: com ele a externa empata e um centavo mais perto do valor atual
    # não empata. A busca vai para onde a margem da externa sobe: o fee para cima; o
    # gasto para cima ou para baixo, conforme ele traga mais receita ou mais custo. Para
    # o fee a margem é monótona e a fronteira é o menor valor que empata; para o gasto os
    # arredondamentos fazem a margem oscilar centavos, e a fronteira é a mais próxima
    # da estimativa.
    gasto_para_empatar: Dinheiro | None
    motivo_gasto: str | None
    gasto_cabe_no_limite: bool | None  # gasto que empata ≤ saldo × LTV; None sem gasto
    parceiro_interna: Dinheiro
    parceiro_externa: Dinheiro

    def __post_init__(self) -> None:
        if (self.fee_de_gravame_externo_para_empatar is None) == (self.motivo_fee is None):
            raise ValorInvalido("Fee para empatar tem valor ou motivo, só um dos dois")
        if (self.gasto_para_empatar is None) == (self.motivo_gasto is None):
            raise ValorInvalido("Gasto para empatar tem valor ou motivo, só um dos dois")
        if (self.gasto_para_empatar is None) != (self.gasto_cabe_no_limite is None):
            raise ValorInvalido("Só o gasto que empata diz se cabe no limite")


@dataclass(frozen=True, slots=True)
class BarraDoTornado:
    """Margem da plataforma por cliente por mês nos dois extremos de uma premissa.

    `valor_baixo` e `valor_alto` são crus (dinheiro em reais), já cortados pela
    coerência. A barra vai do mínimo ao máximo de {baixo, base, alto}.
    """

    premissa: NomeDaPremissa
    valor_baixo: Decimal
    valor_alto: Decimal
    margem_no_baixo: Dinheiro
    margem_no_alto: Dinheiro
    minimo: Dinheiro
    maximo: Dinheiro
    amplitude: Dinheiro
    cortado_pela_coerencia: bool


@dataclass(frozen=True, slots=True)
class ResultadoEconomico:
    premissas: Premissas
    mes: ResultadoDoMes
    ano: dict[str, Visao]  # chaves "plataforma", "parceiro" e "arranjo"
    carteira: ResultadoDaCarteira
    equilibrio: PontoDeEquilibrio
    interna_x_externa: ComparacaoInternaExterna
    tornado: tuple[BarraDoTornado, ...]


# ------------------------------------------------------------------- simulação
def simular_negocio(
    premissas: Premissas,
    perdas: TaxasDePerda,
    esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa],
    incluir_tornado: bool = True,
) -> ResultadoEconomico:
    """O mês, o ano, a carteira e as análises de um conjunto de premissas válidas."""
    mes = calcular_mes(premissas, perdas)
    margem = mes.plataforma.margem
    return ResultadoEconomico(
        premissas=premissas,
        mes=mes,
        ano=resultado_do_ano(mes),
        carteira=resultado_da_carteira(premissas, mes),
        equilibrio=ponto_de_equilibrio(margem, premissas.custo_fixo_mensal),
        interna_x_externa=comparar_interna_externa(premissas, perdas, esquema),
        tornado=analisar_tornado(premissas, perdas, esquema, margem) if incluir_tornado else (),
    )


def _vezes(valor: Dinheiro, quantidade: int) -> Dinheiro:
    """{BRL} × inteiro -> {BRL}, exato."""
    return Dinheiro(valor.centavos * quantidade)


def resultado_do_ano(mes: ResultadoDoMes) -> dict[str, Visao]:
    """Cada visão vezes 12 meses: {BRL/mês} × {mês/ano} -> {BRL/ano}.

    Chaves "plataforma", "parceiro" e "arranjo" (o arranjo não é uma `Parte`).
    """
    return {
        str(visao.parte): Visao.de(
            str(visao.parte),
            _vezes(visao.receita, MESES_NO_ANO),
            _vezes(visao.custo, MESES_NO_ANO),
        )
        for visao in (mes.plataforma, mes.parceiro, mes.arranjo)
    }


def resultado_da_carteira(premissas: Premissas, mes: ResultadoDoMes) -> ResultadoDaCarteira:
    clientes = premissas.parceiros * premissas.clientes_por_parceiro  # {cliente}
    fixo = premissas.custo_fixo_mensal
    plataforma_mes = _vezes(mes.plataforma.margem, clientes) - fixo
    parceiro_mes = _vezes(mes.parceiro.margem, premissas.clientes_por_parceiro)
    return ResultadoDaCarteira(
        clientes_ativos=clientes,
        plataforma_mes=plataforma_mes,
        plataforma_ano=_vezes(plataforma_mes, MESES_NO_ANO),
        parceiro_por_parceiro_mes=parceiro_mes,
        parceiro_por_parceiro_ano=_vezes(parceiro_mes, MESES_NO_ANO),
        arranjo_mes=_vezes(mes.arranjo.margem, clientes) - fixo,
    )


# ---------------------------------------------------------- ponto de equilíbrio
def ponto_de_equilibrio(
    margem_por_cliente_mes: Dinheiro, custo_fixo_mes: Dinheiro
) -> PontoDeEquilibrio:
    """N = ⌈F ÷ m⌉ em centavos: N clientes pagam o custo fixo e N − 1 não pagam."""
    m = margem_por_cliente_mes.centavos
    f = max(custo_fixo_mes.centavos, 0)
    if m <= 0:
        motivo = MOTIVO_SEM_EQUILIBRIO if f > 0 else MOTIVO_SEM_EQUILIBRIO_SEM_CUSTO_FIXO
        return PontoDeEquilibrio(None, motivo)
    return PontoDeEquilibrio(-(-f // m), None)


# ------------------------------------------------------------ interna x externa
def _margem_da_plataforma(premissas: Premissas, perdas: TaxasDePerda) -> Dinheiro:
    return calcular_mes(premissas, perdas).plataforma.margem


def comparar_interna_externa(
    premissas: Premissas,
    perdas: TaxasDePerda,
    esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa],
) -> ComparacaoInternaExterna:
    interna = calcular_mes(premissas.com(fracao_interna=_UM, fracao_externa=_ZERO), perdas)
    toda_externa = premissas.com(fracao_interna=_ZERO, fracao_externa=_UM)
    externa = calcular_mes(toda_externa, perdas)
    alvo = interna.plataforma.margem
    custo_a_mais = alvo - externa.plataforma.margem

    fee: Dinheiro | None = None
    gasto: Dinheiro | None = None
    cabe: bool | None = None
    if not custo_a_mais.positivo:
        motivo_fee: str | None = MOTIVO_EXTERNA_NAO_CUSTA_MAIS
        motivo_gasto: str | None = MOTIVO_EXTERNA_NAO_CUSTA_MAIS
    else:
        # O fee só fecha a diferença se houver gravame e a receita de fee estiver ligada.
        if premissas.gravames_ao_mes <= 0:
            motivo_fee = MOTIVO_SEM_GRAVAME
        elif FonteDeReceita.fee_gravame not in premissas.receitas_ligadas:
            motivo_fee = MOTIVO_FEE_DE_GRAVAME_DESLIGADO
        else:
            fee, motivo_fee = _valor_para_empatar(
                toda_externa,
                perdas,
                esquema[N.fee_gravame_externa],
                alvo,
                PASSO_DO_FEE,
                "fee de gravame externo",
            )
        # O gasto é procurado sem exigir coerência; o limite só entra na resposta.
        gasto, motivo_gasto = _valor_para_empatar(
            toda_externa, perdas, esquema[N.gasto_mensal], alvo, PASSO_DO_GASTO, "gasto mensal"
        )
        if gasto is not None:
            limite = premissas.saldo_em_garantia.aplicar_razao(premissas.ltv_medio.valor)
            cabe = gasto <= limite

    return ComparacaoInternaExterna(
        margem_interna=alvo,
        margem_externa=externa.plataforma.margem,
        custo_a_mais_da_externa=custo_a_mais,
        fee_de_gravame_externo_para_empatar=fee,
        motivo_fee=motivo_fee,
        gasto_para_empatar=gasto,
        motivo_gasto=motivo_gasto,
        gasto_cabe_no_limite=cabe,
        parceiro_interna=interna.parceiro.margem,
        parceiro_externa=externa.parceiro.margem,
    )


class _SemEmpate(Enum):
    """Por que a busca não devolveu valor."""

    fora_da_faixa = "fora_da_faixa"  # nem a busca nem as duas pontas da faixa empatam
    nao_confirmado = "nao_confirmado"  # o recálculo da margem não confirmou a fronteira


def _valor_para_empatar(
    toda_externa: Premissas,
    perdas: TaxasDePerda,
    faixa: EsquemaDaPremissa,
    alvo: Dinheiro,
    passo: Dinheiro,
    rotulo: str,
) -> tuple[Dinheiro | None, str | None]:
    """Valor de fronteira da premissa em dinheiro `faixa.nome`, dentro da faixa aceita,
    ou None e o motivo. As outras premissas ficam como estão."""
    campo = faixa.nome.value

    def margem_com(centavos: int) -> int:
        variada = toda_externa.com(**{campo: Dinheiro(centavos)})
        return _margem_da_plataforma(variada, perdas).centavos

    atual: Dinheiro = getattr(toda_externa, campo)
    minimo = Dinheiro.de_decimal(faixa.minimo)
    maximo = Dinheiro.de_decimal(faixa.maximo)
    achado = _buscar_empate(
        margem_com, alvo.centavos, atual.centavos, passo.centavos, minimo.centavos, maximo.centavos
    )
    match achado:
        case _SemEmpate.fora_da_faixa:
            return None, (
                f"Nenhum {rotulo} na faixa aceita, de {minimo.formatado()} a "
                f"{maximo.formatado()}, empata a externa com a interna."
            )
        case _SemEmpate.nao_confirmado:
            return None, (
                f"A busca do {rotulo} que empata não se confirmou ao recalcular a margem."
            )
        case _:
            return Dinheiro(achado), None


def _buscar_empate(
    margem_com: Callable[[int], int],
    alvo: int,
    inicio: int,
    passo: int,
    minimo: int,
    maximo: int,
) -> int | _SemEmpate:
    """Valor de fronteira em centavos, entre `minimo` e `maximo`: a margem nele alcança o
    alvo e um centavo mais perto de `inicio` não alcança.

    Pré-condição: em `inicio` a margem ainda não alcança o alvo (a externa custa mais).

    Mede a inclinação de `inicio` a `inicio + passo` e de `inicio` a `inicio - passo` e
    procura primeiro para o lado em que a margem sobe: pula para a estimativa da reta,
    corrige pela secante e anda centavo a centavo (no máximo `MAXIMO_DE_PASSOS`); se os
    passos acabam, bissecção entre um valor que não empata e um que empata. Se por esse
    lado nada empata, ou se a margem não sobe para lado nenhum (o que também acontece
    quando o passo é pequeno demais para mexer numa linha arredondada), olha a ponta da
    faixa de cada lado e, se ela empata, a fronteira sai por bissecção entre o início e
    ela. A resposta é confirmada recalculando a margem.

    Sem valor, diz por quê: `fora_da_faixa` quando nem a busca nem as pontas da faixa
    alcançam o alvo; `nao_confirmado` quando o recálculo não confirma a fronteira.

    Cada linha é arredondada, então a margem oscila um ou dois centavos em torno da reta.
    Quando a inclinação é pequena (o gasto mexe pouco na margem) pode haver mais de uma
    fronteira; o valor devolvido é a mais próxima da estimativa.
    """
    memoria: dict[int, int] = {}

    def margem(valor: int) -> int:
        if valor not in memoria:
            memoria[valor] = margem_com(valor)
        return memoria[valor]

    inicio = min(max(inicio, minimo), maximo)
    m_inicio = margem(inicio)
    acima = min(inicio + passo, maximo)
    abaixo = max(inicio - passo, minimo)
    if acima > inicio and margem(acima) > m_inicio:
        sentidos = ((1, True), (-1, False))
    elif abaixo < inicio and margem(abaixo) > m_inicio:
        sentidos = ((-1, True), (1, False))
    else:
        sentidos = ((1, False), (-1, False))

    for sentido, pela_reta in sentidos:
        # Para baixo é para cima no espelho: x = −valor, com a faixa invertida.
        def espelhada(x: int, s: int = sentido) -> int:
            return margem(s * x)

        def espelhada_sem_memoria(x: int, s: int = sentido) -> int:
            return margem_com(s * x)

        de, ate = (minimo, maximo) if sentido == 1 else (-maximo, -minimo)
        if pela_reta:
            achado = _procurar_pela_reta(espelhada, alvo, sentido * inicio, passo, de, ate)
        else:
            achado = _procurar_pela_ponta(espelhada, alvo, sentido * inicio, ate)
        if achado is not None:
            if not _e_fronteira(espelhada_sem_memoria, alvo, achado, de):
                return _SemEmpate.nao_confirmado
            return sentido * achado
    return _SemEmpate.fora_da_faixa


def _procurar_pela_reta(
    margem: Callable[[int], int], alvo: int, inicio: int, passo: int, minimo: int, maximo: int
) -> int | None:
    """Para cima a partir de `inicio`, onde a margem sobe: estimativa, secante e passos."""
    m_inicio = margem(inicio)

    def estimar(outro: int) -> int:
        inclinacao = Fraction(margem(outro) - m_inicio, outro - inicio)  # {centavo/centavo}
        salto = math.ceil((alvo - m_inicio) / inclinacao)
        return min(max(inicio + salto, minimo), maximo)

    valor = estimar(min(inicio + passo, maximo))
    for _ in range(_CORRECOES):
        if valor == inicio or (margem(valor) - m_inicio) * (valor - inicio) <= 0:
            break
        corrigido = estimar(valor)
        if corrigido == valor:
            break
        valor = corrigido
    return _andar_ate_a_fronteira(margem, alvo, inicio, valor, minimo, maximo)


def _procurar_pela_ponta(
    margem: Callable[[int], int], alvo: int, inicio: int, maximo: int
) -> int | None:
    """Sem inclinação para seguir: se a ponta da faixa empata, bissecção até ela."""
    if maximo <= inicio or margem(maximo) < alvo:
        return None
    return _bisseccao(margem, alvo, abaixo=inicio, acima=maximo)


def _andar_ate_a_fronteira(
    margem: Callable[[int], int],
    alvo: int,
    inicio: int,
    valor: int,
    minimo: int,
    maximo: int,
) -> int | None:
    """Centavo a centavo a partir da estimativa; bissecção se os passos acabarem."""
    if margem(valor) >= alvo:
        for _ in range(MAXIMO_DE_PASSOS):
            if valor == minimo or margem(valor - 1) < alvo:
                return valor
            valor -= 1
        return _bisseccao(margem, alvo, abaixo=inicio, acima=valor)
    for _ in range(MAXIMO_DE_PASSOS):
        if valor >= maximo:
            return None
        valor += 1
        if margem(valor) >= alvo:
            return valor
    acima = _primeiro_que_alcanca(margem, alvo, valor, maximo)
    return None if acima is None else _bisseccao(margem, alvo, abaixo=valor, acima=acima)


def _primeiro_que_alcanca(
    margem: Callable[[int], int], alvo: int, desde: int, maximo: int
) -> int | None:
    """Dobra a distância a partir de `desde` até achar um valor que empata, ou o máximo."""
    distancia = 1
    while True:
        candidato = min(desde + distancia, maximo)
        if margem(candidato) >= alvo:
            return candidato
        if candidato >= maximo:
            return None
        distancia *= 2


def _bisseccao(margem: Callable[[int], int], alvo: int, abaixo: int, acima: int) -> int:
    """Com margem(abaixo) < alvo ≤ margem(acima), um valor que empata e um centavo a menos
    não empata."""
    while acima - abaixo > 1:
        meio = (abaixo + acima) // 2
        if margem(meio) >= alvo:
            acima = meio
        else:
            abaixo = meio
    return acima


def _e_fronteira(margem_com: Callable[[int], int], alvo: int, valor: int, minimo: int) -> bool:
    """Confirmação, sem memória: no valor empata e um centavo a menos não empata."""
    if margem_com(valor) < alvo:
        return False
    return valor == minimo or margem_com(valor - 1) < alvo


# ----------------------------------------------------------------------- tornado
def analisar_tornado(
    premissas: Premissas,
    perdas: TaxasDePerda,
    esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa],
    margem_base: Dinheiro | None = None,
) -> tuple[BarraDoTornado, ...]:
    """Uma barra por premissa com faixa razoável, da maior amplitude para a menor.

    A fração interna fica fora: ela anda junto com a externa (interna = 1 − externa).
    Empate de amplitude desempata pelo nome da premissa.
    """
    base = margem_base if margem_base is not None else _margem_da_plataforma(premissas, perdas)
    barras: list[BarraDoTornado] = []
    for nome in NomeDaPremissa:
        if nome is N.fracao_interna or nome not in esquema:
            continue
        faixa = esquema[nome]
        if faixa.razoavel_minimo is None or faixa.razoavel_maximo is None:
            continue
        baixo, cortou_baixo = _coerente(premissas, nome, faixa.razoavel_minimo)
        alto, cortou_alto = _coerente(premissas, nome, faixa.razoavel_maximo)
        no_baixo = _margem_da_plataforma(_variar(premissas, faixa, baixo), perdas)
        no_alto = _margem_da_plataforma(_variar(premissas, faixa, alto), perdas)
        minimo = min(no_baixo, base, no_alto)
        maximo = max(no_baixo, base, no_alto)
        barras.append(
            BarraDoTornado(
                premissa=nome,
                valor_baixo=baixo,
                valor_alto=alto,
                margem_no_baixo=no_baixo,
                margem_no_alto=no_alto,
                minimo=minimo,
                maximo=maximo,
                amplitude=maximo - minimo,
                cortado_pela_coerencia=cortou_baixo or cortou_alto,
            )
        )
    barras.sort(key=lambda barra: (-barra.amplitude.centavos, barra.premissa.value))
    return tuple(barras)


def _variar(premissas: Premissas, faixa: EsquemaDaPremissa, valor: Decimal) -> Premissas:
    """Troca uma premissa pelo valor cru; a fração externa leva a interna junto."""
    mudancas: dict[str, object] = {faixa.nome.value: converter_para_o_campo(valor, faixa.tipo)}
    if faixa.nome is N.fracao_externa:
        mudancas[N.fracao_interna.value] = Fracao(1 - valor)
    return premissas.com(**mudancas)


def _coerente(premissas: Premissas, nome: NomeDaPremissa, valor: Decimal) -> tuple[Decimal, bool]:
    """O valor cortado para manter a coerência das premissas, e se houve corte.

    - o gasto não passa de ⌊saldo × LTV⌋;
    - o saldo não fica abaixo de ⌈gasto ÷ LTV⌉, em centavos;
    - o LTV não fica abaixo de gasto ÷ saldo, arredondado para cima em 4 casas;
    - parceiros × clientes por parceiro não passa do limite da carteira.
    """
    p = premissas
    match nome:
        case N.gasto_mensal:
            limite = p.saldo_em_garantia.aplicar_razao(p.ltv_medio.valor).para_decimal()
            if valor > limite:
                return limite, True
        case N.saldo_em_garantia:
            piso = _saldo_minimo(p.gasto_mensal, p.ltv_medio.valor)
            if piso is not None and valor < piso.para_decimal():
                return piso.para_decimal(), True
        case N.ltv_medio:
            if p.saldo_em_garantia.positivo:
                # ⌈G × 10⁴ ÷ S⌉ ÷ 10⁴, em inteiros: S × LTV ≥ G, então ⌊S × LTV⌋ ≥ G.
                dez_mil_avos = -(-p.gasto_mensal.centavos * 10_000 // p.saldo_em_garantia.centavos)
                piso_ltv = Decimal(dez_mil_avos).scaleb(-4)
                if valor < piso_ltv:
                    return piso_ltv, True
        case N.parceiros:
            teto = LIMITE_DE_CLIENTES_NA_CARTEIRA // p.clientes_por_parceiro
            if valor > teto:
                return Decimal(teto), True
        case N.clientes_por_parceiro:
            teto = LIMITE_DE_CLIENTES_NA_CARTEIRA // p.parceiros
            if valor > teto:
                return Decimal(teto), True
        case _:
            pass
    return valor, False


def _saldo_minimo(gasto: Dinheiro, ltv: Decimal) -> Dinheiro | None:
    """Menor saldo em centavos com ⌊saldo × LTV⌋ ≥ gasto: {BRL} ÷ {1} -> {BRL}."""
    if ltv <= 0:
        return None
    with localcontext() as contexto:
        contexto.prec = 34
        centavos = int((Decimal(gasto.centavos) / ltv).to_integral_value(rounding=ROUND_CEILING))
    while Dinheiro(centavos).aplicar_razao(ltv) < gasto:  # pragma: no cover - trava
        centavos += 1
    return Dinheiro(centavos)
