"""Premissas do modelo econômico em TOML, e a perda que vem do motor de risco.

O TOML tem o esquema das 34 premissas (faixas e unidades) e os conjuntos prontos. Um
arquivo quebrado é erro de implantação, não de entrada: levanta `ErroNoArquivoDePremissas`
na carga, com o nome da chave. A perda condicional de cada cenário sai de duas rodadas de
`simular_painel` sobre a carteira sintética, com toda garantia interna e com toda externa.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.dominio.economia.calculo import TaxasDePerda
from app.dominio.economia.premissas import (
    EsquemaDaPremissa,
    FonteDeReceita,
    NomeDaPremissa,
    Premissas,
    PremissasInvalidas,
    TipoDaPremissa,
    montar_premissas,
)
from app.dominio.stress import simular_painel
from app.dominio.taxas import Fracao
from app.infra.carteira_sintetica import carregar_parametros, gerar_carteira
from app.infra.politica_referencia import carregar_politica_de_referencia

ARQUIVO_PADRAO = Path(__file__).resolve().parent.parent / "premissas_economia.toml"
TAMANHO_DA_CARTEIRA_DE_RISCO = 1000  # {cliente}
SEMENTE_DA_CARTEIRA_DE_RISCO = 42

_TIPOS_DE_TAXA = {
    TipoDaPremissa.taxa_ao_ano,
    TipoDaPremissa.taxa_ao_mes,
    TipoDaPremissa.frequencia_ao_ano,
}


class ErroNoArquivoDePremissas(RuntimeError):
    """O TOML de premissas está quebrado: é bug de implantação, não entrada do usuário."""


@dataclass(frozen=True, slots=True)
class Conjunto:
    id: str
    nome: str
    descricao: str
    valores: Mapping[NomeDaPremissa, Decimal]
    receitas_ligadas: frozenset[FonteDeReceita]
    cenario_de_risco: str


@dataclass(frozen=True, slots=True)
class ParametrosDaEconomia:
    esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa]
    conjuntos: Mapping[str, Conjunto]


# ------------------------------------------------------------------- leitura
def _decimal(texto: object, chave: str) -> Decimal:
    if not isinstance(texto, str):
        raise ErroNoArquivoDePremissas(f'{chave}: o valor precisa ser texto, como "0.30"')
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        raise ErroNoArquivoDePremissas(f'{chave}: "{texto}" não é um número') from None
    if not valor.is_finite():
        raise ErroNoArquivoDePremissas(f'{chave}: "{texto}" não é um número finito')
    return valor


def _texto(tabela: Mapping[str, Any], campo: str, chave: str) -> str:
    valor = tabela.get(campo)
    if not isinstance(valor, str) or not valor:
        raise ErroNoArquivoDePremissas(f"{chave}.{campo}: falta ou não é texto")
    return valor


def _ler_esquema(chave_nome: str, tabela: object) -> EsquemaDaPremissa:
    chave = f"premissas.{chave_nome}"
    if not isinstance(tabela, dict):
        raise ErroNoArquivoDePremissas(f"{chave}: precisa ser uma tabela")
    try:
        nome = NomeDaPremissa(chave_nome)
    except ValueError:
        raise ErroNoArquivoDePremissas(f"{chave}: nome fora de NomeDaPremissa") from None
    try:
        tipo = TipoDaPremissa(_texto(tabela, "tipo", chave))
    except ValueError:
        raise ErroNoArquivoDePremissas(f'{chave}.tipo: "{tabela["tipo"]}" desconhecido') from None
    minimo = _decimal(tabela.get("minimo"), f"{chave}.minimo")
    maximo = _decimal(tabela.get("maximo"), f"{chave}.maximo")
    razoavel_minimo: Decimal | None = None
    razoavel_maximo: Decimal | None = None
    if "razoavel" in tabela:
        faixa = tabela["razoavel"]
        if not isinstance(faixa, list) or len(faixa) != 2:
            raise ErroNoArquivoDePremissas(f"{chave}.razoavel: precisa de 2 itens, mínimo e máximo")
        razoavel_minimo = _decimal(faixa[0], f"{chave}.razoavel[0]")
        razoavel_maximo = _decimal(faixa[1], f"{chave}.razoavel[1]")
    if not minimo <= maximo:
        raise ErroNoArquivoDePremissas(f"{chave}: minimo precisa ser <= maximo")
    if (
        razoavel_minimo is not None
        and razoavel_maximo is not None
        and not minimo <= razoavel_minimo <= razoavel_maximo <= maximo
    ):
        raise ErroNoArquivoDePremissas(
            f"{chave}.razoavel: precisa valer minimo <= razoavel_minimo <= razoavel_maximo "
            "<= maximo"
        )
    if tipo is TipoDaPremissa.fracao and not (minimo >= 0 and maximo <= 1):
        raise ErroNoArquivoDePremissas(f"{chave}: fração precisa ficar entre 0 e 1")
    if tipo in _TIPOS_DE_TAXA and minimo < 0:
        raise ErroNoArquivoDePremissas(f"{chave}.minimo: taxa não pode ser negativa")
    fonte = tabela.get("fonte")
    if fonte is not None and not isinstance(fonte, str):
        raise ErroNoArquivoDePremissas(f"{chave}.fonte: precisa ser texto")
    return EsquemaDaPremissa(
        nome=nome,
        nome_exibicao=_texto(tabela, "nome", chave),
        tipo=tipo,
        unidade=_texto(tabela, "unidade", chave),
        minimo=minimo,
        maximo=maximo,
        razoavel_minimo=razoavel_minimo,
        razoavel_maximo=razoavel_maximo,
        origem=_texto(tabela, "origem", chave),
        fonte=fonte,
    )


def _ler_conjunto(conjunto_id: str, tabela: object) -> Conjunto:
    chave = f"conjuntos.{conjunto_id}"
    if not isinstance(tabela, dict):
        raise ErroNoArquivoDePremissas(f"{chave}: precisa ser uma tabela")
    brutos = tabela.get("valores")
    if not isinstance(brutos, dict):
        raise ErroNoArquivoDePremissas(f"{chave}.valores: falta a tabela de valores")
    sobra = set(brutos) - {n.value for n in NomeDaPremissa}
    if sobra:
        raise ErroNoArquivoDePremissas(f"{chave}.valores: nomes desconhecidos {sorted(sobra)}")
    valores: dict[NomeDaPremissa, Decimal] = {}
    for nome in NomeDaPremissa:
        if nome.value not in brutos:
            raise ErroNoArquivoDePremissas(f"{chave}.valores.{nome.value}: falta")
        valores[nome] = _decimal(brutos[nome.value], f"{chave}.valores.{nome.value}")
    ligadas = tabela.get("receitas_ligadas")
    if not isinstance(ligadas, list):
        raise ErroNoArquivoDePremissas(f"{chave}.receitas_ligadas: falta a lista")
    try:
        receitas = frozenset(FonteDeReceita(f) for f in ligadas)
    except ValueError as erro:
        raise ErroNoArquivoDePremissas(f"{chave}.receitas_ligadas: {erro}") from None
    return Conjunto(
        id=conjunto_id,
        nome=_texto(tabela, "nome", chave),
        descricao=_texto(tabela, "descricao", chave),
        valores=valores,
        receitas_ligadas=receitas,
        cenario_de_risco=_texto(tabela, "cenario_de_risco", chave),
    )


@lru_cache
def carregar_parametros_da_economia(arquivo: Path = ARQUIVO_PADRAO) -> ParametrosDaEconomia:
    with arquivo.open("rb") as conteudo:
        dados = tomllib.load(conteudo)
    brutas = dados.get("premissas")
    if not isinstance(brutas, dict):
        raise ErroNoArquivoDePremissas("premissas: faltam as tabelas [premissas.<nome>]")
    esperados = {n.value for n in NomeDaPremissa}
    if set(brutas) != esperados:
        raise ErroNoArquivoDePremissas(
            f"premissas: faltam {sorted(esperados - set(brutas))} "
            f"e sobram {sorted(set(brutas) - esperados)}"
        )
    esquema = {NomeDaPremissa(n): _ler_esquema(n, brutas[n]) for n in NomeDaPremissa}
    brutos = dados.get("conjuntos")
    if not isinstance(brutos, dict) or not brutos:
        raise ErroNoArquivoDePremissas("conjuntos: faltam as tabelas [conjuntos.<id>]")
    conjuntos = {cid: _ler_conjunto(cid, tabela) for cid, tabela in brutos.items()}
    cenarios = carregar_parametros().cenarios
    for conjunto in conjuntos.values():
        chave = f"conjuntos.{conjunto.id}"
        if conjunto.cenario_de_risco not in cenarios:
            raise ErroNoArquivoDePremissas(
                f'{chave}.cenario_de_risco: "{conjunto.cenario_de_risco}" não existe; '
                f"use um de {sorted(cenarios)}"
            )
        try:
            montar_premissas(
                conjunto.valores, conjunto.receitas_ligadas, conjunto.cenario_de_risco, esquema
            )
        except PremissasInvalidas as erro:
            raise ErroNoArquivoDePremissas(f"{chave}.valores: {erro}") from erro
    return ParametrosDaEconomia(esquema=esquema, conjuntos=conjuntos)


# ------------------------------------------------------------------- uso
def premissas_do_conjunto(conjunto_id: str) -> Premissas:
    parametros = carregar_parametros_da_economia()
    conjunto = parametros.conjuntos.get(conjunto_id)
    if conjunto is None:
        validos = ", ".join(parametros.conjuntos)
        raise PremissasInvalidas(
            f'Conjunto "{conjunto_id}" não existe; use um de: {validos}',
            erros=[{"campo": "conjunto", "mensagem": f"conjunto desconhecido; use: {validos}"}],
        )
    return montar_premissas(
        conjunto.valores, conjunto.receitas_ligadas, conjunto.cenario_de_risco, parametros.esquema
    )


def cenarios_de_risco() -> list[tuple[str, str]]:
    """(id, nome) de cada cenário do motor de risco."""
    return [(c.id, c.nome) for c in carregar_parametros().cenarios.values()]


@lru_cache
def taxas_de_perda(cenario_id: str) -> TaxasDePerda:
    """Perda esperada ÷ exposição do cenário, com toda garantia interna e com toda externa."""
    parametros = carregar_parametros()
    cenario = parametros.cenarios.get(cenario_id)
    if cenario is None:
        validos = ", ".join(parametros.cenarios)
        raise PremissasInvalidas(
            f'Cenário de risco "{cenario_id}" não existe; use um de: {validos}',
            erros=[
                {
                    "campo": "cenario_de_risco",
                    "mensagem": f"cenário desconhecido; use um de: {validos}",
                }
            ],
        )
    politica = carregar_politica_de_referencia()

    def condicional(fracao_externa: Decimal) -> Fracao:
        carteira = gerar_carteira(
            replace(parametros, fracao_externa=fracao_externa),
            TAMANHO_DA_CARTEIRA_DE_RISCO,
            SEMENTE_DA_CARTEIRA_DE_RISCO,
            politica,
        )
        painel = simular_painel(
            carteira, cenario.choques, politica, cenario.taxa_sem_reforco, cenario.premissas
        )
        exposicao = painel.perda.exposicao
        if exposicao.centavos == 0:
            return Fracao(Decimal(0))
        return Fracao(
            Decimal(painel.perda.perda_esperada_total.centavos) / Decimal(exposicao.centavos)
        )

    return TaxasDePerda(
        cenario_id=cenario.id,
        nome=cenario.nome,
        condicional_interna=condicional(Decimal(0)),
        condicional_externa=condicional(Decimal(1)),
    )
