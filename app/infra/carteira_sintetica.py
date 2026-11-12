"""Carteira sintética para o painel de risco.

Gera em memória, a partir de uma semente, uma carteira de clientes com garantias
variadas. Não lê nem grava nada no banco de nenhum parceiro. Os parâmetros (mistura de
ativos, faixas de valor, uso do limite) estão em `app/carteira_sintetica.toml` e são
ilustrativos.

Dinheiro é sorteado em centavos inteiros. Os pesos e probabilidades usam o gerador do
`random` com semente, que é determinístico; nenhum float vira dinheiro.
"""

from __future__ import annotations

import random
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import ValorInvalido
from app.dominio.politica import PoliticaRisco, TipoAtivo
from app.dominio.stress import ContaNaCarteira, GarantiaNaCarteira, PremissasDaGarantiaExterna
from app.infra.politica_referencia import carregar_politica_de_referencia

ARQUIVO_PADRAO = Path(__file__).resolve().parent.parent / "carteira_sintetica.toml"
TAMANHO_PADRAO = 1000
TAMANHO_MAXIMO = 5000
CENTAVOS_POR_REAL = 100


@dataclass(frozen=True, slots=True)
class FaixaDeValor:
    de_centavos: int
    ate_centavos: int
    peso: int


@dataclass(frozen=True, slots=True)
class Cenario:
    id: str
    nome: str
    descricao: str
    choques: Mapping[TipoAtivo, Decimal]
    taxa_sem_reforco: Decimal
    premissas: PremissasDaGarantiaExterna


@dataclass(frozen=True, slots=True)
class ParametrosDaCarteira:
    pesos_por_ativo: Mapping[TipoAtivo, int]
    pesos_de_quantidade: Sequence[int]
    faixas_de_valor: Sequence[FaixaDeValor]
    fracao_externa: Decimal
    pesos_por_custodiante: Mapping[str, int]
    uso_minimo_percentual: int
    uso_maximo_percentual: int
    fracao_em_chamada: Decimal
    desconto_diario_por_ativo: Mapping[TipoAtivo, Decimal]
    cenarios: Mapping[str, Cenario]


@lru_cache
def carregar_parametros(arquivo: Path = ARQUIVO_PADRAO) -> ParametrosDaCarteira:
    with arquivo.open("rb") as conteudo:
        dados = tomllib.load(conteudo)
    desconto = {
        TipoAtivo(tipo): Decimal(valor)
        for tipo, valor in dados["desconto_diario_por_ativo"].items()
    }
    cenarios = {
        cenario_id: Cenario(
            id=cenario_id,
            nome=str(campos["nome"]),
            descricao=str(campos["descricao"]),
            choques={TipoAtivo(t): Decimal(v) for t, v in campos["choques"].items()},
            taxa_sem_reforco=Decimal(campos["taxa_sem_reforco"]),
            premissas=PremissasDaGarantiaExterna(
                custodiantes_indisponiveis=frozenset(campos["custodiantes_indisponiveis"]),
                dias_extra_de_execucao=int(campos["dias_extra_de_execucao"]),
                desconto_diario_por_ativo=desconto,
            ),
        )
        for cenario_id, campos in dados["cenarios"].items()
    }
    externa = dados["garantia_externa"]
    uso = dados["uso_do_limite"]
    return ParametrosDaCarteira(
        pesos_por_ativo={TipoAtivo(t): int(p) for t, p in dados["pesos_por_ativo"].items()},
        pesos_de_quantidade=[int(p) for p in dados["garantias_por_cliente"]["pesos"]],
        faixas_de_valor=[
            FaixaDeValor(
                int(faixa["de"]) * CENTAVOS_POR_REAL,
                int(faixa["ate"]) * CENTAVOS_POR_REAL,
                int(faixa["peso"]),
            )
            for faixa in dados["faixas_de_valor"]
        ],
        fracao_externa=Decimal(externa["fracao"]),
        pesos_por_custodiante={c: int(p) for c, p in externa["pesos_por_custodiante"].items()},
        uso_minimo_percentual=int(uso["minimo_percentual"]),
        uso_maximo_percentual=int(uso["maximo_percentual"]),
        fracao_em_chamada=Decimal(uso["fracao_em_chamada"]),
        desconto_diario_por_ativo=desconto,
        cenarios=cenarios,
    )


def _sorteia(sorteio: random.Random, probabilidade: Decimal) -> bool:
    # Inteiro em pontos-base contra a probabilidade: nada de float na comparação.
    return sorteio.randrange(10_000) < int(probabilidade * 10_000)


def gerar_carteira(
    parametros: ParametrosDaCarteira,
    tamanho: int,
    semente: int,
    politica: PoliticaRisco | None = None,
) -> list[ContaNaCarteira]:
    """`tamanho` clientes sintéticos. Mesma semente, mesma carteira."""
    if not 1 <= tamanho <= TAMANHO_MAXIMO:
        raise ValorInvalido(f"A carteira sintética vai de 1 a {TAMANHO_MAXIMO} clientes")
    politica = politica or carregar_politica_de_referencia()
    sorteio = random.Random(semente)  # noqa: S311 - simulação, não criptografia
    # Listas em ordem fixa: a iteração de dicionário segue a ordem do arquivo, mas a
    # ordem explícita deixa o resultado imune a qualquer mudança nisso.
    ativos = sorted(parametros.pesos_por_ativo, key=str)
    pesos_ativos = [parametros.pesos_por_ativo[a] for a in ativos]
    custodiantes = sorted(parametros.pesos_por_custodiante)
    pesos_custodiantes = [parametros.pesos_por_custodiante[c] for c in custodiantes]
    faixas = list(parametros.faixas_de_valor)
    quantidades = list(range(1, len(parametros.pesos_de_quantidade) + 1))

    carteira = []
    for numero in range(tamanho):
        garantias = []
        limite_garantido = Dinheiro.zero()
        for _ in range(sorteio.choices(quantidades, parametros.pesos_de_quantidade)[0]):
            tipo = sorteio.choices(ativos, pesos_ativos)[0]
            faixa = sorteio.choices(faixas, [f.peso for f in faixas])[0]
            valor = Dinheiro(sorteio.randint(faixa.de_centavos, faixa.ate_centavos))
            externa = tipo is not TipoAtivo.DEPOSITO and _sorteia(
                sorteio, parametros.fracao_externa
            )
            custodiante = sorteio.choices(custodiantes, pesos_custodiantes)[0] if externa else None
            garantias.append(GarantiaNaCarteira(tipo, valor, custodiante))
            limite_garantido += politica.limite_da_garantia(tipo, valor)

        if _sorteia(sorteio, parametros.fracao_em_chamada):
            # Já em chamada: dívida de 101% a 110% do limite garantido.
            uso = Decimal(sorteio.randint(101, 110)) / 100
            divida = limite_garantido.aplicar_razao(uso, para_cima=True)
        else:
            limite = limite_garantido
            if politica.teto_limite_por_cliente is not None:
                limite = min(limite, politica.teto_limite_por_cliente)
            uso = (
                Decimal(
                    sorteio.randint(
                        parametros.uso_minimo_percentual, parametros.uso_maximo_percentual
                    )
                )
                / 100
            )
            divida = limite.aplicar_razao(uso)
        carteira.append(
            ContaNaCarteira(
                conta_id=f"sint_{numero:05d}", divida=divida, garantias=tuple(garantias)
            )
        )
    return carteira
