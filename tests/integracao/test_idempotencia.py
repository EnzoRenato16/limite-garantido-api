"""Idempotência persistida: repetição, conflito de payload, expiração e escopo."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.infra.relogio import RelogioFixo
from tests.conftest import Parceiro, criar_parceiro

COMPRA = {"valor": "100.00", "descricao": "Loja Exemplo"}


def _conta_com_limite(parceiro: Parceiro, valor: str = "1000.00") -> str:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", valor)
    return conta


def test_operacoes_com_dinheiro_exigem_a_chave(parceiro: Parceiro) -> None:
    conta = _conta_com_limite(parceiro)
    for caminho, corpo in (
        (f"/v1/contas/{conta}/garantias", {"tipo_ativo": "cdb", "valor": "10.00"}),
        (f"/v1/contas/{conta}/compras", COMPRA),
        (f"/v1/contas/{conta}/pagamentos", {"valor": "10.00"}),
    ):
        resposta = parceiro.post(caminho, corpo, chave=None)
        assert resposta.status_code == 400, caminho
        assert resposta.json()["codigo"] == "chave_de_idempotencia_ausente"
    assert parceiro.limite(conta)["divida_total"] == "0.00"


def test_repeticao_devolve_a_resposta_original_sem_executar_de_novo(parceiro: Parceiro) -> None:
    conta = _conta_com_limite(parceiro)
    primeira = parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k1")
    segunda = parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k1")
    assert primeira.status_code == segunda.status_code == 201
    assert primeira.json() == segunda.json()
    assert "Idempotent-Replayed" not in primeira.headers
    assert segunda.headers["Idempotent-Replayed"] == "true"
    assert parceiro.limite(conta)["divida_total"] == "100.00"
    assert parceiro.tipos_de_evento(conta).count("compra.aprovada") == 1


def test_mesma_chave_com_payload_diferente_e_conflito(parceiro: Parceiro) -> None:
    conta = _conta_com_limite(parceiro)
    parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k2")
    outro_valor = parceiro.post(
        f"/v1/contas/{conta}/compras", {**COMPRA, "valor": "999.00"}, chave="k2"
    )
    assert outro_valor.status_code == 422
    assert outro_valor.json()["codigo"] == "chave_de_idempotencia_reutilizada"
    # A mesma chave em outro caminho também é outro pedido.
    outro_caminho = parceiro.post(f"/v1/contas/{conta}/pagamentos", {"valor": "100.00"}, chave="k2")
    assert outro_caminho.status_code == 422
    assert parceiro.limite(conta)["divida_total"] == "100.00"


def test_compra_negada_tambem_e_idempotente(parceiro: Parceiro) -> None:
    conta = _conta_com_limite(parceiro, "50.00")
    primeira = parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k3")
    segunda = parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k3")
    assert primeira.status_code == segunda.status_code == 402
    assert primeira.json()["compra_id"] == segunda.json()["compra_id"]
    assert segunda.headers["content-type"] == "application/problem+json"
    assert parceiro.tipos_de_evento(conta).count("compra.negada") == 1
    # Mesmo depois de ganhar limite, a chave antiga continua devolvendo a decisão antiga.
    parceiro.bloquear(conta, "deposito", "500.00")
    assert parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k3").status_code == 402
    assert parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k3-nova").status_code == 201


def test_chave_expira_depois_do_prazo(parceiro: Parceiro, relogio: RelogioFixo) -> None:
    conta = _conta_com_limite(parceiro)
    parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k4")
    relogio.avancar(hours=23)
    ainda_vale = parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k4")
    assert ainda_vale.headers.get("Idempotent-Replayed") == "true"
    relogio.avancar(hours=2)  # passou das 24h
    nova = parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave="k4")
    assert nova.status_code == 201
    assert "Idempotent-Replayed" not in nova.headers
    assert parceiro.limite(conta)["divida_total"] == "200.00"


def test_operacao_que_falha_nao_consome_a_chave(parceiro: Parceiro) -> None:
    conta = _conta_com_limite(parceiro)
    pagamento = {"valor": "100.00"}
    sem_divida = parceiro.post(f"/v1/contas/{conta}/pagamentos", pagamento, chave="k5")
    assert sem_divida.status_code == 422
    assert sem_divida.json()["codigo"] == "pagamento_excede_divida"
    parceiro.comprar(conta, "100.00")
    # O erro desfez tudo, inclusive o registro da chave: a nova tentativa executa.
    agora_vai = parceiro.post(f"/v1/contas/{conta}/pagamentos", pagamento, chave="k5")
    assert agora_vai.status_code == 201
    assert parceiro.limite(conta)["divida_total"] == "0.00"


def test_chave_malformada_e_recusada(parceiro: Parceiro) -> None:
    conta = _conta_com_limite(parceiro)
    for chave in ("x" * 256, "com\tcontrole"):
        resposta = parceiro.post(f"/v1/contas/{conta}/compras", COMPRA, chave=chave)
        assert resposta.status_code == 400
        assert resposta.json()["codigo"] == "chave_de_idempotencia_invalida"


def test_chaves_sao_por_parceiro(parceiro: Parceiro, cliente: TestClient) -> None:
    outro = criar_parceiro(cliente)
    conta_a, conta_b = _conta_com_limite(parceiro), _conta_com_limite(outro)
    a = parceiro.post(f"/v1/contas/{conta_a}/compras", COMPRA, chave="mesma-chave")
    b = outro.post(f"/v1/contas/{conta_b}/compras", COMPRA, chave="mesma-chave")
    assert a.status_code == b.status_code == 201
    assert a.json()["compra"]["id"] != b.json()["compra"]["id"]


def test_chave_opcional_na_abertura_de_conta(parceiro: Parceiro) -> None:
    corpo = {"cliente_ref": "cliente-idempotente"}
    primeira = parceiro.post("/v1/contas", corpo, chave="k6")
    segunda = parceiro.post("/v1/contas", corpo, chave="k6")
    assert primeira.status_code == segunda.status_code == 201
    assert primeira.json()["id"] == segunda.json()["id"]
    sem_chave = parceiro.post("/v1/contas", corpo, chave=None)
    assert sem_chave.status_code == 409
