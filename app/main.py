"""API da versão 0.1 (prova de conceito, em memória)."""

from decimal import Decimal
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.servico import ErroDeNegocio, Servico

app = FastAPI(title="Limite Garantido as a Service", version="0.1.0")
servico = Servico()
# Idempotência em memória: chave -> resposta já devolvida.
respostas_idempotentes: dict[str, Any] = {}


def _json(dados: Any) -> Any:
    """Serializa a resposta. Dinheiro sai como texto ("4250.00"), nunca como float."""
    return jsonable_encoder(dados, custom_encoder={Decimal: str})


ChaveIdempotencia = Annotated[str | None, Header(alias="Idempotency-Key")]


class NovaGarantia(BaseModel):
    cliente_id: str
    tipo_ativo: str
    valor: Decimal = Field(gt=0)


class Remarcacao(BaseModel):
    valor_de_mercado: Decimal = Field(gt=0)


class NovaCompra(BaseModel):
    cliente_id: str
    valor: Decimal = Field(gt=0)


class Pagamento(BaseModel):
    cliente_id: str
    valor: Decimal = Field(gt=0)


class Atraso(BaseModel):
    cliente_id: str
    dias: int = Field(ge=1)


@app.exception_handler(ErroDeNegocio)
async def tratar_erro_de_negocio(_: Any, erro: ErroDeNegocio) -> JSONResponse:
    return JSONResponse(
        status_code=erro.status, content={"codigo": erro.codigo, "mensagem": erro.mensagem}
    )


@app.post("/v1/garantias", status_code=201)
def bloquear_garantia(corpo: NovaGarantia, chave: ChaveIdempotencia = None) -> Any:
    if chave and f"garantia:{chave}" in respostas_idempotentes:
        return respostas_idempotentes[f"garantia:{chave}"]
    garantia = servico.bloquear_garantia(corpo.cliente_id, corpo.tipo_ativo, corpo.valor)
    resposta = _json({"garantia": garantia, "limite": servico.posicao(corpo.cliente_id)})
    if chave:
        respostas_idempotentes[f"garantia:{chave}"] = resposta
    return resposta


@app.post("/v1/garantias/{garantia_id}/remarcar")
def remarcar(garantia_id: str, corpo: Remarcacao) -> Any:
    garantia = servico.remarcar(garantia_id, corpo.valor_de_mercado)
    return _json({"garantia": garantia, "limite": servico.posicao(garantia.cliente_id)})


@app.post("/v1/garantias/{garantia_id}/liberar")
def liberar(garantia_id: str) -> Any:
    garantia = servico.liberar(garantia_id)
    return _json({"garantia": garantia, "limite": servico.posicao(garantia.cliente_id)})


@app.post("/v1/compras", status_code=201)
def autorizar_compra(corpo: NovaCompra, chave: ChaveIdempotencia = None) -> Any:
    if chave and f"compra:{chave}" in respostas_idempotentes:
        return respostas_idempotentes[f"compra:{chave}"]
    resposta = _json(servico.autorizar_compra(corpo.cliente_id, corpo.valor))
    if chave:
        respostas_idempotentes[f"compra:{chave}"] = resposta
    return resposta


@app.post("/v1/pagamentos")
def pagar(corpo: Pagamento) -> Any:
    return _json(servico.pagar(corpo.cliente_id, corpo.valor))


@app.post("/v1/atrasos")
def registrar_atraso(corpo: Atraso) -> Any:
    return _json(servico.registrar_atraso(corpo.cliente_id, corpo.dias))


@app.get("/v1/clientes/{cliente_id}/limite")
def consultar_limite(cliente_id: str) -> Any:
    return _json(servico.posicao(cliente_id))


@app.get("/v1/eventos")
def listar_eventos() -> Any:
    return _json(servico.eventos)
