from __future__ import annotations

from decimal import Decimal

import pytest

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.premissas import (
    FonteDeReceita,
    NomeDaPremissa,
    Premissas,
    PremissasInvalidas,
    montar_premissas,
)
from app.dominio.erros import ValorInvalido
from app.dominio.taxas import Fracao, TaxaAoAno
from tests.economia_base import ESQUEMA, VALORES_BASE, premissas_base

N = NomeDaPremissa


def _montar(**mudancas: str) -> Premissas:
    valores = dict(VALORES_BASE)
    for nome, valor in mudancas.items():
        valores[N(nome)] = Decimal(valor)
    return montar_premissas(valores, set(FonteDeReceita), "custodiante_fora_do_ar", ESQUEMA)


def _erros(**mudancas: str) -> list[dict[str, str]]:
    with pytest.raises(PremissasInvalidas) as info:
        _montar(**mudancas)
    erros: list[dict[str, str]] = info.value.detalhes["erros"]  # type: ignore[assignment]
    return erros


def test_sao_34_premissas_e_o_esquema_cobre_todas() -> None:
    assert len(N) == 34
    assert set(ESQUEMA) == set(N)
    assert set(VALORES_BASE) == set(N)


def test_base_monta_com_tipos_certos() -> None:
    p = premissas_base()
    assert p.gasto_mensal == Dinheiro(150000)
    assert p.ltv_medio == Fracao(Decimal("0.85"))
    assert p.cdi_ao_ano == TaxaAoAno(Decimal("0.1365"))
    assert p.garantias_por_cliente == Decimal("1.5")
    assert p.meses_no_rotativo == 2
    assert isinstance(p.meses_no_rotativo, int)
    assert p.receitas_ligadas == frozenset(FonteDeReceita)
    assert p.cenario_de_risco == "custodiante_fora_do_ar"
    assert p.valores() == VALORES_BASE


def test_com_troca_campos_sem_mexer_no_original() -> None:
    p = premissas_base()
    outra = p.com(gasto_mensal=Dinheiro(100000))
    assert outra.gasto_mensal == Dinheiro(100000)
    assert p.gasto_mensal == Dinheiro(150000)


def test_erro_e_um_valor_invalido_com_codigo_proprio() -> None:
    assert issubclass(PremissasInvalidas, ValorInvalido)
    assert PremissasInvalidas.codigo == "premissas_invalidas"


def test_fora_da_faixa_lista_campo_e_unidade() -> None:
    erros = _erros(ltv_medio="1.5")
    assert [e["campo"] for e in erros] == ["ltv_medio"]
    assert "0,60" in erros[0]["mensagem"]
    assert "1,00" in erros[0]["mensagem"]
    assert "fração" in erros[0]["mensagem"]


def test_taxa_em_percentual_e_recusada_com_unidade() -> None:
    (erro,) = _erros(cdi_ao_ano="13.65")
    assert erro["campo"] == "cdi_ao_ano"
    assert "ao ano" in erro["mensagem"]
    assert "0,50" in erro["mensagem"]
    assert "0,1365 = 13,65%" in erro["mensagem"]


def test_interna_mais_externa_precisa_dar_100() -> None:
    erros = _erros(fracao_interna="0.6", fracao_externa="0.3")
    assert [e["campo"] for e in erros] == ["fracao_interna"]
    assert "100%" in erros[0]["mensagem"]


def test_interna_mais_externa_igual_a_um_e_aceita() -> None:
    p = _montar(fracao_interna="0.25", fracao_externa="0.75")
    assert p.fracao_externa.valor == Decimal("0.75")


def test_gasto_acima_do_limite_da_garantia() -> None:
    # 8.000 x 0,85 = 6.800,00
    (erro,) = _erros(gasto_mensal="6800.01")
    assert erro["campo"] == "gasto_mensal"
    assert "R$ 6.800,00" in erro["mensagem"]


def test_gasto_igual_ao_limite_e_aceito() -> None:
    assert _montar(gasto_mensal="6800.00").gasto_mensal == Dinheiro(680000)


def test_limite_da_garantia_arredonda_para_baixo() -> None:
    # 100,01 x 0,85 = 85,0085 -> 85,00
    _montar(saldo_em_garantia="100.01", gasto_mensal="85.00")
    (erro,) = _erros(saldo_em_garantia="100.01", gasto_mensal="85.01")
    assert "R$ 85,00" in erro["mensagem"]


def test_carteira_acima_do_teto_e_recusada() -> None:
    erros = _erros(parceiros="11", clientes_por_parceiro="10000000")
    assert [e["campo"] for e in erros] == ["parceiros"]
    assert "100.000.000" in erros[0]["mensagem"]
    _montar(parceiros="10", clientes_por_parceiro="10000000")


def test_varios_erros_voltam_juntos() -> None:
    erros = _erros(cdi_ao_ano="13.65", ltv_medio="2", meses_no_rotativo="99")
    assert {e["campo"] for e in erros} == {"cdi_ao_ano", "ltv_medio", "meses_no_rotativo"}


def test_erros_de_faixa_e_de_coerencia_vem_juntos() -> None:
    erros = _erros(cdi_ao_ano="13.65", fracao_interna="0.1")
    assert {e["campo"] for e in erros} == {"cdi_ao_ano", "fracao_interna"}


def test_dinheiro_com_mais_de_duas_casas_e_recusado() -> None:
    (erro,) = _erros(fee_por_garantia_ativa="2.005")
    assert erro["campo"] == "fee_por_garantia_ativa"
    assert "centavos" in erro["mensagem"]


def test_inteiro_com_casas_e_recusado() -> None:
    (erro,) = _erros(parceiros="2.5")
    assert erro["campo"] == "parceiros"
    assert "inteiro" in erro["mensagem"]


def test_inteiro_com_zeros_a_direita_e_aceito() -> None:
    assert _montar(parceiros="5.0").parceiros == 5


def test_premissa_faltando_e_recusada() -> None:
    valores = dict(VALORES_BASE)
    del valores[N.cdi_ao_ano]
    with pytest.raises(PremissasInvalidas) as info:
        montar_premissas(valores, set(FonteDeReceita), "base", ESQUEMA)
    (erro,) = info.value.detalhes["erros"]  # type: ignore[misc]
    assert erro["campo"] == "cdi_ao_ano"
    assert "falta" in erro["mensagem"]


def test_valor_nao_finito_e_recusado() -> None:
    erros = _erros(cdi_ao_ano="NaN")
    assert erros[0]["campo"] == "cdi_ao_ano"


def test_receitas_ligadas_sao_guardadas() -> None:
    p = montar_premissas(VALORES_BASE, [FonteDeReceita.intercambio], "crash_da_bolsa", ESQUEMA)
    assert p.receitas_ligadas == frozenset({FonteDeReceita.intercambio})
    assert p.cenario_de_risco == "crash_da_bolsa"
