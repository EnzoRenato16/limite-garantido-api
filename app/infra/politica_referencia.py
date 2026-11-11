"""Carrega a política de risco de referência de um arquivo TOML."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from app.dominio.politica import PoliticaRisco

ARQUIVO_PADRAO = Path(__file__).resolve().parent.parent / "politica_referencia.toml"


@lru_cache
def carregar_politica_de_referencia(arquivo: Path = ARQUIVO_PADRAO) -> PoliticaRisco:
    with arquivo.open("rb") as conteudo:
        return PoliticaRisco.de_dict(tomllib.load(conteudo))
