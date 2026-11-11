import pytest

from app.dominio.erros import LancamentoDesbalanceado
from app.dominio.ledger import (
    ContaContabil,
    Lancamento,
    Saldos,
    TipoLancamento,
    credito,
    debito,
    ledger_fecha,
)
from tests.apoio import AGORA, reais


def _lancamento(*partidas: object) -> Lancamento:
    return Lancamento(
        id="lan_1",
        tipo=TipoLancamento.COMPRA,
        partidas=tuple(partidas),  # type: ignore[arg-type]
        referencia_id="cmp_1",
        descricao="teste",
        criado_em=AGORA,
    )


def test_lancamento_balanceado_e_aceito() -> None:
    lancamento = _lancamento(
        debito(ContaContabil.RECEBIVEIS_A_FATURAR, reais("100.00")),
        credito(ContaContabil.LIQUIDACAO_A_PAGAR, reais("100.00")),
    )
    assert lancamento.total_de_debitos == lancamento.total_de_creditos == reais("100.00")


def test_lancamento_desbalanceado_nao_existe() -> None:
    with pytest.raises(LancamentoDesbalanceado) as erro:
        _lancamento(
            debito(ContaContabil.RECEBIVEIS_A_FATURAR, reais("100.00")),
            credito(ContaContabil.LIQUIDACAO_A_PAGAR, reais("99.99")),
        )
    assert erro.value.codigo == "lancamento_desbalanceado"


def test_lancamento_precisa_de_duas_partidas_positivas() -> None:
    with pytest.raises(LancamentoDesbalanceado):
        _lancamento(debito(ContaContabil.CAIXA_RECEBIMENTOS, reais("10.00")))
    with pytest.raises(LancamentoDesbalanceado):
        _lancamento(
            debito(ContaContabil.CAIXA_RECEBIMENTOS, reais("0.00")),
            credito(ContaContabil.RECEBIVEIS_FATURADOS, reais("0.00")),
        )


def test_saldos_respeitam_a_natureza_de_cada_conta() -> None:
    saldos = Saldos()
    saldos.aplicar(
        _lancamento(
            debito(ContaContabil.RECEBIVEIS_A_FATURAR, reais("100.00")),
            credito(ContaContabil.LIQUIDACAO_A_PAGAR, reais("100.00")),
        )
    )
    # Conta devedora cresce no débito; conta credora cresce no crédito.
    assert saldos.saldo(ContaContabil.RECEBIVEIS_A_FATURAR) == reais("100.00")
    assert saldos.saldo(ContaContabil.LIQUIDACAO_A_PAGAR) == reais("100.00")
    assert saldos.debitos_menos_creditos() == reais("0.00")


def test_saldo_por_garantia_e_total_da_conta_contabil() -> None:
    saldos = Saldos()
    for garantia_id, valor in (("gar_a", "5000.00"), ("gar_b", "1000.00")):
        saldos.aplicar(
            _lancamento(
                debito(ContaContabil.GARANTIAS_CUSTODIADAS, reais(valor), garantia_id),
                credito(ContaContabil.GARANTIAS_A_DEVOLVER, reais(valor), garantia_id),
            )
        )
    assert saldos.saldo(ContaContabil.GARANTIAS_CUSTODIADAS, "gar_a") == reais("5000.00")
    assert saldos.saldo(ContaContabil.GARANTIAS_CUSTODIADAS, "gar_x") == reais("0.00")
    assert saldos.saldo_total(ContaContabil.GARANTIAS_CUSTODIADAS) == reais("6000.00")
    assert saldos.saldo_total(ContaContabil.GARANTIAS_A_DEVOLVER) == reais("6000.00")


def test_saldos_podem_partir_de_valores_ja_agregados() -> None:
    saldos = Saldos({(ContaContabil.RECEBIVEIS_FATURADOS, None): reais("250.00")})
    assert saldos.saldo(ContaContabil.RECEBIVEIS_FATURADOS) == reais("250.00")


def test_ledger_fecha_quando_debitos_e_creditos_batem() -> None:
    lancamento = _lancamento(
        debito(ContaContabil.CAIXA_RECEBIMENTOS, reais("30.00")),
        credito(ContaContabil.RECEBIVEIS_FATURADOS, reais("20.00")),
        credito(ContaContabil.RECEBIVEIS_A_FATURAR, reais("10.00")),
    )
    assert ledger_fecha([lancamento, lancamento])
    assert ledger_fecha([])
