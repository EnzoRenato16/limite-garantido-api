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
from app.dominio.regulacao import CatalogoRegulatorio, Produto, Trilho
from app.dominio.stress import ContaNaCarteira, GarantiaNaCarteira, PremissasDaGarantiaExterna
from app.infra.politica_referencia import carregar_politica_de_referencia
from app.infra.regulacao import carregar_catalogo

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
    fracao_de_emprestimo: Decimal
    pesos_por_ativo: Mapping[Produto, Mapping[TipoAtivo, int]]
    pesos_de_quantidade: Sequence[int]
    faixas_de_valor: Sequence[FaixaDeValor]
    pesos_por_agente_de_custodia: Mapping[str, int]
    entidade_operadora: str
    pesos_por_emissor: Mapping[str, int]
    prazos_em_anos: Mapping[TipoAtivo, Sequence[int]]
    # "Toda garantia de fora" da conta do negócio.
    pesos_por_agente_de_fora: Mapping[str, int]
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
                atraso_por_onde_esta={
                    local: int(dias)
                    for local, dias in campos.get("atraso_por_onde_esta", {}).items()
                },
                emissores_em_quebra=frozenset(campos.get("emissores_em_quebra", [])),
                desconto_diario_por_ativo=desconto,
            ),
        )
        for cenario_id, campos in dados["cenarios"].items()
    }
    onde = dados["onde_esta"]
    uso = dados["uso_do_limite"]
    return ParametrosDaCarteira(
        fracao_de_emprestimo=Decimal(dados["produtos"]["fracao_de_emprestimo"]),
        pesos_por_ativo={
            Produto.CARTAO: {
                TipoAtivo(t): int(p) for t, p in dados["pesos_por_ativo_no_cartao"].items()
            },
            Produto.EMPRESTIMO: {
                TipoAtivo(t): int(p) for t, p in dados["pesos_por_ativo_no_emprestimo"].items()
            },
        },
        pesos_de_quantidade=[int(p) for p in dados["garantias_por_cliente"]["pesos"]],
        faixas_de_valor=[
            FaixaDeValor(
                int(faixa["de"]) * CENTAVOS_POR_REAL,
                int(faixa["ate"]) * CENTAVOS_POR_REAL,
                int(faixa["peso"]),
            )
            for faixa in dados["faixas_de_valor"]
        ],
        pesos_por_agente_de_custodia={
            a: int(p) for a, p in onde["pesos_por_agente_de_custodia"].items()
        },
        entidade_operadora=str(onde["entidade_operadora"]),
        pesos_por_emissor={e: int(p) for e, p in dados["emissores"]["pesos"].items()},
        prazos_em_anos={
            TipoAtivo(t): [int(a) for a in anos] for t, anos in dados["prazos_em_anos"].items()
        },
        pesos_por_agente_de_fora={
            a: int(p) for a, p in dados["garantia_de_fora"]["pesos_por_agente"].items()
        },
        uso_minimo_percentual=int(uso["minimo_percentual"]),
        uso_maximo_percentual=int(uso["maximo_percentual"]),
        fracao_em_chamada=Decimal(uso["fracao_em_chamada"]),
        desconto_diario_por_ativo=desconto,
        cenarios=cenarios,
    )


def _sorteia(sorteio: random.Random, probabilidade: Decimal) -> bool:
    # Inteiro em pontos-base contra a probabilidade: nada de float na comparação.
    return sorteio.randrange(10_000) < int(probabilidade * 10_000)


# Como cada ativo chega à condição de garantia, para conferir a combinação no catálogo.
_TRILHO_DO_ATIVO = {
    TipoAtivo.TESOURO_SELIC: Trilho.TD_GARANTIA,
    TipoAtivo.TESOURO_PREFIXADO: Trilho.TD_GARANTIA,
    TipoAtivo.TESOURO_IPCA: Trilho.TD_GARANTIA,
    TipoAtivo.PREVIDENCIA: Trilho.RES_CONJUNTA_12,
}
# Resgate em {dia_util} dos ativos que vão ao cartão (CDB com liquidez diária, fundo DI D+1).
_LIQUIDEZ_NO_CARTAO = {TipoAtivo.CDB: 0, TipoAtivo.FUNDO_DI: 1}


def gerar_carteira(
    parametros: ParametrosDaCarteira,
    tamanho: int,
    semente: int,
    politica: PoliticaRisco | None = None,
    catalogo: CatalogoRegulatorio | None = None,
) -> list[ContaNaCarteira]:
    """`tamanho` clientes sintéticos. Mesma semente, mesma carteira.

    Cada garantia sai com produto, onde está, emissor e prazo. A combinação ativo, trilho
    e produto é conferida no catálogo regulatório: se o catálogo recusar, é bug da
    configuração da carteira e a exceção sobe.
    """
    if not 1 <= tamanho <= TAMANHO_MAXIMO:
        raise ValorInvalido(f"A carteira sintética vai de 1 a {TAMANHO_MAXIMO} clientes")
    politica = politica or carregar_politica_de_referencia()
    catalogo = catalogo or carregar_catalogo()
    sorteio = random.Random(semente)  # noqa: S311 - simulação, não criptografia
    # Listas em ordem fixa: a iteração de dicionário segue a ordem do arquivo, mas a
    # ordem explícita deixa o resultado imune a qualquer mudança nisso.
    ativos = {
        produto: sorted(pesos, key=str) for produto, pesos in parametros.pesos_por_ativo.items()
    }
    pesos_ativos = {
        produto: [parametros.pesos_por_ativo[produto][a] for a in lista]
        for produto, lista in ativos.items()
    }
    agentes = sorted(parametros.pesos_por_agente_de_custodia)
    pesos_agentes = [parametros.pesos_por_agente_de_custodia[a] for a in agentes]
    emissores = sorted(parametros.pesos_por_emissor)
    pesos_emissores = [parametros.pesos_por_emissor[e] for e in emissores]
    faixas = list(parametros.faixas_de_valor)
    quantidades = list(range(1, len(parametros.pesos_de_quantidade) + 1))

    carteira = []
    for numero in range(tamanho):
        produto = (
            Produto.EMPRESTIMO
            if _sorteia(sorteio, parametros.fracao_de_emprestimo)
            else Produto.CARTAO
        )
        garantias = []
        limite_garantido = Dinheiro.zero()
        for _ in range(sorteio.choices(quantidades, parametros.pesos_de_quantidade)[0]):
            tipo = sorteio.choices(ativos[produto], pesos_ativos[produto])[0]
            faixa = sorteio.choices(faixas, [f.peso for f in faixas])[0]
            valor = Dinheiro(sorteio.randint(faixa.de_centavos, faixa.ate_centavos))
            catalogo.exigir_combinacao(
                tipo,
                _TRILHO_DO_ATIVO.get(tipo, Trilho.CASA),
                produto,
                liquidez_dias=_LIQUIDEZ_NO_CARTAO.get(tipo) if produto is Produto.CARTAO else None,
            )
            onde_esta: str | None = None
            if tipo in _TRILHO_DO_ATIVO and tipo is TipoAtivo.PREVIDENCIA:
                onde_esta = parametros.entidade_operadora
            elif tipo in _TRILHO_DO_ATIVO:
                onde_esta = sorteio.choices(agentes, pesos_agentes)[0]
            emissor = (
                sorteio.choices(emissores, pesos_emissores)[0]
                if tipo in (TipoAtivo.CDB, TipoAtivo.LCI, TipoAtivo.LCA)
                else None
            )
            prazos = parametros.prazos_em_anos.get(tipo)
            vencimento = Decimal(sorteio.choice(prazos)) if prazos else None
            garantias.append(
                GarantiaNaCarteira(tipo, valor, onde_esta, emissor, vencimento, produto)
            )
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
