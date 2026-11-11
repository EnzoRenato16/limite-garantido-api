from decimal import Decimal

import pytest

from app.dominio.erros import AtivoNaoAceito, PoliticaInvalida
from app.dominio.politica import PoliticaRisco, TipoAtivo
from tests.apoio import LTV_PADRAO, ORDEM_PADRAO, politica, reais


def test_limite_da_garantia_e_valor_vezes_ltv_para_baixo() -> None:
    pol = politica()
    assert pol.limite_da_garantia(TipoAtivo.CDB, reais("5000.00")) == reais("4250.00")
    assert pol.limite_da_garantia(TipoAtivo.FUNDO_DI, reais("33.33")) == reais("31.66")
    assert pol.limite_da_garantia(TipoAtivo.DEPOSITO, reais("1.00")) == reais("1.00")


def test_ativo_fora_da_politica_nao_e_aceito() -> None:
    so_cdb = politica(ltv={TipoAtivo.CDB: Decimal("0.85")})
    assert so_cdb.aceita(TipoAtivo.CDB)
    assert not so_cdb.aceita(TipoAtivo.ACOES)
    with pytest.raises(AtivoNaoAceito) as erro:
        so_cdb.ltv(TipoAtivo.ACOES)
    assert erro.value.codigo == "ativo_nao_aceito"


@pytest.mark.parametrize("ltv", ["0", "-0.1", "1.01", "NaN"])
def test_ltv_fora_do_intervalo_e_recusado(ltv: str) -> None:
    with pytest.raises(PoliticaInvalida):
        politica(ltv={TipoAtivo.CDB: Decimal(ltv)})


def test_politica_precisa_de_ativos_prazos_e_tetos_validos() -> None:
    with pytest.raises(PoliticaInvalida):
        politica(ltv={})
    with pytest.raises(PoliticaInvalida):
        politica(dias_para_execucao=0)
    with pytest.raises(PoliticaInvalida):
        politica(prazo_margem=0)
    with pytest.raises(PoliticaInvalida):
        politica(teto_cliente="0")


def test_todo_ativo_aceito_precisa_estar_na_ordem_de_execucao() -> None:
    with pytest.raises(PoliticaInvalida):
        PoliticaRisco(
            ltv_por_ativo=LTV_PADRAO,
            ordem_de_execucao=(TipoAtivo.CDB,),
            dias_para_execucao=7,
            prazo_chamada_de_margem_dias=5,
        )
    with pytest.raises(PoliticaInvalida):
        PoliticaRisco(
            ltv_por_ativo={TipoAtivo.CDB: Decimal("0.85")},
            ordem_de_execucao=(TipoAtivo.CDB, TipoAtivo.CDB),
            dias_para_execucao=7,
            prazo_chamada_de_margem_dias=5,
        )


def test_prioridade_de_execucao_segue_a_ordem_configurada() -> None:
    pol = politica()
    assert pol.prioridade_de_execucao(TipoAtivo.DEPOSITO) == 0
    assert pol.prioridade_de_execucao(TipoAtivo.CDB) < pol.prioridade_de_execucao(TipoAtivo.ACOES)
    so_cdb = PoliticaRisco(
        ltv_por_ativo={TipoAtivo.CDB: Decimal("0.85")},
        ordem_de_execucao=(TipoAtivo.CDB,),
        dias_para_execucao=7,
        prazo_chamada_de_margem_dias=5,
    )
    assert so_cdb.prioridade_de_execucao(TipoAtivo.ACOES) == 1


def test_ida_e_volta_por_dicionario() -> None:
    original = politica(teto_cliente="50000.00", teto_parceiro="1000000.00")
    assert PoliticaRisco.de_dict(original.para_dict()) == original
    assert original.para_dict()["ltv_por_ativo"]["cdb"] == "0.85"
    assert PoliticaRisco.de_dict(politica().para_dict()).teto_limite_por_cliente is None


@pytest.mark.parametrize(
    "estrago",
    [
        {"ltv_por_ativo": {"ouro": "0.5"}},
        {"ltv_por_ativo": {"cdb": "abc"}},
        {"dias_para_execucao": "sete"},
        {"teto_limite_por_cliente": 10.5},
        {"teto_limite_por_cliente": "10.001"},
    ],
)
def test_dicionario_malformado_vira_politica_invalida(estrago: dict[str, object]) -> None:
    dados = {**politica().para_dict(), **estrago}
    with pytest.raises(PoliticaInvalida):
        PoliticaRisco.de_dict(dados)
    with pytest.raises(PoliticaInvalida):
        PoliticaRisco.de_dict({})


def test_ordem_padrao_dos_testes_cobre_todos_os_ativos() -> None:
    assert set(ORDEM_PADRAO) == set(TipoAtivo)
