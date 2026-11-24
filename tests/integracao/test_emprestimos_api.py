"""Empréstimo parcelado com garantia pela API."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import ADMIN, Parceiro, criar_parceiro, politica_de_teste


def _cdb_para_emprestimo(parceiro: Parceiro, conta: str, valor: str = "20000.00") -> str:
    corpo = parceiro.bloquear(conta, "cdb", valor, produto="emprestimo")
    assert corpo["garantia"]["produto"] == "emprestimo"
    assert corpo["limite"]["limite_total"] == "0.00"  # empréstimo não gera limite de cartão
    return str(corpo["garantia"]["id"])


def _contratar(parceiro: Parceiro, conta: str, garantia: str, valor: str = "5000.00") -> Any:
    return parceiro.post(
        f"/v1/contas/{conta}/emprestimos",
        {"valor": valor, "parcelas": 6, "garantia_ids": [garantia]},
    )


def test_contrata_com_cronograma_e_marca_a_garantia(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = _cdb_para_emprestimo(parceiro, conta)
    resposta = _contratar(parceiro, conta, garantia)
    assert resposta.status_code == 201, resposta.text
    emprestimo = resposta.json()["emprestimo"]
    assert emprestimo["id"].startswith("emp_")
    assert emprestimo["principal"] == "5000.00"
    assert emprestimo["estado"] == "ativo"
    assert len(emprestimo["parcelas"]) == 6
    assert emprestimo["parcelas"][0]["vencimento"] == "2026-04-01"  # 30 dias corridos
    soma = sum(float(p["amortizacao"]) for p in emprestimo["parcelas"])
    assert abs(soma - 5000.00) < 0.005  # a última parcela fecha o saldo
    assert parceiro.garantia(garantia)["emprestimo_id"] == emprestimo["id"]
    assert "emprestimo.contratado" in parceiro.tipos_de_evento(conta)
    # A garantia que cobre empréstimo em aberto não se libera.
    liberar = parceiro.post(f"/v1/garantias/{garantia}/liberacao")
    assert liberar.status_code == 409
    assert liberar.json()["codigo"] == "garantia_cobrindo_divida"


def test_primeiro_vencimento_fora_da_janela_e_422(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = _cdb_para_emprestimo(parceiro, conta)
    for data in ("2031-03-02", "9999-12-31"):
        resposta = parceiro.post(
            f"/v1/contas/{conta}/emprestimos",
            {
                "valor": "5000.00",
                "parcelas": 6,
                "garantia_ids": [garantia],
                "primeiro_vencimento": data,
            },
        )
        assert resposta.status_code == 422, (data, resposta.text)
    # A data de hoje do sandbox é 2026-03-02: dentro da janela passa.
    resposta = parceiro.post(
        f"/v1/contas/{conta}/emprestimos",
        {
            "valor": "5000.00",
            "parcelas": 6,
            "garantia_ids": [garantia],
            "primeiro_vencimento": "2026-04-30",
        },
    )
    assert resposta.status_code == 201, resposta.text


def test_valor_acima_do_que_a_garantia_sustenta_e_recusado(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = _cdb_para_emprestimo(parceiro, conta, "1000.00")  # sustenta 850,00
    resposta = _contratar(parceiro, conta, garantia, "851.00")
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "valor_invalido"
    assert resposta.json()["valor_maximo"] == "850.00"


def test_garantia_de_cartao_nao_serve_ao_emprestimo(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    cartao = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]["id"]
    resposta = _contratar(parceiro, conta, cartao, "1000.00")
    assert resposta.status_code == 422


def test_lista_consulta_e_paga_ate_quitar(cliente: TestClient, parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = _cdb_para_emprestimo(parceiro, conta)
    emprestimo = _contratar(parceiro, conta, garantia, "1000.00").json()["emprestimo"]

    lista = parceiro.get(f"/v1/contas/{conta}/emprestimos").json()
    assert [e["id"] for e in lista["dados"]] == [emprestimo["id"]]
    consultado = parceiro.get(f"/v1/emprestimos/{emprestimo['id']}").json()
    assert consultado["saldo_devedor"] == "1000.00"
    assert parceiro.get("/v1/emprestimos/emp_nao_existe").status_code == 404

    excedente = parceiro.post(
        f"/v1/emprestimos/{emprestimo['id']}/pagamentos", {"valor": "1000.01"}
    )
    assert excedente.status_code == 422
    assert excedente.json()["codigo"] == "pagamento_excede_divida"

    pago = parceiro.post(f"/v1/emprestimos/{emprestimo['id']}/pagamentos", {"valor": "1000.00"})
    assert pago.status_code == 201, pago.text
    assert pago.json()["emprestimo"]["estado"] == "quitado"
    # Quitado: a garantia sai com a baixa pedida (2 dias úteis).
    liberada = parceiro.garantia(garantia)
    assert liberada["gravame"]["baixa_prazo"] == "2026-03-04"
    quitado = parceiro.get(f"/v1/emprestimos/{emprestimo['id']}").json()
    assert quitado["estado"] == "quitado"
    assert parceiro.tipos_de_evento(conta)[-1] != "emprestimo.contratado"

    resumo = cliente.post("/v1/admin/rotinas/baixas", headers=ADMIN).json()
    assert resumo["contas_com_erro"] == 0
    assert parceiro.garantia(garantia)["estado"] == "baixado"


def test_emprestimo_de_outro_parceiro_e_404(cliente: TestClient, parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = _cdb_para_emprestimo(parceiro, conta)
    emprestimo = _contratar(parceiro, conta, garantia, "1000.00").json()["emprestimo"]
    outro = criar_parceiro(cliente)
    assert outro.get(f"/v1/emprestimos/{emprestimo['id']}").status_code == 404
    assert (
        outro.post(
            f"/v1/contas/{conta}/emprestimos",
            {"valor": "1.00", "parcelas": 1, "garantia_ids": [garantia]},
        ).status_code
        == 404
    )


def test_teto_do_parceiro_vale_para_o_emprestimo(cliente: TestClient) -> None:
    com_teto = criar_parceiro(
        cliente, politica=politica_de_teste(teto_limite_do_parceiro="4000.00")
    )
    conta = com_teto.abrir_conta()
    garantia = _cdb_para_emprestimo(com_teto, conta)
    recusado = _contratar(com_teto, conta, garantia, "4000.01")
    assert recusado.status_code == 422
    assert recusado.json()["codigo"] == "emprestimo_teto_do_parceiro_excedido"
    assert _contratar(com_teto, conta, garantia, "4000.00").status_code == 201


def test_conciliacao_administrativa_nao_acha_divergencia_em_conta_em_ordem(
    cliente: TestClient, parceiro: Parceiro
) -> None:
    conta = parceiro.abrir_conta()
    garantia = _cdb_para_emprestimo(parceiro, conta)
    _contratar(parceiro, conta, garantia, "1000.00")
    resposta = cliente.get(
        "/v1/admin/conciliacao", params={"parceiro_id": parceiro.id}, headers=ADMIN
    )
    assert resposta.status_code == 200
    assert resposta.json() == {"divergencias": [], "total": 0}
    assert cliente.get("/v1/admin/conciliacao", headers=parceiro.cabecalhos).status_code == 401
