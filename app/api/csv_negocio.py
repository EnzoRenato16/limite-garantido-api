"""CSV do modelo econômico, para abrir direto no Excel em português.

UTF-8 com BOM, separador `;`, vírgula decimal sem separador de milhar e `\\r\\n`. Todo
texto passa por `neutralizar`, que barra injeção de fórmula; número sai de `numero_csv`
e nunca é neutralizado (`-1234,56` continua número).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from decimal import Decimal

from app.dominio.dinheiro import Dinheiro
from app.dominio.economia.analises import ResultadoEconomico
from app.dominio.economia.premissas import EsquemaDaPremissa, NomeDaPremissa

BOM = "﻿"
_GATILHOS_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def neutralizar(texto: str) -> str:
    """Texto que o Excel leria como fórmula ganha `'` na frente."""
    return "'" + texto if texto.startswith(_GATILHOS_DE_FORMULA) else texto


def numero_csv(valor: Decimal | Dinheiro | int) -> str:
    """Número em português: vírgula decimal, sem separador de milhar."""
    if isinstance(valor, Dinheiro):
        valor = valor.para_decimal()
    if isinstance(valor, int):
        return str(valor)
    return format(valor, "f").replace(".", ",")


def _sim_nao(valor: bool | None) -> str:
    return "" if valor is None else ("sim" if valor else "não")


def gerar_csv(
    resultado: ResultadoEconomico, esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa]
) -> bytes:
    saida = io.StringIO(newline="")
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    t = neutralizar

    def secao(titulo: str, cabecalho: list[str], linhas: list[list[str]]) -> None:
        if saida.tell():
            escritor.writerow([])
        escritor.writerow([t(titulo)])
        escritor.writerow([t(c) for c in cabecalho])
        escritor.writerows(linhas)

    p = resultado.premissas
    valores = p.valores()
    linhas_de_premissa = [
        [t(n.value), numero_csv(valores[n]), t(e.unidade), t(e.origem)] for n, e in esquema.items()
    ]
    linhas_de_premissa.append(
        [
            t("receitas_ligadas"),
            t(", ".join(sorted(f.value for f in p.receitas_ligadas))),
            "",
            "",
        ]
    )
    linhas_de_premissa.append([t("cenario_de_risco"), t(p.cenario_de_risco), "", ""])
    secao("Premissas", ["nome", "valor", "unidade", "origem"], linhas_de_premissa)

    secao(
        "Linhas por cliente por mês",
        ["linha", "origem", "destino", "valor (R$)", "fórmula"],
        [
            [t(x.nome), t(str(x.origem)), t(str(x.destino)), numero_csv(x.valor), t(x.formula)]
            for x in resultado.mes.linhas
        ],
    )

    mes, ano = resultado.mes, resultado.ano
    c, ix, eq = resultado.carteira, resultado.interna_x_externa, resultado.equilibrio

    def tres(nome: str, a: Dinheiro, b: Dinheiro, g: Dinheiro) -> list[str]:
        return [t(nome), numero_csv(a), numero_csv(b), numero_csv(g)]

    def so_plataforma(nome: str, valor: str) -> list[str]:
        return [t(nome), valor, "", ""]

    def opcional(valor: Dinheiro | None) -> str:
        return "" if valor is None else numero_csv(valor)

    visoes_mes = (mes.plataforma, mes.parceiro, mes.arranjo)
    visoes_ano = (ano["plataforma"], ano["parceiro"], ano["arranjo"])
    resultados: list[list[str]] = []
    for rotulo, visoes in (
        ("por cliente por mês", visoes_mes),
        ("por cliente por ano", visoes_ano),
    ):
        resultados += [
            tres(f"Receita {rotulo}", *(v.receita for v in visoes)),
            tres(f"Custo {rotulo}", *(v.custo for v in visoes)),
            tres(f"Margem {rotulo}", *(v.margem for v in visoes)),
        ]
    resultados += [
        so_plataforma("Clientes ativos", numero_csv(c.clientes_ativos)),
        [
            t("Resultado da carteira no mês (com custo fixo)"),
            numero_csv(c.plataforma_mes),
            numero_csv(c.parceiro_por_parceiro_mes),
            numero_csv(c.arranjo_mes),
        ],
        [
            t("Resultado da carteira no ano"),
            numero_csv(c.plataforma_ano),
            numero_csv(c.parceiro_por_parceiro_ano),
            "",
        ],
        so_plataforma(
            "Ponto de equilíbrio (clientes)", "" if eq.clientes is None else numero_csv(eq.clientes)
        ),
        so_plataforma("Motivo do ponto de equilíbrio", t(eq.motivo or "")),
        [
            t("Margem com garantia interna"),
            numero_csv(ix.margem_interna),
            numero_csv(ix.parceiro_interna),
            "",
        ],
        [
            t("Margem com garantia externa"),
            numero_csv(ix.margem_externa),
            numero_csv(ix.parceiro_externa),
            "",
        ],
        so_plataforma("Custo a mais da externa", numero_csv(ix.custo_a_mais_da_externa)),
        so_plataforma(
            "Fee de gravame externo para empatar", opcional(ix.fee_de_gravame_externo_para_empatar)
        ),
        so_plataforma("Por que não há fee para empatar", t(ix.motivo_fee or "")),
        so_plataforma("Gasto mensal para empatar", opcional(ix.gasto_para_empatar)),
        so_plataforma("Por que não há gasto para empatar", t(ix.motivo_gasto or "")),
        so_plataforma("Gasto que empata cabe no limite", _sim_nao(ix.gasto_cabe_no_limite)),
    ]
    secao("Resultados", ["indicador", "plataforma", "parceiro", "arranjo"], resultados)

    if resultado.tornado:
        secao(
            "Tornado",
            [
                "premissa",
                "valor baixo",
                "valor alto",
                "margem no baixo",
                "margem no alto",
                "amplitude",
            ],
            [
                [
                    t(esquema[b.premissa].nome_exibicao),
                    numero_csv(b.valor_baixo),
                    numero_csv(b.valor_alto),
                    numero_csv(b.margem_no_baixo),
                    numero_csv(b.margem_no_alto),
                    numero_csv(b.amplitude),
                ]
                for b in resultado.tornado
            ],
        )
    return (BOM + saida.getvalue()).encode("utf-8")
