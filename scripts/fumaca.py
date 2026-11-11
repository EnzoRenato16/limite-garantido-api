"""Teste de fumaça contra um ambiente no ar (docker compose ou scripts/dev.sh).

    python scripts/fumaca.py [http://localhost:8000]

Usa só a biblioteca padrão. Percorre o caminho feliz com o parceiro de exemplo e espera
os webhooks darem a volta completa: operação, outbox, worker, receptor da demo.
Sai com código diferente de zero se algo não bater.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"
SEGUNDOS_ESPERANDO_WEBHOOKS = 30


def chamar(
    metodo: str, caminho: str, corpo: Any = None, chave: str | None = None
) -> tuple[int, Any]:
    cabecalhos = {"Content-Type": "application/json"}
    if chave:
        cabecalhos["Authorization"] = f"Bearer {chave}"
    if metodo == "POST":
        cabecalhos["Idempotency-Key"] = str(uuid.uuid4())
    dados = None if corpo is None else json.dumps(corpo).encode()
    requisicao = urllib.request.Request(  # noqa: S310 - a URL vem de quem roda o script
        f"{BASE}{caminho}", data=dados, method=metodo, headers=cabecalhos
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=10) as resposta:  # noqa: S310
            return resposta.status, json.loads(resposta.read() or b"null")
    except urllib.error.HTTPError as erro:
        return erro.code, json.loads(erro.read() or b"null")


def conferir(condicao: bool, mensagem: str) -> None:
    print(("ok   " if condicao else "FALHA") + " " + mensagem)
    if not condicao:
        raise SystemExit(1)


def main() -> None:
    status, saude = chamar("GET", "/health")
    conferir(status == 200 and saude["banco"] == "ok", "API no ar e falando com o banco")

    status, config = chamar("GET", "/demo/config")
    conferir(status == 200, "parceiro de exemplo configurado (seed rodou)")
    chave = config["api_key"]

    status, conta = chamar("POST", "/v1/contas", {"cliente_ref": f"fumaca-{uuid.uuid4()}"}, chave)
    conferir(status == 201, "conta aberta")
    conta_id = conta["id"]

    status, garantia = chamar(
        "POST", f"/v1/contas/{conta_id}/garantias", {"tipo_ativo": "cdb", "valor": "5000.00"}, chave
    )
    conferir(
        status == 201 and garantia["limite"]["limite_total"] == "4250.00",
        "R$ 5.000 em CDB viraram R$ 4.250 de limite",
    )

    status, _ = chamar(
        "POST", f"/v1/contas/{conta_id}/compras", {"valor": "4000.00", "descricao": "Fumaça"}, chave
    )
    conferir(status == 201, "compra de R$ 4.000 aprovada")
    status, negada = chamar(
        "POST", f"/v1/contas/{conta_id}/compras", {"valor": "500.00", "descricao": "Fumaça"}, chave
    )
    conferir(
        status == 402 and negada["codigo"] == "limite_insuficiente",
        "compra de R$ 500 negada com 402",
    )

    esperados = {"garantia.bloqueada", "compra.aprovada", "compra.negada"}
    recebidos: list[dict[str, Any]] = []
    limite = time.monotonic() + SEGUNDOS_ESPERANDO_WEBHOOKS
    while time.monotonic() < limite:
        _, recebidos = chamar("GET", f"/demo/webhooks?conta_id={conta_id}")
        if esperados <= {r["evento"].get("tipo") for r in recebidos}:
            break
        time.sleep(0.5)
    tipos = {r["evento"].get("tipo") for r in recebidos}
    conferir(esperados <= tipos, f"worker entregou os webhooks ({len(recebidos)} recebidos)")
    conferir(all(r["assinatura_valida"] for r in recebidos), "todas as assinaturas HMAC conferem")

    status, metricas = 0, ""
    with urllib.request.urlopen(f"{BASE}/metrics", timeout=10) as resposta:  # noqa: S310
        status, metricas = resposta.status, resposta.read().decode()
    conferir(status == 200 and "lg_eventos_total" in metricas, "/metrics expõe os eventos")
    print("\nTudo certo.")


if __name__ == "__main__":
    main()
