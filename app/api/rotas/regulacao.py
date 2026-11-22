"""A matriz regulatória: o que cada ativo aceita, por qual trilho, em qual produto."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.matriz import matriz_do_catalogo
from app.api.recursos import Recursos

rotas_de_regulacao = APIRouter(prefix="/v1/regulacao", tags=["Regulação"])


@rotas_de_regulacao.get("/matriz", summary="Matriz regulatória do catálogo")
def matriz(request: Request) -> dict[str, Any]:
    """Serialização do catálogo regulatório (`app/regulacao.toml`): para cada ativo, a
    infraestrutura (B3, Selic, escriturador, plataforma da Res. Conjunta 12), o
    instrumento (cessão fiduciária, alienação fiduciária, penhor ou direito de resgate), os
    trilhos de entrada, o produto permitido (cartão ou empréstimo), a retenção na execução e
    a faixa de LTV; para cada trilho, o prazo em dias úteis. Toda afirmação traz a categoria
    (`norma` com norma e artigo, `pratica_de_mercado`, `interpretacao`, `imprensa` ou
    `nao_verificado`).

    **Pública**: não exige API key. O projeto simula o registro; não integra com B3, Selic
    nem com a plataforma da Res. Conjunta 12.
    """
    recursos: Recursos = request.app.state.recursos
    return matriz_do_catalogo(recursos.catalogo)
