"""Contas, garantias, faturas e pagamentos pela API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.fabrica import criar_app
from app.config import Configuracao
from app.infra.custodia import CustodiaAssincrona
from app.infra.relogio import RelogioFixo
from tests.conftest import ADMIN, Parceiro, criar_parceiro, politica_de_teste


def test_conta_nasce_ativa_e_sem_limite(parceiro: Parceiro) -> None:
    resposta = parceiro.post("/v1/contas", {"cliente_ref": "cliente-novo"})
    assert resposta.status_code == 201
    conta = resposta.json()
    assert conta["id"].startswith("cta_")
    assert conta["estado"] == "ativa"
    assert conta["limite"] == {
        "valor_das_garantias": "0.00",
        "limite_garantido": "0.00",
        "limite_total": "0.00",
        "divida_total": "0.00",
        "divida_faturada": "0.00",
        "limite_disponivel": "0.00",
        "deficit": "0.00",
    }
    assert conta["fatura_em_aberto"] is None
    assert conta["chamada_de_margem"] is None


def test_cliente_ref_e_unico_por_parceiro(parceiro: Parceiro, cliente: TestClient) -> None:
    parceiro.abrir_conta("cliente-repetido")
    repetida = parceiro.post("/v1/contas", {"cliente_ref": "cliente-repetido"})
    assert repetida.status_code == 409
    assert repetida.json()["codigo"] == "conta_ja_existe"
    # Outro parceiro pode usar o mesmo cliente_ref.
    outro = criar_parceiro(cliente)
    assert outro.post("/v1/contas", {"cliente_ref": "cliente-repetido"}).status_code == 201


def test_ativo_fora_da_politica_do_parceiro_e_recusado(cliente: TestClient) -> None:
    so_cdb = criar_parceiro(
        cliente,
        politica=politica_de_teste(ltv_por_ativo={"cdb": "0.85"}, ordem_de_execucao=["cdb"]),
    )
    conta = so_cdb.abrir_conta()
    resposta = so_cdb.post(
        f"/v1/contas/{conta}/garantias", {"tipo_ativo": "acoes", "valor": "1000.00"}
    )
    assert resposta.status_code == 422
    assert resposta.json()["codigo"] == "ativo_nao_aceito"
    assert resposta.json()["tipo_ativo"] == "acoes"


def test_valores_invalidos_sao_recusados_na_borda(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    for valor in ("0", "-10.00", "10.001", "abc", None):
        resposta = parceiro.post(
            f"/v1/contas/{conta}/garantias", {"tipo_ativo": "cdb", "valor": valor}
        )
        assert resposta.status_code == 422, valor
        corpo = resposta.json()
        assert corpo["codigo"] == "requisicao_invalida"
        assert corpo["erros"][0]["campo"] == "valor"
    desconhecido = parceiro.post(
        f"/v1/contas/{conta}/garantias", {"tipo_ativo": "ouro", "valor": "10.00"}
    )
    assert desconhecido.json()["erros"][0]["campo"] == "tipo_ativo"
    extra = parceiro.post(
        f"/v1/contas/{conta}/garantias", {"tipo_ativo": "cdb", "valor": "10.00", "ltv": "1.0"}
    )
    assert extra.status_code == 422  # campo que não existe não é ignorado em silêncio


def test_valor_pode_vir_como_numero_json_sem_perder_centavos(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    resposta = parceiro.post(
        f"/v1/contas/{conta}/garantias", {"tipo_ativo": "deposito", "valor": 1234.56}
    )
    assert resposta.status_code == 201
    assert resposta.json()["garantia"]["valor_de_mercado"] == "1234.56"
    assert resposta.json()["limite"]["limite_total"] == "1234.56"


def test_garantia_devolve_ltv_e_limite_gerado(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    assert garantia["id"].startswith("gar_")
    assert garantia["ltv"] == "0.85"
    assert garantia["valor_aplicado"] == "5000.00"
    assert garantia["limite_gerado"] == "4250.00"
    consultada = parceiro.get(f"/v1/garantias/{garantia['id']}").json()
    assert consultada == garantia
    assert parceiro.tipos_de_evento(conta) == ["garantia.bloqueada", "limite.atualizado"]


def test_custodia_assincrona_deixa_a_garantia_pendente_ate_a_confirmacao(
    config: Configuracao, engine: Engine, relogio: RelogioFixo
) -> None:
    app: FastAPI = criar_app(
        config, engine=engine, relogio=relogio, custodiante=CustodiaAssincrona()
    )
    with TestClient(app) as cliente:
        parceiro = criar_parceiro(cliente, politica=politica_de_teste())
        conta = parceiro.abrir_conta()
        resposta = parceiro.post(
            f"/v1/contas/{conta}/garantias", {"tipo_ativo": "tesouro_selic", "valor": "1000.00"}
        )
        # 202: pedido aceito, mas o gravame ainda não foi confirmado. Sem limite ainda.
        assert resposta.status_code == 202
        garantia = resposta.json()["garantia"]
        assert garantia["estado"] == "pendente"
        assert resposta.json()["limite"]["limite_total"] == "0.00"
        assert parceiro.comprar(conta, "10.00").status_code == 402
        assert parceiro.tipos_de_evento(conta) == ["compra.negada"]

        # Pendente não pode ser liberada nem remarcada.
        liberar = parceiro.post(f"/v1/garantias/{garantia['id']}/liberacao")
        assert liberar.status_code == 409
        assert liberar.json()["codigo"] == "transicao_de_estado_invalida"
        assert liberar.json()["estado_atual"] == "pendente"

        # Quem confirma é a custódia, pela API administrativa. O parceiro não consegue.
        caminho = f"/v1/admin/garantias/{garantia['id']}/confirmacao-de-bloqueio"
        assert cliente.post(caminho, headers=parceiro.cabecalhos).status_code == 401
        confirmada = cliente.post(caminho, headers=ADMIN)
        assert confirmada.status_code == 200
        assert confirmada.json()["garantia"]["estado"] == "bloqueada"
        assert confirmada.json()["limite"]["limite_total"] == "950.00"

        de_novo = cliente.post(caminho, headers=ADMIN)
        assert de_novo.status_code == 409
        assert de_novo.json()["codigo"] == "transicao_de_estado_invalida"
        inexistente = cliente.post(
            "/v1/admin/garantias/gar_nao_existe/confirmacao-de-bloqueio", headers=ADMIN
        )
        assert inexistente.status_code == 404


def test_transicoes_invalidas_respondem_409_com_os_estados(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "100.00")["garantia"]
    assert parceiro.post(f"/v1/garantias/{garantia['id']}/liberacao").status_code == 200
    de_novo = parceiro.post(f"/v1/garantias/{garantia['id']}/liberacao")
    assert de_novo.status_code == 409
    corpo = de_novo.json()
    assert corpo["codigo"] == "transicao_de_estado_invalida"
    assert corpo["estado_atual"] == "liberada"
    assert corpo["type"] == "/v1/erros/transicao_de_estado_invalida"
    remarcar = parceiro.remarcar(garantia["id"], variacao_percentual="-10")
    assert remarcar.status_code == 409
    assert parceiro.post("/v1/garantias/gar_inexistente/liberacao").status_code == 404


def test_ciclo_da_fatura_fechar_pagar_e_listar(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")
    sem_saldo = parceiro.fechar_fatura(conta)
    assert sem_saldo.status_code == 409
    assert sem_saldo.json()["codigo"] == "sem_saldo_para_faturar"

    parceiro.comprar(conta, "1000.00")
    passado = parceiro.fechar_fatura(conta, "2026-03-01")
    assert passado.status_code == 422
    assert passado.json()["codigo"] == "vencimento_invalido"

    fatura = parceiro.fechar_fatura(conta)
    assert fatura.status_code == 201
    assert fatura.json()["estado"] == "fechada"
    assert fatura.json()["valor_fechado"] == "1000.00"
    assert fatura.json()["vencimento"] == "2026-03-12"  # padrão: 10 dias corridos

    parceiro.comprar(conta, "200.00")  # compra nova entra no próximo ciclo
    em_aberto = parceiro.fechar_fatura(conta)
    assert em_aberto.status_code == 409
    assert em_aberto.json()["codigo"] == "fatura_em_aberto"

    demais = parceiro.pagar(conta, "1200.01")
    assert demais.status_code == 422
    assert demais.json()["codigo"] == "pagamento_excede_divida"

    parcial = parceiro.pagar(conta, "400.00").json()
    assert parcial["pagamento"]["valor_na_fatura"] == "400.00"
    assert parceiro.conta(conta)["fatura_em_aberto"]["saldo_em_aberto"] == "600.00"

    resto = parceiro.pagar(conta, "700.00").json()
    assert resto["pagamento"]["valor_na_fatura"] == "600.00"
    assert resto["pagamento"]["valor_antecipado"] == "100.00"
    assert resto["limite"]["divida_total"] == "100.00"
    assert parceiro.conta(conta)["fatura_em_aberto"] is None
    assert "fatura.paga" in parceiro.tipos_de_evento(conta)

    faturas = parceiro.get(f"/v1/contas/{conta}/faturas").json()["dados"]
    assert [(f["estado"], f["saldo_em_aberto"]) for f in faturas] == [("paga", "0.00")]


def test_fatura_em_atraso_suspende_novas_compras(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")
    parceiro.comprar(conta, "1000.00")
    parceiro.fechar_fatura(conta, "2026-03-02")
    atrasada = parceiro.avancar_dias(conta, 1).json()
    assert atrasada["fatura_em_aberto"]["estado"] == "em_atraso"
    negada = parceiro.comprar(conta, "10.00")
    assert negada.status_code == 402
    assert negada.json()["codigo"] == "fatura_em_atraso"
    evento = next(e for e in parceiro.eventos(conta) if e["tipo"] == "fatura.em_atraso")
    assert evento["dados"]["dias_em_atraso"] == 1
    assert evento["dados"]["execucao_a_partir_de"] == "2026-03-08"


def test_compras_aprovadas_e_negadas_ficam_registradas(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "deposito", "100.00")
    aprovada = parceiro.comprar(conta, "60.00", "Padaria").json()
    assert aprovada["compra"]["id"].startswith("cmp_")
    assert aprovada["limite"]["limite_disponivel"] == "40.00"
    negada = parceiro.comprar(conta, "50.00", "Posto").json()
    compras = parceiro.get(f"/v1/contas/{conta}/compras").json()["dados"]
    assert [(c["descricao"], c["estado"], c["motivo_negacao"]) for c in compras] == [
        ("Posto", "negada", "limite_insuficiente"),
        ("Padaria", "aprovada", None),
    ]
    assert negada["compra_id"] == compras[0]["id"]
    assert negada["limite_disponivel"] == "40.00"
