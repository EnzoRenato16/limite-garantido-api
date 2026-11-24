"""Gera os blocos de `docs/regulacao.md` que vêm do catálogo regulatório.

    python scripts/gerar_regulacao_md.py            # reescreve os blocos no documento
    python scripts/gerar_regulacao_md.py --conferir # sai com 1 se o documento está velho

O que sai daqui é o bloco da matriz (entre `<!-- matriz:inicio -->` e
`<!-- matriz:fim -->`) e o do vocabulário (entre `<!-- vocabulario:inicio -->` e
`<!-- vocabulario:fim -->`). O resto do documento é texto escrito à mão. A matriz sai de
`app/regulacao.toml`, a mesma fonte da página /regulacao: nada é digitado duas vezes.

`celulas_do_ativo` é também o que o teste da página usa para conferir cada célula.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.api.matriz import matriz_do_catalogo  # noqa: E402
from app.infra.regulacao import carregar_catalogo  # noqa: E402

DOCUMENTO = RAIZ / "docs" / "regulacao.md"

CATEGORIAS = {
    "norma": "norma",
    "pratica_de_mercado": "prática de mercado",
    "interpretacao": "interpretação",
    "imprensa": "imprensa",
    "nao_verificado": "não verificado",
}

COLUNAS = [
    ("o_que_funciona", "O que funciona"),
    ("trilho", "Trilho"),
    ("instrumento", "Instrumento"),
    ("infraestrutura", "Infraestrutura"),
    ("produto", "Produto"),
    ("prazo", "Prazo"),
    ("risco_de_execucao", "Risco de execução"),
    ("norma", "Norma e artigo"),
]

INFRAESTRUTURAS = {
    "b3_balcao": "B3 Balcão",
    "b3_central_depositaria": "B3 Central Depositária",
    "selic_td_garantia": "Selic (TD Garantia)",
    "registradora_ou_b3_escriturador": "Registradora ou B3, com comando do escriturador",
    "plataforma_res_conjunta_12": "Plataforma da Res. Conjunta 12 (simulada)",
}
INSTRUMENTOS = {
    "cessao_fiduciaria": "cessão fiduciária",
    "alienacao_fiduciaria": "alienação fiduciária",
    "penhor": "penhor",
    "direito_de_resgate": "direito de resgate",
}
PRODUTOS = {"cartao": "cartão", "emprestimo": "empréstimo"}
CONDICOES = {
    "liquidez_diaria": "cartão só com liquidez diária",
    "resgate_ate_d1": "cartão só com resgate em D+0 ou D+1",
    "ctvm_agente_do_td": "cartão só se a CTVM do grupo for agente de custódia do TD",
}

NOTA_JURIDICA = "O autor não é advogado; a modelagem jurídica precisa ser validada."


def _dias(n: int) -> str:
    return f"{n} dia útil" if n == 1 else f"{n} dias úteis"


def _valor_do_prazo(matriz: dict[str, Any], tipo: str, trilho: str) -> str:
    regra = matriz["trilhos"][trilho]
    if trilho == "casa":
        return f"{regra['nome']}: no mesmo dia"
    por_ativo = regra["prazos_por_ativo"] or {}
    dias = por_ativo.get(tipo, regra["prazo_dias_uteis"])
    if dias is None:
        return f"{regra['nome']}: sem prazo fixo"
    origem = "prazo da norma" if regra["prazo_e_regulatorio"] else "ilustrativo do projeto"
    return f"{regra['nome']}: {_dias(dias)} ({origem})"


def celulas_do_ativo(matriz: dict[str, Any], tipo: str) -> dict[str, dict[str, Any]]:
    """Para cada coluna: `valor` (o dado estruturado, ou None) e `itens` (afirmações com a
    categoria, ou, em "norma", as normas citadas)."""
    ativo = matriz["ativos"][tipo]
    todas = list(ativo["afirmacoes"])
    if ativo["retencao"]:
        todas += ativo["retencao"]["afirmacoes"]
    celulas: dict[str, dict[str, Any]] = {}
    for coluna, _ in COLUNAS[:-1]:
        celulas[coluna] = {
            "valor": None,
            "itens": [
                {"texto": a["texto"], "categoria": a["categoria"]}
                for a in ativo["afirmacoes"]
                if a["tema"] == coluna
            ],
        }
    celulas["trilho"]["valor"] = ", ".join(matriz["trilhos"][t]["nome"] for t in ativo["trilhos"])
    celulas["instrumento"]["valor"] = ", ".join(INSTRUMENTOS[i] for i in ativo["instrumentos"])
    celulas["infraestrutura"]["valor"] = INFRAESTRUTURAS[ativo["infraestrutura"]]
    produto = " e ".join(PRODUTOS[p] for p in ativo["produtos"])
    condicao = ativo["condicao_no_cartao"]
    if condicao and "cartao" in ativo["produtos"]:
        produto += f" ({CONDICOES[condicao]})"
    elif "cartao" not in ativo["produtos"]:
        produto = "só " + produto
    celulas["produto"]["valor"] = produto[0].upper() + produto[1:]
    celulas["prazo"]["valor"] = "; ".join(
        _valor_do_prazo(matriz, tipo, t) for t in ativo["trilhos"]
    )
    citadas: list[str] = []
    for a in todas:
        if a["norma"]:
            texto = a["norma"] + (f", {a['artigo']}" if a["artigo"] else "")
            if texto not in citadas:
                citadas.append(texto)
    celulas["norma"] = {"valor": None, "itens": [{"texto": t, "categoria": None} for t in citadas]}
    return celulas


def _fonte(a: dict[str, Any]) -> str:
    if not a["norma"]:
        return ""
    return f" ({a['norma']}{', ' + a['artigo'] if a['artigo'] else ''})"


def _linha(a: dict[str, Any]) -> str:
    return f"[{CATEGORIAS[a['categoria']]}] {a['texto']}{_fonte(a)}"


def _limpar(texto: str) -> str:
    return texto.replace("\n", " ")


def gerar_matriz(matriz: dict[str, Any]) -> str:
    saida: list[str] = []
    saida.append("### Matriz por ativo")
    saida.append("")
    saida.append(
        "Cada afirmação traz a categoria entre colchetes: "
        + ", ".join(CATEGORIAS.values())
        + ". Prazos em dias úteis, pelo calendário de feriados nacionais do projeto."
    )
    for tipo, ativo in matriz["ativos"].items():
        saida += ["", f"#### {ativo['nome']}", ""]
        if not ativo["recomendado"]:
            saida += ["Não recomendado como garantia.", ""]
        celulas = celulas_do_ativo(matriz, tipo)
        for coluna, titulo in COLUNAS:
            celula = celulas[coluna]
            if coluna == "norma":
                if celula["itens"]:
                    saida.append(
                        f"- **{titulo}:** " + "; ".join(i["texto"] for i in celula["itens"])
                    )
                continue
            partes = [celula["valor"]] if celula["valor"] else []
            saida.append(
                f"- **{titulo}:** " + _limpar(" ".join(partes)) if partes else f"- **{titulo}:**"
            )
            for afirmacao in ativo["afirmacoes"]:
                if afirmacao["tema"] == coluna:
                    saida.append(f"  - {_limpar(_linha(afirmacao))}")
    saida += ["", "### Trilhos de entrada", ""]
    for trilho in matriz["trilhos"].values():
        regulatorio = "regulatório" if trilho["prazo_e_regulatorio"] else "não regulatório"
        recusa = "pode recusar" if trilho["pode_recusar"] else "não pode recusar"
        dias = trilho["prazo_dias_uteis"]
        prazo = (
            f"{_dias(dias)}, {regulatorio}" if dias is not None else "prazo por ativo ou sem prazo"
        )
        saida.append(f"#### {trilho['nome']}")
        saida.append("")
        saida.append(f"- **Rota:** `{trilho['rota']}`")
        saida.append(f"- **Prazo:** {prazo}; {recusa}")
        por_ativo = trilho["prazos_por_ativo"]
        if por_ativo:
            saida.append(
                "- **Prazos por ativo:** "
                + ", ".join(f"{t}: {_dias(d)}" for t, d in por_ativo.items())
            )
        for afirmacao in trilho["afirmacoes"]:
            saida.append(f"  - {_limpar(_linha(afirmacao))}")
        saida.append("")
    saida += ["### Regras gerais", ""]
    for afirmacao in matriz["afirmacoes"]:
        saida.append(f"- {_limpar(_linha(afirmacao))}")
    saida += [
        "",
        f"Teto do FGC: R$ {matriz['teto_do_fgc']} por titular e conglomerado. "
        f"Baixa do gravame: pedida em até {_dias(matriz['prazo_da_baixa_dias_uteis'])} "
        "depois da quitação, da liberação ou da execução.",
    ]
    while saida and saida[-1] == "":
        saida.pop()
    return "\n".join(saida)


VOCABULARIO = [
    ("guardado, guardar", "custodiado, custodiar, custodiante"),
    (
        "bloqueio no app",
        "gravame registrado na B3 ou no Selic (conta gravame, conta de ônus e gravames)",
    ),
    ("corretora do cliente (no Tesouro)", "agente de custódia"),
    (
        "seguradora (na Res. Conjunta 12)",
        "entidade operadora (seguradora, EAPC ou sociedade de capitalização)",
    ),
    (
        "B3 registra",
        "a registradora ou o depositário central registra; o custodiante comanda",
    ),
    ("parceiro correspondente", "entidade tomadora de BaaS"),
    (
        "portabilidade de CDB pela CVM",
        "transferência de custódia de CDB (fora da CVM 210)",
    ),
    (
        "garantia elimina o risco",
        "garantia reduz a perda; restam risco do emissor, de preço e de liquidez",
    ),
]


def gerar_vocabulario() -> str:
    linhas = ["| Evite | Use |", "|---|---|"]
    linhas += [f"| {evite} | {use} |" for evite, use in VOCABULARIO]
    return "\n".join(linhas)


BLOCOS = {
    "matriz": lambda: gerar_matriz(matriz_do_catalogo(carregar_catalogo())),
    "vocabulario": gerar_vocabulario,
}


def bloco_entre_marcadores(texto: str, nome: str) -> str:
    inicio = f"<!-- {nome}:inicio -->"
    fim = f"<!-- {nome}:fim -->"
    return texto.split(inicio, 1)[1].split(fim, 1)[0].strip("\n")


def substituir(texto: str, nome: str, conteudo: str) -> str:
    inicio = f"<!-- {nome}:inicio -->"
    fim = f"<!-- {nome}:fim -->"
    antes, resto = texto.split(inicio, 1)
    _, depois = resto.split(fim, 1)
    return f"{antes}{inicio}\n{conteudo}\n{fim}{depois}"


def atualizar(texto: str) -> str:
    for nome, gerar in BLOCOS.items():
        texto = substituir(texto, nome, gerar())
    return texto


def main(argumentos: list[str]) -> int:
    atual = DOCUMENTO.read_text(encoding="utf-8")
    novo = atualizar(atual)
    if "--conferir" in argumentos:
        if novo != atual:
            print("docs/regulacao.md está desatualizado: rode scripts/gerar_regulacao_md.py")
            return 1
        return 0
    DOCUMENTO.write_text(novo, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
