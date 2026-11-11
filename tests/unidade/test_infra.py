"""Peças pequenas de infraestrutura: configuração, relógio, chaves e tipos de coluna."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.aplicacao.chaves import (
    chave_confere,
    descrever_api_key,
    gerar_api_key,
    gerar_segredo_de_webhook,
    hash_da_chave,
    prefixo_da_chave,
)
from app.config import Ambiente, Configuracao
from app.dominio.dinheiro import Dinheiro
from app.dominio.ids import novo_id
from app.infra.banco.tipos import Centavos, DataHoraUTC
from app.infra.politica_referencia import carregar_politica_de_referencia
from app.infra.relogio import RelogioDoSistema, RelogioFixo

TOKEN_FORTE = "k7Qw2ZrX9mB4tVn8yLc3pHs6dFg1jUaE5oIxW0"


def _config(**valores: Any) -> Configuracao:
    return Configuracao(_env_file=None, **valores)  # type: ignore[call-arg]


# ------------------------------------------------------------- configuração
def test_os_padroes_sao_os_seguros() -> None:
    config = _config()
    assert config.demo_habilitado is False
    assert config.sandbox_habilitado is False
    assert config.webhook_permitir_rede_interna is False
    assert config.admin_token is None  # sem token, a API administrativa não existe
    assert config.custodia_modo == "interna"


def test_producao_bem_configurada_sobe() -> None:
    config = _config(
        ambiente="producao",
        database_url="postgresql+psycopg://app@banco/lg",
        admin_token=TOKEN_FORTE,
    )
    assert config.em_producao
    assert TOKEN_FORTE not in repr(config)  # segredo não aparece em log nem em repr


@pytest.mark.parametrize(
    ("mudanca", "trecho"),
    [
        ({"demo_habilitado": True}, "DEMO_HABILITADO"),
        ({"sandbox_habilitado": True}, "SANDBOX_HABILITADO"),
        ({"webhook_permitir_rede_interna": True}, "WEBHOOK_PERMITIR_REDE_INTERNA"),
        ({"database_url": "sqlite:///./local.db"}, "DATABASE_URL"),
        ({"admin_token": "curto"}, "ADMIN_TOKEN"),
        ({"admin_token": "token-de-exemplo-troque-antes-de-usar-0000"}, "ADMIN_TOKEN"),
    ],
)
def test_producao_se_recusa_a_subir_com_configuracao_de_desenvolvimento(
    mudanca: dict[str, Any], trecho: str
) -> None:
    valores: dict[str, Any] = {
        "ambiente": "producao",
        "database_url": "postgresql+psycopg://app@banco/lg",
        "admin_token": TOKEN_FORTE,
    }
    valores.update(mudanca)
    with pytest.raises(ValidationError, match=trecho):
        _config(**valores)


def test_fora_de_producao_a_mesma_configuracao_e_aceita() -> None:
    config = _config(
        ambiente=Ambiente.DESENVOLVIMENTO, demo_habilitado=True, sandbox_habilitado=True
    )
    assert not config.em_producao


def test_politica_de_referencia_vem_do_arquivo_e_e_valida() -> None:
    politica = carregar_politica_de_referencia()
    assert str(politica.ltv_por_ativo["cdb"]) == "0.85"  # type: ignore[index]
    assert politica.dias_para_execucao == 7
    assert politica.teto_limite_por_cliente == Dinheiro.de_decimal("50000.00")


# ------------------------------------------------------------------ relógio
def test_data_de_negocio_segue_o_fuso_de_brasilia() -> None:
    # 01h30 UTC do dia 3 ainda é 22h30 do dia 2 em Brasília.
    relogio = RelogioFixo(datetime(2026, 3, 3, 1, 30, tzinfo=UTC))
    assert relogio.agora().date() == date(2026, 3, 3)
    assert relogio.hoje() == date(2026, 3, 2)
    relogio.avancar(hours=2)
    assert relogio.hoje() == date(2026, 3, 3)


def test_relogio_exige_fuso_e_o_do_sistema_devolve_utc() -> None:
    with pytest.raises(ValueError, match="fuso"):
        RelogioFixo(datetime(2026, 3, 3, 1, 30))
    agora = RelogioDoSistema().agora()
    assert agora.tzinfo is UTC
    assert isinstance(RelogioDoSistema().hoje(), date)


# ------------------------------------------------------------------- chaves
def test_api_key_tem_prefixo_localizavel_e_hash_que_confere() -> None:
    gerada = gerar_api_key()
    assert gerada.chave.startswith("lgk_")
    assert prefixo_da_chave(gerada.chave) == gerada.prefixo
    assert gerada.hash == hash_da_chave(gerada.chave)
    assert len(gerada.hash) == 64
    assert chave_confere(gerada.chave, gerada.hash)
    assert not chave_confere(gerada.chave + "x", gerada.hash)
    assert gerar_api_key().chave != gerada.chave
    assert descrever_api_key(gerada.chave) == gerada


@pytest.mark.parametrize(
    "chave", ["", "lgk_", "lgk_abc", "lgk__segredo", "abc_def_ghi", "sem-formato"]
)
def test_chave_fora_do_formato_nao_tem_prefixo(chave: str) -> None:
    assert prefixo_da_chave(chave) is None
    with pytest.raises(ValueError, match="formato"):
        descrever_api_key(chave)


def test_segredo_de_webhook_e_longo_e_unico() -> None:
    segredo = gerar_segredo_de_webhook()
    assert segredo.startswith("whsec_")
    assert len(segredo) > 40
    assert gerar_segredo_de_webhook() != segredo


def test_ids_tem_prefixo_e_ordenam_por_criacao() -> None:
    ids = [novo_id("gar") for _ in range(200)]
    assert all(i.startswith("gar_") and len(i) == 30 for i in ids)
    assert len(set(ids)) == 200
    import time

    antes = novo_id("x")
    time.sleep(0.002)
    assert antes < novo_id("x")


# ------------------------------------------------------------ tipos de coluna
def test_coluna_de_dinheiro_so_aceita_dinheiro() -> None:
    tipo = Centavos()
    assert tipo.process_bind_param(Dinheiro(425_000), None) == 425_000  # type: ignore[arg-type]
    assert tipo.process_result_value(425_000, None) == Dinheiro(425_000)  # type: ignore[arg-type]
    assert tipo.process_bind_param(None, None) is None  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tipo.process_bind_param(4250.0, None)  # type: ignore[arg-type]


def test_coluna_de_instante_exige_fuso_e_devolve_utc() -> None:
    tipo = DataHoraUTC()
    with pytest.raises(ValueError, match="fuso"):
        tipo.process_bind_param(datetime(2026, 3, 2, 12, 0), None)  # type: ignore[arg-type]
    # O SQLite devolve sem fuso: o tipo recoloca UTC.
    lido = tipo.process_result_value(datetime(2026, 3, 2, 12, 0), None)  # type: ignore[arg-type]
    assert lido == datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
