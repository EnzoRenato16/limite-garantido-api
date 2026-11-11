"""Os 6 testes da versão 0.1, adaptados à interface da 1.0.

O comportamento coberto é o mesmo. O que mudou: os caminhos (recursos aninhados em
`/v1/contas/{id}`), a autenticação por parceiro, o formato de erro (RFC 7807) e a
execução, que agora vende só o necessário e mantém a própria garantia como
`parcialmente_executada` em vez de criar uma garantia nova para a sobra.
"""

from __future__ import annotations

from tests.conftest import Parceiro


def test_bloqueio_calcula_limite_pelo_ltv(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    corpo = parceiro.bloquear(conta, "cdb", "5000.00")
    assert corpo["garantia"]["estado"] == "bloqueada"
    assert corpo["limite"]["limite_total"] == "4250.00"  # 5.000 x 85%
    # Arredondamento sempre para baixo: 33,33 x 95% = 31,6635 -> 31,66
    outra = parceiro.abrir_conta()
    corpo = parceiro.bloquear(outra, "fundo_di", "33.33")
    assert corpo["limite"]["limite_total"] == "31.66"


def test_compra_aprovada_e_compra_negada_com_402(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")
    aprovada = parceiro.comprar(conta, "4000.00")
    assert aprovada.status_code == 201
    negada = parceiro.comprar(conta, "500.00")
    assert negada.status_code == 402
    assert negada.headers["content-type"] == "application/problem+json"
    assert negada.json()["codigo"] == "limite_insuficiente"
    assert parceiro.limite(conta)["limite_disponivel"] == "250.00"
    assert "compra.negada" in parceiro.tipos_de_evento(conta)


def test_remarcacao_dispara_chamada_de_margem(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    parceiro.comprar(conta, "4000.00")
    resposta = parceiro.remarcar(garantia["id"], valor_de_mercado="4500.00")
    assert resposta.status_code == 200
    assert resposta.json()["limite"]["limite_total"] == "3825.00"
    assert resposta.json()["limite"]["limite_disponivel"] == "0.00"
    assert "chamada_de_margem" in parceiro.tipos_de_evento(conta)


def test_liberacao_e_bloqueada_enquanto_a_garantia_cobre_divida(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    parceiro.comprar(conta, "1000.00")
    negada = parceiro.post(f"/v1/garantias/{garantia['id']}/liberacao")
    assert negada.status_code == 409
    assert negada.json()["codigo"] == "garantia_cobrindo_divida"
    parceiro.pagar(conta, "1000.00")
    liberada = parceiro.post(f"/v1/garantias/{garantia['id']}/liberacao")
    assert liberada.status_code == 200
    assert liberada.json()["garantia"]["estado"] == "liberada"
    assert liberada.json()["limite"]["limite_total"] == "0.00"


def test_execucao_no_setimo_dia_comeca_pelo_mais_liquido_e_mantem_a_sobra_bloqueada(
    parceiro: Parceiro,
) -> None:
    conta = parceiro.abrir_conta()
    acoes = parceiro.bloquear(conta, "acoes", "10000.00")["garantia"]
    cdb = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    parceiro.comprar(conta, "4000.00")
    assert parceiro.fechar_fatura(conta, "2026-03-02").status_code == 201

    # Até o 6º dia de atraso nada é executado.
    antes = parceiro.avancar_dias(conta, 6).json()
    assert antes["limite"]["divida_total"] == "4000.00"
    assert antes["fatura_em_aberto"]["dias_em_atraso"] == 6
    assert "garantia.executada" not in parceiro.tipos_de_evento(conta)

    depois = parceiro.avancar_dias(conta, 1).json()
    assert depois["limite"]["divida_total"] == "0.00"
    executadas = [e for e in parceiro.eventos(conta) if e["tipo"] == "garantia.executada"]
    # O CDB é mais líquido que ações: só ele é vendido, e sobram R$ 1.000 bloqueados.
    assert [e["dados"]["garantia_id"] for e in executadas] == [cdb["id"]]
    assert executadas[0]["dados"]["valor_executado"] == "4000.00"
    assert executadas[0]["dados"]["valor_remanescente"] == "1000.00"
    assert executadas[0]["dados"]["dias_em_atraso"] == 7
    assert parceiro.get(f"/v1/garantias/{cdb['id']}").json()["estado"] == "parcialmente_executada"
    assert parceiro.get(f"/v1/garantias/{acoes['id']}").json()["estado"] == "bloqueada"
    # Limite depois: ações 10.000 x 60% + sobra do CDB 1.000 x 85%.
    assert depois["limite"]["limite_total"] == "6850.00"


def test_idempotencia_nao_duplica_garantia_nem_compra(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    corpo = {"tipo_ativo": "cdb", "valor": "5000.00"}
    primeira = parceiro.post(f"/v1/contas/{conta}/garantias", corpo, chave="chave-garantia")
    segunda = parceiro.post(f"/v1/contas/{conta}/garantias", corpo, chave="chave-garantia")
    assert primeira.json()["garantia"]["id"] == segunda.json()["garantia"]["id"]
    assert segunda.headers["Idempotent-Replayed"] == "true"
    assert parceiro.limite(conta)["limite_total"] == "4250.00"

    compra = {"valor": "100.00", "descricao": "Loja Exemplo"}
    parceiro.post(f"/v1/contas/{conta}/compras", compra, chave="chave-compra")
    parceiro.post(f"/v1/contas/{conta}/compras", compra, chave="chave-compra")
    assert parceiro.limite(conta)["divida_total"] == "100.00"
