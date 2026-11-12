"""O TOML de premissas: esquema, três conjuntos e a perda vinda do motor de risco."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.dominio.economia.analises import simular_negocio
from app.dominio.economia.premissas import NomeDaPremissa, PremissasInvalidas
from app.infra import economia
from app.infra.carteira_sintetica import carregar_parametros
from app.infra.economia import (
    carregar_parametros_da_economia,
    cenarios_de_risco,
    premissas_do_conjunto,
    taxas_de_perda,
)
from tests.economia_base import ESQUEMA, VALORES_BASE

CONJUNTOS = ("conservador", "base", "otimista")


def test_toml_tem_todas_as_premissas() -> None:
    parametros = carregar_parametros_da_economia()
    assert set(parametros.esquema) == set(NomeDaPremissa)
    assert len(parametros.esquema) == 34
    assert tuple(parametros.esquema) == tuple(NomeDaPremissa)
    assert set(parametros.conjuntos) == set(CONJUNTOS)
    for conjunto in parametros.conjuntos.values():
        assert set(conjunto.valores) == set(NomeDaPremissa)


def test_toml_e_o_apoio_de_teste_dizem_o_mesmo() -> None:
    parametros = carregar_parametros_da_economia()
    assert dict(parametros.esquema) == ESQUEMA
    assert dict(parametros.conjuntos["base"].valores) == VALORES_BASE


def test_conjunto_dentro_das_faixas() -> None:
    parametros = carregar_parametros_da_economia()
    for conjunto_id in CONJUNTOS:
        premissas = premissas_do_conjunto(conjunto_id)  # valida pelas faixas aceitas
        assert premissas.cenario_de_risco in carregar_parametros().cenarios
        assert len(premissas.receitas_ligadas) == 6
        for nome, valor in parametros.conjuntos[conjunto_id].valores.items():
            esquema = parametros.esquema[nome]
            assert esquema.minimo <= valor <= esquema.maximo


def test_faixa_razoavel_dentro_da_aceita() -> None:
    for esquema in carregar_parametros_da_economia().esquema.values():
        if esquema.razoavel_minimo is None or esquema.razoavel_maximo is None:
            assert esquema.razoavel_minimo is None
            assert esquema.razoavel_maximo is None
            continue
        assert esquema.minimo <= esquema.razoavel_minimo
        assert esquema.razoavel_minimo <= esquema.razoavel_maximo
        assert esquema.razoavel_maximo <= esquema.maximo


def _margens(conjunto_id: str) -> tuple[Decimal, Decimal]:
    premissas = premissas_do_conjunto(conjunto_id)
    resultado = simular_negocio(
        premissas, taxas_de_perda(premissas.cenario_de_risco), ESQUEMA, incluir_tornado=False
    )
    return (
        resultado.mes.plataforma.margem.para_decimal(),
        resultado.mes.parceiro.margem.para_decimal(),
    )


def test_tres_conjuntos_rodam_e_ficam_em_ordem() -> None:
    (cp, cpar), (bp, bpar), (op, opar) = (_margens(c) for c in CONJUNTOS)
    assert cp <= bp <= op
    assert cpar <= bpar <= opar
    assert bp > 0


def test_taxas_de_perda_do_custodiante_fora_do_ar_maior_na_externa() -> None:
    taxas = taxas_de_perda("custodiante_fora_do_ar")
    assert taxas.cenario_id == "custodiante_fora_do_ar"
    assert taxas.condicional_externa.valor > taxas.condicional_interna.valor
    assert 0 <= taxas.condicional_externa.valor <= 1


def test_taxas_de_perda_em_cache() -> None:
    assert taxas_de_perda("crash_da_bolsa") is taxas_de_perda("crash_da_bolsa")


def test_cenario_desconhecido() -> None:
    with pytest.raises(PremissasInvalidas) as erro:
        taxas_de_perda("inexistente")
    assert erro.value.detalhes["erros"][0]["campo"] == "cenario_de_risco"
    assert "custodiante_fora_do_ar" in str(erro.value)


def test_conjunto_desconhecido() -> None:
    with pytest.raises(PremissasInvalidas) as erro:
        premissas_do_conjunto("inexistente")
    assert erro.value.detalhes["erros"][0]["campo"] == "conjunto"


def test_lista_de_cenarios_de_risco() -> None:
    ids = [i for i, _ in cenarios_de_risco()]
    assert ids == list(carregar_parametros().cenarios)
    assert all(nome for _, nome in cenarios_de_risco())


def _toml_com(tmp_path: Path, antigo: str, novo: str) -> Path:
    texto = economia.ARQUIVO_PADRAO.read_text(encoding="utf-8")
    assert antigo in texto
    arquivo = tmp_path / "premissas.toml"
    arquivo.write_text(texto.replace(antigo, novo, 1), encoding="utf-8")
    return arquivo


@pytest.mark.parametrize(
    ("antigo", "novo", "chave"),
    [
        ('tipo = "dinheiro"', 'tipo = "xpto"', "gasto_mensal"),
        ("[premissas.parceiros]", "[premissas.parceiros_extra]", "parceiros"),
        ('razoavel = ["500", "4000"]', 'razoavel = ["4000", "500"]', "gasto_mensal"),
        ('razoavel = ["500", "4000"]', 'razoavel = ["500", "60000"]', "gasto_mensal"),
        ('maximo = "1.00"', 'maximo = "1.50"', "ltv_medio"),
        ('cenario_de_risco = "crash_da_bolsa"', 'cenario_de_risco = "nao_existe"', "otimista"),
        ('gasto_mensal = "1500"', 'gasto_mensal = "99999999"', "base"),
    ],
)
def test_toml_quebrado_levanta_erro_claro(
    tmp_path: Path, antigo: str, novo: str, chave: str
) -> None:
    arquivo = _toml_com(tmp_path, antigo, novo)
    with pytest.raises(economia.ErroNoArquivoDePremissas) as erro:
        carregar_parametros_da_economia(arquivo)
    assert chave in str(erro.value)
