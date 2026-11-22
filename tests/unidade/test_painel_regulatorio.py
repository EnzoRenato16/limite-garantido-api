"""Painel regulatório: haircut, concentração do FGC e carteiras de provisão."""

from __future__ import annotations

from decimal import Decimal

from app.dominio.dinheiro import Dinheiro
from app.dominio.politica import TipoAtivo as T
from app.dominio.regulacao import CarteiraDeProvisao as C
from app.dominio.regulacao import Produto
from app.dominio.stress import (
    ContaNaCarteira,
    GarantiaNaCarteira,
    PainelRegulatorio,
    PremissasDaGarantiaExterna,
    simular_painel,
)
from app.infra.regulacao import carregar_catalogo
from tests.apoio import politica

CATALOGO = carregar_catalogo()
POL = politica(teto_cliente="50000.00")


def reais(valor: int) -> Dinheiro:
    return Dinheiro(valor * 100)


def garantia(tipo: T, valor: int, emissor: str | None = None) -> GarantiaNaCarteira:
    return GarantiaNaCarteira(tipo, reais(valor), emissor=emissor, produto=Produto.EMPRESTIMO)


def painel(*contas: ContaNaCarteira) -> PainelRegulatorio:
    return simular_painel(
        list(contas), {}, POL, Decimal("0.30"), PremissasDaGarantiaExterna(), CATALOGO
    ).regulatorio


def conta(nome: str, divida: int, *garantias: GarantiaNaCarteira) -> ContaNaCarteira:
    return ContaNaCarteira(nome, reais(divida), garantias)


def test_concentracao_soma_cdb_lci_e_lca_por_cliente_e_conglomerado() -> None:
    p = painel(
        conta(
            "a",
            1000,
            garantia(T.CDB, 150_000, "conglomerado_a"),
            garantia(T.LCI, 70_000, "conglomerado_a"),
            garantia(T.LCA, 40_000, "conglomerado_a"),  # soma 260.000: passa 10.000
            garantia(T.CDB, 200_000, "conglomerado_b"),  # outro conglomerado: dentro do teto
            garantia(T.ACOES, 500_000),  # fora do FGC
        ),
        conta("b", 1000, garantia(T.CDB, 250_000, "conglomerado_a")),  # exatamente o teto
        conta("c", 1000, garantia(T.CDB, 100_000, "conglomerado_a")),
    )
    por_nome = {c.conglomerado: c for c in p.concentracao_por_conglomerado}
    a = por_nome["conglomerado_a"]
    assert (a.clientes, a.clientes_acima_do_teto) == (3, 1)
    assert a.valor == reais(260_000 + 250_000 + 100_000)
    assert a.excesso_total == reais(10_000)
    b = por_nome["conglomerado_b"]
    assert (b.clientes_acima_do_teto, b.excesso_total) == (0, Dinheiro.zero())
    assert p.teto_do_fgc == reais(250_000)


def test_provisao_cai_na_carteira_de_menor_provisao_entre_as_garantias() -> None:
    p = painel(
        # CDB (C2) com LCI (C3): vale a C2, a de menor provisão (art. 81, §1º).
        conta("a", 10_000, garantia(T.CDB, 20_000, "conglomerado_a"), garantia(T.LCI, 9_000, "x")),
        # Só LCI (C3).
        conta("b", 4_000, garantia(T.LCI, 9_000, "conglomerado_a")),
    )
    por_carteira = {x.carteira: x for x in p.carteiras_de_provisao}
    assert por_carteira[C.C2].contas == 1
    assert por_carteira[C.C3].contas == 1


def test_conta_sem_garantia_cai_na_c5_e_os_pisos_do_primeiro_e_do_13o_mes() -> None:
    p = painel(
        conta("sem", 1_000),
        conta("c2", 1_000, garantia(T.CDB, 2_000, "conglomerado_a")),
        conta("c3", 1_000, garantia(T.PREVIDENCIA, 2_000)),
    )
    por_carteira = {x.carteira: x for x in p.carteiras_de_provisao}
    assert set(por_carteira) == {C.C2, C.C3, C.C5}
    assert por_carteira[C.C5].provisao_primeiro_mes == reais(500)  # 50%
    assert por_carteira[C.C5].provisao_decimo_terceiro_mes == reais(908)  # 90,8%
    assert por_carteira[C.C2].provisao_primeiro_mes == reais(300)  # 30%
    assert por_carteira[C.C2].provisao_decimo_terceiro_mes == reais(708)  # 70,8%
    assert por_carteira[C.C3].provisao_primeiro_mes == reais(450)  # 45%
    assert por_carteira[C.C3].provisao_decimo_terceiro_mes == reais(894)  # 89,4%


def test_haircut_por_classe_e_prazo_e_o_complemento_do_ltv_da_faixa() -> None:
    p = painel(conta("a", 0))
    faixas = [h for h in p.haircut_por_classe_e_prazo if h.tipo_ativo is T.TESOURO_PREFIXADO]
    assert len(faixas) > 1  # o prazo muda o haircut
    assert [h.ate_anos for h in faixas] == sorted(
        (h.ate_anos for h in faixas), key=lambda a: float("inf") if a is None else a
    )
    assert all(0 < h.haircut_minimo <= h.haircut_maximo < 1 for h in faixas)
    assert faixas[0].haircut_maximo < faixas[-1].haircut_maximo  # prazo longo desconta mais
    assert T.COE not in {h.tipo_ativo for h in p.haircut_por_classe_e_prazo}
