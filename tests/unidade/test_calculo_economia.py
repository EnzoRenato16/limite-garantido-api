"""Linhas de receita e custo de um cliente ativo médio por mês, e as três visões."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

import pytest

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.calculo import (
    ARRANJO,
    TETO_DE_JUROS_E_ENCARGOS,
    Linha,
    Parte,
    ResultadoDoMes,
    TaxasDePerda,
    calcular_mes,
)
from app.dominio.economia.premissas import FonteDeReceita, NomeDaPremissa, Premissas
from app.dominio.taxas import (
    DIAS_CORRIDOS_NO_ANO,
    MESES_NO_ANO,
    Fracao,
    TaxaAoAno,
    TaxaAoMes,
)
from tests.economia_base import premissas_base

N = NomeDaPremissa

PERDAS = TaxasDePerda(
    cenario_id="custodiante_fora_do_ar",
    nome="Custodiante fora do ar",
    condicional_interna=Fracao(Decimal("0.000052")),
    condicional_externa=Fracao(Decimal("0.08552")),
)

CUSTOS = (
    "funding_fidc",
    "funding_balanco",
    "perda_esperada",
    "processamento",
    "gravame_custodiante",
    "execucao_interna",
    "execucao_externa",
    "tributos_plataforma",
    "tributos_parceiro",
)

ENTRE_PLATAFORMA_E_PARCEIRO = {"repasse_intercambio", "fee_garantia_ativa", "fee_gravame"}


def _linha(resultado: ResultadoDoMes, id_: str) -> Linha:
    achadas = [linha for linha in resultado.linhas if linha.id == id_]
    assert len(achadas) == 1, f"esperava uma linha {id_}, achei {len(achadas)}"
    return achadas[0]


def _ids(resultado: ResultadoDoMes) -> list[str]:
    return [linha.id for linha in resultado.linhas]


def _para_baixo(exato: Decimal) -> int:
    return int(exato.to_integral_value(rounding=ROUND_FLOOR))


def _para_cima(exato: Decimal) -> int:
    return int(exato.to_integral_value(rounding=ROUND_CEILING))


def _saldo_exato(p: Premissas) -> Decimal:
    """G × dias × 12 ÷ 365 + R × n, em centavos (a divisão por último)."""
    with localcontext() as contexto:
        contexto.prec = 34
        g = Decimal(p.gasto_mensal.centavos)
        r = g * p.fracao_de_clientes_no_rotativo.valor * p.fracao_da_fatura_rolada.valor
        if FonteDeReceita.juros_rotativo not in p.receitas_ligadas:
            r = Decimal(0)
        dias = g * p.dias_financiados * MESES_NO_ANO / DIAS_CORRIDOS_NO_ANO
        return dias + r * p.meses_no_rotativo


# ------------------------------------------------------------------ exemplos


def test_tudo_desligado_e_custos_zerados_da_margem_zero() -> None:
    p = premissas_base().com(
        receitas_ligadas=frozenset(),
        dias_financiados=Decimal(0),
        custo_de_processamento_por_cartao=Dinheiro(0),
        gravames_ao_mes=Decimal(0),
        execucoes_ao_ano=Decimal(0),
    )
    r = calcular_mes(p, PERDAS)

    assert _ids(r) == list(CUSTOS)
    assert all(linha.fonte is None for linha in r.linhas)
    assert all(linha.valor == Dinheiro(0) for linha in r.linhas)
    for visao in (r.plataforma, r.parceiro, r.arranjo):
        assert visao.receita == Dinheiro(0)
        assert visao.custo == Dinheiro(0)
        assert visao.margem == Dinheiro(0)
    assert r.perda.saldo_financiado == Dinheiro(0)
    assert r.rotativo_no_teto is False


def test_base_tem_as_16_linhas_na_ordem_do_desenho() -> None:
    r = calcular_mes(premissas_base(), PERDAS)
    assert _ids(r) == [
        "intercambio",
        "repasse_intercambio",
        "fee_garantia_ativa",
        "fee_gravame",
        "receita_sobre_investimento",
        "juros_rotativo",
        "taxa_fidc",
        *CUSTOS,
    ]


def test_plataforma_mais_parceiro_e_o_arranjo() -> None:
    r = calcular_mes(premissas_base(), PERDAS)

    assert r.plataforma.parte == Parte.plataforma
    assert r.parceiro.parte == Parte.parceiro
    assert r.arranjo.parte == ARRANJO
    for visao in (r.plataforma, r.parceiro, r.arranjo):
        assert visao.margem == visao.receita - visao.custo
    assert r.plataforma.margem + r.parceiro.margem == r.arranjo.margem
    assert r.plataforma.margem.positivo
    assert r.parceiro.margem.positivo


def test_transferencias_nao_entram_no_arranjo() -> None:
    base = premissas_base()
    r = calcular_mes(base, PERDAS)
    do_arranjo = {Parte.plataforma, Parte.parceiro}

    transferencias = [
        linha for linha in r.linhas if linha.origem in do_arranjo and linha.destino in do_arranjo
    ]
    assert {linha.id for linha in transferencias} == ENTRE_PLATAFORMA_E_PARCEIRO

    receita_de_fora = Dinheiro.somar(
        linha.valor
        for linha in r.linhas
        if linha.destino in do_arranjo and linha.origem not in do_arranjo
    )
    assert r.arranjo.receita == receita_de_fora
    assert r.arranjo.receita == Dinheiro.somar(
        _linha(r, i).valor
        for i in ("intercambio", "receita_sobre_investimento", "juros_rotativo", "taxa_fidc")
    )

    # Mudar o preço de uma transferência não muda a receita do arranjo (só os tributos).
    outro = calcular_mes(base.com(fee_por_garantia_ativa=Dinheiro(900)), PERDAS)
    assert outro.arranjo.receita == r.arranjo.receita
    assert outro.plataforma.receita > r.plataforma.receita


def test_intercambio_exemplo() -> None:
    r = calcular_mes(premissas_base(), PERDAS)

    intercambio = _linha(r, "intercambio")
    assert intercambio.valor == Dinheiro(1800)
    assert (intercambio.origem, intercambio.destino) == (Parte.lojista, Parte.plataforma)
    assert intercambio.fonte is FonteDeReceita.intercambio
    assert intercambio.formula == "R$ 1.500,00 × 1,20%"
    assert intercambio.premissas == (N.gasto_mensal, N.taxa_de_intercambio)

    repasse = _linha(r, "repasse_intercambio")
    assert repasse.valor == Dinheiro(900)
    assert (repasse.origem, repasse.destino) == (Parte.plataforma, Parte.parceiro)
    assert repasse.fonte is FonteDeReceita.intercambio
    assert "50,00%" in repasse.formula
    assert r.parceiro.receita == Dinheiro(900)


def test_transferencia_arredonda_para_baixo_uma_vez_so() -> None:
    # 1.333,33 × 1,20% = 15,99996 -> 15,99; repasse de 50% = 7,99998 -> 7,99.
    p = premissas_base().com(gasto_mensal=Dinheiro(133333))
    r = calcular_mes(p, PERDAS)
    assert _linha(r, "intercambio").valor == Dinheiro(1599)
    assert _linha(r, "repasse_intercambio").valor == Dinheiro(799)


def test_fees_do_parceiro_para_a_plataforma() -> None:
    r = calcular_mes(premissas_base(), PERDAS)
    # 1,5 garantias × R$ 2,00 = R$ 3,00.
    assert _linha(r, "fee_garantia_ativa").valor == Dinheiro(300)
    # 0,10 × (0,70 × R$ 1,00 + 0,30 × R$ 8,00) = R$ 0,31.
    assert _linha(r, "fee_gravame").valor == Dinheiro(31)
    for id_ in ("fee_garantia_ativa", "fee_gravame"):
        linha = _linha(r, id_)
        assert (linha.origem, linha.destino) == (Parte.parceiro, Parte.plataforma)


def test_juros_do_rotativo_respeitam_o_teto() -> None:
    assert Decimal(1) == TETO_DE_JUROS_E_ENCARGOS
    p = premissas_base().com(
        taxa_do_rotativo_ao_mes=TaxaAoMes(Decimal("0.30")), meses_no_rotativo=6
    )
    r = calcular_mes(p, PERDAS)

    juros = _linha(r, "juros_rotativo")
    # 1,30^6 − 1 = 3,83 passa de 100%: o fator fica em 1, e os juros valem o rolado.
    # Rolado: R$ 1.500,00 × 5% × 30% = R$ 22,50.
    assert r.rotativo_no_teto is True
    assert juros.valor == Dinheiro(2250)
    assert "teto" in juros.formula
    assert "Lei 14.690/2023" in juros.formula


def test_juros_do_rotativo_abaixo_do_teto() -> None:
    r = calcular_mes(premissas_base(), PERDAS)
    # R$ 22,50 × (1,08² − 1) = R$ 22,50 × 0,1664 = R$ 3,744 -> R$ 3,74.
    assert r.rotativo_no_teto is False
    juros = _linha(r, "juros_rotativo")
    assert juros.valor == Dinheiro(374)
    assert (juros.origem, juros.destino) == (Parte.cliente, Parte.plataforma)
    assert "teto" not in juros.formula


def test_rotativo_sem_juros() -> None:
    base = premissas_base()

    # Taxa zero: a linha existe e vale zero, e o saldo rolado continua sendo financiado.
    sem_taxa = base.com(taxa_do_rotativo_ao_mes=TaxaAoMes(Decimal(0)))
    r = calcular_mes(sem_taxa, PERDAS)
    assert _linha(r, "juros_rotativo").valor == Dinheiro(0)
    assert r.rotativo_no_teto is False
    assert r.perda.saldo_financiado == Dinheiro(_para_cima(_saldo_exato(sem_taxa)))

    # Fonte desligada: sem rotativo não há linha nem saldo rolado.
    desligado = base.com(receitas_ligadas=base.receitas_ligadas - {FonteDeReceita.juros_rotativo})
    r = calcular_mes(desligado, PERDAS)
    assert "juros_rotativo" not in _ids(r)
    # Só os 10 dias de gasto: R$ 1.500,00 × 10 ÷ (365/12) = R$ 493,15068... -> R$ 493,16.
    assert r.perda.saldo_financiado == Dinheiro(49316)


def test_saldo_financiado_soma_dias_e_rotativo() -> None:
    r = calcular_mes(premissas_base(), PERDAS)
    # R$ 493,15068... + R$ 22,50 × 2 = R$ 538,15068... -> R$ 538,16.
    assert r.perda.saldo_financiado == Dinheiro(53816)


@pytest.mark.parametrize(("dias", "centavos"), [(6, 7200), (4, 4800), (10, 12000)])
def test_saldo_financiado_exato_quando_a_conta_da_inteiro(dias: int, centavos: int) -> None:
    # R$ 365,00 × dias × 12 ÷ 365 dá um número inteiro de centavos. Dividindo primeiro,
    # 12/365 vira dízima e o produto cai um fio acima ou abaixo do inteiro, o que move o
    # arredondamento em 1 centavo. Sem rotativo, o saldo é só esse.
    p = premissas_base().com(
        gasto_mensal=Dinheiro(36500),
        dias_financiados=Decimal(dias),
        fracao_de_clientes_no_rotativo=Fracao(Decimal(0)),
    )
    r = calcular_mes(p, PERDAS)

    assert r.perda.saldo_financiado == Dinheiro(centavos)
    saldo = Decimal(centavos)
    with localcontext() as contexto:
        contexto.prec = 34
        fidc = p.cdi_ao_ano.composta_com(p.spread_fidc_ao_ano).ao_mes().valor
        balanco = p.cdi_ao_ano.composta_com(p.spread_balanco_ao_ano).ao_mes().valor
        taxa_fidc = p.taxa_fidc_ao_ano.ao_mes().valor
        assert _linha(r, "funding_fidc").valor == Dinheiro(
            _para_cima(saldo * Decimal("0.60") * fidc)
        )
        assert _linha(r, "funding_balanco").valor == Dinheiro(
            _para_cima(saldo * Decimal("0.40") * balanco)
        )
        assert _linha(r, "taxa_fidc").valor == Dinheiro(
            _para_baixo(saldo * Decimal("0.60") * taxa_fidc)
        )
        assert _linha(r, "perda_esperada").valor == Dinheiro(
            _para_cima(saldo * r.perda.taxa_ao_mes.valor)
        )
    texto = f"saldo financiado de {Dinheiro(centavos).formatado()}"
    for id_ in ("funding_fidc", "funding_balanco", "perda_esperada", "taxa_fidc"):
        assert texto in _linha(r, id_).formula


def test_receita_sobre_investimento_so_na_interna() -> None:
    base = premissas_base()
    toda_externa = base.com(fracao_interna=Fracao(Decimal(0)), fracao_externa=Fracao(Decimal(1)))
    assert _linha(calcular_mes(toda_externa, PERDAS), "receita_sobre_investimento").valor == (
        Dinheiro(0)
    )

    toda_interna = base.com(fracao_interna=Fracao(Decimal(1)), fracao_externa=Fracao(Decimal(0)))
    mensal = TaxaAoAno(Decimal("0.003")).ao_mes().valor
    esperado = _para_baixo(Decimal(800000) * mensal)
    linha = _linha(calcular_mes(toda_interna, PERDAS), "receita_sobre_investimento")
    assert linha.valor == Dinheiro(esperado)
    assert (linha.origem, linha.destino) == (Parte.cliente, Parte.plataforma)

    # No base, 70% do saldo é interno.
    no_base = _linha(calcular_mes(base, PERDAS), "receita_sobre_investimento")
    assert no_base.valor == Dinheiro(_para_baixo(Decimal(800000) * Decimal("0.70") * mensal))


def test_perda_usa_mistura_das_condicionais() -> None:
    p = premissas_base()
    r = calcular_mes(p, PERDAS)
    conta = r.perda

    mista = Decimal("0.70") * Decimal("0.000052") + Decimal("0.30") * Decimal("0.08552")
    assert conta.condicional_interna == PERDAS.condicional_interna
    assert conta.condicional_externa == PERDAS.condicional_externa
    assert conta.condicional_mista == Fracao(mista)
    ao_ano = Decimal("0.005") + Decimal("0.05") * mista
    assert conta.taxa_ao_ano == TaxaAoAno(ao_ano)
    assert conta.taxa_ao_mes == TaxaAoAno(ao_ano).ao_mes()
    assert conta.taxa_base_ao_ano == p.taxa_base_de_perda_ao_ano
    assert conta.frequencia_ao_ano == p.frequencia_do_cenario_ao_ano

    perda = _linha(r, "perda_esperada")
    with localcontext() as contexto:
        contexto.prec = 34
        esperado = _para_cima(_saldo_exato(p) * conta.taxa_ao_mes.valor)
    assert perda.valor == Dinheiro(esperado)
    assert (perda.origem, perda.destino) == (Parte.plataforma, Parte.inadimplencia)

    # Mais garantia externa, mais perda: a externa perde mais no cenário.
    externa = calcular_mes(
        p.com(fracao_interna=Fracao(Decimal(0)), fracao_externa=Fracao(Decimal(1))), PERDAS
    )
    assert externa.perda.condicional_mista == PERDAS.condicional_externa
    assert _linha(externa, "perda_esperada").valor >= perda.valor


def test_funding_e_fidc_dividem_o_saldo_pela_fracao_cedida() -> None:
    p = premissas_base()
    r = calcular_mes(p, PERDAS)
    saldo = _saldo_exato(p)
    with localcontext() as contexto:
        contexto.prec = 34
        cdi = p.cdi_ao_ano
        fidc = cdi.composta_com(p.spread_fidc_ao_ano).ao_mes().valor
        balanco = cdi.composta_com(p.spread_balanco_ao_ano).ao_mes().valor
        taxa = p.taxa_fidc_ao_ano.ao_mes().valor
        assert _linha(r, "funding_fidc").valor == Dinheiro(
            _para_cima(saldo * Decimal("0.60") * fidc)
        )
        assert _linha(r, "funding_balanco").valor == Dinheiro(
            _para_cima(saldo * Decimal("0.40") * balanco)
        )
        assert _linha(r, "taxa_fidc").valor == Dinheiro(_para_baixo(saldo * Decimal("0.60") * taxa))
    destinos = {
        "funding_fidc": Parte.fidc,
        "funding_balanco": Parte.financiadores,
        "processamento": Parte.fornecedores,
        "gravame_custodiante": Parte.custodiante,
        "execucao_interna": Parte.fornecedores,
        "execucao_externa": Parte.custodiante,
        "tributos_plataforma": Parte.governo,
    }
    for id_, destino in destinos.items():
        linha = _linha(r, id_)
        assert (linha.origem, linha.destino) == (Parte.plataforma, destino)
    assert _linha(r, "taxa_fidc").origem == Parte.fidc
    assert _linha(r, "tributos_parceiro").origem == Parte.parceiro


def test_custos_operacionais_arredondam_para_cima() -> None:
    r = calcular_mes(premissas_base(), PERDAS)
    assert _linha(r, "processamento").valor == Dinheiro(300)
    # 0,10 × 0,30 × R$ 5,00 = R$ 0,15.
    assert _linha(r, "gravame_custodiante").valor == Dinheiro(15)
    # 0,02 ÷ 12 × 0,70 × R$ 15,00 = R$ 0,0175 -> R$ 0,02.
    assert _linha(r, "execucao_interna").valor == Dinheiro(2)
    # 0,02 ÷ 12 × 0,30 × R$ 60,00 = R$ 0,03 exatos.
    assert _linha(r, "execucao_externa").valor == Dinheiro(3)


def test_tributos_sobre_a_receita_ja_arredondada() -> None:
    r = calcular_mes(premissas_base(), PERDAS)
    receita_plataforma = Dinheiro.somar(
        linha.valor for linha in r.linhas if linha.destino == Parte.plataforma
    )
    assert _linha(r, "tributos_plataforma").valor == Dinheiro(
        _para_cima(Decimal(receita_plataforma.centavos) * Decimal("0.10"))
    )
    # Parceiro recebe R$ 9,00 de repasse: 10% = R$ 0,90.
    assert _linha(r, "tributos_parceiro").valor == Dinheiro(90)


@pytest.mark.parametrize(
    ("fonte", "somem"),
    [
        (FonteDeReceita.intercambio, {"intercambio", "repasse_intercambio"}),
        (FonteDeReceita.fee_garantia_ativa, {"fee_garantia_ativa"}),
        (FonteDeReceita.fee_gravame, {"fee_gravame"}),
        (FonteDeReceita.receita_sobre_investimento, {"receita_sobre_investimento"}),
        (FonteDeReceita.juros_rotativo, {"juros_rotativo"}),
        (FonteDeReceita.taxa_fidc, {"taxa_fidc"}),
    ],
)
def test_fonte_desligada_nao_gera_linha(fonte: FonteDeReceita, somem: set[str]) -> None:
    base = premissas_base()
    todas = set(_ids(calcular_mes(base, PERDAS)))
    sem = calcular_mes(base.com(receitas_ligadas=base.receitas_ligadas - {fonte}), PERDAS)
    assert set(_ids(sem)) == todas - somem


def test_cada_linha_cita_premissas_e_tem_formula() -> None:
    r = calcular_mes(premissas_base(), PERDAS)
    for linha in r.linhas:
        assert linha.formula
        assert linha.nome
        assert linha.premissas
        assert all(isinstance(nome, NomeDaPremissa) for nome in linha.premissas)
