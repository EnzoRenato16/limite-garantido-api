"""O ledger de partidas dobradas visto pela API e pelo banco."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.infra.banco.modelos import Base
from app.infra.banco.tipos import Centavos
from tests.conftest import ADMIN, Parceiro


def _historia_completa(parceiro: Parceiro) -> str:
    """Bloqueio, compras, queda do ativo, fatura, pagamento parcial, execução, liberação."""
    conta = parceiro.abrir_conta()
    cdb = parceiro.bloquear(conta, "cdb", "5000.00")["garantia"]
    acoes = parceiro.bloquear(conta, "acoes", "2000.00")["garantia"]
    parceiro.comprar(conta, "3000.00")
    parceiro.comprar(conta, "9999.00")  # negada: não mexe no ledger
    parceiro.remarcar(acoes["id"], variacao_percentual="-30")
    parceiro.remarcar(cdb["id"], variacao_percentual="2")
    parceiro.fechar_fatura(conta, "2026-03-02")
    parceiro.pagar(conta, "500.00")
    parceiro.comprar(conta, "100.00")
    parceiro.avancar_dias(conta, 7)  # executa os R$ 2.500 que faltam da fatura
    parceiro.pagar(conta, "100.00")
    parceiro.post(f"/v1/garantias/{acoes['id']}/liberacao")
    return conta


def _extrato(parceiro: Parceiro, conta: str) -> list[dict[str, Any]]:
    resposta = parceiro.get(f"/v1/contas/{conta}/lancamentos", params={"limite": 100})
    assert resposta.status_code == 200
    lancamentos: list[dict[str, Any]] = resposta.json()["dados"]
    return list(reversed(lancamentos))


def _soma(partidas: list[dict[str, Any]], sentido: str) -> Decimal:
    return sum((Decimal(p["valor"]) for p in partidas if p["sentido"] == sentido), Decimal(0))


def test_todo_lancamento_do_extrato_fecha_debitos_e_creditos(parceiro: Parceiro) -> None:
    conta = _historia_completa(parceiro)
    extrato = _extrato(parceiro, conta)
    assert [x["tipo"] for x in extrato] == [
        "bloqueio_garantia",
        "bloqueio_garantia",
        "compra",
        "ajuste_marcacao",
        "ajuste_marcacao",
        "fechamento_fatura",
        "pagamento",
        "compra",
        "execucao_garantia",
        "pagamento",
        "liberacao_garantia",
    ]
    for lancamento in extrato:
        assert lancamento["id"].startswith("lan_")
        assert len(lancamento["partidas"]) >= 2
        assert _soma(lancamento["partidas"], "D") == _soma(lancamento["partidas"], "C")
        assert all(Decimal(p["valor"]) > 0 for p in lancamento["partidas"])


def test_o_saldo_da_api_e_exatamente_a_soma_das_partidas(parceiro: Parceiro) -> None:
    conta = _historia_completa(parceiro)
    liquido: dict[str, Decimal] = {}
    for lancamento in _extrato(parceiro, conta):
        for partida in lancamento["partidas"]:
            sinal = 1 if partida["sentido"] == "D" else -1
            nome = partida["conta_contabil"]
            liquido[nome] = liquido.get(nome, Decimal(0)) + sinal * Decimal(partida["valor"])
    limite = parceiro.limite(conta)
    divida = liquido["recebiveis_a_faturar"] + liquido["recebiveis_faturados"]
    assert Decimal(limite["divida_total"]) == divida == Decimal("0.00")
    assert Decimal(limite["divida_faturada"]) == liquido["recebiveis_faturados"]
    assert Decimal(limite["valor_das_garantias"]) == liquido["garantias_custodiadas"]
    # Contas de compensação andam juntas; caixa é tudo o que entrou; e o total zera.
    assert liquido["garantias_custodiadas"] == -liquido["garantias_a_devolver"]
    assert liquido["caixa_recebimentos"] == Decimal("3100.00")  # 500 + 2.500 + 100
    assert liquido["liquidacao_a_pagar"] == Decimal("-3100.00")
    assert sum(liquido.values()) == 0


def test_execucao_gera_um_lancamento_de_quatro_partidas(parceiro: Parceiro) -> None:
    conta = _historia_completa(parceiro)
    execucao = next(x for x in _extrato(parceiro, conta) if x["tipo"] == "execucao_garantia")
    partidas = {(p["conta_contabil"], p["sentido"]): p["valor"] for p in execucao["partidas"]}
    assert partidas == {
        ("garantias_a_devolver", "D"): "2500.00",
        ("garantias_custodiadas", "C"): "2500.00",
        ("caixa_recebimentos", "D"): "2500.00",
        ("recebiveis_faturados", "C"): "2500.00",
    }


def test_verificacao_administrativa_confirma_que_o_ledger_inteiro_fecha(
    parceiro: Parceiro, cliente: TestClient
) -> None:
    _historia_completa(parceiro)
    resposta = cliente.get("/v1/admin/ledger/verificacao", headers=ADMIN)
    assert resposta.status_code == 200
    verificacao = resposta.json()
    assert verificacao["fecha"] is True
    assert verificacao["lancamentos_desbalanceados"] == 0
    assert verificacao["total_de_debitos"] == verificacao["total_de_creditos"]
    assert verificacao["lancamentos"] >= 11


def test_no_banco_inteiro_debitos_e_creditos_somam_o_mesmo(
    parceiro: Parceiro, engine: Engine
) -> None:
    _historia_completa(parceiro)
    with engine.connect() as conexao:
        debitos, creditos = conexao.execute(
            text(
                "SELECT COALESCE(SUM(CASE WHEN sentido = 'D' THEN valor END), 0), "
                "COALESCE(SUM(CASE WHEN sentido = 'C' THEN valor END), 0) FROM partidas"
            )
        ).one()
        desbalanceados = conexao.execute(
            text(
                "SELECT COUNT(*) FROM (SELECT lancamento_sequencia FROM partidas "
                "GROUP BY lancamento_sequencia "
                "HAVING SUM(CASE WHEN sentido = 'D' THEN valor ELSE -valor END) <> 0) x"
            )
        ).scalar_one()
    assert debitos == creditos
    assert debitos > 0
    assert desbalanceados == 0


def test_nenhuma_tabela_tem_coluna_de_saldo() -> None:
    """Saldo é sempre derivado do ledger. Se alguém criar um campo solto, este teste avisa."""
    proibidas = ("saldo", "divida", "limite_total", "limite_disponivel", "valor_de_mercado")
    colunas = [
        f"{tabela.name}.{coluna.name}"
        for tabela in Base.metadata.tables.values()
        for coluna in tabela.columns
    ]
    suspeitas = [c for c in colunas if any(p in c.split(".")[1] for p in proibidas)]
    assert suspeitas == []


def test_toda_coluna_de_dinheiro_e_inteiro_em_centavos() -> None:
    nomes_de_dinheiro = ("valor", "deficit")
    for tabela in Base.metadata.tables.values():
        for coluna in tabela.columns:
            if coluna.name.startswith(nomes_de_dinheiro):
                assert isinstance(coluna.type, Centavos), f"{tabela.name}.{coluna.name}"
    numericas = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.columns
        if type(c.type).__name__ in ("Numeric", "Float", "DECIMAL", "REAL")
    ]
    assert numericas == []
