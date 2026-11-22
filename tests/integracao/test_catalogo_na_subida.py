"""O catálogo regulatório é validado na subida, e os códigos dele estão no catálogo de erros."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api import fabrica
from app.api.erros import CATALOGO
from app.api.fabrica import criar_app
from app.config import Configuracao
from app.infra import regulacao
from app.infra.relogio import RelogioFixo

CODIGOS_DO_CATALOGO = {
    "previdencia_so_no_emprestimo",
    "ativo_so_no_emprestimo",
    "cartao_exige_liquidez_diaria",
    "fundo_no_cartao_so_ate_d1",
    "tesouro_no_cartao_exige_agente_do_td",
    "trilho_nao_atende_o_ativo",
    "ativo_ja_em_garantia",
    "coe_nao_recomendado",
    "ativo_nao_recomendado",
    "plano_fora_da_res_conjunta_12",
}


def test_catalogo_quebrado_impede_a_api_de_subir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: Configuracao,
    engine: Engine,
    relogio: RelogioFixo,
) -> None:
    quebrado = tmp_path / "regulacao.toml"
    quebrado.write_text("ctvm_do_grupo_e_agente_do_td = false\n", encoding="utf-8")
    monkeypatch.setattr(fabrica, "carregar_catalogo", lambda: regulacao.carregar_catalogo(quebrado))
    with pytest.raises(regulacao.ErroNoCatalogoRegulatorio):
        criar_app(config, engine=engine, relogio=relogio)


def test_codigos_de_combinacao_sao_422_e_publicados(cliente: TestClient) -> None:
    assert {codigo: CATALOGO[codigo][0] for codigo in CODIGOS_DO_CATALOGO} == dict.fromkeys(
        CODIGOS_DO_CATALOGO, 422
    )
    resposta = cliente.get("/v1/erros")
    assert resposta.status_code == 200
    assert set(resposta.text.split('"')) >= CODIGOS_DO_CATALOGO
