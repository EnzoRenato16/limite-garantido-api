"""Modelo econômico: premissas prontas e simulação em JSON ou CSV."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api import apresentacao, esquemas
from app.api.csv_negocio import gerar_csv
from app.api.dependencias import ParceiroAutenticado
from app.api.rotas.contas import ERROS_COMUNS
from app.dominio.economia.analises import simular_negocio
from app.dominio.economia.premissas import PremissasInvalidas, montar_premissas
from app.infra.economia import (
    carregar_parametros_da_economia,
    cenarios_de_risco,
    taxas_de_perda,
)

rotas_de_negocio = APIRouter(prefix="/v1/negocio", tags=["Negócio"], responses=ERROS_COMUNS)


@rotas_de_negocio.get(
    "/premissas",
    response_model=esquemas.CatalogoDoNegocio,
    summary="Premissas, conjuntos prontos e cenários do modelo econômico",
)
def consultar_premissas(_: ParceiroAutenticado) -> esquemas.CatalogoDoNegocio:
    """Cada premissa com unidade, faixa aceita, faixa razoável (a do tornado) e origem; os
    conjuntos prontos de valores; os cenários de risco que dão a perda; e as fontes de
    receita que se pode ligar ou desligar. Todos os números são ilustrativos."""
    return apresentacao.catalogo_do_negocio(carregar_parametros_da_economia(), cenarios_de_risco())


@rotas_de_negocio.post(
    "/simulacoes",
    response_model=esquemas.ResultadoDoNegocio,
    summary="Simular receitas, custos e margem por cliente",
    responses={
        200: {
            "description": "Resultado em JSON; com `Accept: text/csv`, o mesmo resultado em "
            "CSV (UTF-8 com BOM, `;` e vírgula decimal) para abrir no Excel.",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
def simular(
    corpo: esquemas.SimulacaoDeNegocio, requisicao: Request, _: ParceiroAutenticado
) -> esquemas.ResultadoDoNegocio | Response:
    """Parte do conjunto escolhido, aplica as premissas e as fontes de receita do corpo e
    calcula, por cliente por mês e por ano: as linhas de dinheiro entre as partes, as
    margens da plataforma, do parceiro e do arranjo, a carteira, o ponto de equilíbrio, a
    comparação entre garantia interna e externa e o tornado (sensibilidade).

    Premissa fora da faixa responde 422 com `codigo` `premissas_invalidas` e a lista
    `erros`. Envie `Accept: text/csv` para baixar o CSV. Números ilustrativos."""
    parametros = carregar_parametros_da_economia()
    conjunto = parametros.conjuntos.get(corpo.conjunto)
    if conjunto is None:
        validos = ", ".join(parametros.conjuntos)
        raise PremissasInvalidas(
            f'Conjunto "{corpo.conjunto}" não existe; use um de: {validos}',
            erros=[{"campo": "conjunto", "mensagem": f"conjunto desconhecido; use: {validos}"}],
        )
    valores = {**conjunto.valores, **(corpo.premissas or {})}
    ligadas = set(conjunto.receitas_ligadas)
    for fonte, ligada in (corpo.receitas_ligadas or {}).items():
        if ligada:
            ligadas.add(fonte)
        else:
            ligadas.discard(fonte)
    cenario = corpo.cenario_de_risco or conjunto.cenario_de_risco
    premissas = montar_premissas(valores, frozenset(ligadas), cenario, parametros.esquema)
    resultado = simular_negocio(
        premissas,
        taxas_de_perda(cenario),
        parametros.esquema,
        incluir_tornado=corpo.incluir_tornado,
    )
    if "text/csv" in requisicao.headers.get("accept", ""):
        return Response(
            content=gerar_csv(resultado, parametros.esquema),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="negocio.csv"'},
        )
    return apresentacao.resultado_do_negocio(resultado, parametros.esquema, cenarios_de_risco())
