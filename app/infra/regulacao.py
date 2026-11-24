"""Carrega o catálogo regulatório de `app/regulacao.toml`.

Arquivo quebrado é erro de implantação, não de entrada: levanta
`ErroNoCatalogoRegulatorio` na carga, com o caminho da chave que falhou. A aplicação lê o
catálogo na subida (`criar_app`), então um TOML inválido impede a API de subir.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.dominio.dinheiro import Dinheiro, ValorMonetarioInvalido
from app.dominio.erros import ErroDominio
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import (
    Afirmacao,
    CarteiraDeProvisao,
    CatalogoRegulatorio,
    Categoria,
    CondicaoNoCartao,
    FaixaDaTabelaRegressiva,
    FaixaDeLtv,
    Infraestrutura,
    Instrumento,
    PisoDeProvisao,
    Produto,
    RegraDeRetencao,
    RegraDoAtivo,
    RegraDoTrilho,
    RetencaoNaExecucao,
    TemaDaAfirmacao,
    Trilho,
)

ARQUIVO_PADRAO = Path(__file__).resolve().parent.parent / "regulacao.toml"


class ErroNoCatalogoRegulatorio(RuntimeError):
    """O TOML do catálogo regulatório está quebrado: bug de implantação."""


class _Erro(Exception):
    """Erro de leitura com o caminho da chave."""


def _texto(dados: Mapping[str, Any], chave: str, caminho: str) -> str:
    valor = dados.get(chave)
    if not isinstance(valor, str) or not valor.strip():
        raise _Erro(f"{caminho}.{chave}: precisa ser texto não vazio")
    return valor


def _texto_opcional(dados: Mapping[str, Any], chave: str, caminho: str) -> str | None:
    if chave not in dados:
        return None
    return _texto(dados, chave, caminho)


def _decimal(valor: object, caminho: str) -> Decimal:
    if not isinstance(valor, str):
        raise _Erro(f'{caminho}: o valor precisa ser texto decimal, como "0.85"')
    try:
        return Decimal(valor)
    except InvalidOperation:
        raise _Erro(f'{caminho}: "{valor}" não é um número') from None


def _inteiro(valor: object, caminho: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise _Erro(f"{caminho}: precisa ser inteiro")
    return valor


def _inteiro_opcional(dados: Mapping[str, Any], chave: str, caminho: str) -> int | None:
    if chave not in dados:
        return None
    return _inteiro(dados[chave], f"{caminho}.{chave}")


def _booleano(dados: Mapping[str, Any], chave: str, caminho: str) -> bool:
    valor = dados.get(chave)
    if not isinstance(valor, bool):
        raise _Erro(f"{caminho}.{chave}: precisa ser true ou false")
    return valor


def _lista(dados: Mapping[str, Any], chave: str, caminho: str) -> list[Any]:
    valor = dados.get(chave, [])
    if not isinstance(valor, list):
        raise _Erro(f"{caminho}.{chave}: precisa ser lista")
    return valor


def _tabela(dados: Mapping[str, Any], chave: str, caminho: str) -> Mapping[str, Any]:
    valor = dados.get(chave)
    if not isinstance(valor, dict):
        raise _Erro(f"{caminho}.{chave}: precisa ser tabela")
    return valor


def _enum[E: StrEnum](tipo: type[E], valor: object, caminho: str) -> E:
    if not isinstance(valor, str):
        raise _Erro(f"{caminho}: precisa ser texto, não {valor!r}")
    try:
        return tipo(valor)
    except ValueError:
        aceitos = ", ".join(item.value for item in tipo)
        raise _Erro(f"{caminho}: {valor!r} não é um de {aceitos}") from None


def _afirmacoes(dados: Mapping[str, Any], caminho: str) -> tuple[Afirmacao, ...]:
    afirmacoes = []
    for posicao, item in enumerate(_lista(dados, "afirmacoes", caminho)):
        aqui = f"{caminho}.afirmacoes[{posicao}]"
        if not isinstance(item, dict):
            raise _Erro(f"{aqui}: precisa ser tabela")
        afirmacoes.append(
            Afirmacao(
                texto=_texto(item, "texto", aqui),
                categoria=_enum(Categoria, item.get("categoria"), f"{aqui}.categoria"),
                norma=_texto_opcional(item, "norma", aqui),
                artigo=_texto_opcional(item, "artigo", aqui),
                tema=_enum(TemaDaAfirmacao, item.get("tema", "geral"), f"{aqui}.tema"),
            )
        )
    return tuple(afirmacoes)


def _trilho(trilho: Trilho, dados: Mapping[str, Any], caminho: str) -> RegraDoTrilho:
    prazos = None
    if "prazos_por_ativo" in dados:
        bruto = _tabela(dados, "prazos_por_ativo", caminho)
        prazos = MappingProxyType(
            {
                _enum(TipoAtivo, tipo, f"{caminho}.prazos_por_ativo"): _inteiro(
                    dias, f"{caminho}.prazos_por_ativo.{tipo}"
                )
                for tipo, dias in bruto.items()
            }
        )
    return RegraDoTrilho(
        trilho=trilho,
        nome=_texto(dados, "nome", caminho),
        prazo_dias_uteis=_inteiro_opcional(dados, "prazo_dias_uteis", caminho),
        prazo_e_regulatorio=_booleano(dados, "prazo_e_regulatorio", caminho),
        pode_recusar=_booleano(dados, "pode_recusar", caminho),
        afirmacoes=_afirmacoes(dados, caminho),
        rota=_texto(dados, "rota", caminho),
        prazos_por_ativo=prazos,
    )


def _faixas(dados: Mapping[str, Any], caminho: str) -> tuple[FaixaDeLtv, ...]:
    faixas = []
    for posicao, item in enumerate(_lista(dados, "faixas_de_ltv", caminho)):
        aqui = f"{caminho}.faixas_de_ltv[{posicao}]"
        if not isinstance(item, dict):
            raise _Erro(f"{aqui}: precisa ser tabela")
        faixas.append(
            FaixaDeLtv(
                ate_anos=_inteiro_opcional(item, "ate_anos", aqui),
                minimo=_decimal(item.get("minimo"), f"{aqui}.minimo"),
                maximo=_decimal(item.get("maximo"), f"{aqui}.maximo"),
            )
        )
    return tuple(faixas)


def _retencao(dados: Mapping[str, Any], caminho: str) -> RetencaoNaExecucao | None:
    if "retencao" not in dados:
        return None
    bruto = _tabela(dados, "retencao", caminho)
    aqui = f"{caminho}.retencao"
    percentual = bruto.get("percentual")
    return RetencaoNaExecucao(
        regra=_enum(RegraDeRetencao, bruto.get("regra"), f"{aqui}.regra"),
        percentual=None if percentual is None else _decimal(percentual, f"{aqui}.percentual"),
        afirmacoes=_afirmacoes(bruto, aqui),
    )


def _ativo(tipo: TipoAtivo, dados: Mapping[str, Any], caminho: str) -> RegraDoAtivo:
    carteiras = _tabela(dados, "carteira_de_provisao", caminho)
    condicao = dados.get("condicao_no_cartao")
    return RegraDoAtivo(
        tipo=tipo,
        nome=_texto(dados, "nome", caminho),
        infraestrutura=_enum(
            Infraestrutura, dados.get("infraestrutura"), f"{caminho}.infraestrutura"
        ),
        instrumentos=tuple(
            _enum(Instrumento, item, f"{caminho}.instrumentos")
            for item in _lista(dados, "instrumentos", caminho)
        ),
        trilhos=tuple(
            _enum(Trilho, item, f"{caminho}.trilhos") for item in _lista(dados, "trilhos", caminho)
        ),
        produtos=tuple(
            _enum(Produto, item, f"{caminho}.produtos")
            for item in _lista(dados, "produtos", caminho)
        ),
        condicao_no_cartao=(
            None
            if condicao is None
            else _enum(CondicaoNoCartao, condicao, f"{caminho}.condicao_no_cartao")
        ),
        dias_minimos_execucao=_inteiro_opcional(dados, "dias_minimos_execucao", caminho),
        faixas_de_ltv=_faixas(dados, caminho),
        carteira_de_provisao=MappingProxyType(
            {
                _enum(Instrumento, instrumento, f"{caminho}.carteira_de_provisao"): _enum(
                    CarteiraDeProvisao, carteira, f"{caminho}.carteira_de_provisao.{instrumento}"
                )
                for instrumento, carteira in carteiras.items()
            }
        ),
        recomendado=_booleano(dados, "recomendado", caminho),
        afirmacoes=_afirmacoes(dados, caminho),
        retencao=_retencao(dados, caminho),
        liquidez_maxima_no_cartao_dias_uteis=_inteiro_opcional(
            dados, "liquidez_maxima_no_cartao_dias_uteis", caminho
        ),
        planos_aceitos=tuple(str(plano) for plano in _lista(dados, "planos_aceitos", caminho)),
    )


def _montar(dados: Mapping[str, Any]) -> CatalogoRegulatorio:
    ativos_brutos = _tabela(dados, "ativos", "")
    trilhos_brutos = _tabela(dados, "trilhos", "")
    pisos_brutos = _tabela(dados, "pisos_de_provisao", "")
    ativos = {}
    for nome, bruto in ativos_brutos.items():
        caminho = f"ativos.{nome}"
        if not isinstance(bruto, dict):
            raise _Erro(f"{caminho}: precisa ser tabela")
        tipo = _enum(TipoAtivo, nome, caminho)
        ativos[tipo] = _ativo(tipo, bruto, caminho)
    trilhos = {}
    for nome, bruto in trilhos_brutos.items():
        caminho = f"trilhos.{nome}"
        if not isinstance(bruto, dict):
            raise _Erro(f"{caminho}: precisa ser tabela")
        trilho = _enum(Trilho, nome, caminho)
        trilhos[trilho] = _trilho(trilho, bruto, caminho)
    pisos = {}
    for nome, bruto in pisos_brutos.items():
        caminho = f"pisos_de_provisao.{nome}"
        if not isinstance(bruto, dict):
            raise _Erro(f"{caminho}: precisa ser tabela")
        pisos[_enum(CarteiraDeProvisao, nome, caminho)] = PisoDeProvisao(
            primeiro_mes=_decimal(bruto.get("primeiro_mes"), f"{caminho}.primeiro_mes"),
            decimo_terceiro_mes=_decimal(
                bruto.get("decimo_terceiro_mes"), f"{caminho}.decimo_terceiro_mes"
            ),
        )
    tabela = []
    for posicao, item in enumerate(_lista(dados, "tabela_regressiva_renda_fixa", "")):
        caminho = f"tabela_regressiva_renda_fixa[{posicao}]"
        if not isinstance(item, dict):
            raise _Erro(f"{caminho}: precisa ser tabela")
        tabela.append(
            FaixaDaTabelaRegressiva(
                ate_dias_corridos=_inteiro_opcional(item, "ate_dias_corridos", caminho),
                aliquota=_decimal(item.get("aliquota"), f"{caminho}.aliquota"),
            )
        )
    teto = dados.get("teto_do_fgc")
    if not isinstance(teto, str):
        raise _Erro('teto_do_fgc: precisa ser texto em reais, como "250000.00"')
    return CatalogoRegulatorio(
        ativos=MappingProxyType(ativos),
        trilhos=MappingProxyType(trilhos),
        ctvm_do_grupo_e_agente_do_td=_booleano(dados, "ctvm_do_grupo_e_agente_do_td", ""),
        prazo_da_baixa_dias_uteis=_inteiro(
            dados.get("prazo_da_baixa_dias_uteis"), "prazo_da_baixa_dias_uteis"
        ),
        pisos_de_provisao=MappingProxyType(pisos),
        teto_do_fgc=Dinheiro.de_decimal(teto),
        tabela_regressiva_renda_fixa=tuple(tabela),
        rota_do_emprestimo=_texto(dados, "rota_do_emprestimo", ""),
        rota_da_matriz=_texto(dados, "rota_da_matriz", ""),
        afirmacoes=_afirmacoes(dados, ""),
    )


def montar_catalogo(dados: Mapping[str, Any]) -> CatalogoRegulatorio:
    """Monta e valida o catálogo a partir do TOML já lido."""
    try:
        return _montar(dados)
    except (_Erro, ErroDominio, ValorMonetarioInvalido) as erro:
        raise ErroNoCatalogoRegulatorio(f"Catálogo regulatório inválido: {erro}") from erro


@lru_cache
def carregar_catalogo(arquivo: Path = ARQUIVO_PADRAO) -> CatalogoRegulatorio:
    try:
        with arquivo.open("rb") as conteudo:
            dados = tomllib.load(conteudo)
    except (OSError, tomllib.TOMLDecodeError) as erro:
        raise ErroNoCatalogoRegulatorio(f"Não consegui ler {arquivo.name}: {erro}") from erro
    return montar_catalogo(dados)
