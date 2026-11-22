"""Garantias que moram no próprio banco: migração e gatilhos do ledger."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.dominio.conta import Conta
from app.dominio.ledger import ContaContabil
from app.dominio.parceiro import Parceiro as ParceiroDominio
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto
from app.infra.banco.modelos import Base
from app.infra.banco.repositorios import RepositorioDeContasSql, RepositorioDeParceirosSql
from app.infra.banco.sessao import criar_engine, criar_fabrica_de_sessoes
from tests.apoio import AGORA, CATALOGO, HOJE, prazo_da_baixa, reais
from tests.conftest import Parceiro, rodar_migracoes
from tests.unidade.test_emprestimo import POL as POL_EMPRESTIMO
from tests.unidade.test_emprestimo import _agora, _ativo, _contratar, _previdencia


def test_migracao_alembic_gera_exatamente_o_esquema_dos_modelos(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migracao.db'}"
    rodar_migracoes(url)
    engine = criar_engine(url)
    with engine.connect() as conexao:
        diferencas = compare_metadata(MigrationContext.configure(conexao), Base.metadata)
    engine.dispose()
    assert diferencas == []


@pytest.mark.postgres
def test_no_postgres_a_migracao_tambem_bate_com_os_modelos(engine: Engine) -> None:
    with engine.connect() as conexao:
        contexto = MigrationContext.configure(conexao, opts={"compare_type": True})
        assert compare_metadata(contexto, Base.metadata) == []


def _conta_com_movimento(parceiro: Parceiro) -> str:
    conta = parceiro.abrir_conta()
    parceiro.bloquear(conta, "cdb", "100.00")
    parceiro.comprar(conta, "10.00")
    return conta


@pytest.mark.postgres
@pytest.mark.parametrize(
    "comando",
    [
        "UPDATE partidas SET valor = valor + 1 WHERE conta_id = :conta",
        "DELETE FROM partidas WHERE conta_id = :conta",
        "UPDATE lancamentos SET descricao = 'adulterado' WHERE conta_id = :conta",
        "DELETE FROM lancamentos WHERE conta_id = :conta",
    ],
)
def test_ledger_e_somente_insercao_ate_para_sql_escrito_a_mao(
    parceiro: Parceiro, engine: Engine, comando: str
) -> None:
    conta = _conta_com_movimento(parceiro)
    with pytest.raises(DBAPIError, match="somente inserção"), engine.begin() as conexao:
        conexao.execute(text(comando), {"conta": conta})
    assert parceiro.limite(conta)["divida_total"] == "10.00"


@pytest.mark.postgres
def test_o_banco_recusa_lancamento_desbalanceado_no_commit(
    parceiro: Parceiro, engine: Engine
) -> None:
    conta = _conta_com_movimento(parceiro)
    inserir_cabecalho = text(
        "INSERT INTO lancamentos (id, conta_id, parceiro_id, tipo, referencia_id, descricao, "
        "criado_em) VALUES (:id, :conta, :parceiro, 'compra', 'cmp_x', 'na mão', now()) "
        "RETURNING sequencia"
    )
    inserir_partida = text(
        "INSERT INTO partidas (lancamento_sequencia, conta_id, parceiro_id, conta_contabil, "
        "sentido, valor) VALUES (:seq, :conta, :parceiro, :contabil, :sentido, :valor)"
    )
    base = {"conta": conta, "parceiro": parceiro.id}

    def lancar_na_mao(id_do_lancamento: str, debito: int, credito: int) -> None:
        with engine.begin() as conexao:
            seq = conexao.execute(inserir_cabecalho, {**base, "id": id_do_lancamento}).scalar_one()
            for contabil, sentido, valor in (
                ("recebiveis_a_faturar", "D", debito),
                ("liquidacao_a_pagar", "C", credito),
            ):
                conexao.execute(
                    inserir_partida,
                    {**base, "seq": seq, "contabil": contabil, "sentido": sentido, "valor": valor},
                )

    # Débito de R$ 50 com crédito de R$ 49,99: o commit não passa.
    with pytest.raises(DBAPIError, match="desbalanceado"):
        lancar_na_mao("lan_torto", 5000, 4999)
    assert parceiro.limite(conta)["divida_total"] == "10.00"  # nada foi gravado

    # O mesmo lançamento, fechado, é aceito: o gatilho só barra o que não fecha.
    lancar_na_mao("lan_certo", 5000, 5000)
    assert parceiro.limite(conta)["divida_total"] == "60.00"


@pytest.mark.postgres
def test_partida_com_valor_zero_ou_sentido_invalido_e_recusada(
    parceiro: Parceiro, engine: Engine
) -> None:
    conta = _conta_com_movimento(parceiro)
    for sentido, valor, restricao in (("D", 0, "valor_positivo"), ("X", 100, "sentido")):
        with pytest.raises(DBAPIError, match=restricao), engine.begin() as conexao:
            conexao.execute(
                text(
                    "INSERT INTO partidas (lancamento_sequencia, conta_id, parceiro_id, "
                    "conta_contabil, sentido, valor) SELECT sequencia, conta_id, parceiro_id, "
                    "'caixa_recebimentos', :sentido, :valor FROM lancamentos "
                    "WHERE conta_id = :conta LIMIT 1"
                ),
                {"conta": conta, "sentido": sentido, "valor": valor},
            )


# ------------------------------------------------- gravame, garantia e empréstimo
def _parceiro_de_dominio(sessao: Session) -> ParceiroDominio:
    sufixo = uuid.uuid4().hex[:12]
    parceiro = ParceiroDominio(
        id=f"par_{sufixo}",
        nome="Parceiro do teste de banco",
        politica=POL_EMPRESTIMO,
        limite_de_requisicoes_por_minuto=60,
        criado_em=AGORA,
    )
    RepositorioDeParceirosSql(sessao).adicionar(parceiro, f"pre{sufixo}", "hash")
    return parceiro


def test_gravame_garantia_e_emprestimo_vao_e_voltam_do_banco(engine: Engine) -> None:
    fabrica = criar_fabrica_de_sessoes(engine)
    with fabrica() as sessao:
        parceiro = _parceiro_de_dominio(sessao)
        repo = RepositorioDeContasSql(sessao)
        conta = Conta.abrir(parceiro_id=parceiro.id, cliente_ref="c-banco", agora=AGORA)
        repo.adicionar(conta)
        cdb = _ativo(conta, TipoAtivo.CDB, "20000.00", emissor="banco_x", aplicado_em=HOJE)
        prev = _previdencia(conta)
        emprestimo = _contratar(
            conta, [cdb, prev], valor="9000.00", protocolos={prev.id: "SIM-rc12-1"}
        )
        primeira = emprestimo.parcelas[0]
        dia = primeira.vencimento
        conta.pagar_parcela(emprestimo.id, reais("100.00"), _agora(dia), dia, prazo_da_baixa(dia))
        repo.salvar(conta, parceiro)
        sessao.commit()
        conta_id = conta.id
    with fabrica() as sessao:
        lido = RepositorioDeContasSql(sessao).obter(parceiro.id, conta_id)
        assert lido is not None
        (emp,) = lido.emprestimos
        assert emp.id == emprestimo.id
        assert emp.principal == reais("9000.00")
        assert emp.taxa_ao_mes == emprestimo.taxa_ao_mes
        assert emp.garantia_ids == tuple(sorted((cdb.id, prev.id)))
        assert emp.dias_minimos_execucao == 91
        assert [p.numero for p in emp.parcelas] == list(range(1, 13))
        assert emp.parcelas == emprestimo.parcelas
        assert emp.saldo_devedor(dia) == emprestimo.saldo_devedor(dia)
        garantias = {g.id: g for g in lido.garantias}
        assert garantias[cdb.id].emissor == "banco_x"
        assert garantias[cdb.id].aplicado_em == HOJE
        assert garantias[cdb.id].emprestimo_id == emprestimo.id
        assert garantias[cdb.id].gravame == cdb.gravame
        assert garantias[prev.id].gravame == prev.gravame
        assert garantias[prev.id].informacoes == prev.informacoes
        assert garantias[prev.id].fim_da_acumulacao == prev.fim_da_acumulacao
        assert garantias[prev.id].plano == "vgbl"
        assert lido.saldos.debitos_menos_creditos().nulo
        assert lido.saldos.saldo_total(
            ContaContabil.EMPRESTIMOS_A_RECEBER
        ) == conta.saldos.saldo_total(ContaContabil.EMPRESTIMOS_A_RECEBER)
        # Consultas dos workers.
        repo = RepositorioDeContasSql(sessao)
        antes = dia - timedelta(days=1)
        assert (parceiro.id, conta_id) not in repo.contas_com_emprestimo_a_avaliar(
            antes, parceiro.id
        )
        depois = dia + timedelta(days=3)
        assert repo.contas_com_emprestimo_a_avaliar(depois, parceiro.id) == [
            (parceiro.id, conta_id)
        ]


def test_pedidos_em_aberto_e_baixas_pendentes_sao_achados_pelos_indices(engine: Engine) -> None:
    fabrica = criar_fabrica_de_sessoes(engine)
    with fabrica() as sessao:
        parceiro = _parceiro_de_dominio(sessao)
        repo = RepositorioDeContasSql(sessao)
        conta = Conta.abrir(parceiro_id=parceiro.id, cliente_ref="c-fila", agora=AGORA)
        repo.adicionar(conta)
        pendente = conta.custodiar_na_casa(
            tipo=TipoAtivo.CDB,
            valor=reais("1000.00"),
            produto=Produto.CARTAO,
            politica=POL_EMPRESTIMO,
            catalogo=CATALOGO,
            agora=AGORA,
            hoje=HOJE,
            liquidez_dias=0,
        )
        registrada = _ativo(conta, TipoAtivo.CDB, "1000.00")
        repo.salvar(conta, parceiro)
        sessao.commit()
        assert repo.garantias_aguardando_contraparte(100, parceiro.id) == [
            (parceiro.id, conta.id, pendente.id)
        ]
        assert (parceiro.id, conta.id) not in repo.contas_com_baixa_pendente()
        # Registrar o gravame tira da fila; liberar pede a baixa.
        lida = repo.obter_para_atualizar(parceiro.id, conta.id)
        assert lida is not None
        lida.concluir_entrada(pendente.id, "SIM-1", POL_EMPRESTIMO, CATALOGO, AGORA, HOJE)
        repo.salvar(lida, parceiro)
        assert repo.garantias_aguardando_contraparte(100, parceiro.id) == []
        lida = repo.obter_para_atualizar(parceiro.id, conta.id)
        assert lida is not None
        lida.liberar(pendente.id, POL_EMPRESTIMO, CATALOGO, AGORA, HOJE, prazo_da_baixa(HOJE))
        repo.salvar(lida, parceiro)
        sessao.commit()
        assert (parceiro.id, conta.id) in repo.contas_com_baixa_pendente()
        relida = repo.obter(parceiro.id, conta.id)
        assert relida is not None
        assert relida.garantia(pendente.id).gravame.baixa_prazo == prazo_da_baixa(HOJE)
        assert relida.garantia(registrada.id).gravame.vigente
        relida.confirmar_baixa(pendente.id, AGORA)
        repo.salvar(relida, parceiro)
        sessao.commit()
        assert (parceiro.id, conta.id) not in repo.contas_com_baixa_pendente()
