"""Marcação a mercado, chamada de margem com prazo, tetos e simulador de stress."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.fabrica import criar_app
from app.config import Configuracao
from app.dominio.politica import TipoAtivo
from app.infra.precos import ProvedorDePrecosFake
from app.infra.relogio import RelogioFixo
from tests.conftest import ADMIN, Parceiro, criar_parceiro, politica_de_teste


class PrecosDeTeste:
    """Provedor de preços controlado pelo teste. O que não for informado vale 100."""

    def __init__(self) -> None:
        self.tabela: dict[tuple[TipoAtivo, date], Decimal] = {}

    def definir(self, tipo: str, dia: str, preco: str) -> None:
        self.tabela[(TipoAtivo(tipo), date.fromisoformat(dia))] = Decimal(preco)

    def preco(self, tipo_ativo: TipoAtivo, dia: date) -> Decimal:
        return self.tabela.get((tipo_ativo, dia), Decimal(100))


@pytest.fixture
def precos() -> PrecosDeTeste:
    return PrecosDeTeste()


@pytest.fixture
def cliente(
    config: Configuracao, engine: Engine, relogio: RelogioFixo, precos: PrecosDeTeste
) -> Iterator[TestClient]:
    app = criar_app(config, engine=engine, relogio=relogio, provedor_de_precos=precos)
    with TestClient(app) as cliente:
        yield cliente


def _rodar(cliente: TestClient, rotina: str, parceiro: Parceiro) -> dict[str, int]:
    resposta = cliente.post(
        f"/v1/admin/rotinas/{rotina}", params={"parceiro_id": parceiro.id}, headers=ADMIN
    )
    assert resposta.status_code == 200, resposta.text
    resumo: dict[str, int] = resposta.json()
    return resumo


# ------------------------------------------------------- marcação a mercado
def test_rotina_marca_a_mercado_pelo_provedor_e_abre_chamada_de_margem(
    parceiro: Parceiro, cliente: TestClient, relogio: RelogioFixo, precos: PrecosDeTeste
) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    parceiro.comprar(conta, "4000.00")
    assert garantia["marcada_em"] == "2026-03-02"

    # No mesmo dia do bloqueio não há o que marcar.
    assert _rodar(cliente, "marcacao-a-mercado", parceiro)["contas_processadas"] == 0

    relogio.avancar(days=1)
    precos.definir("cdb", "2026-03-03", "90")  # o CDB caiu 10% de um dia para o outro
    resumo = _rodar(cliente, "marcacao-a-mercado", parceiro)
    assert resumo == {"contas_processadas": 1, "contas_com_erro": 0, "eventos_gerados": 2}

    marcada = parceiro.get(f"/v1/garantias/{garantia['id']}").json()
    assert marcada["valor_de_mercado"] == "4500.00"
    assert marcada["valor_aplicado"] == "5000.00"  # o valor aplicado é histórico
    assert marcada["marcada_em"] == "2026-03-03"
    estado = parceiro.conta(conta)
    assert estado["limite"]["limite_total"] == "3825.00"
    assert estado["limite"]["deficit"] == "175.00"
    assert estado["chamada_de_margem"]["prazo_ate"] == "2026-03-08"  # 5 dias de prazo
    assert parceiro.tipos_de_evento(conta)[-2:] == ["chamada_de_margem", "limite.atualizado"]

    # Rodar de novo no mesmo dia não remarca nem repete evento.
    assert _rodar(cliente, "marcacao-a-mercado", parceiro)["contas_processadas"] == 0


def test_marcacao_acumula_a_variacao_e_arredonda_a_garantia_para_baixo(
    parceiro: Parceiro, cliente: TestClient, relogio: RelogioFixo, precos: PrecosDeTeste
) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "acoes", "33.33")["garantia"]
    relogio.avancar(days=3)  # ficou três dias sem rotina
    precos.definir("acoes", "2026-03-05", "100.5")
    _rodar(cliente, "marcacao-a-mercado", parceiro)
    # 33,33 x 1,005 = 33,49665 -> 33,49 (na dúvida, a garantia vale menos)
    assert parceiro.get(f"/v1/garantias/{garantia['id']}").json()["valor_de_mercado"] == "33.49"

    relogio.avancar(days=1)
    precos.definir("acoes", "2026-03-06", "201")  # dobrou em relação à véspera
    _rodar(cliente, "marcacao-a-mercado", parceiro)
    assert parceiro.get(f"/v1/garantias/{garantia['id']}").json()["valor_de_mercado"] == "66.98"


def test_marcacao_ignora_garantia_liberada(
    parceiro: Parceiro, cliente: TestClient, relogio: RelogioFixo, precos: PrecosDeTeste
) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "1000.00")["garantia"]
    parceiro.post(f"/v1/garantias/{garantia['id']}/liberacao")
    relogio.avancar(days=1)
    precos.definir("cdb", "2026-03-03", "50")
    assert _rodar(cliente, "marcacao-a-mercado", parceiro)["contas_processadas"] == 0


def test_provedor_fake_e_deterministico_e_o_deposito_nao_oscila() -> None:
    a, b = ProvedorDePrecosFake(), ProvedorDePrecosFake()
    dia = date(2026, 11, 11)
    for tipo in TipoAtivo:
        assert a.preco(tipo, dia) == b.preco(tipo, dia)
        assert a.preco(tipo, dia) > 0
    assert a.preco(TipoAtivo.DEPOSITO, dia) == a.preco(TipoAtivo.DEPOSITO, date(2020, 1, 1))
    assert a.preco(TipoAtivo.ACOES, dia) != a.preco(TipoAtivo.ACOES, date(2026, 11, 12))
    assert ProvedorDePrecosFake("outra-semente").preco(TipoAtivo.ACOES, dia) != a.preco(
        TipoAtivo.ACOES, dia
    )


# ------------------------------------------------- chamada de margem e prazo
def test_chamada_de_margem_nao_atendida_no_prazo_bloqueia_a_conta(
    parceiro: Parceiro, cliente: TestClient, relogio: RelogioFixo
) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    parceiro.comprar(conta, "4000.00")
    parceiro.remarcar(garantia["id"], variacao_percentual="-10")
    chamada = parceiro.conta(conta)["chamada_de_margem"]
    assert chamada["estado"] == "aberta"
    assert chamada["deficit_inicial"] == "175.00"
    assert chamada["prazo_ate"] == "2026-03-07"

    relogio.avancar(days=5)  # último dia do prazo: ainda pode regularizar
    assert _rodar(cliente, "virada-do-dia", parceiro)["contas_processadas"] == 0
    assert parceiro.conta(conta)["estado"] == "ativa"

    relogio.avancar(days=1)
    assert _rodar(cliente, "virada-do-dia", parceiro)["contas_processadas"] == 1
    bloqueada = parceiro.conta(conta)
    assert bloqueada["estado"] == "bloqueada"
    assert bloqueada["motivo_bloqueio"] == "chamada_de_margem_vencida"
    assert bloqueada["chamada_de_margem"] is None
    assert bloqueada["limite"]["limite_disponivel"] == "0.00"
    vencida = [e for e in parceiro.eventos(conta) if e["tipo"] == "chamada_de_margem"][-1]
    assert vencida["dados"]["situacao"] == "vencida"
    assert vencida["dados"]["consequencia"] == "conta_bloqueada_para_novas_compras"

    # Pagar parte da dívida não basta se o déficit continua.
    parceiro.pagar(conta, "100.00")
    negada = parceiro.comprar(conta, "1.00")
    assert negada.status_code == 402
    assert negada.json()["codigo"] == "conta_bloqueada"

    # Reforçou a garantia: déficit zerado, conta destravada.
    reforco = parceiro.bloquear(conta, "deposito", "75.00")
    assert reforco["limite"]["deficit"] == "0.00"
    assert parceiro.conta(conta)["estado"] == "ativa"
    assert parceiro.comprar(conta, "0.01").status_code == 402  # sem folga, mas não bloqueada
    assert parceiro.comprar(conta, "0.01").json()["codigo"] == "limite_insuficiente"


def test_chamada_atendida_dentro_do_prazo_nao_bloqueia(
    parceiro: Parceiro, cliente: TestClient, relogio: RelogioFixo
) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    parceiro.comprar(conta, "4000.00")
    parceiro.remarcar(garantia["id"], variacao_percentual="-10")
    relogio.avancar(days=2)
    parceiro.pagar(conta, "175.00")
    assert parceiro.conta(conta)["chamada_de_margem"] is None
    situacoes = [
        e["dados"]["situacao"] for e in parceiro.eventos(conta) if e["tipo"] == "chamada_de_margem"
    ]
    assert situacoes == ["aberta", "atendida"]
    relogio.avancar(days=10)
    _rodar(cliente, "virada-do-dia", parceiro)
    assert parceiro.conta(conta)["estado"] == "ativa"


def test_rotina_diaria_marca_atraso_e_executa_no_setimo_dia(
    parceiro: Parceiro, cliente: TestClient, relogio: RelogioFixo
) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "5000.00")
    parceiro.comprar(conta, "4000.00")
    parceiro.fechar_fatura(conta, "2026-03-05")

    relogio.avancar(days=3)  # dia do vencimento: nada acontece
    assert _rodar(cliente, "virada-do-dia", parceiro)["contas_processadas"] == 0

    relogio.avancar(days=1)  # 1º dia de atraso
    assert _rodar(cliente, "virada-do-dia", parceiro)["eventos_gerados"] == 1
    assert parceiro.conta(conta)["fatura_em_aberto"]["estado"] == "em_atraso"

    relogio.avancar(days=5)  # 6º dia: ainda não executa
    assert _rodar(cliente, "virada-do-dia", parceiro)["eventos_gerados"] == 0
    assert parceiro.limite(conta)["divida_total"] == "4000.00"

    relogio.avancar(days=1)  # 7º dia
    resumo = _rodar(cliente, "virada-do-dia", parceiro)
    assert resumo == {"contas_processadas": 1, "contas_com_erro": 0, "eventos_gerados": 2}
    depois = parceiro.conta(conta)
    assert depois["limite"]["divida_total"] == "0.00"
    assert depois["limite"]["valor_das_garantias"] == "1000.00"
    assert depois["fatura_em_aberto"] is None
    assert depois["estado"] == "ativa"
    (fatura,) = parceiro.get(f"/v1/contas/{conta}/faturas").json()["dados"]
    assert fatura["estado"] == "liquidada_por_execucao"
    assert parceiro.tipos_de_evento(conta)[-3:] == [
        "fatura.em_atraso",
        "garantia.executada",
        "limite.atualizado",
    ]
    # No dia seguinte não há mais nada para a rotina fazer nesta conta.
    relogio.avancar(days=1)
    assert _rodar(cliente, "virada-do-dia", parceiro)["contas_processadas"] == 0


def test_sandbox_recusa_avanco_sem_nada_pendente_e_remarcacao_ambigua(parceiro: Parceiro) -> None:
    conta = parceiro.abrir_conta()
    garantia = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    sem_fatura = parceiro.avancar_dias(conta, 1)
    assert sem_fatura.status_code == 409
    assert sem_fatura.json()["codigo"] == "sem_fatura_em_aberto"
    for corpo in ({}, {"variacao_percentual": "-10", "valor_de_mercado": "1.00"}):
        resposta = parceiro.post(f"/v1/sandbox/garantias/{garantia['id']}/remarcacao", corpo)
        assert resposta.status_code == 422
    assert parceiro.avancar_dias(conta, 0).status_code == 422
    zerada = parceiro.remarcar(garantia["id"], variacao_percentual="-100").json()
    assert zerada["garantia"]["valor_de_mercado"] == "0.00"
    assert zerada["limite"]["limite_total"] == "0.00"


# -------------------------------------------------------------------- tetos
def test_teto_por_cliente_limita_o_limite(cliente: TestClient) -> None:
    parceiro = criar_parceiro(
        cliente, politica=politica_de_teste(teto_limite_por_cliente="3000.00")
    )
    conta = parceiro.abrir_conta()
    limite = parceiro.bloquear(conta, "cdb", "5000.00")["limite"]
    assert limite["limite_garantido"] == "4250.00"
    assert limite["limite_total"] == "3000.00"
    assert parceiro.comprar(conta, "3000.01").status_code == 402
    assert parceiro.comprar(conta, "3000.00").status_code == 201


def test_teto_do_parceiro_vale_para_a_soma_das_contas(cliente: TestClient) -> None:
    parceiro = criar_parceiro(
        cliente, politica=politica_de_teste(teto_limite_do_parceiro="1000.00")
    )
    conta_a, conta_b = parceiro.abrir_conta(), parceiro.abrir_conta()
    parceiro.bloquear(conta_a, "deposito", "800.00")
    parceiro.bloquear(conta_b, "deposito", "800.00")
    assert parceiro.comprar(conta_a, "700.00").status_code == 201
    estourou = parceiro.comprar(conta_b, "400.00")  # 700 + 400 > 1.000
    assert estourou.status_code == 402
    assert estourou.json()["codigo"] == "teto_do_parceiro_excedido"
    assert parceiro.comprar(conta_b, "300.00").status_code == 201  # bate no teto exato
    assert parceiro.comprar(conta_a, "0.01").status_code == 402
    parceiro.pagar(conta_a, "200.00")  # abriu espaço na carteira
    assert parceiro.comprar(conta_b, "200.00").status_code == 201


# ------------------------------------------------------------------- stress
def test_simulador_de_stress_mede_chamadas_e_perda_sem_alterar_nada(parceiro: Parceiro) -> None:
    contas = {}
    for nome, tipo, garantia, compra in (
        ("acoes", "acoes", "10000.00", "5500.00"),  # limite 6.000
        ("prefixado", "tesouro_prefixado", "10000.00", "7500.00"),  # limite 8.000
        ("cdb", "cdb", "5000.00", "1000.00"),  # folgada
    ):
        contas[nome] = parceiro.abrir_conta()
        parceiro.bloquear(contas[nome], tipo, garantia)
        assert parceiro.comprar(contas[nome], compra).status_code == 201
    eventos_antes = len(parceiro.eventos())

    resposta = parceiro.post(
        "/v1/risco/simulacoes-de-stress",
        {"choques": {"acoes": "-0.30", "tesouro_prefixado": "-0.10"}, "detalhar_contas": True},
    )
    assert resposta.status_code == 200
    resultado = resposta.json()
    assert resultado["contas_analisadas"] == 3
    assert resultado["contas_em_chamada_antes"] == 0
    assert resultado["novas_chamadas_de_margem"] == 2
    assert resultado["divida_total"] == "14000.00"
    assert resultado["valor_das_garantias_antes"] == "25000.00"
    assert resultado["valor_das_garantias_depois"] == "21000.00"
    assert resultado["deficit_de_margem_total"] == "1600.00"  # 1.300 (ações) + 300 (prefixado)
    assert resultado["exposicao_descoberta_total"] == "0.00"
    assert {c["conta_id"] for c in resultado["contas"]} == {contas["acoes"], contas["prefixado"]}

    # Choque extremo: nem vendendo toda a garantia cobre.
    extremo = parceiro.post(
        "/v1/risco/simulacoes-de-stress",
        {"choques": {"acoes": "-0.60"}, "probabilidade_de_inadimplencia": "0.5"},
    ).json()
    assert extremo["exposicao_descoberta_total"] == "1500.00"  # 5.500 - 4.000
    assert extremo["perda_esperada"] == "750.00"
    assert extremo["contas"] is None

    # É simulação: limites, garantias e eventos continuam como estavam.
    assert len(parceiro.eventos()) == eventos_antes
    assert parceiro.limite(contas["acoes"])["limite_total"] == "6000.00"
    invalido = parceiro.post("/v1/risco/simulacoes-de-stress", {"choques": {"acoes": "-1.5"}})
    assert invalido.status_code == 422


def test_stress_so_enxerga_a_carteira_do_proprio_parceiro(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "acoes", "1000.00")
    outro = criar_parceiro(cliente)
    resultado = outro.post("/v1/risco/simulacoes-de-stress", {"choques": {"acoes": "-0.9"}}).json()
    assert resultado["contas_analisadas"] == 0
