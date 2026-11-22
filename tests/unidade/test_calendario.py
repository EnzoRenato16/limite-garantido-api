"""Calendário de dias úteis nacionais: prazos regulatórios e operacionais em dias úteis."""

from __future__ import annotations

import tomllib
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.dominio.calendario import CalendarioDeDiasUteis, DiasCorridos, DiasUteis
from app.dominio.erros import ForaDaCobertura, ValorInvalido
from app.infra.calendario import (
    ARQUIVO_PADRAO,
    ErroNoCalendario,
    carregar_calendario,
)


@pytest.fixture(scope="module")
def calendario() -> CalendarioDeDiasUteis:
    return carregar_calendario()


# ------------------------------------------------------------------- exemplos
def test_consciencia_negra_e_fim_de_semana_empurram_o_prazo(
    calendario: CalendarioDeDiasUteis,
) -> None:
    # 20/11/2026 é feriado nacional (Lei 14.759/2023); 21 e 22 caem no fim de semana.
    assert calendario.somar(date(2026, 11, 19), DiasUteis(2)) == date(2026, 11, 24)


def test_natal(calendario: CalendarioDeDiasUteis) -> None:
    assert calendario.somar(date(2026, 12, 24), DiasUteis(1)) == date(2026, 12, 28)


def test_prazo_que_passa_do_fim_da_cobertura_e_erro_claro(
    calendario: CalendarioDeDiasUteis,
) -> None:
    with pytest.raises(ForaDaCobertura) as erro:
        calendario.somar(date(2027, 12, 30), DiasUteis(5))
    assert "2027-12-31" in erro.value.mensagem
    # Até o último dia útil da cobertura, funciona.
    assert calendario.somar(date(2027, 12, 30), DiasUteis(1)) == date(2027, 12, 31)


def test_data_fora_da_cobertura_e_erro(calendario: CalendarioDeDiasUteis) -> None:
    with pytest.raises(ForaDaCobertura):
        calendario.e_dia_util(date(2025, 12, 31))
    with pytest.raises(ForaDaCobertura):
        calendario.somar(date(2028, 1, 3), DiasUteis(1))
    with pytest.raises(ForaDaCobertura):
        calendario.dias_uteis_entre(date(2027, 12, 1), date(2028, 1, 10))


def test_somar_zero_devolve_a_propria_data(calendario: CalendarioDeDiasUteis) -> None:
    assert calendario.somar(date(2026, 11, 21), DiasUteis(0)) == date(2026, 11, 21)


def test_dias_uteis_entre(calendario: CalendarioDeDiasUteis) -> None:
    # Conta os dias úteis depois do início, até o fim inclusive.
    assert calendario.dias_uteis_entre(date(2026, 11, 19), date(2026, 11, 24)) == 2
    assert calendario.dias_uteis_entre(date(2026, 11, 19), date(2026, 11, 19)) == 0
    assert calendario.dias_uteis_entre(date(2026, 11, 20), date(2026, 11, 22)) == 0
    with pytest.raises(ValorInvalido):
        calendario.dias_uteis_entre(date(2026, 11, 24), date(2026, 11, 19))


def test_feriados_fim_de_semana_e_dia_util(calendario: CalendarioDeDiasUteis) -> None:
    assert not calendario.e_dia_util(date(2026, 11, 20))  # Consciência Negra
    assert not calendario.e_dia_util(date(2026, 2, 16))  # Carnaval
    assert not calendario.e_dia_util(date(2026, 2, 17))
    assert not calendario.e_dia_util(date(2026, 6, 4))  # Corpus Christi
    assert not calendario.e_dia_util(date(2027, 3, 26))  # Paixão de Cristo
    assert not calendario.e_dia_util(date(2026, 11, 21))  # sábado
    assert calendario.e_dia_util(date(2026, 2, 18))  # quarta-feira de cinzas
    assert calendario.e_dia_util(date(2026, 11, 23))


def test_unidades_sao_tipos_distintos(calendario: CalendarioDeDiasUteis) -> None:
    assert DiasUteis(2) != DiasCorridos(2)
    with pytest.raises(TypeError):
        calendario.somar(date(2026, 11, 19), DiasCorridos(2))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        calendario.somar(date(2026, 11, 19), 2)  # type: ignore[arg-type]
    for tipo in (DiasUteis, DiasCorridos):
        with pytest.raises(ValorInvalido):
            tipo(-1)
        with pytest.raises(TypeError):
            tipo(True)


def test_calendario_recusa_feriado_fora_da_cobertura() -> None:
    with pytest.raises(ValorInvalido):
        CalendarioDeDiasUteis(
            feriados=frozenset({date(2028, 1, 1)}),
            cobertura_inicio=date(2026, 1, 1),
            cobertura_fim=date(2027, 12, 31),
        )
    with pytest.raises(ValorInvalido):
        CalendarioDeDiasUteis(
            feriados=frozenset(),
            cobertura_inicio=date(2027, 1, 1),
            cobertura_fim=date(2026, 12, 31),
        )


# ------------------------------------------------------------------- o arquivo
FERIADOS_ANBIMA = {
    # 2026, conferidos no calendário de feriados nacionais da ANBIMA.
    date(2026, 1, 1),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 4, 3),
    date(2026, 4, 21),
    date(2026, 5, 1),
    date(2026, 6, 4),
    date(2026, 9, 7),
    date(2026, 10, 12),
    date(2026, 11, 2),
    date(2026, 11, 15),
    date(2026, 11, 20),
    date(2026, 12, 25),
    # 2027.
    date(2027, 1, 1),
    date(2027, 2, 8),
    date(2027, 2, 9),
    date(2027, 3, 26),
    date(2027, 4, 21),
    date(2027, 5, 1),
    date(2027, 5, 27),
    date(2027, 9, 7),
    date(2027, 10, 12),
    date(2027, 11, 2),
    date(2027, 11, 15),
    date(2027, 11, 20),
    date(2027, 12, 25),
}


def test_o_arquivo_tem_os_feriados_da_anbima(calendario: CalendarioDeDiasUteis) -> None:
    assert calendario.feriados == frozenset(FERIADOS_ANBIMA)
    assert calendario.cobertura_inicio == date(2026, 1, 1)
    assert calendario.cobertura_fim == date(2027, 12, 31)
    assert carregar_calendario() is calendario


def test_o_arquivo_registra_fonte_data_da_consulta_e_categoria() -> None:
    with ARQUIVO_PADRAO.open("rb") as arquivo:
        dados = tomllib.load(arquivo)
    fonte = dados["fonte"]
    assert all(url.startswith("https://www.anbima.com.br/") for url in fonte["urls"])
    assert fonte["consultado_em"] == date(2026, 11, 22)
    categorias = {f["nome"]: f["categoria"] for f in dados["feriados"]}
    assert categorias["Carnaval"] == "pratica_de_mercado"
    assert categorias["Corpus Christi"] == "pratica_de_mercado"
    assert categorias["Dia Nacional de Zumbi e da Consciência Negra"] == "norma"
    for feriado in dados["feriados"]:
        if feriado["categoria"] == "norma":
            assert feriado["norma"]
            assert feriado["artigo"]


def _com(texto_extra: str, tmp_path: Path, nome: str = "feriados.toml") -> Path:
    arquivo = tmp_path / nome
    arquivo.write_text(ARQUIVO_PADRAO.read_text(encoding="utf-8") + texto_extra, "utf-8")
    return arquivo


def test_feriado_de_norma_sem_artigo_falha(tmp_path: Path) -> None:
    extra = (
        '\n[[feriados]]\ndata = 2027-12-24\nnome = "Véspera"\ncategoria = "norma"\n'
        'norma = "Lei inventada"\n'
    )
    with pytest.raises(ErroNoCalendario, match="artigo"):
        carregar_calendario(_com(extra, tmp_path))


def test_feriado_repetido_ou_fora_da_cobertura_falha(tmp_path: Path) -> None:
    repetido = (
        '\n[[feriados]]\ndata = 2026-12-25\nnome = "Natal"\ncategoria = "pratica_de_mercado"\n'
    )
    with pytest.raises(ErroNoCalendario, match="repetid"):
        carregar_calendario(_com(repetido, tmp_path))
    fora = (
        '\n[[feriados]]\ndata = 2028-01-01\nnome = "Ano-novo"\ncategoria = "pratica_de_mercado"\n'
    )
    with pytest.raises(ErroNoCalendario):
        carregar_calendario(_com(fora, tmp_path, "fora.toml"))


def test_dia_da_semana_de_cada_feriado_bate_com_o_que_a_anbima_publica() -> None:
    # A tabela da ANBIMA traz o dia da semana; o TOML guarda o mesmo, para conferir a data.
    nomes = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
    with ARQUIVO_PADRAO.open("rb") as arquivo:
        dados = tomllib.load(arquivo)
    for feriado in dados["feriados"]:
        assert nomes[feriado["data"].weekday()] == feriado["dia_da_semana"], feriado


def test_cobertura_sem_buraco(calendario: CalendarioDeDiasUteis) -> None:
    dia, uteis = calendario.cobertura_inicio, 0
    while dia <= calendario.cobertura_fim:
        uteis += calendario.e_dia_util(dia)
        dia += timedelta(days=1)
    # 2026: 261 dias de semana menos 12 feriados em dia de semana (15/11 cai no domingo);
    # 2027: 261 menos 10 (1º/5, 20/11 e 25/12 caem no sábado).
    assert uteis == (261 - 12) + (261 - 10)
