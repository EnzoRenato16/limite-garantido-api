"""Carteira, ano, ponto de equilíbrio, interna x externa e tornado."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.analises import (
    MOTIVO_SEM_EQUILIBRIO,
    BarraDoTornado,
    _andar_ate_a_fronteira,
    _buscar_empate,
    _e_fronteira,
    analisar_tornado,
    comparar_interna_externa,
    ponto_de_equilibrio,
    simular_negocio,
)
from app.dominio.economia.calculo import ARRANJO, TaxasDePerda, calcular_mes
from app.dominio.economia.premissas import FonteDeReceita, NomeDaPremissa, Premissas
from app.dominio.taxas import Fracao, TaxaAoAno
from tests.economia_base import ESQUEMA, premissas_base

N = NomeDaPremissa

PERDAS = TaxasDePerda(
    cenario_id="custodiante_fora_do_ar",
    nome="Custodiante fora do ar",
    condicional_interna=Fracao(Decimal("0.000052")),
    condicional_externa=Fracao(Decimal("0.08552")),
)

TODA_INTERNA = {"fracao_interna": Fracao(Decimal(1)), "fracao_externa": Fracao(Decimal(0))}
TODA_EXTERNA = {"fracao_interna": Fracao(Decimal(0)), "fracao_externa": Fracao(Decimal(1))}


def _margem(p: Premissas) -> Dinheiro:
    return calcular_mes(p, PERDAS).plataforma.margem


def _barra(barras: tuple[BarraDoTornado, ...], nome: NomeDaPremissa) -> BarraDoTornado:
    achadas = [barra for barra in barras if barra.premissa is nome]
    assert len(achadas) == 1, f"esperava uma barra {nome}, achei {len(achadas)}"
    return achadas[0]


# ------------------------------------------------------------ ponto de equilíbrio


def test_equilibrio_exemplo() -> None:
    # R$ 4.000,00 ÷ R$ 3,00 = 1.333,33... -> 1.334 clientes.
    equilibrio = ponto_de_equilibrio(Dinheiro(300), Dinheiro(400000))
    assert equilibrio.clientes == 1334
    assert equilibrio.motivo is None
    # Conta exata não sobe: R$ 3.000,00 ÷ R$ 3,00 = 1.000.
    assert ponto_de_equilibrio(Dinheiro(300), Dinheiro(300000)).clientes == 1000


@pytest.mark.parametrize("margem", [0, -1, -12345])
def test_sem_equilibrio_com_margem_zero_ou_negativa(margem: int) -> None:
    equilibrio = ponto_de_equilibrio(Dinheiro(margem), Dinheiro(400000))
    assert equilibrio.clientes is None
    assert equilibrio.motivo == MOTIVO_SEM_EQUILIBRIO
    assert "Não há ponto de equilíbrio" in MOTIVO_SEM_EQUILIBRIO


def test_custo_fixo_zero_equilibrio_em_zero_clientes() -> None:
    equilibrio = ponto_de_equilibrio(Dinheiro(1), Dinheiro(0))
    assert equilibrio.clientes == 0
    assert equilibrio.motivo is None


def test_equilibrio_no_base_vem_da_margem_da_plataforma() -> None:
    p = premissas_base()
    r = simular_negocio(p, PERDAS, ESQUEMA, incluir_tornado=False)
    m = r.mes.plataforma.margem.centavos
    assert r.equilibrio == ponto_de_equilibrio(r.mes.plataforma.margem, p.custo_fixo_mensal)
    assert r.equilibrio.clientes is not None
    assert r.equilibrio.clientes * m >= p.custo_fixo_mensal.centavos
    assert (r.equilibrio.clientes - 1) * m < p.custo_fixo_mensal.centavos


# --------------------------------------------------------------- ano e carteira


def test_ano_e_12_vezes_o_mes() -> None:
    r = simular_negocio(premissas_base(), PERDAS, ESQUEMA, incluir_tornado=False)
    assert set(r.ano) == {"plataforma", "parceiro", ARRANJO}
    for chave, mes in (
        ("plataforma", r.mes.plataforma),
        ("parceiro", r.mes.parceiro),
        (ARRANJO, r.mes.arranjo),
    ):
        ano = r.ano[chave]
        assert ano.parte == chave
        assert ano.receita == Dinheiro(mes.receita.centavos * 12)
        assert ano.custo == Dinheiro(mes.custo.centavos * 12)
        assert ano.margem == Dinheiro(mes.margem.centavos * 12)
    assert r.ano["plataforma"].margem + r.ano["parceiro"].margem == r.ano[ARRANJO].margem


def test_carteira_multiplica_o_mes_por_inteiros() -> None:
    p = premissas_base()
    r = simular_negocio(p, PERDAS, ESQUEMA, incluir_tornado=False)
    c = r.carteira
    clientes = 5 * 20000
    fixo = 400000_00
    assert c.clientes_ativos == clientes
    assert c.plataforma_mes == Dinheiro(r.mes.plataforma.margem.centavos * clientes - fixo)
    assert c.plataforma_ano == Dinheiro(c.plataforma_mes.centavos * 12)
    assert c.parceiro_por_parceiro_mes == Dinheiro(r.mes.parceiro.margem.centavos * 20000)
    assert c.parceiro_por_parceiro_ano == Dinheiro(c.parceiro_por_parceiro_mes.centavos * 12)
    assert c.arranjo_mes == Dinheiro(r.mes.arranjo.margem.centavos * clientes - fixo)


def test_resultado_guarda_premissas_e_mes() -> None:
    p = premissas_base()
    r = simular_negocio(p, PERDAS, ESQUEMA, incluir_tornado=False)
    assert r.premissas == p
    assert r.mes == calcular_mes(p, PERDAS)
    assert r.tornado == ()


# ------------------------------------------------------------- interna x externa


def test_externa_custa_mais_no_base() -> None:
    p = premissas_base()
    c = comparar_interna_externa(p, PERDAS, ESQUEMA)

    interna = calcular_mes(p.com(**TODA_INTERNA), PERDAS)
    externa = calcular_mes(p.com(**TODA_EXTERNA), PERDAS)
    assert c.margem_interna == interna.plataforma.margem
    assert c.margem_externa == externa.plataforma.margem
    assert c.parceiro_interna == interna.parceiro.margem
    assert c.parceiro_externa == externa.parceiro.margem
    assert c.custo_a_mais_da_externa == c.margem_interna - c.margem_externa
    assert c.custo_a_mais_da_externa.positivo


def test_fee_para_empatar_empata() -> None:
    p = premissas_base()
    c = comparar_interna_externa(p, PERDAS, ESQUEMA)

    fee = c.fee_de_gravame_externo_para_empatar
    assert fee is not None
    assert fee > p.fee_gravame_externa
    externa = p.com(**TODA_EXTERNA)
    assert _margem(externa.com(fee_gravame_externa=fee)) >= c.margem_interna
    um_centavo_a_menos = fee - Dinheiro(1)
    assert _margem(externa.com(fee_gravame_externa=um_centavo_a_menos)) < c.margem_interna


def test_gasto_para_empatar_empata_e_cabe_no_base() -> None:
    p = premissas_base()
    c = comparar_interna_externa(p, PERDAS, ESQUEMA)

    gasto = c.gasto_para_empatar
    assert gasto is not None
    assert gasto > p.gasto_mensal
    externa = p.com(**TODA_EXTERNA)
    assert _margem(externa.com(gasto_mensal=gasto)) >= c.margem_interna
    assert _margem(externa.com(gasto_mensal=gasto - Dinheiro(1))) < c.margem_interna
    limite = p.saldo_em_garantia.aplicar_razao(p.ltv_medio.valor)
    assert gasto <= limite
    assert c.gasto_cabe_no_limite is True


def test_gasto_para_empatar_avisa_quando_nao_cabe() -> None:
    # Saldo pequeno (limite de R$ 1.700,00) e execução externa cara: o gasto que
    # empataria passa do limite que a garantia gera.
    p = premissas_base().com(
        saldo_em_garantia=Dinheiro(200000),
        custo_de_execucao_externa=Dinheiro(60000),
    )
    c = comparar_interna_externa(p, PERDAS, ESQUEMA)

    gasto = c.gasto_para_empatar
    assert gasto is not None
    limite = p.saldo_em_garantia.aplicar_razao(p.ltv_medio.valor)
    assert limite == Dinheiro(170000)
    assert gasto > limite
    assert c.gasto_cabe_no_limite is False
    externa = p.com(**TODA_EXTERNA)
    assert _margem(externa.com(gasto_mensal=gasto)) >= c.margem_interna
    assert _margem(externa.com(gasto_mensal=gasto - Dinheiro(1))) < c.margem_interna


def test_externa_melhor_nao_procura_empate() -> None:
    # Fee externo alto: a externa rende mais que a interna, não há o que empatar.
    p = premissas_base().com(fee_gravame_externa=Dinheiro(50000))
    c = comparar_interna_externa(p, PERDAS, ESQUEMA)
    assert not c.custo_a_mais_da_externa.positivo
    assert c.fee_de_gravame_externo_para_empatar is None
    assert c.gasto_para_empatar is None
    assert c.gasto_cabe_no_limite is None


def test_sem_gravame_nao_ha_fee_para_empatar() -> None:
    base = premissas_base()
    sem_gravames = comparar_interna_externa(base.com(gravames_ao_mes=Decimal(0)), PERDAS, ESQUEMA)
    assert sem_gravames.custo_a_mais_da_externa.positivo
    assert sem_gravames.fee_de_gravame_externo_para_empatar is None

    sem_fee = base.com(receitas_ligadas=base.receitas_ligadas - {FonteDeReceita.fee_gravame})
    desligado = comparar_interna_externa(sem_fee, PERDAS, ESQUEMA)
    assert desligado.custo_a_mais_da_externa.positivo
    assert desligado.fee_de_gravame_externo_para_empatar is None


def test_fee_fora_da_faixa_aceita_nao_empata() -> None:
    # Execução externa muito cara: nem R$ 500,00 de fee (o teto aceito) empata.
    p = premissas_base().com(
        execucoes_ao_ano=Decimal(12), custo_de_execucao_externa=Dinheiro(500000)
    )
    c = comparar_interna_externa(p, PERDAS, ESQUEMA)
    assert c.custo_a_mais_da_externa.positivo
    assert c.fee_de_gravame_externo_para_empatar is None


def test_gasto_sem_inclinacao_positiva_nao_empata() -> None:
    # Sem intercâmbio, sem rotativo e sem FIDC, mais gasto só traz custo de funding.
    base = premissas_base()
    p = base.com(
        receitas_ligadas=base.receitas_ligadas
        - {FonteDeReceita.intercambio, FonteDeReceita.juros_rotativo, FonteDeReceita.taxa_fidc}
    )
    c = comparar_interna_externa(p, PERDAS, ESQUEMA)
    assert c.custo_a_mais_da_externa.positivo
    assert c.gasto_para_empatar is None
    assert c.gasto_cabe_no_limite is None


# ----------------------------------------------------------------------- tornado


def test_tornado_tem_as_premissas_com_faixa_razoavel() -> None:
    barras = analisar_tornado(premissas_base(), PERDAS, ESQUEMA)
    esperadas = {
        nome
        for nome in N
        if ESQUEMA[nome].razoavel_minimo is not None and nome is not N.fracao_interna
    }
    assert {barra.premissa for barra in barras} == esperadas
    assert len(barras) == 33


def test_tornado_ordenado_por_amplitude() -> None:
    barras = analisar_tornado(premissas_base(), PERDAS, ESQUEMA)
    chaves = [(-barra.amplitude.centavos, barra.premissa.value) for barra in barras]
    assert chaves == sorted(chaves)
    assert barras[0].amplitude.positivo
    for barra in barras:
        assert barra.minimo == min(barra.margem_no_baixo, barra.margem_no_alto, barra.minimo)
        assert barra.maximo == max(barra.margem_no_baixo, barra.margem_no_alto, barra.maximo)
        assert barra.amplitude == barra.maximo - barra.minimo


def test_tornado_usa_os_extremos_da_faixa_razoavel() -> None:
    p = premissas_base()
    barras = analisar_tornado(p, PERDAS, ESQUEMA)

    cdi = _barra(barras, N.cdi_ao_ano)
    assert (cdi.valor_baixo, cdi.valor_alto) == (Decimal("0.10"), Decimal("0.16"))
    assert cdi.cortado_pela_coerencia is False
    assert cdi.margem_no_baixo == _margem(p.com(cdi_ao_ano=TaxaAoAno(Decimal("0.10"))))
    assert cdi.margem_no_alto == _margem(p.com(cdi_ao_ano=TaxaAoAno(Decimal("0.16"))))
    assert cdi.margem_no_alto < cdi.margem_no_baixo

    meses = _barra(barras, N.meses_no_rotativo)
    assert meses.margem_no_alto == _margem(p.com(meses_no_rotativo=6))

    fixo = _barra(barras, N.custo_fixo_mensal)
    assert fixo.amplitude == Dinheiro(0)


def test_tornado_acopla_as_fracoes() -> None:
    p = premissas_base()
    barra = _barra(analisar_tornado(p, PERDAS, ESQUEMA), N.fracao_externa)
    assert (barra.valor_baixo, barra.valor_alto) == (Decimal(0), Decimal(1))
    c = comparar_interna_externa(p, PERDAS, ESQUEMA)
    assert barra.margem_no_baixo == c.margem_interna
    assert barra.margem_no_alto == c.margem_externa


def test_tornado_corta_gasto_no_limite() -> None:
    no_base = _barra(analisar_tornado(premissas_base(), PERDAS, ESQUEMA), N.gasto_mensal)
    assert no_base.valor_alto == Decimal("4000")
    assert no_base.cortado_pela_coerencia is False

    # Saldo de R$ 4.000,00 × 85% = R$ 3.400,00: o gasto alto (R$ 4.000,00) não cabe.
    p = premissas_base().com(saldo_em_garantia=Dinheiro(400000))
    barra = _barra(analisar_tornado(p, PERDAS, ESQUEMA), N.gasto_mensal)
    assert barra.valor_baixo == Decimal("500")
    assert barra.valor_alto == Decimal("3400.00")
    assert barra.cortado_pela_coerencia is True
    assert barra.margem_no_alto == _margem(p.com(gasto_mensal=Dinheiro(340000)))


def test_tornado_corta_saldo_e_ltv() -> None:
    # Gasto de R$ 2.000,00 com saldo de R$ 2.500,00: LTV mínimo 0,80 e saldo mínimo
    # R$ 2.000,00 ÷ 0,85 = R$ 2.352,94... -> R$ 2.352,95.
    p = premissas_base().com(gasto_mensal=Dinheiro(200000), saldo_em_garantia=Dinheiro(250000))
    barras = analisar_tornado(p, PERDAS, ESQUEMA)

    saldo = _barra(barras, N.saldo_em_garantia)
    assert saldo.valor_baixo == Decimal("2352.95")
    assert saldo.valor_alto == Decimal("30000")
    assert saldo.cortado_pela_coerencia is True

    ltv = _barra(barras, N.ltv_medio)
    assert ltv.valor_baixo == Decimal("0.8000")
    assert ltv.valor_alto == Decimal("0.95")
    assert ltv.cortado_pela_coerencia is True

    # O gasto alto também é cortado: R$ 2.500,00 × 85% = R$ 2.125,00.
    gasto = _barra(barras, N.gasto_mensal)
    assert gasto.valor_alto == Decimal("2125.00")


def test_tornado_corta_a_carteira_no_limite_de_clientes() -> None:
    p = premissas_base().com(clientes_por_parceiro=10_000_000)
    barra = _barra(analisar_tornado(p, PERDAS, ESQUEMA), N.parceiros)
    assert barra.valor_alto == Decimal(10)
    assert barra.cortado_pela_coerencia is True


def test_tornado_corta_clientes_por_parceiro_sem_revalidar() -> None:
    # Com `.com` (sem validação) dá para pedir 2.000 parceiros: 100 milhões ÷ 2.000 =
    # 50.000 clientes por parceiro no máximo.
    p = premissas_base().com(parceiros=2000)
    barra = _barra(analisar_tornado(p, PERDAS, ESQUEMA), N.clientes_por_parceiro)
    assert barra.valor_alto == Decimal(50000)
    assert barra.cortado_pela_coerencia is True


def test_tornado_sem_saldo_nem_ltv_nao_corta() -> None:
    # Saldo e LTV zerados (fora da faixa aceita, só para a conta não dividir por zero).
    p = premissas_base().com(
        gasto_mensal=Dinheiro(0), saldo_em_garantia=Dinheiro(0), ltv_medio=Fracao(Decimal(0))
    )
    barras = analisar_tornado(p, PERDAS, ESQUEMA)
    for nome in (N.saldo_em_garantia, N.ltv_medio):
        assert _barra(barras, nome).cortado_pela_coerencia is False


def test_simular_negocio_inclui_o_tornado() -> None:
    p = premissas_base()
    r = simular_negocio(p, PERDAS, ESQUEMA)
    assert r.tornado == analisar_tornado(p, PERDAS, ESQUEMA)
    assert r.interna_x_externa == comparar_interna_externa(p, PERDAS, ESQUEMA)


def test_simular_negocio_rapido() -> None:
    p = premissas_base()
    inicio = time.perf_counter()
    simular_negocio(p, PERDAS, ESQUEMA)
    assert time.perf_counter() - inicio < 1.0


# ------------------------------------------------------------- busca do empate
# Margem sintética em degraus: 1 centavo a cada R$ 50,00. A fronteira de 10 centavos
# fica em R$ 500,00 (50.000 centavos).


def _degraus(valor: int) -> int:
    return valor // 5000


def test_busca_acha_a_fronteira_pela_inclinacao() -> None:
    assert _buscar_empate(_degraus, 10, 0, 10000, 0, 10**6) == 50000


def test_busca_sem_inclinacao_positiva_ou_sem_espaco() -> None:
    assert _buscar_empate(lambda valor: -valor, 10, 0, 100, 0, 10**6) is None
    assert _buscar_empate(lambda valor: 0, 10, 0, 100, 0, 10**6) is None
    assert _buscar_empate(_degraus, 10, 10**6, 100, 0, 10**6) is None
    # Fora da faixa: o empate fica em 50.000, mas o máximo é 40.000.
    assert _buscar_empate(_degraus, 10, 0, 10000, 0, 40000) is None


def test_busca_cai_na_bisseccao_quando_os_passos_acabam() -> None:
    # Estimativa longe por baixo: mil centavos não chegam, a distância dobra e a
    # bissecção acha a fronteira.
    assert _andar_ate_a_fronteira(_degraus, 10, 0, 20000, 0, 10**6) == 50000
    # Longe por cima: mil centavos para baixo não chegam, bissecção desde o início.
    assert _andar_ate_a_fronteira(_degraus, 10, 0, 80000, 0, 10**6) == 50000
    # Por baixo e sem empate até o máximo.
    assert _andar_ate_a_fronteira(_degraus, 10, 0, 20000, 0, 40000) is None


def test_confirmacao_recusa_o_que_nao_e_fronteira() -> None:
    assert _e_fronteira(_degraus, 10, 50000, 0) is True
    assert _e_fronteira(_degraus, 10, 49999, 0) is False
    assert _e_fronteira(_degraus, 10, 50001, 0) is False
    assert _e_fronteira(_degraus, 0, 0, 0) is True
