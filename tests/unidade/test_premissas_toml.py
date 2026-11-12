"""O TOML de premissas: esquema, três conjuntos e a perda vinda do motor de risco."""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.analises import simular_negocio
from app.dominio.economia.calculo import calcular_mes
from app.dominio.economia.premissas import NomeDaPremissa, PremissasInvalidas
from app.dominio.politica import TipoAtivo
from app.dominio.stress import ContaNaCarteira
from app.dominio.taxas import Fracao
from app.infra import economia
from app.infra.carteira_sintetica import carregar_parametros, gerar_carteira
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


def test_toda_premissa_explica_a_origem() -> None:
    for esquema in carregar_parametros_da_economia().esquema.values():
        origem = esquema.origem.strip()
        assert len(origem) >= 30, esquema.nome
        assert origem.lower() not in {"ilustrativa", "referência", "referencia"}, esquema.nome
        if esquema.fonte is not None:
            assert esquema.fonte.strip().lower() not in {"ilustrativa", "referência"}


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


def test_conservador_acha_o_gasto_que_empata_para_baixo() -> None:
    # No conservador a margem da externa cai quando o gasto sobe: o empate está abaixo do
    # gasto do conjunto, e a busca precisa olhar para baixo para achar.
    p = premissas_do_conjunto("conservador")
    perdas = taxas_de_perda(p.cenario_de_risco)
    c = simular_negocio(p, perdas, ESQUEMA, incluir_tornado=False).interna_x_externa
    assert c.custo_a_mais_da_externa.positivo
    gasto = c.gasto_para_empatar
    assert gasto is not None, c.motivo_gasto
    assert c.motivo_gasto is None
    assert gasto < p.gasto_mensal
    externa = p.com(fracao_interna=Fracao(Decimal(0)), fracao_externa=Fracao(Decimal(1)))

    def margem(gasto_mensal: Dinheiro) -> Dinheiro:
        return calcular_mes(externa.com(gasto_mensal=gasto_mensal), perdas).plataforma.margem

    assert margem(gasto) >= c.margem_interna
    assert margem(gasto + Dinheiro(1)) < c.margem_interna


def test_taxas_de_perda_do_custodiante_fora_do_ar_maior_na_externa() -> None:
    taxas = taxas_de_perda("custodiante_fora_do_ar")
    assert taxas.cenario_id == "custodiante_fora_do_ar"
    assert taxas.condicional_externa.valor > taxas.condicional_interna.valor
    assert 0 <= taxas.condicional_externa.valor <= 1


def test_interna_e_externa_saem_da_mesma_carteira() -> None:
    interna, externa = economia.carteiras_da_perda()
    assert len(interna) == len(externa) == economia.TAMANHO_DA_CARTEIRA_DE_RISCO
    original = gerar_carteira(
        carregar_parametros(),
        economia.TAMANHO_DA_CARTEIRA_DE_RISCO,
        economia.SEMENTE_DA_CARTEIRA_DE_RISCO,
    )
    for antes, i, e in zip(original, interna, externa, strict=True):
        # Os mesmos clientes: contas, dívidas, tipos e valores.
        assert antes.conta_id == i.conta_id == e.conta_id
        assert antes.divida == i.divida == e.divida
        sem_custodiante = [(g.tipo_ativo, g.valor_de_mercado) for g in antes.garantias]
        assert [(g.tipo_ativo, g.valor_de_mercado) for g in i.garantias] == sem_custodiante
        assert [(g.tipo_ativo, g.valor_de_mercado) for g in e.garantias] == sem_custodiante
        # Interna: ninguém de fora. Externa: tudo que não é depósito está fora, e quem já
        # estava fora fica com o mesmo custodiante.
        assert all(g.custodiante_id is None for g in i.garantias)
        for g_antes, g in zip(antes.garantias, e.garantias, strict=True):
            assert (g.custodiante_id is None) == (g.tipo_ativo is TipoAtivo.DEPOSITO)
            if g_antes.custodiante_id is not None:
                assert g.custodiante_id == g_antes.custodiante_id
    assert economia.carteiras_da_perda() == (interna, externa)  # determinística


def test_sem_choque_no_custodiante_a_externa_nao_perde_menos() -> None:
    # Com os mesmos clientes, a garantia externa nunca recupera mais que a interna.
    for cenario_id, _ in cenarios_de_risco():
        taxas = taxas_de_perda(cenario_id)
        assert taxas.condicional_externa.valor >= taxas.condicional_interna.valor, cenario_id
    normal = taxas_de_perda("dia_normal")
    assert normal.condicional_externa.valor >= normal.condicional_interna.valor


def test_exposicao_zero_e_erro_de_configuracao() -> None:
    cenario = carregar_parametros().cenarios["dia_normal"]
    sem_divida = [ContaNaCarteira("sint_00000", Dinheiro(0), ())]
    with pytest.raises(economia.ErroNaPerdaDoCenario) as erro:
        economia.perda_condicional(sem_divida, cenario)
    assert not isinstance(erro.value, PremissasInvalidas)
    assert isinstance(erro.value, RuntimeError)
    assert "exposição zero" in str(erro.value)


def test_perda_maior_que_a_divida_e_erro_de_configuracao(monkeypatch: pytest.MonkeyPatch) -> None:
    perda = SimpleNamespace(exposicao=Dinheiro(100), perda_esperada_total=Dinheiro(101))
    monkeypatch.setattr(economia, "simular_painel", lambda *_: SimpleNamespace(perda=perda))
    cenario = carregar_parametros().cenarios["dia_normal"]
    with pytest.raises(economia.ErroNaPerdaDoCenario, match="entre 0 e 1"):
        economia.perda_condicional([], cenario)


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
        ('minimo = "0"', "minimo = 0", "premissas.gasto_mensal.minimo"),
        ('maximo = "50000"', 'maximo = "muito"', "premissas.gasto_mensal.maximo"),
        ('maximo = "50000"', 'maximo = "Infinity"', "premissas.gasto_mensal.maximo"),
        ('maximo = "50000"', 'maximo = "NaN"', "premissas.gasto_mensal.maximo"),
        ('nome = "Gasto mensal"', 'nome = ""', "premissas.gasto_mensal.nome"),
        ('razoavel = ["500", "4000"]', 'razoavel = ["500"]', "premissas.gasto_mensal.razoavel"),
        (
            'minimo = "0"\nmaximo = "50000"',
            'minimo = "60000"\nmaximo = "50000"',
            "premissas.gasto_mensal",
        ),
        (
            'minimo = "0"\nmaximo = "0.50"\nrazoavel = ["0.10", "0.16"]',
            'minimo = "-0.01"\nmaximo = "0.50"\nrazoavel = ["0.10", "0.16"]',
            "premissas.cdi_ao_ano.minimo",
        ),
        ('fonte = "DI-B3 13,65% a.a. em 06/11/2026, ANBIMA"', "fonte = 1", "cdi_ao_ano.fonte"),
        ("[conjuntos.base.valores]", "[conjuntos.base.outros]", "conjuntos.base.valores"),
        (
            'gasto_mensal = "1500"',
            'gasto_mensal = "1500"\ninventada = "1"',
            "conjuntos.base.valores: nomes desconhecidos ['inventada']",
        ),
        ('gasto_mensal = "1500"\n', "", "conjuntos.base.valores.gasto_mensal: falta"),
        (
            'receitas_ligadas = ["intercambio",',
            'receitas_ligadas = "intercambio"\nx = ["intercambio",',
            "conjuntos.conservador.receitas_ligadas",
        ),
        (
            'receitas_ligadas = ["intercambio",',
            'receitas_ligadas = ["xpto",',
            "conjuntos.conservador.receitas_ligadas",
        ),
    ],
)
def test_toml_quebrado_levanta_erro_claro(
    tmp_path: Path, antigo: str, novo: str, chave: str
) -> None:
    arquivo = _toml_com(tmp_path, antigo, novo)
    with pytest.raises(economia.ErroNoArquivoDePremissas) as erro:
        carregar_parametros_da_economia(arquivo)
    assert chave in str(erro.value)


@pytest.mark.parametrize(
    ("corte", "chave"),
    [
        (lambda texto: texto[texto.index("[conjuntos.") :], "premissas: faltam as tabelas"),
        (lambda texto: texto[: texto.index("[conjuntos.")], "conjuntos: faltam as tabelas"),
    ],
)
def test_toml_sem_premissas_ou_sem_conjuntos(
    tmp_path: Path, corte: Callable[[str], str], chave: str
) -> None:
    arquivo = tmp_path / "premissas.toml"
    arquivo.write_text(corte(economia.ARQUIVO_PADRAO.read_text(encoding="utf-8")), "utf-8")
    with pytest.raises(economia.ErroNoArquivoDePremissas, match=re.escape(chave)):
        carregar_parametros_da_economia(arquivo)


@pytest.mark.parametrize(
    ("bloco", "chave"),
    [
        (r"\[premissas\.parceiros\]\n.*?\n\n", "premissas.parceiros"),
        (r"\[conjuntos\.base\]\n.*?\[conjuntos\.base\.valores\]\n.*?\n\n", "conjuntos.base"),
    ],
)
def test_entrada_que_nao_e_tabela_levanta_erro_claro(
    tmp_path: Path, bloco: str, chave: str
) -> None:
    texto = economia.ARQUIVO_PADRAO.read_text(encoding="utf-8")
    sem_o_bloco = re.sub(bloco, "", texto, count=1, flags=re.DOTALL)
    arquivo = tmp_path / "premissas.toml"
    arquivo.write_text(f"{chave} = 3\n{sem_o_bloco}", encoding="utf-8")
    with pytest.raises(economia.ErroNoArquivoDePremissas) as erro:
        carregar_parametros_da_economia(arquivo)
    assert chave in str(erro.value)
