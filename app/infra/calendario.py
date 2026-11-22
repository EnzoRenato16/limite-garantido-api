"""Carrega o calendário de dias úteis de `app/feriados_nacionais.toml`.

Arquivo quebrado é erro de implantação: levanta `ErroNoCalendario` na carga. Cada
feriado é conferido contra o dia da semana publicado na fonte, para uma data digitada
errada não passar em silêncio.
"""

from __future__ import annotations

import tomllib
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.dominio.calendario import CalendarioDeDiasUteis
from app.dominio.erros import ErroDominio
from app.dominio.regulacao import Afirmacao, Categoria

ARQUIVO_PADRAO = Path(__file__).resolve().parent.parent / "feriados_nacionais.toml"

_DIAS_DA_SEMANA = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")


class ErroNoCalendario(RuntimeError):
    """O TOML de feriados está quebrado: bug de implantação."""


def _data(valor: object, caminho: str) -> date:
    # tomllib devolve data local como `date`; data e hora viria como `datetime`.
    if not isinstance(valor, date) or type(valor) is not date:
        raise ErroNoCalendario(f"{caminho}: precisa ser data TOML, como 2026-11-20")
    return valor


def _feriado(item: Any, caminho: str) -> date:
    if not isinstance(item, dict):
        raise ErroNoCalendario(f"{caminho}: precisa ser tabela")
    dia = _data(item.get("data"), f"{caminho}.data")
    nome = item.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise ErroNoCalendario(f"{caminho}.nome: precisa ser texto")
    categoria = item.get("categoria")
    if not isinstance(categoria, str):
        raise ErroNoCalendario(f"{caminho}.categoria: precisa ser texto")
    try:
        # Mesma regra das afirmações do catálogo: categoria norma exige norma e artigo.
        Afirmacao(
            texto=nome,
            categoria=Categoria(categoria),
            norma=item.get("norma"),
            artigo=item.get("artigo"),
        )
    except (ValueError, ErroDominio) as erro:
        raise ErroNoCalendario(f"{caminho} ({dia.isoformat()}): {erro}") from erro
    publicado = item.get("dia_da_semana")
    if publicado is not None and publicado != _DIAS_DA_SEMANA[dia.weekday()]:
        raise ErroNoCalendario(
            f"{caminho}: {dia.isoformat()} é {_DIAS_DA_SEMANA[dia.weekday()]}, "
            f"mas a fonte diz {publicado}"
        )
    return dia


def montar_calendario(dados: dict[str, Any]) -> CalendarioDeDiasUteis:
    inicio = _data(dados.get("cobertura_inicio"), "cobertura_inicio")
    fim = _data(dados.get("cobertura_fim"), "cobertura_fim")
    fonte = dados.get("fonte")
    if not isinstance(fonte, dict) or not fonte.get("urls") or "consultado_em" not in fonte:
        raise ErroNoCalendario("fonte: precisa trazer urls e consultado_em")
    _data(fonte["consultado_em"], "fonte.consultado_em")
    itens = dados.get("feriados")
    if not isinstance(itens, list) or not itens:
        raise ErroNoCalendario("feriados: precisa ser lista não vazia")
    feriados: set[date] = set()
    for posicao, item in enumerate(itens):
        dia = _feriado(item, f"feriados[{posicao}]")
        if dia in feriados:
            raise ErroNoCalendario(f"feriados[{posicao}]: {dia.isoformat()} repetido")
        feriados.add(dia)
    try:
        return CalendarioDeDiasUteis(frozenset(feriados), inicio, fim)
    except ErroDominio as erro:
        raise ErroNoCalendario(f"Calendário inválido: {erro}") from erro


@lru_cache
def carregar_calendario(arquivo: Path = ARQUIVO_PADRAO) -> CalendarioDeDiasUteis:
    try:
        with arquivo.open("rb") as conteudo:
            dados = tomllib.load(conteudo)
    except (OSError, tomllib.TOMLDecodeError) as erro:
        raise ErroNoCalendario(f"Não consegui ler {arquivo.name}: {erro}") from erro
    return montar_calendario(dados)
