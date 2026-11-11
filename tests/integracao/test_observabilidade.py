"""Logs estruturados, request_id, /health e /metrics."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api.fabrica import criar_app
from app.config import Configuracao
from app.infra.logs import FormatadorJson
from app.infra.relogio import RelogioFixo
from tests.conftest import Parceiro


def test_health_responde_ok_com_o_banco_no_ar(cliente: TestClient) -> None:
    resposta = cliente.get("/health")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "banco": "ok", "versao": "1.0.0"}


def test_health_responde_503_com_o_banco_fora(config: Configuracao, relogio: RelogioFixo) -> None:
    banco_fora = create_engine(
        "postgresql+psycopg://x:x@127.0.0.1:1/x", connect_args={"connect_timeout": 1}
    )
    with TestClient(criar_app(config, engine=banco_fora, relogio=relogio)) as cliente:
        resposta = cliente.get("/health")
    assert resposta.status_code == 503
    assert resposta.json()["codigo"] == "servico_indisponivel"
    assert "127.0.0.1" not in resposta.text  # nada de detalhe de infraestrutura na resposta


def test_toda_resposta_tem_request_id(parceiro: Parceiro, cliente: TestClient) -> None:
    gerado = cliente.get("/health").headers["X-Request-Id"]
    assert gerado.startswith("req_")
    # O id do chamador é aceito se for bem-comportado...
    meu = parceiro.http.get(
        "/v1/contas", headers={**parceiro.cabecalhos, "X-Request-Id": "pedido-12345678"}
    )
    assert meu.headers["X-Request-Id"] == "pedido-12345678"
    # ...e trocado se puder causar problema em log ou cabeçalho.
    for malicioso in ("curto", "a" * 100, "com espaco e\tcontrole"):
        resposta = cliente.get("/health", headers={"X-Request-Id": malicioso})
        assert resposta.headers["X-Request-Id"].startswith("req_")


def test_metricas_usam_o_template_da_rota_e_contam_eventos_de_negocio(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "100.00")
    parceiro.comprar(conta, "10.00")
    parceiro.comprar(conta, "500.00")
    parceiro.put("/v1/parceiro/webhook", {"url": "https://parceiro.exemplo/hook"})
    parceiro.comprar(conta, "1.00")

    resposta = cliente.get("/metrics")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/plain")
    texto = resposta.text
    # O id da conta não vira label: senão cada conta criaria uma série nova.
    assert conta not in texto
    compras = 'lg_http_requisicoes_total{metodo="POST",rota="/v1/contas/{conta_id}/compras"'
    assert f'{compras},status="201"}} 2.0' in texto
    assert f'{compras},status="402"}} 1.0' in texto
    assert 'lg_eventos_total{tipo="compra.aprovada"} 2.0' in texto
    assert 'lg_eventos_total{tipo="compra.negada"} 1.0' in texto
    assert 'lg_eventos_total{tipo="garantia.bloqueada"} 1.0' in texto
    assert "lg_http_duracao_segundos_bucket" in texto
    assert 'lg_webhooks_na_fila{estado="pendente"}' in texto


def test_evento_de_operacao_desfeita_nao_entra_na_metrica(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    conta = parceiro.abrir_conta()
    # Pagamento maior que a dívida: erro de negócio, rollback, nenhum evento confirmado.
    parceiro.bloquear(conta, "deposito", "100.00")
    assert parceiro.pagar(conta, "1.00").status_code == 422
    texto = cliente.get("/metrics").text
    assert 'lg_eventos_total{tipo="garantia.bloqueada"} 1.0' in texto
    assert "fatura.paga" not in texto


def test_log_de_acesso_sai_em_json_com_request_id_e_parceiro_id(
    parceiro: Parceiro, app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    conta = parceiro.abrir_conta()
    with caplog.at_level(logging.INFO, logger="app.acesso"):
        resposta = parceiro.get(f"/v1/contas/{conta}")
        parceiro.http.get("/health")
    registros = [r for r in caplog.records if r.name == "app.acesso"]
    assert len(registros) == 1  # /health e /metrics não poluem o log de acesso
    registro = registros[0]
    # O contexto da requisição já foi encerrado; o que vale é o que foi gravado na linha.
    linha = json.loads(FormatadorJson().format(registro))
    assert linha["mensagem"] == "requisicao"
    assert linha["nivel"] == "INFO"
    assert linha["metodo"] == "GET"
    assert linha["rota"] == "/v1/contas/{conta_id}"
    assert linha["caminho"] == f"/v1/contas/{conta}"
    assert linha["status"] == 200
    assert linha["duracao_ms"] >= 0
    assert linha["request_id"] == resposta.headers["X-Request-Id"]
    assert linha["parceiro_id"] == parceiro.id
    assert parceiro.api_key not in json.dumps(linha)


def test_campos_extras_entram_na_linha_e_podem_informar_o_parceiro(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """Fora de uma requisição (seed, worker, rotinas) quem loga informa o parceiro."""
    with caplog.at_level(logging.INFO, logger="app.teste"):
        logging.getLogger("app.teste").info(
            "Rotina concluída", extra={"parceiro_id": "par_123", "contas_processadas": 7}
        )
    linha = json.loads(FormatadorJson().format(caplog.records[-1]))
    assert linha["mensagem"] == "Rotina concluída"
    assert linha["parceiro_id"] == "par_123"
    assert linha["contas_processadas"] == 7
    assert linha["request_id"] is None
    assert not [campo for campo in linha if campo.startswith("ctx_")]  # nada de campo interno


def test_respostas_da_api_nao_ficam_em_cache_e_nao_sao_farejadas(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    conta = parceiro.abrir_conta()
    for resposta in (parceiro.get(f"/v1/contas/{conta}"), cliente.get("/v1/contas")):
        assert resposta.headers["Cache-Control"] == "no-store"
        assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert cliente.get("/health").headers["X-Content-Type-Options"] == "nosniff"


def test_corpo_gigante_e_recusado_antes_de_chegar_na_aplicacao(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    gigante = {"valor": "10.00", "descricao": "x" * 70_000}
    resposta = parceiro.post(f"/v1/contas/{conta}/compras", gigante)
    assert resposta.status_code == 413
    assert resposta.headers["content-type"] == "application/problem+json"
    assert resposta.json()["codigo"] == "corpo_grande_demais"
    assert resposta.json()["request_id"] == resposta.headers["X-Request-Id"]

    # Content-Length mentiroso ou ausente não fura o teto: os bytes são contados.
    def em_pedacos():  # type: ignore[no-untyped-def]
        for _ in range(70):
            yield b"x" * 1024

    sem_tamanho = parceiro.http.post(
        f"/v1/contas/{conta}/compras",
        content=em_pedacos(),
        headers={**parceiro.cabecalhos, "Content-Type": "application/json", "Idempotency-Key": "k"},
    )
    assert sem_tamanho.status_code == 413
    assert sem_tamanho.json()["codigo"] == "corpo_grande_demais"

    # O caso perigoso: um JSON válido seguido de lixo. Se o corpo fosse só cortado, a
    # compra seria executada e o cliente receberia 413. Não pode executar.
    parceiro.bloquear(conta, "deposito", "100.00")

    def valido_e_depois_lixo():  # type: ignore[no-untyped-def]
        yield b'{"valor": "10.00", "descricao": "compra escondida"}'
        for _ in range(70):
            yield b" " * 1024

    escondida = parceiro.http.post(
        f"/v1/contas/{conta}/compras",
        content=valido_e_depois_lixo(),
        headers={
            **parceiro.cabecalhos,
            "Content-Type": "application/json",
            "Idempotency-Key": "k2",
        },
    )
    assert escondida.status_code == 413
    assert parceiro.limite(conta)["divida_total"] == "0.00"
    assert "compra.aprovada" not in parceiro.tipos_de_evento(conta)


def test_erro_de_banco_nao_leva_dado_de_cliente_para_o_log(
    parceiro: Parceiro, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    from app.infra.banco.repositorios import RepositorioDeContasSql

    segredo = "cliente-que-nao-pode-aparecer-no-log"

    def consulta_quebrada(self: RepositorioDeContasSql, parceiro_id: str, conta_id: str) -> None:
        self._sessao.execute(
            text("SELECT * FROM tabela_que_nao_existe WHERE x = :x"), {"x": segredo}
        )

    monkeypatch.setattr(RepositorioDeContasSql, "obter", consulta_quebrada)
    with caplog.at_level(logging.ERROR, logger="app.erros"):
        resposta = parceiro.get("/v1/contas/cta_qualquer")
    assert resposta.status_code == 500
    assert segredo not in resposta.text
    (registro,) = [r for r in caplog.records if r.name == "app.erros"]
    linha = FormatadorJson().format(registro)
    assert "tabela_que_nao_existe" in linha  # o comando aparece, para depurar
    assert segredo not in linha  # o valor do parâmetro, não
