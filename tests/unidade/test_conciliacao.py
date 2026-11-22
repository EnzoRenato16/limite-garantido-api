"""Conciliação entre a dívida do ledger e o gravame registrado (só leitura)."""

from __future__ import annotations

from datetime import date, timedelta

from app.aplicacao.conciliacao import Divergencia, TipoDeDivergencia, conciliar
from app.dominio.conta import Conta
from app.dominio.emprestimo import EstadoEmprestimo
from app.dominio.garantia import Garantia
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto
from tests.apoio import AGORA, CATALOGO, HOJE, custodiar, nova_conta, politica, reais

POL = politica()


def _emprestimo_contratado(conta: Conta) -> tuple[Garantia, str]:
    garantia = custodiar(conta, TipoAtivo.CDB, "20000.00", POL, produto=Produto.EMPRESTIMO)
    emprestimo = conta.contratar_emprestimo(
        valor=reais("1000.00"),
        parcelas=6,
        garantia_ids=[garantia.id],
        politica=POL,
        catalogo=CATALOGO,
        primeiro_vencimento=HOJE + timedelta(days=30),
        agora=AGORA,
        hoje=HOJE,
        protocolos_da_previdencia={},
    )
    return garantia, emprestimo.id


def _tipos(divergencias: list[Divergencia]) -> list[str]:
    return [d.tipo.value for d in divergencias]


def test_conta_em_ordem_nao_tem_divergencia() -> None:
    conta = nova_conta()
    custodiar(conta, TipoAtivo.CDB, "5000.00", POL)
    conta.autorizar_compra(reais("1000.00"), "Loja", POL, CATALOGO, AGORA, HOJE)
    _emprestimo_contratado(nova := nova_conta())
    assert conciliar([conta, nova], HOJE) == []


def test_aponta_gravame_vigente_de_emprestimo_quitado() -> None:
    conta = nova_conta()
    garantia, emprestimo_id = _emprestimo_contratado(conta)
    conta.emprestimo(emprestimo_id).estado = EstadoEmprestimo.QUITADO  # quitado, gravame ficou
    [achado] = conciliar([conta], HOJE)
    assert achado.tipo is TipoDeDivergencia.GRAVAME_SEM_DIVIDA
    assert (achado.conta_id, achado.garantia_id, achado.emprestimo_id) == (
        conta.id,
        garantia.id,
        emprestimo_id,
    )
    # Com a baixa pedida, o gravame já não é vigente: a divergência some.
    garantia.gravame.pedir_baixa(AGORA, HOJE + timedelta(days=2))
    assert conciliar([conta], HOJE) == []


def test_aponta_divida_de_cartao_sem_gravame_vigente() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00", POL)
    conta.autorizar_compra(reais("1000.00"), "Loja", POL, CATALOGO, AGORA, HOJE)
    garantia.gravame.pedir_baixa(AGORA, HOJE + timedelta(days=2))  # gravame fora de vigência
    achados = conciliar([conta], HOJE)
    assert _tipos(achados) == ["divida_sem_gravame"]
    assert achados[0].garantia_id is None
    assert "1000.00" in achados[0].detalhe


def test_aponta_emprestimo_aberto_sem_gravame_vigente() -> None:
    conta = nova_conta()
    garantia, emprestimo_id = _emprestimo_contratado(conta)
    garantia.gravame.pedir_baixa(AGORA, HOJE + timedelta(days=2))
    [achado] = conciliar([conta], HOJE)
    assert (achado.tipo, achado.emprestimo_id) == (
        TipoDeDivergencia.DIVIDA_SEM_GRAVAME,
        emprestimo_id,
    )


def test_aponta_baixa_atrasada_so_depois_do_prazo() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "5000.00", POL)
    garantia.gravame.pedir_baixa(AGORA, date(2026, 3, 4))
    assert conciliar([conta], date(2026, 3, 4)) == []  # no prazo
    [achado] = conciliar([conta], date(2026, 3, 5))
    assert achado.tipo is TipoDeDivergencia.BAIXA_ATRASADA
    assert achado.garantia_id == garantia.id
