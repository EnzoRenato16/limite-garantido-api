"""O TOML do catálogo regulatório: o arquivo real carrega e um arquivo quebrado não sobe."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest

from app.dominio.politica import TipoAtivo
from app.infra.regulacao import (
    ARQUIVO_PADRAO,
    ErroNoCatalogoRegulatorio,
    carregar_catalogo,
    montar_catalogo,
)


def _dados() -> dict[str, Any]:
    with ARQUIVO_PADRAO.open("rb") as arquivo:
        return tomllib.load(arquivo)


def test_o_toml_real_carrega_e_fica_em_cache() -> None:
    catalogo = carregar_catalogo()
    assert set(catalogo.ativos) == set(TipoAtivo)
    assert carregar_catalogo() is catalogo


def _afirmacao_de_norma_sem(campo: str) -> str:
    texto = ARQUIVO_PADRAO.read_text(encoding="utf-8")
    campos = {"norma": '"Lei 12.810/2013"', "artigo": '"art. 26"'}
    del campos[campo]
    linhas = "\n".join(f"{chave} = {valor}" for chave, valor in campos.items())
    return (
        texto
        + "\n[[ativos.cdb.afirmacoes]]\n"
        + 'tema = "o_que_funciona"\n'
        + 'texto = "Afirmação de norma incompleta."\n'
        + 'categoria = "norma"\n'
        + linhas
        + "\n"
    )


@pytest.mark.parametrize("campo", ["norma", "artigo"])
def test_afirmacao_de_norma_sem_norma_ou_artigo_falha(tmp_path: Path, campo: str) -> None:
    arquivo = tmp_path / "regulacao.toml"
    arquivo.write_text(_afirmacao_de_norma_sem(campo), encoding="utf-8")
    with pytest.raises(ErroNoCatalogoRegulatorio, match=campo):
        carregar_catalogo(arquivo)


def test_toml_que_nao_e_toml_falha(tmp_path: Path) -> None:
    arquivo = tmp_path / "regulacao.toml"
    arquivo.write_text("isto não é [toml", encoding="utf-8")
    with pytest.raises(ErroNoCatalogoRegulatorio):
        carregar_catalogo(arquivo)


def _quebrar(mudanca: Any) -> None:
    dados = copy.deepcopy(_dados())
    mudanca(dados)
    with pytest.raises(ErroNoCatalogoRegulatorio):
        montar_catalogo(dados)


def test_faixas_de_ltv_fora_de_ordem_falham() -> None:
    def inverter(dados: dict[str, Any]) -> None:
        faixas = dados["ativos"]["tesouro_prefixado"]["faixas_de_ltv"]
        faixas[0]["ate_anos"], faixas[1]["ate_anos"] = faixas[1]["ate_anos"], faixas[0]["ate_anos"]

    _quebrar(inverter)


@pytest.mark.parametrize(("minimo", "maximo"), [("0", "0.5"), ("0.9", "0.8"), ("0.9", "1.1")])
def test_faixas_de_ltv_fora_de_0_a_1_falham(minimo: str, maximo: str) -> None:
    def trocar(dados: dict[str, Any]) -> None:
        dados["ativos"]["cdb"]["faixas_de_ltv"][0].update(minimo=minimo, maximo=maximo)

    _quebrar(trocar)


def test_faixa_aberta_no_meio_falha() -> None:
    def abrir(dados: dict[str, Any]) -> None:
        del dados["ativos"]["tesouro_ipca"]["faixas_de_ltv"][0]["ate_anos"]

    _quebrar(abrir)


def test_tipo_de_ativo_sem_regra_falha() -> None:
    _quebrar(lambda dados: dados["ativos"].pop("etf"))


def test_tipo_de_ativo_desconhecido_falha() -> None:
    _quebrar(lambda dados: dados["ativos"].update(deposito=dados["ativos"]["cdb"]))


def test_instrumento_sem_carteira_de_provisao_falha() -> None:
    _quebrar(lambda dados: dados["ativos"]["cdb"]["carteira_de_provisao"].pop("penhor"))


def test_trilho_desconhecido_no_ativo_falha() -> None:
    _quebrar(lambda dados: dados["ativos"]["cdb"].update(trilhos=["casa", "drex"]))


def test_percentual_obrigatorio_na_retencao_sobre_a_venda() -> None:
    _quebrar(lambda dados: dados["ativos"]["acoes"]["retencao"].pop("percentual"))


def test_ativo_recomendado_sem_tema_da_matriz_falha() -> None:
    def tirar_prazo(dados: dict[str, Any]) -> None:
        afirmacoes = dados["ativos"]["cdb"]["afirmacoes"]
        dados["ativos"]["cdb"]["afirmacoes"] = [a for a in afirmacoes if a["tema"] != "prazo"]

    _quebrar(tirar_prazo)


def test_portabilidade_sem_prazo_para_um_ativo_falha() -> None:
    _quebrar(lambda dados: dados["trilhos"]["portabilidade_cvm_210"]["prazos_por_ativo"].pop("fii"))


def test_categoria_desconhecida_falha() -> None:
    _quebrar(lambda dados: dados["ativos"]["cdb"]["afirmacoes"][0].update(categoria="boato"))


def test_politica_de_referencia_fica_no_inicio_das_faixas_do_catalogo() -> None:
    from decimal import Decimal

    from app.infra.politica_referencia import carregar_politica_de_referencia

    catalogo = carregar_catalogo()
    politica = carregar_politica_de_referencia()
    assert TipoAtivo.COE not in politica.ltv_por_ativo
    assert set(politica.ltv_por_ativo) == set(TipoAtivo) - {TipoAtivo.COE}
    for tipo, ltv in politica.ltv_por_ativo.items():
        # Início da faixa mais curta (a de maior LTV): o ponto de partida conservador.
        assert ltv == catalogo.regra(tipo).faixas_de_ltv[0].minimo, tipo
    assert politica.ltv_por_ativo[TipoAtivo.TESOURO_PREFIXADO] == Decimal("0.85")
    assert politica.ordem_de_execucao[0] is TipoAtivo.CDB
    assert politica.ordem_de_execucao[-1] is TipoAtivo.PREVIDENCIA
    assert politica.dias_para_execucao_emprestimo == 30
    assert politica.taxa_do_emprestimo_ao_mes == Decimal("0.0199")
