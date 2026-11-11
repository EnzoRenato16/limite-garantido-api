"""Fixtures da suíte.

Por padrão os testes de integração rodam em SQLite (um arquivo temporário), sem Docker.
Com `DATABASE_URL_TESTE` apontando para um Postgres, a mesma suíte roda nele, e os
testes marcados com `postgres` (concorrência, gatilhos do ledger) deixam de ser pulados.

Cada teste cria o seu próprio parceiro. Como a API é multi-tenant, isso já isola os
dados: não é preciso limpar o banco entre um teste e outro.
"""

from __future__ import annotations

import itertools
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, text

from app.api.fabrica import criar_app
from app.config import Ambiente, Configuracao
from app.infra.banco.modelos import Base
from app.infra.banco.sessao import criar_engine
from app.infra.relogio import RelogioFixo

RAIZ = Path(__file__).resolve().parent.parent
ADMIN_TOKEN = "token-administrativo-so-para-os-testes-0001"
# Segredos de callback dos custodiantes simulados, só para os testes.
SEGREDOS_DE_CUSTODIA = {
    "custodiante_agil": "segredo-de-callback-do-agil-para-testes-01",
    "custodiante_lento": "segredo-de-callback-do-lento-para-testes-1",
    "teste_confirma": "segredo-de-callback-teste-confirma-000001",
    "teste_recusa": "segredo-de-callback-teste-recusa-00000001",
    "teste_mudo": "segredo-de-callback-teste-mudo-0000000001",
}
INSTANTE_INICIAL = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("DATABASE_URL_TESTE", "").startswith("postgresql"):
        return
    pular = pytest.mark.skip(reason="precisa de Postgres: defina DATABASE_URL_TESTE")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(pular)


def rodar_migracoes(url: str) -> None:
    configuracao = Config(str(RAIZ / "alembic.ini"))
    configuracao.set_main_option("script_location", str(RAIZ / "alembic"))
    configuracao.set_main_option("sqlalchemy.url", url)
    configuracao.attributes["configurar_logs"] = False
    command.upgrade(configuracao, "head")


@pytest.fixture(scope="session")
def url_do_banco(tmp_path_factory: pytest.TempPathFactory) -> str:
    url = os.environ.get("DATABASE_URL_TESTE")
    if url:
        return url
    return f"sqlite:///{tmp_path_factory.mktemp('banco') / 'teste.db'}"


@pytest.fixture(scope="session")
def engine(url_do_banco: str) -> Iterator[Engine]:
    engine = criar_engine(url_do_banco)
    if engine.dialect.name == "postgresql":
        # Banco de teste descartável: recomeça do zero e sobe pelo Alembic, para os
        # gatilhos do ledger entrarem no teste também.
        with engine.begin() as conexao:
            conexao.execute(text("DROP SCHEMA public CASCADE"))
            conexao.execute(text("CREATE SCHEMA public"))
        rodar_migracoes(url_do_banco)
    else:
        Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def relogio() -> RelogioFixo:
    return RelogioFixo(INSTANTE_INICIAL)


def configuracao_de_teste(url_do_banco: str, **mudancas: Any) -> Configuracao:
    valores: dict[str, Any] = {
        "ambiente": Ambiente.TESTE,
        "database_url": url_do_banco,
        "admin_token": ADMIN_TOKEN,
        "sandbox_habilitado": True,
        "webhook_permitir_rede_interna": True,
        "log_nivel": "WARNING",
        "custodia_segredos": SEGREDOS_DE_CUSTODIA,
    }
    valores.update(mudancas)
    return Configuracao(_env_file=None, **valores)  # type: ignore[call-arg]


@pytest.fixture
def config(url_do_banco: str) -> Configuracao:
    return configuracao_de_teste(url_do_banco)


@pytest.fixture
def app(config: Configuracao, engine: Engine, relogio: RelogioFixo) -> FastAPI:
    return criar_app(config, engine=engine, relogio=relogio)


@pytest.fixture
def cliente(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as cliente:
        yield cliente


ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
_contador = itertools.count(1)


@dataclass
class Parceiro:
    """Um parceiro de teste e atalhos para chamar a API como ele."""

    http: TestClient
    id: str
    api_key: str

    @property
    def cabecalhos(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def get(self, caminho: str, **kwargs: Any) -> Response:
        return self.http.get(caminho, headers=self.cabecalhos, **kwargs)

    def post(
        self, caminho: str, json: Any = None, chave: str | None = "auto", **kwargs: Any
    ) -> Response:
        cabecalhos = dict(self.cabecalhos)
        if chave == "auto":
            chave = str(uuid.uuid4())
        if chave is not None:
            cabecalhos["Idempotency-Key"] = chave
        return self.http.post(caminho, json=json, headers=cabecalhos, **kwargs)

    def put(self, caminho: str, json: Any = None) -> Response:
        return self.http.put(caminho, json=json, headers=self.cabecalhos)

    # --------------------------------------------------------------- atalhos
    def abrir_conta(self, cliente_ref: str | None = None) -> str:
        resposta = self.post(
            "/v1/contas", {"cliente_ref": cliente_ref or f"cliente-{next(_contador)}"}
        )
        assert resposta.status_code == 201, resposta.text
        return str(resposta.json()["id"])

    def bloquear(self, conta_id: str, tipo_ativo: str, valor: str) -> dict[str, Any]:
        resposta = self.post(
            f"/v1/contas/{conta_id}/garantias", {"tipo_ativo": tipo_ativo, "valor": valor}
        )
        assert resposta.status_code == 201, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def comprar(self, conta_id: str, valor: str, descricao: str = "Loja Exemplo") -> Response:
        return self.post(f"/v1/contas/{conta_id}/compras", {"valor": valor, "descricao": descricao})

    def pagar(self, conta_id: str, valor: str) -> Response:
        return self.post(f"/v1/contas/{conta_id}/pagamentos", {"valor": valor})

    def fechar_fatura(self, conta_id: str, vencimento: str | None = None) -> Response:
        corpo = {"vencimento": vencimento} if vencimento else {}
        return self.post(f"/v1/contas/{conta_id}/faturas", corpo)

    def remarcar(self, garantia_id: str, **corpo: str) -> Response:
        return self.post(f"/v1/sandbox/garantias/{garantia_id}/remarcacao", corpo)

    def bloquear_externa(
        self, conta_id: str, tipo_ativo: str, valor: str, custodiante_id: str
    ) -> dict[str, Any]:
        resposta = self.post(
            f"/v1/contas/{conta_id}/garantias",
            {"tipo_ativo": tipo_ativo, "valor": valor, "custodiante_id": custodiante_id},
        )
        assert resposta.status_code == 202, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def garantia(self, garantia_id: str) -> dict[str, Any]:
        resposta = self.get(f"/v1/garantias/{garantia_id}")
        assert resposta.status_code == 200, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def avancar_dias(self, conta_id: str, dias: int) -> Response:
        return self.post(f"/v1/sandbox/contas/{conta_id}/avanco-de-dias", {"dias": dias})

    def conta(self, conta_id: str) -> dict[str, Any]:
        resposta = self.get(f"/v1/contas/{conta_id}")
        assert resposta.status_code == 200, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def limite(self, conta_id: str) -> dict[str, str]:
        limite: dict[str, str] = self.conta(conta_id)["limite"]
        return limite

    def eventos(self, conta_id: str | None = None) -> list[dict[str, Any]]:
        """Eventos em ordem cronológica (a API devolve do mais novo para o mais antigo)."""
        parametros: dict[str, Any] = {"limite": 100}
        if conta_id:
            parametros["conta_id"] = conta_id
        resposta = self.get("/v1/eventos", params=parametros)
        assert resposta.status_code == 200, resposta.text
        eventos: list[dict[str, Any]] = resposta.json()["dados"]
        return list(reversed(eventos))

    def tipos_de_evento(self, conta_id: str | None = None) -> list[str]:
        return [evento["tipo"] for evento in self.eventos(conta_id)]


def criar_parceiro(cliente: TestClient, **corpo: Any) -> Parceiro:
    corpo.setdefault("nome", f"Parceiro de Teste {next(_contador)}")
    resposta = cliente.post("/v1/admin/parceiros", json=corpo, headers=ADMIN)
    assert resposta.status_code == 201, resposta.text
    dados = resposta.json()
    return Parceiro(http=cliente, id=dados["parceiro"]["id"], api_key=dados["api_key"])


def politica_de_teste(**mudancas: Any) -> dict[str, Any]:
    """A política de referência, sem tetos, com as mudanças pedidas."""
    politica: dict[str, Any] = {
        "ltv_por_ativo": {
            "deposito": "1.00",
            "fundo_di": "0.95",
            "tesouro_selic": "0.95",
            "cdb": "0.85",
            "tesouro_prefixado": "0.80",
            "tesouro_ipca": "0.80",
            "acoes": "0.60",
        },
        "ordem_de_execucao": [
            "deposito",
            "fundo_di",
            "tesouro_selic",
            "cdb",
            "tesouro_prefixado",
            "tesouro_ipca",
            "acoes",
        ],
        "dias_para_execucao": 7,
        "prazo_chamada_de_margem_dias": 5,
    }
    politica.update(mudancas)
    return politica


@pytest.fixture
def parceiro(cliente: TestClient) -> Parceiro:
    return criar_parceiro(cliente, politica=politica_de_teste())
