"""Migração 0003: gravame, trilhos e empréstimo, sobre dados da revisão 0002."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.infra.banco.sessao import criar_engine
from tests.conftest import RAIZ

TIPOS_DA_0002 = {
    "deposito",
    "fundo_di",
    "tesouro_selic",
    "tesouro_prefixado",
    "tesouro_ipca",
    "cdb",
    "acoes",
}
AGORA = "2026-03-02 15:00:00+00:00"
DATA = "2026-03-02"

# id, tipo, origem, estado antigo; o resultado esperado vem em ESPERADO.
GARANTIAS = [
    ("gar_m01", "deposito", "interna", "bloqueada"),
    ("gar_m02", "cdb", "interna", "parcialmente_executada"),
    ("gar_m03", "fundo_di", "externa", "gravame_pendente"),
    ("gar_m04", "tesouro_selic", "externa", "bloqueada"),
    ("gar_m05", "acoes", "interna", "liberacao_pendente"),
    ("gar_m06", "cdb", "interna", "execucao_solicitada"),
    ("gar_m07", "cdb", "interna", "pendente"),
    ("gar_m08", "cdb", "interna", "liberada"),
    ("gar_m09", "cdb", "interna", "executada"),
    ("gar_m10", "tesouro_ipca", "externa", "gravame_recusado"),
    ("gar_m11", "fundo_di", "externa", "gravame_recusado"),
    ("gar_m12", "cdb", "externa", "gravame_expirado"),
    ("gar_m13", "fundo_di", "externa", "solicitada"),
    ("gar_m14", "fundo_di", "externa", "bloqueada"),
]
# id -> (tipo, estado do gravame, trilho, baixa pedida?, motivo)
ESPERADO = {
    "gar_m01": ("cdb", "registrado", "casa", False, None),
    "gar_m02": ("cdb", "registrado", "casa", False, None),
    "gar_m03": ("fundo_di", "cancelado", "portabilidade_cvm_210", False, "trilho_substituido"),
    "gar_m04": ("tesouro_selic", "registrado", "td_garantia", False, None),
    "gar_m05": ("acoes", "registrado", "casa", True, None),
    "gar_m06": ("cdb", "liberado_para_excussao", "casa", False, None),
    "gar_m07": ("cdb", "solicitado", "casa", False, None),
    "gar_m08": ("cdb", "baixado", "casa", True, None),
    "gar_m09": ("cdb", "baixado", "casa", True, None),
    "gar_m10": ("tesouro_ipca", "recusado", "td_garantia", False, None),
    "gar_m11": ("fundo_di", "cancelado", "portabilidade_cvm_210", False, None),
    "gar_m12": ("cdb", "cancelado", "transferencia_de_custodia", False, None),
    "gar_m13": ("fundo_di", "cancelado", "portabilidade_cvm_210", False, "trilho_substituido"),
    "gar_m14": ("fundo_di", "registrado", "portabilidade_cvm_210", False, None),
}


def _configuracao(url: str) -> Config:
    configuracao = Config(str(RAIZ / "alembic.ini"))
    configuracao.set_main_option("script_location", str(RAIZ / "alembic"))
    configuracao.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    configuracao.attributes["configurar_logs"] = False
    return configuracao


@pytest.fixture(params=["sqlite", "postgres"])
def url_vazia(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'migracao0003.db'}"
        return
    base = os.environ.get("DATABASE_URL_TESTE", "")
    if not base.startswith("postgresql"):
        pytest.skip("precisa de Postgres: defina DATABASE_URL_TESTE")
    # Esquema próprio, para não mexer no da suíte (o usuário do teste não cria bancos).
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conexao:
        conexao.execute(text("DROP SCHEMA IF EXISTS mig0003 CASCADE"))
        conexao.execute(text("CREATE SCHEMA mig0003"))
    alvo = make_url(base).update_query_dict({"options": "-csearch_path=mig0003"})
    yield alvo.render_as_string(hide_password=False)
    with admin.connect() as conexao:
        conexao.execute(text("DROP SCHEMA IF EXISTS mig0003 CASCADE"))
    admin.dispose()


def _popular_revisao_0002(engine: Engine) -> None:
    politica = json.dumps(
        {
            "ltv_por_ativo": {"deposito": "0.95", "acoes": "0.50"},
            "ordem_de_execucao": ["deposito", "acoes"],
            "dias_para_execucao": 7,
            "prazo_chamada_de_margem_dias": 5,
        }
    )
    cast = "CAST(:politica AS jsonb)" if engine.dialect.name == "postgresql" else ":politica"
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO parceiros (id, nome, chave_prefixo, chave_hash, politica, "  # noqa: S608
                "limite_de_requisicoes_por_minuto, ativo, criado_em) VALUES "
                f"('par_m', 'P', 'pre', 'hash', {cast}, 60, true, :agora)"
            ),
            {"politica": politica, "agora": AGORA},
        )
        c.execute(
            text(
                "INSERT INTO contas (id, parceiro_id, cliente_ref, estado, versao, criada_em) "
                "VALUES ('cta_m', 'par_m', 'c1', 'ativa', 0, :agora)"
            ),
            {"agora": AGORA},
        )
        for identificador, tipo, origem, estado in GARANTIAS:
            c.execute(
                text(
                    "INSERT INTO garantias (id, conta_id, parceiro_id, tipo_ativo, "
                    "valor_aplicado, estado, marcada_em, criada_em, origem, custodiante_id, "
                    "solicitacao_id) VALUES (:id, 'cta_m', 'par_m', :tipo, 100000, :estado, "
                    ":data, :agora, :origem, :cust, :sol)"
                ),
                {
                    "id": identificador,
                    "tipo": tipo,
                    "estado": estado,
                    "data": DATA,
                    "agora": AGORA,
                    "origem": origem,
                    "cust": "custodiante_agil" if origem == "externa" else None,
                    "sol": "sol_m03" if identificador == "gar_m03" else None,
                },
            ) if identificador != "gar_m03" else None
            if identificador == "gar_m03":
                c.execute(
                    text(
                        "INSERT INTO garantias (id, conta_id, parceiro_id, tipo_ativo, "
                        "valor_aplicado, estado, marcada_em, criada_em, origem, custodiante_id) "
                        "VALUES ('gar_m03', 'cta_m', 'par_m', 'fundo_di', 100000, "
                        "'gravame_pendente', :data, :agora, 'externa', 'custodiante_agil')"
                    ),
                    {"data": DATA, "agora": AGORA},
                )
                c.execute(
                    text(
                        "INSERT INTO solicitacoes_de_custodia (id, garantia_id, conta_id, "
                        "parceiro_id, custodiante_id, tipo, criada_em, prazo_ate) VALUES "
                        "('sol_m03', 'gar_m03', 'cta_m', 'par_m', 'custodiante_agil', "
                        "'gravame', :agora, :agora)"
                    ),
                    {"agora": AGORA},
                )
                c.execute(
                    text("UPDATE garantias SET solicitacao_id = 'sol_m03' WHERE id = 'gar_m03'")
                )
        c.execute(
            text(
                "INSERT INTO lancamentos (id, conta_id, parceiro_id, tipo, referencia_id, "
                "descricao, criado_em) VALUES ('lan_m', 'cta_m', 'par_m', 'bloqueio_garantia', "
                "'gar_m01', 'bloqueio', :agora)"
            ),
            {"agora": AGORA},
        )
        sequencia = c.execute(text("SELECT sequencia FROM lancamentos WHERE id = 'lan_m'")).scalar()
        for conta_contabil, sentido in (
            ("garantias_custodiadas", "D"),
            ("garantias_a_devolver", "C"),
        ):
            c.execute(
                text(
                    "INSERT INTO partidas (lancamento_sequencia, conta_id, parceiro_id, "
                    "conta_contabil, sentido, valor, garantia_id) VALUES "
                    "(:seq, 'cta_m', 'par_m', :cc, :s, 100000, 'gar_m01')"
                ),
                {"seq": sequencia, "cc": conta_contabil, "s": sentido},
            )
        # gar_m05 (liberacao_pendente) ainda tem o valor no ledger, como no modelo antigo.
        c.execute(
            text(
                "INSERT INTO lancamentos (id, conta_id, parceiro_id, tipo, referencia_id, "
                "descricao, criado_em) VALUES ('lan_m5', 'cta_m', 'par_m', 'bloqueio_garantia', "
                "'gar_m05', 'bloqueio', :agora)"
            ),
            {"agora": AGORA},
        )
        sequencia = c.execute(
            text("SELECT sequencia FROM lancamentos WHERE id = 'lan_m5'")
        ).scalar()
        for conta_contabil, sentido in (
            ("garantias_custodiadas", "D"),
            ("garantias_a_devolver", "C"),
        ):
            c.execute(
                text(
                    "INSERT INTO partidas (lancamento_sequencia, conta_id, parceiro_id, "
                    "conta_contabil, sentido, valor, garantia_id) VALUES "
                    "(:seq, 'cta_m', 'par_m', :cc, :s, 100000, 'gar_m05')"
                ),
                {"seq": sequencia, "cc": conta_contabil, "s": sentido},
            )


def test_migracao_0003_mapeia_estados_tipos_politicas_e_desce(url_vazia: str) -> None:
    configuracao = _configuracao(url_vazia)
    command.upgrade(configuracao, "0002")
    engine = criar_engine(url_vazia)
    try:
        _popular_revisao_0002(engine)
        command.upgrade(configuracao, "0003")
        with engine.connect() as c:
            linhas = c.execute(
                text(
                    "SELECT g.id, g.tipo_ativo, g.liquidez_dias, g.produto, g.solicitacao_id, "
                    "v.estado, v.trilho, v.baixa_pedida_em, v.motivo, v.protocolo "
                    "FROM garantias g JOIN gravames v ON v.garantia_id = g.id ORDER BY g.id"
                )
            ).all()
            assert len(linhas) == len(GARANTIAS)
            for linha in linhas:
                tipo, estado, trilho, baixa, motivo = ESPERADO[linha.id]
                achado = (linha.tipo_ativo, linha.estado, linha.trilho)
                assert achado == (tipo, estado, trilho), linha.id
                assert (linha.baixa_pedida_em is not None) == baixa, linha.id
                assert linha.motivo == motivo, linha.id
                assert linha.produto == "cartao"
                registrado = estado in ("registrado", "liberado_para_excussao", "baixado")
                assert (linha.protocolo is not None) == registrado, linha.id
            por_id = {linha.id: linha for linha in linhas}
            assert por_id["gar_m01"].liquidez_dias == 0  # deposito -> cdb D+0
            assert por_id["gar_m02"].liquidez_dias is None
            assert por_id["gar_m03"].solicitacao_id is None  # pedido cancelado pela migração
            assert (
                c.execute(
                    text("SELECT tipo FROM solicitacoes_de_custodia WHERE id = 'sol_m03'")
                ).scalar()
                == "entrada"
            )
            # Liberação pendente: o valor sai do ledger (compensação da migração).
            saldos = dict(
                c.execute(
                    text(
                        "SELECT garantia_id, "
                        "SUM(CASE WHEN sentido = 'D' THEN valor ELSE -valor END) "
                        "FROM partidas WHERE conta_contabil = 'garantias_custodiadas' "
                        "GROUP BY garantia_id"
                    )
                ).all()
            )
            assert saldos["gar_m05"] == 0
            assert saldos["gar_m01"] == 100000  # a garantia vigente continua no ledger
            # O ledger continua fechando.
            soma = c.execute(
                text(
                    "SELECT COALESCE(SUM(CASE WHEN sentido = 'D' THEN valor ELSE -valor END), 0)"
                    " FROM partidas"
                )
            ).scalar()
            assert soma == 0
            politica: Any = c.execute(text("SELECT politica FROM parceiros")).scalar()
            politica = json.loads(politica) if isinstance(politica, str) else politica
            assert "deposito" not in politica["ltv_por_ativo"]
            assert politica["ltv_por_ativo"]["cdb"] == "0.95"
            assert politica["ltv_por_ativo"]["previdencia"] == "0.60"
            assert politica["ltv_por_ativo"]["lci"] == "0.80"
            assert politica["ordem_de_execucao"][:2] == ["cdb", "acoes"]
            assert "lci" in politica["ordem_de_execucao"]
            indices = {
                linha[0]
                for linha in c.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                        if engine.dialect.name == "sqlite"
                        else "SELECT indexname FROM pg_indexes"
                    )
                )
            }
            assert {"ix_gravames_aguardando_contraparte", "ix_gravames_baixa_pendente"} <= indices
        command.downgrade(configuracao, "0002")
        with engine.connect() as c:
            estados = dict(c.execute(text("SELECT id, estado FROM garantias")).all())
            assert estados["gar_m05"] == "liberacao_pendente"
            assert estados["gar_m07"] == "pendente"
            assert estados["gar_m06"] == "execucao_solicitada"
            tipos = dict(c.execute(text("SELECT id, tipo_ativo FROM garantias")).all())
            assert tipos["gar_m01"] == "deposito"  # o depósito convertido volta
            assert tipos["gar_m02"] == "cdb"
            # O ledger volta ao que era: sem a compensação da migração, saída não dobra.
            assert c.execute(text("SELECT COUNT(*) FROM lancamentos")).scalar() == 2
            assert not c.execute(
                text("SELECT COUNT(*) FROM lancamentos WHERE id LIKE 'lan_mig0003_%'")
            ).scalar()
            saldos = dict(
                c.execute(
                    text(
                        "SELECT garantia_id, "
                        "SUM(CASE WHEN sentido = 'D' THEN valor ELSE -valor END) "
                        "FROM partidas WHERE conta_contabil = 'garantias_custodiadas' "
                        "GROUP BY garantia_id"
                    )
                ).all()
            )
            assert saldos["gar_m05"] == 100000
            # A política volta no formato estrito da 0002.
            politica = c.execute(text("SELECT politica FROM parceiros")).scalar()
            politica = json.loads(politica) if isinstance(politica, str) else politica
            assert set(politica["ltv_por_ativo"]) <= TIPOS_DA_0002
            assert set(politica["ordem_de_execucao"]) <= TIPOS_DA_0002
            assert politica["ltv_por_ativo"]["deposito"] == "0.95"
            assert "deposito" in politica["ordem_de_execucao"]
        # Subir de novo funciona (o downgrade não deixou tabela nem coluna para trás).
        command.upgrade(configuracao, "0003")
    finally:
        engine.dispose()


def test_migracao_0003_so_acrescenta_tipos_que_nao_existiam_na_0002(url_vazia: str) -> None:
    configuracao = _configuracao(url_vazia)
    command.upgrade(configuracao, "0002")
    engine = criar_engine(url_vazia)
    try:
        cast = "CAST(:politica AS jsonb)" if engine.dialect.name == "postgresql" else ":politica"
        politica = json.dumps(
            {
                # O parceiro tirou acoes, fundo_di e os títulos do Tesouro da política.
                "ltv_por_ativo": {"cdb": "0.70"},
                "ordem_de_execucao": ["cdb"],
                "dias_para_execucao": 7,
                "prazo_chamada_de_margem_dias": 5,
            }
        )
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO parceiros (id, nome, chave_prefixo, chave_hash, politica, "  # noqa: S608
                    "limite_de_requisicoes_por_minuto, ativo, criado_em) VALUES "
                    f"('par_i4', 'P', 'pre', 'hash', {cast}, 60, true, :agora)"
                ),
                {"politica": politica, "agora": AGORA},
            )
        command.upgrade(configuracao, "0003")
        with engine.connect() as c:
            lido: Any = c.execute(text("SELECT politica FROM parceiros")).scalar()
        lido = json.loads(lido) if isinstance(lido, str) else lido
        assert lido["ltv_por_ativo"]["cdb"] == "0.70"  # o que o parceiro escolheu fica
        assert set(lido["ltv_por_ativo"]) == {"cdb", "lci", "lca", "fii", "etf", "previdencia"}
        assert set(lido["ordem_de_execucao"]) == set(lido["ltv_por_ativo"])
        for tipo in ("acoes", "fundo_di", "tesouro_selic", "tesouro_ipca", "tesouro_prefixado"):
            assert tipo not in lido["ltv_por_ativo"]
    finally:
        engine.dispose()
