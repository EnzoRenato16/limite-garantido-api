"""História completa da versão 0.1, rodando contra a API em memória.

Uso: python demo.py
"""

from fastapi.testclient import TestClient

from app.main import app

api = TestClient(app)
CLIENTE = "cliente-demo"


def passo(titulo: str) -> None:
    print(f"\n== {titulo}")


def mostrar_limite() -> None:
    limite = api.get(f"/v1/clientes/{CLIENTE}/limite").json()
    print(
        f"   limite total R$ {limite['limite_total']} | dívida R$ {limite['divida']} "
        f"| disponível R$ {limite['limite_disponivel']}"
    )


passo("1. Cliente aplica R$ 5.000 em CDB e o investimento vira garantia")
garantia = api.post(
    "/v1/garantias",
    json={"cliente_id": CLIENTE, "tipo_ativo": "cdb", "valor": "5000.00"},
    headers={"Idempotency-Key": "demo-garantia-1"},
).json()["garantia"]
mostrar_limite()

passo("2. Compra de R$ 4.000 no cartão: aprovada")
resposta = api.post("/v1/compras", json={"cliente_id": CLIENTE, "valor": "4000.00"})
print(f"   HTTP {resposta.status_code}")
mostrar_limite()

passo("3. Compra de R$ 500: negada, o limite não cobre")
resposta = api.post("/v1/compras", json={"cliente_id": CLIENTE, "valor": "500.00"})
print(f"   HTTP {resposta.status_code} -> {resposta.json()['codigo']}")

passo("4. O CDB cai 10% na marcação a mercado: chamada de margem")
api.post(f"/v1/garantias/{garantia['id']}/remarcar", json={"valor_de_mercado": "4500.00"})
mostrar_limite()

passo("5. A fatura atrasa. No 7º dia a garantia é executada")
api.post("/v1/atrasos", json={"cliente_id": CLIENTE, "dias": 7})
mostrar_limite()

passo("Eventos gerados")
for evento in api.get("/v1/eventos").json():
    print(f"   - {evento['tipo']}")
