"""Serialização do catálogo regulatório para a matriz pública (`GET /v1/regulacao/matriz`).

É só leitura do catálogo: nada aqui decide regra. Dinheiro e razões saem como texto.
"""

from __future__ import annotations

from typing import Any

from app.dominio.regulacao import (
    Afirmacao,
    CatalogoRegulatorio,
    RegraDoAtivo,
    RegraDoTrilho,
    RetencaoNaExecucao,
)


def _afirmacao(valor: Afirmacao) -> dict[str, Any]:
    return {
        "tema": valor.tema.value,
        "texto": valor.texto,
        "categoria": valor.categoria.value,
        "norma": valor.norma,
        "artigo": valor.artigo,
    }


def _afirmacoes(valores: tuple[Afirmacao, ...]) -> list[dict[str, Any]]:
    return [_afirmacao(a) for a in valores]


def _retencao(valor: RetencaoNaExecucao | None) -> dict[str, Any] | None:
    if valor is None:
        return None
    return {
        "regra": valor.regra.value,
        "percentual": None if valor.percentual is None else str(valor.percentual),
        "afirmacoes": _afirmacoes(valor.afirmacoes),
    }


def _trilho(valor: RegraDoTrilho) -> dict[str, Any]:
    return {
        "nome": valor.nome,
        "rota": valor.rota,
        "prazo_dias_uteis": valor.prazo_dias_uteis,
        "prazos_por_ativo": None
        if valor.prazos_por_ativo is None
        else {tipo.value: dias for tipo, dias in valor.prazos_por_ativo.items()},
        "prazo_e_regulatorio": valor.prazo_e_regulatorio,
        "pode_recusar": valor.pode_recusar,
        "afirmacoes": _afirmacoes(valor.afirmacoes),
    }


def _ativo(valor: RegraDoAtivo) -> dict[str, Any]:
    return {
        "nome": valor.nome,
        "recomendado": valor.recomendado,
        "infraestrutura": valor.infraestrutura.value,
        "instrumentos": [i.value for i in valor.instrumentos],
        "trilhos": [t.value for t in valor.trilhos],
        "produtos": [p.value for p in valor.produtos],
        "condicao_no_cartao": None
        if valor.condicao_no_cartao is None
        else valor.condicao_no_cartao.value,
        "liquidez_maxima_no_cartao_dias_uteis": valor.liquidez_maxima_no_cartao_dias_uteis,
        "dias_minimos_execucao": valor.dias_minimos_execucao,
        "planos_aceitos": list(valor.planos_aceitos),
        "faixas_de_ltv": [
            {"ate_anos": f.ate_anos, "minimo": str(f.minimo), "maximo": str(f.maximo)}
            for f in valor.faixas_de_ltv
        ],
        "carteira_de_provisao": {i.value: c.value for i, c in valor.carteira_de_provisao.items()},
        "retencao": _retencao(valor.retencao),
        "afirmacoes": _afirmacoes(valor.afirmacoes),
    }


def matriz_do_catalogo(catalogo: CatalogoRegulatorio) -> dict[str, Any]:
    return {
        "ctvm_do_grupo_e_agente_do_td": catalogo.ctvm_do_grupo_e_agente_do_td,
        "prazo_da_baixa_dias_uteis": catalogo.prazo_da_baixa_dias_uteis,
        "teto_do_fgc": catalogo.teto_do_fgc.texto(),
        "rota_do_emprestimo": catalogo.rota_do_emprestimo,
        "rota_da_matriz": catalogo.rota_da_matriz,
        "afirmacoes": _afirmacoes(catalogo.afirmacoes),
        "tabela_regressiva_renda_fixa": [
            {"ate_dias_corridos": f.ate_dias_corridos, "aliquota": str(f.aliquota)}
            for f in catalogo.tabela_regressiva_renda_fixa
        ],
        "pisos_de_provisao": {
            c.value: {
                "primeiro_mes": str(p.primeiro_mes),
                "decimo_terceiro_mes": str(p.decimo_terceiro_mes),
            }
            for c, p in catalogo.pisos_de_provisao.items()
        },
        "trilhos": {t.value: _trilho(r) for t, r in catalogo.trilhos.items()},
        "ativos": {t.value: _ativo(r) for t, r in catalogo.ativos.items()},
    }
