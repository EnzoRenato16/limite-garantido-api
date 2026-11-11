"""Concorrência: duas compras simultâneas não podem passar do limite.

Os testes com muitas threads só fazem sentido num banco com trava de linha de verdade,
por isso exigem Postgres (`DATABASE_URL_TESTE`). O teste da trava otimista roda em
qualquer banco.
"""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.aplicacao import contas as casos
from app.aplicacao.erros import ConflitoDeConcorrencia
from app.dominio.dinheiro import Dinheiro
from tests.conftest import ADMIN, Parceiro, criar_parceiro, politica_de_teste

THREADS = 40


def _disparar_juntas(quantidade: int, tarefa):  # type: ignore[no-untyped-def]
    """Roda `tarefa(i)` em `quantidade` threads que largam ao mesmo tempo."""
    largada = threading.Barrier(quantidade)

    def corredor(indice: int):  # type: ignore[no-untyped-def]
        largada.wait(timeout=30)
        return tarefa(indice)

    with ThreadPoolExecutor(max_workers=quantidade) as executor:
        return list(executor.map(corredor, range(quantidade)))


@pytest.mark.postgres
def test_compras_simultaneas_nunca_passam_do_limite(parceiro: Parceiro) -> None:
    """40 compras de R$ 100 ao mesmo tempo num limite de R$ 1.000: passam exatamente 10."""
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "1000.00")

    respostas = _disparar_juntas(THREADS, lambda _: parceiro.comprar(conta, "100.00"))

    status = Counter(resposta.status_code for resposta in respostas)
    assert status == {201: 10, 402: 30}
    assert {r.json()["codigo"] for r in respostas if r.status_code == 402} == {
        "limite_insuficiente"
    }
    limite = parceiro.limite(conta)
    assert limite["divida_total"] == "1000.00"
    assert limite["limite_disponivel"] == "0.00"
    # No ledger: exatamente 10 lançamentos de compra, R$ 1.000 no total.
    extrato = parceiro.get(f"/v1/contas/{conta}/lancamentos", params={"limite": 100}).json()
    compras = [x for x in extrato["dados"] if x["tipo"] == "compra"]
    assert len(compras) == 10
    eventos = Counter(parceiro.tipos_de_evento(conta))
    assert eventos["compra.aprovada"] == 10
    assert eventos["compra.negada"] == 30


@pytest.mark.postgres
def test_compras_de_valores_diferentes_respeitam_o_limite(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")  # limite 4.250
    valores = [f"{37 + 13 * (i % 7)}.{i:02d}" for i in range(THREADS * 2)]

    respostas = _disparar_juntas(len(valores), lambda i: parceiro.comprar(conta, valores[i]))

    aprovado = sum(
        (Decimal(valores[i]) for i, r in enumerate(respostas) if r.status_code == 201), Decimal(0)
    )
    assert {r.status_code for r in respostas} <= {201, 402}
    assert aprovado <= Decimal("4250.00")
    assert Decimal(parceiro.limite(conta)["divida_total"]) == aprovado
    # Sobrou menos que a menor compra negada: o limite foi usado até onde dava.
    negadas = [Decimal(valores[i]) for i, r in enumerate(respostas) if r.status_code == 402]
    assert negadas
    assert Decimal("4250.00") - aprovado < max(negadas)


@pytest.mark.postgres
def test_teto_do_parceiro_vale_mesmo_com_compras_simultaneas_em_contas_diferentes(
    cliente: TestClient,
) -> None:
    parceiro = criar_parceiro(
        cliente, politica=politica_de_teste(teto_limite_do_parceiro="1000.00")
    )
    contas = [parceiro.abrir_conta() for _ in range(4)]
    for conta in contas:
        parceiro.bloquear(conta, "deposito", "1000.00")  # cada conta sozinha caberia no teto

    respostas = _disparar_juntas(
        THREADS, lambda i: parceiro.comprar(contas[i % len(contas)], "100.00")
    )

    status = Counter(resposta.status_code for resposta in respostas)
    assert status == {201: 10, 402: 30}
    total = sum(Decimal(parceiro.limite(conta)["divida_total"]) for conta in contas)
    assert total == Decimal("1000.00")


@pytest.mark.postgres
def test_mesma_chave_de_idempotencia_em_paralelo_executa_uma_vez_so(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "1000.00")
    chave = str(uuid.uuid4())
    corpo = {"valor": "100.00", "descricao": "Loja Exemplo"}

    respostas = _disparar_juntas(
        20, lambda _: parceiro.post(f"/v1/contas/{conta}/compras", corpo, chave=chave)
    )

    assert {r.status_code for r in respostas} == {201}
    assert len({r.json()["compra"]["id"] for r in respostas}) == 1
    assert sum("Idempotent-Replayed" not in r.headers for r in respostas) == 1
    assert parceiro.limite(conta)["divida_total"] == "100.00"


@pytest.mark.postgres
def test_pagamentos_e_compras_misturados_mantem_o_ledger_fechado(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "500.00")
    parceiro.comprar(conta, "400.00")

    def tarefa(indice: int):  # type: ignore[no-untyped-def]
        if indice % 2:
            return parceiro.pagar(conta, "10.00")
        return parceiro.comprar(conta, "30.00")

    respostas = _disparar_juntas(THREADS, tarefa)
    assert {r.status_code for r in respostas} <= {201, 402, 422}
    limite = parceiro.limite(conta)
    assert Decimal(limite["divida_total"]) <= Decimal("500.00")
    assert Decimal(limite["limite_disponivel"]) >= 0
    verificacao = cliente.get("/v1/admin/ledger/verificacao", headers=ADMIN).json()
    assert verificacao["fecha"] is True


def test_trava_otimista_recusa_gravacao_sobre_leitura_velha(
    parceiro: Parceiro, app: FastAPI
) -> None:
    """Duas transações leem a mesma versão da conta *sem* trava de linha e ambas aprovam
    uma compra que, sozinha, caberia. A segunda a gravar é recusada pela versão: mesmo
    se alguém esquecer o FOR UPDATE, o limite não é estourado."""
    conta_id = parceiro.abrir_conta()
    parceiro.bloquear(conta_id, "deposito", "100.00")
    recursos = app.state.recursos
    with recursos.abrir_unidade() as leitura:
        dominio = leitura.parceiros.obter(parceiro.id)
    assert dominio is not None

    with recursos.abrir_unidade() as uow_a, recursos.abrir_unidade() as uow_b:
        conta_a = uow_a.contas.obter(parceiro.id, conta_id)
        conta_b = uow_b.contas.obter(parceiro.id, conta_id)
        assert conta_a is not None
        assert conta_b is not None
        hoje, agora = uow_a.relogio.hoje(), uow_a.relogio.agora()
        for conta in (conta_a, conta_b):
            compra = conta.autorizar_compra(Dinheiro(8000), "loja", dominio.politica, agora, hoje)
            assert compra.aprovada  # cada uma, olhando o saldo velho, acha que cabe
        uow_a.contas.salvar(conta_a, dominio)
        uow_a.commit()
        with pytest.raises(ConflitoDeConcorrencia):
            uow_b.contas.salvar(conta_b, dominio)

    assert parceiro.limite(conta_id)["divida_total"] == "80.00"


def test_caminho_normal_carrega_a_conta_com_trava_de_linha(
    parceiro: Parceiro, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toda escrita passa por `obter_para_atualizar` (SELECT ... FOR UPDATE)."""
    conta_id = parceiro.abrir_conta()
    chamadas: list[str] = []
    from app.infra.banco.repositorios import RepositorioDeContasSql

    original = RepositorioDeContasSql.obter_para_atualizar

    def espiao(self: RepositorioDeContasSql, parceiro_id: str, id_da_conta: str):  # type: ignore[no-untyped-def]
        chamadas.append(id_da_conta)
        return original(self, parceiro_id, id_da_conta)

    monkeypatch.setattr(RepositorioDeContasSql, "obter_para_atualizar", espiao)
    garantia = parceiro.bloquear(conta_id, "deposito", "100.00")["garantia"]
    parceiro.comprar(conta_id, "10.00")
    parceiro.fechar_fatura(conta_id)
    parceiro.pagar(conta_id, "10.00")
    parceiro.remarcar(garantia["id"], variacao_percentual="1")
    parceiro.post(f"/v1/garantias/{garantia['id']}/liberacao")
    assert chamadas == [conta_id] * 6
    assert casos.autorizar_compra.__module__ == "app.aplicacao.contas"
