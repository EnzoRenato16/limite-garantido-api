"""Outbox, entrega assinada, retentativa com backoff, dead letter e reenvio."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.fabrica import criar_app
from app.config import Configuracao
from app.infra.banco.unidade import UnidadeDeTrabalhoSql
from app.infra.relogio import RelogioFixo
from app.infra.webhooks.assinatura import (
    AssinaturaInvalida,
    assinar,
    verificar_assinatura,
)
from app.infra.webhooks.entregador import Entregador, calcular_espera
from tests.conftest import Parceiro, configuracao_de_teste, criar_parceiro

URL = "https://parceiro.exemplo/webhooks"


class Destino:
    """Faz o papel do servidor do parceiro: guarda o que chega e responde o que mandarem."""

    def __init__(self) -> None:
        self.recebidas: list[httpx2.Request] = []
        self.status = 200
        self.erro: Exception | None = None

    def __call__(self, requisicao: httpx2.Request) -> httpx2.Response:
        self.recebidas.append(requisicao)
        if self.erro is not None:
            raise self.erro
        return httpx2.Response(self.status)


@pytest.fixture
def destino() -> Destino:
    return Destino()


@pytest.fixture(autouse=True)
def fila_vazia(engine: Engine) -> None:
    """O worker entrega a fila de todos os parceiros, e os testes dividem o banco.
    Tira da fila o que outros testes deixaram, para cada um contar só o que criou."""
    with engine.begin() as conexao:
        conexao.execute(
            text("UPDATE entregas_de_webhook SET estado = 'entregue' WHERE estado = 'pendente'")
        )


@pytest.fixture
def entregador(app: FastAPI, destino: Destino, relogio: RelogioFixo) -> Entregador:
    recursos = app.state.recursos
    return Entregador(
        recursos.fabrica_de_sessoes,
        recursos.config,
        relogio,
        cliente_http=httpx2.Client(transport=httpx2.MockTransport(destino)),
        metricas=recursos.metricas,
    )


def _configurar(parceiro: Parceiro, url: str = URL) -> str:
    resposta = parceiro.put("/v1/parceiro/webhook", {"url": url})
    assert resposta.status_code == 200, resposta.text
    return str(resposta.json()["segredo"])


def _entregas(parceiro: Parceiro, **filtros: str) -> list[dict[str, Any]]:
    resposta = parceiro.get("/v1/webhooks/entregas", params={"limite": 100, **filtros})
    assert resposta.status_code == 200, resposta.text
    dados: list[dict[str, Any]] = resposta.json()["dados"]
    return dados


def _esvaziar(entregador: Entregador) -> None:
    while entregador.processar_lote().total:
        pass


def test_evento_e_entrega_nascem_na_mesma_transacao_da_operacao(parceiro: Parceiro) -> None:
    _configurar(parceiro)
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")
    entregas = _entregas(parceiro)
    assert sorted(e["evento_tipo"] for e in entregas) == ["garantia.bloqueada", "limite.atualizado"]
    assert {e["estado"] for e in entregas} == {"pendente"}
    assert {e["tentativas"] for e in entregas} == {0}
    assert sorted(e["evento_id"] for e in entregas) == sorted(e["id"] for e in parceiro.eventos())


def test_se_a_operacao_e_desfeita_nao_sobra_evento_nem_entrega(
    parceiro: Parceiro, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configurar(parceiro)
    conta = parceiro.abrir_conta()

    def commit_que_falha(self: UnidadeDeTrabalhoSql) -> None:
        raise RuntimeError("queda simulada logo antes do commit")

    with monkeypatch.context() as contexto:
        contexto.setattr(UnidadeDeTrabalhoSql, "commit", commit_que_falha)
        resposta = parceiro.post(
            f"/v1/contas/{conta}/garantias", {"tipo_ativo": "cdb", "valor": "5000.00"}
        )
        assert resposta.status_code == 500
    # Nem garantia, nem lançamento, nem evento, nem webhook: tudo ou nada.
    assert parceiro.eventos() == []
    assert _entregas(parceiro) == []
    assert parceiro.limite(conta)["limite_total"] == "0.00"
    assert parceiro.get(f"/v1/contas/{conta}/lancamentos").json()["dados"] == []


def test_sem_webhook_configurado_o_evento_fica_so_no_log(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")
    assert len(parceiro.eventos()) == 2
    assert _entregas(parceiro) == []


def test_worker_entrega_com_assinatura_hmac_que_o_parceiro_consegue_conferir(
    parceiro: Parceiro, entregador: Entregador, destino: Destino, relogio: RelogioFixo
) -> None:
    segredo = _configurar(parceiro)
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")

    resultado = entregador.processar_lote()
    assert (resultado.entregues, resultado.reagendadas, resultado.mortas) == (2, 0, 0)
    assert entregador.processar_lote().total == 0  # nada é entregue duas vezes

    agora = int(relogio.agora().timestamp())
    for requisicao in destino.recebidas:
        assert str(requisicao.url) == URL
        assert requisicao.headers["content-type"] == "application/json"
        cabecalho = requisicao.headers["X-LG-Assinatura"]
        assert cabecalho.startswith(f"t={agora},v1=")
        # É exatamente a função que o parceiro roda do lado dele.
        verificar_assinatura(segredo, requisicao.content, cabecalho, agora=agora)
        corpo = json.loads(requisicao.content)
        assert requisicao.headers["X-LG-Evento-Id"] == corpo["id"]
        assert requisicao.headers["X-LG-Evento-Tipo"] == corpo["tipo"]
        assert corpo["conta_id"] == conta
        assert corpo["dados"]["conta_id"] == conta

    tipos = sorted(json.loads(r.content)["tipo"] for r in destino.recebidas)
    assert tipos == ["garantia.bloqueada", "limite.atualizado"]
    bloqueada = next(
        json.loads(r.content) for r in destino.recebidas if b"garantia.bloqueada" in r.content
    )
    assert bloqueada["dados"]["valor_de_mercado"] == "5000.00"
    assert bloqueada["dados"]["limite_gerado"] == "4250.00"

    entregas = _entregas(parceiro)
    assert {e["estado"] for e in entregas} == {"entregue"}
    assert {e["tentativas"] for e in entregas} == {1}
    assert {e["ultimo_status_http"] for e in entregas} == {200}


def test_entrega_segue_a_ordem_em_que_os_eventos_aconteceram(
    parceiro: Parceiro, entregador: Entregador, destino: Destino
) -> None:
    _configurar(parceiro)
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "100.00")
    for _ in range(5):
        parceiro.comprar(conta, "10.00")
    parceiro.comprar(conta, "999.00")
    _esvaziar(entregador)
    entregues = [json.loads(r.content)["id"] for r in destino.recebidas]
    assert entregues == [evento["id"] for evento in parceiro.eventos()]
    # Dentro de uma mesma operação a ordem também é a do domínio.
    tipos = [json.loads(r.content)["tipo"] for r in destino.recebidas]
    assert tipos[:4] == [
        "garantia.bloqueada",
        "limite.atualizado",
        "compra.aprovada",
        "limite.atualizado",
    ]
    assert tipos[-1] == "compra.negada"


def test_falha_reagenda_com_backoff_exponencial_ate_virar_dead_letter(
    url_do_banco: str, engine: Engine, relogio: RelogioFixo, destino: Destino
) -> None:
    config = configuracao_de_teste(
        url_do_banco, webhook_max_tentativas=3, webhook_backoff_base_segundos=30
    )
    app = criar_app(config, engine=engine, relogio=relogio)
    entregador = Entregador(
        app.state.recursos.fabrica_de_sessoes,
        config,
        relogio,
        cliente_http=httpx2.Client(transport=httpx2.MockTransport(destino)),
    )
    with TestClient(app) as cliente:
        parceiro = criar_parceiro(cliente)
        _configurar(parceiro)
        parceiro.abrir_conta()
        # Deixa só um evento na fila para acompanhar: uma compra negada.
        conta = parceiro.abrir_conta()
        parceiro.comprar(conta, "10.00")
        destino.status = 500

        # 1ª tentativa falha: reagenda para ~30s depois.
        assert entregador.processar_lote().reagendadas == 1
        (entrega,) = _entregas(parceiro)
        assert entrega["estado"] == "pendente"
        assert entrega["tentativas"] == 1
        assert entrega["ultimo_status_http"] == 500
        assert "HTTP 500" in entrega["ultimo_erro"]
        assert entregador.processar_lote().total == 0  # ainda não é hora

        relogio.avancar(seconds=29)
        assert entregador.processar_lote().total == 0
        relogio.avancar(seconds=5)  # 34s: passou dos 30s + até 10% de folga
        # 2ª tentativa falha: agora espera ~60s.
        assert entregador.processar_lote().reagendadas == 1
        relogio.avancar(seconds=59)
        assert entregador.processar_lote().total == 0
        relogio.avancar(seconds=8)
        # 3ª e última: vai para a dead letter e o worker para de tentar.
        assert entregador.processar_lote().mortas == 1
        (entrega,) = _entregas(parceiro)
        assert entrega["estado"] == "morta"
        assert entrega["tentativas"] == 3
        assert entrega["proxima_tentativa_em"] is None
        relogio.avancar(days=30)
        assert entregador.processar_lote().total == 0
        assert len(destino.recebidas) == 3
        assert [e["id"] for e in _entregas(parceiro, estado="morta")] == [entrega["id"]]

        # Reenvio manual: nova entrega do mesmo evento; a morta fica no histórico.
        destino.status = 204
        reenvio = parceiro.post(f"/v1/webhooks/entregas/{entrega['id']}/reenvio", chave=None)
        assert reenvio.status_code == 202
        assert reenvio.json()["evento_id"] == entrega["evento_id"]
        assert reenvio.json()["id"] != entrega["id"]
        assert entregador.processar_lote().entregues == 1
        estados = sorted(e["estado"] for e in _entregas(parceiro))
        assert estados == ["entregue", "morta"]
        ids_de_evento = {json.loads(r.content)["id"] for r in destino.recebidas}
        assert ids_de_evento == {entrega["evento_id"]}  # o receptor reconhece a repetição


def test_erro_de_rede_tambem_conta_como_tentativa(
    parceiro: Parceiro, entregador: Entregador, destino: Destino
) -> None:
    _configurar(parceiro)
    conta = parceiro.abrir_conta()
    parceiro.comprar(conta, "10.00")
    destino.erro = httpx2.ConnectTimeout("tempo esgotado")
    assert entregador.processar_lote().reagendadas == 1
    (entrega,) = _entregas(parceiro)
    assert entrega["ultimo_status_http"] is None
    assert "ConnectTimeout" in entrega["ultimo_erro"]


def test_reenvio_exige_entrega_do_proprio_parceiro(parceiro: Parceiro, cliente: TestClient) -> None:
    _configurar(parceiro)
    conta = parceiro.abrir_conta()
    parceiro.comprar(conta, "10.00")
    (entrega,) = _entregas(parceiro)
    intruso = criar_parceiro(cliente)
    assert _entregas(intruso) == []
    negado = intruso.post(f"/v1/webhooks/entregas/{entrega['id']}/reenvio", chave=None)
    assert negado.status_code == 404
    assert negado.json()["codigo"] == "entrega_nao_encontrada"


def test_rotacionar_o_segredo_vale_para_as_proximas_entregas(
    parceiro: Parceiro, entregador: Entregador, destino: Destino, relogio: RelogioFixo
) -> None:
    antigo = _configurar(parceiro)
    assert _configurar(parceiro) == antigo  # reconfigurar a URL não troca o segredo
    novo = parceiro.put("/v1/parceiro/webhook", {"url": URL, "rotacionar_segredo": True}).json()[
        "segredo"
    ]
    assert novo != antigo
    conta = parceiro.abrir_conta()
    parceiro.comprar(conta, "10.00")
    _esvaziar(entregador)
    requisicao = destino.recebidas[-1]
    agora = int(relogio.agora().timestamp())
    verificar_assinatura(
        novo, requisicao.content, requisicao.headers["X-LG-Assinatura"], agora=agora
    )
    with pytest.raises(AssinaturaInvalida):
        verificar_assinatura(
            antigo, requisicao.content, requisicao.headers["X-LG-Assinatura"], agora=agora
        )


# ------------------------------------------------------- assinatura e replay
SEGREDO = "whsec_segredo-de-teste"
CORPO = b'{"id":"evt_1","tipo":"compra.aprovada"}'


def test_assinatura_valida_passa() -> None:
    verificar_assinatura(SEGREDO, CORPO, assinar(SEGREDO, CORPO, 1_000), agora=1_000)
    verificar_assinatura(SEGREDO, CORPO, assinar(SEGREDO, CORPO, 1_000), agora=1_299)


@pytest.mark.parametrize(
    ("cabecalho", "corpo", "segredo", "agora"),
    [
        (assinar(SEGREDO, CORPO, 1_000), CORPO, SEGREDO, 1_301),  # replay: passou de 5 min
        (assinar(SEGREDO, CORPO, 2_000), CORPO, SEGREDO, 1_000),  # timestamp no futuro
        (assinar(SEGREDO, CORPO, 1_000), CORPO + b" ", SEGREDO, 1_000),  # corpo adulterado
        (assinar(SEGREDO, CORPO, 1_000), CORPO, "whsec_outro", 1_000),  # segredo errado
        (assinar(SEGREDO, CORPO, 1_000).replace("t=1000", "t=1001"), CORPO, SEGREDO, 1_001),
        ("t=1000", CORPO, SEGREDO, 1_000),
        ("v1=abc", CORPO, SEGREDO, 1_000),
        ("t=agora,v1=abc", CORPO, SEGREDO, 1_000),
        ("", CORPO, SEGREDO, 1_000),
        (None, CORPO, SEGREDO, 1_000),
    ],
)
def test_assinatura_invalida_e_recusada(
    cabecalho: str | None, corpo: bytes, segredo: str, agora: int
) -> None:
    with pytest.raises(AssinaturaInvalida):
        verificar_assinatura(segredo, corpo, cabecalho, agora=agora)


def test_trocar_o_timestamp_invalida_a_assinatura() -> None:
    """O timestamp faz parte do que é assinado: não dá para "renovar" uma entrega velha."""
    antiga = assinar(SEGREDO, CORPO, 1_000)
    renovada = antiga.replace("t=1000", "t=9000")
    with pytest.raises(AssinaturaInvalida, match="não confere"):
        verificar_assinatura(SEGREDO, CORPO, renovada, agora=9_000)


def test_backoff_dobra_a_cada_tentativa_e_respeita_o_teto() -> None:
    sem_folga = [calcular_espera(n, 30, 3600, aleatorio=lambda: 0.0) for n in range(1, 10)]
    assert [e.total_seconds() for e in sem_folga] == [30, 60, 120, 240, 480, 960, 1920, 3600, 3600]
    com_folga = calcular_espera(1, 30, 3600, aleatorio=lambda: 1.0)
    assert com_folga == timedelta(seconds=33)


# -------------------------------------------------------------------- SSRF
@pytest.mark.parametrize(
    "url",
    [
        "http://parceiro.exemplo/webhooks",  # sem TLS
        "https://127.0.0.1/webhooks",  # loopback
        "https://10.0.0.5/webhooks",  # rede privada
        "https://169.254.169.254/latest/meta-data",  # metadados de nuvem
        "https://[::1]/webhooks",
        "https://usuario:senha@8.8.8.8/webhooks",  # credencial na URL
    ],
)
def test_url_de_webhook_interna_ou_sem_tls_e_recusada(
    url: str, url_do_banco: str, engine: Engine, relogio: RelogioFixo
) -> None:
    config = configuracao_de_teste(url_do_banco, webhook_permitir_rede_interna=False)
    with TestClient(criar_app(config, engine=engine, relogio=relogio)) as cliente:
        parceiro = criar_parceiro(cliente)
        resposta = parceiro.put("/v1/parceiro/webhook", {"url": url})
        assert resposta.status_code == 422
        assert resposta.json()["codigo"] in ("url_de_webhook_invalida", "requisicao_invalida")
        # Um IP público com https passa (sem depender de DNS no teste).
        assert (
            parceiro.put("/v1/parceiro/webhook", {"url": "https://8.8.8.8/hook"}).status_code == 200
        )


def test_worker_confere_o_destino_de_novo_na_hora_de_enviar(
    config: Configuracao, parceiro: Parceiro, app: FastAPI, destino: Destino, relogio: RelogioFixo
) -> None:
    """A URL foi aceita num ambiente permissivo; um worker estrito não entrega para ela."""
    _configurar(parceiro, "http://127.0.0.1:9/interno")
    conta = parceiro.abrir_conta()
    parceiro.comprar(conta, "10.00")
    estrito = Entregador(
        app.state.recursos.fabrica_de_sessoes,
        config.model_copy(update={"webhook_permitir_rede_interna": False}),
        relogio,
        cliente_http=httpx2.Client(transport=httpx2.MockTransport(destino)),
    )
    assert estrito.processar_lote().reagendadas == 1
    assert destino.recebidas == []  # nenhuma requisição saiu
    (entrega,) = _entregas(parceiro)
    assert "https" in entrega["ultimo_erro"]
