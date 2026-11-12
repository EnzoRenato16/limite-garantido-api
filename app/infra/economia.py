"""Premissas do modelo econômico em TOML, e a perda que vem do motor de risco.

O TOML tem o esquema das 34 premissas (faixas e unidades) e os conjuntos prontos. Um
arquivo quebrado é erro de implantação, não de entrada: levanta `ErroNoArquivoDePremissas`
na carga, com o nome da chave. A perda condicional de cada cenário sai de duas rodadas de
`simular_painel` sobre os mesmos clientes da carteira sintética: uma com toda garantia
interna e outra com toda garantia que não é depósito em outro custodiante.
"""

from __future__ import annotations

import random
import tomllib
from collections.abc import Mapping, Sequence
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
from app.dominio.politica import TipoAtivo
from app.dominio.stress import ContaNaCarteira, GarantiaNaCarteira, simular_painel
from app.dominio.taxas import Fracao
from app.infra.carteira_sintetica import Cenario, carregar_parametros, gerar_carteira
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


class ErroNaPerdaDoCenario(RuntimeError):
    """A carteira sintética não dá uma perda condicional com sentido (exposição zero ou
    perda maior que a dívida): é configuração quebrada, não entrada do usuário."""


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


def _ler_esquema(nome: NomeDaPremissa, tabela: object) -> EsquemaDaPremissa:
    # O nome já foi conferido contra NomeDaPremissa: o conjunto de tabelas é exato.
    chave = f"premissas.{nome.value}"
    if not isinstance(tabela, dict):
        raise ErroNoArquivoDePremissas(f"{chave}: precisa ser uma tabela")
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
def carregar_parametros_da_economia(arquivo: Path | None = None) -> ParametrosDaEconomia:
    """Lê e valida o TOML (o padrão é `ARQUIVO_PADRAO`). A API chama na subida, então um
    arquivo quebrado impede a aplicação de subir em vez de virar 500 depois."""
    arquivo = ARQUIVO_PADRAO if arquivo is None else arquivo
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
    esquema = {nome: _ler_esquema(nome, brutas[nome.value]) for nome in NomeDaPremissa}
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


def carteiras_da_perda() -> tuple[list[ContaNaCarteira], list[ContaNaCarteira]]:
    """Os mesmos clientes em duas versões: toda garantia interna e toda externa.

    Uma carteira só, com a semente de sempre. Na interna, nenhuma garantia tem custodiante.
    Na externa, toda garantia que não é depósito fica num custodiante: a que já tinha um
    continua com ele, e a que não tinha ganha um sorteado pelos pesos da carteira, com um
    gerador separado de mesma semente, na ordem das contas e das garantias. Assim a
    diferença entre as duas perdas é só a garantia externa, não clientes diferentes.
    """
    parametros = carregar_parametros()
    carteira = gerar_carteira(
        parametros,
        TAMANHO_DA_CARTEIRA_DE_RISCO,
        SEMENTE_DA_CARTEIRA_DE_RISCO,
        carregar_politica_de_referencia(),
    )
    sorteio = random.Random(SEMENTE_DA_CARTEIRA_DE_RISCO)  # noqa: S311 - simulação
    custodiantes = sorted(parametros.pesos_por_custodiante)
    pesos = [parametros.pesos_por_custodiante[c] for c in custodiantes]

    def para_fora(garantia: GarantiaNaCarteira) -> GarantiaNaCarteira:
        if garantia.tipo_ativo is TipoAtivo.DEPOSITO or garantia.custodiante_id is not None:
            return garantia
        return replace(garantia, custodiante_id=sorteio.choices(custodiantes, pesos)[0])

    interna = [
        replace(
            conta,
            garantias=tuple(replace(g, custodiante_id=None) for g in conta.garantias),
        )
        for conta in carteira
    ]
    externa = [
        replace(conta, garantias=tuple(para_fora(g) for g in conta.garantias)) for conta in carteira
    ]
    return interna, externa


def perda_condicional(carteira: Sequence[ContaNaCarteira], cenario: Cenario) -> Fracao:
    """Perda esperada ÷ exposição do cenário: {BRL} ÷ {BRL} -> {1} por evento."""
    painel = simular_painel(
        carteira,
        cenario.choques,
        carregar_politica_de_referencia(),
        cenario.taxa_sem_reforco,
        cenario.premissas,
    )
    exposicao = painel.perda.exposicao.centavos
    perda = painel.perda.perda_esperada_total.centavos
    if exposicao <= 0:
        raise ErroNaPerdaDoCenario(
            f'Cenário "{cenario.id}": a carteira sintética tem exposição zero; '
            "confira app/carteira_sintetica.toml e a política de referência"
        )
    if not 0 <= perda <= exposicao:
        raise ErroNaPerdaDoCenario(
            f'Cenário "{cenario.id}": perda de {perda} centavos com exposição de '
            f"{exposicao}; a perda condicional precisa ficar entre 0 e 1"
        )
    return Fracao(Decimal(perda) / Decimal(exposicao))


@lru_cache
def taxas_de_perda(cenario_id: str) -> TaxasDePerda:
    """Perda esperada ÷ exposição do cenário, nos mesmos clientes com toda garantia
    interna e com toda externa (`carteiras_da_perda`).

    Cenário desconhecido é erro de quem pede (422). Exposição zero ou perda maior que a
    dívida é configuração quebrada: levanta `ErroNaPerdaDoCenario` (500)."""
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
    interna, externa = carteiras_da_perda()
    return TaxasDePerda(
        cenario_id=cenario.id,
        nome=cenario.nome,
        condicional_interna=perda_condicional(interna, cenario),
        condicional_externa=perda_condicional(externa, cenario),
    )
