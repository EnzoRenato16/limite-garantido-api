"""Simulador de custodiantes externos: determinístico, guiado pelo relógio."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.aplicacao.portas import (
    CustodianteDesconhecido,
    PedidoAoCustodiante,
    StatusDaResposta,
)
from app.dominio.garantia import TipoSolicitacao
from app.dominio.politica import TipoAtivo
from app.infra.custodia import (
    ARQUIVO_DE_PERFIS,
    CustodiaAssincrona,
    CustodiaInterna,
    PerfilDeCustodiante,
    SimuladorDeCustodiantes,
    carregar_perfis,
)
from app.infra.relogio import RelogioFixo
from tests.apoio import AGORA, reais


def _perfil(**mudancas: object) -> PerfilDeCustodiante:
    valores: dict[str, object] = {
        "id": "custodiante_teste",
        "nome": "Custodiante de Teste (simulador)",
        "descricao": "Para os testes.",
        "semente": 7,
        "latencia": timedelta(minutes=10),
        "prazo_de_resposta": timedelta(hours=1),
        "probabilidade_de_recusa": Decimal("0.2"),
        "probabilidade_sem_resposta": Decimal("0.1"),
        "fator_de_preco_na_venda": Decimal("0.98"),
        "motivos_de_recusa": ("ativo_ja_onerado", "titularidade_divergente"),
    }
    valores.update(mudancas)
    return PerfilDeCustodiante(**valores)  # type: ignore[arg-type]


def _pedido(
    solicitacao_id: str = "sol_1",
    tipo: TipoSolicitacao = TipoSolicitacao.GRAVAME,
    custodiante_id: str = "custodiante_teste",
) -> PedidoAoCustodiante:
    return PedidoAoCustodiante(
        solicitacao_id=solicitacao_id,
        custodiante_id=custodiante_id,
        tipo=tipo,
        garantia_id="gar_1",
        tipo_ativo=TipoAtivo.TESOURO_SELIC,
        valor=reais("1000.00"),
        criado_em=AGORA,
    )


def _simulador(relogio: RelogioFixo, **mudancas: object) -> SimuladorDeCustodiantes:
    return SimuladorDeCustodiantes([_perfil(**mudancas)], relogio)


def test_antes_da_latencia_o_pedido_esta_pendente() -> None:
    relogio = RelogioFixo(AGORA)
    simulador = _simulador(
        relogio, probabilidade_de_recusa=Decimal(0), probabilidade_sem_resposta=Decimal(0)
    )
    resposta = simulador.solicitar_gravame(_pedido())
    assert resposta.status is StatusDaResposta.PENDENTE
    assert resposta.evento_id is None

    relogio.avancar(minutes=10)
    resposta = simulador.consultar(_pedido())
    assert resposta.status is StatusDaResposta.CONFIRMADA
    assert resposta.evento_id is not None


def test_o_mesmo_pedido_tem_sempre_o_mesmo_destino_e_o_mesmo_evento() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=2))
    primeiro = _simulador(relogio)
    segundo = _simulador(relogio)
    for numero in range(50):
        pedido = _pedido(f"sol_{numero}")
        assert primeiro.consultar(pedido) == segundo.consultar(pedido)
        assert primeiro.consultar(pedido) == primeiro.solicitar_gravame(pedido)


def test_sem_resposta_nunca_responde() -> None:
    relogio = RelogioFixo(AGORA)
    simulador = _simulador(
        relogio, probabilidade_de_recusa=Decimal(0), probabilidade_sem_resposta=Decimal(1)
    )
    relogio.avancar(days=365)
    assert simulador.consultar(_pedido()).status is StatusDaResposta.PENDENTE


def test_recusa_vem_com_motivo_do_perfil() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=1))
    simulador = _simulador(
        relogio, probabilidade_de_recusa=Decimal(1), probabilidade_sem_resposta=Decimal(0)
    )
    resposta = simulador.consultar(_pedido())
    assert resposta.status is StatusDaResposta.RECUSADA
    assert resposta.motivo in ("ativo_ja_onerado", "titularidade_divergente")


def test_execucao_confirmada_traz_o_valor_obtido_pelo_fator_de_preco_para_baixo() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=1))
    simulador = _simulador(
        relogio,
        probabilidade_de_recusa=Decimal(0),
        probabilidade_sem_resposta=Decimal(0),
        fator_de_preco_na_venda=Decimal("0.9999"),
    )
    pedido = PedidoAoCustodiante(
        solicitacao_id="sol_x",
        custodiante_id="custodiante_teste",
        tipo=TipoSolicitacao.EXECUCAO,
        garantia_id="gar_1",
        tipo_ativo=TipoAtivo.ACOES,
        valor=reais("10.01"),
        criado_em=AGORA,
    )
    resposta = simulador.solicitar_execucao(pedido)
    assert resposta.status is StatusDaResposta.CONFIRMADA
    # 1001 centavos x 0.9999 = 1000.8999 -> 1000 centavos
    assert resposta.valor_obtido == reais("10.00")


def test_as_proporcoes_seguem_o_perfil() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=1))
    simulador = _simulador(relogio)
    contagem = dict.fromkeys(StatusDaResposta, 0)
    for numero in range(2000):
        contagem[simulador.consultar(_pedido(f"sol_{numero}")).status] += 1
    assert 300 <= contagem[StatusDaResposta.RECUSADA] <= 500  # 20%
    assert 120 <= contagem[StatusDaResposta.PENDENTE] <= 280  # 10%


def test_custodiante_desconhecido() -> None:
    simulador = _simulador(RelogioFixo(AGORA))
    with pytest.raises(CustodianteDesconhecido):
        simulador.consultar(_pedido(custodiante_id="nao_existe"))
    assert simulador.conhece("custodiante_teste")
    assert not simulador.conhece("nao_existe")


@pytest.mark.parametrize(
    "mudanca",
    [
        {"probabilidade_de_recusa": Decimal("1.1")},
        {"probabilidade_sem_resposta": Decimal("-0.1")},
        {"probabilidade_de_recusa": Decimal("0.6"), "probabilidade_sem_resposta": Decimal("0.5")},
        {"latencia": timedelta(seconds=-1)},
        {"prazo_de_resposta": timedelta(0)},
        {"fator_de_preco_na_venda": Decimal(0)},
        {"motivos_de_recusa": ()},
    ],
)
def test_perfil_invalido_e_recusado(mudanca: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="inválido"):
        _perfil(**mudanca)


def test_arquivo_tem_dois_custodiantes_simulados_e_nenhum_nome_real() -> None:
    perfis = carregar_perfis(ARQUIVO_DE_PERFIS)
    assert [perfil.id for perfil in perfis] == ["custodiante_agil", "custodiante_lento"]
    for perfil in perfis:
        assert "(simulador)" in perfil.nome
    agil, lento = perfis
    assert agil.probabilidade_de_recusa == 0
    assert lento.latencia > agil.latencia


def test_arquivo_invalido_e_recusado(tmp_path: Path) -> None:
    arquivo = tmp_path / "perfis.toml"
    arquivo.write_text('[x]\nnome = "X"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="incompleto"):
        carregar_perfis(arquivo)


def test_a_custodia_da_fase_1_segue_igual() -> None:
    relogio = RelogioFixo(AGORA)
    assert SimuladorDeCustodiantes([], relogio).solicitar_bloqueio(None) is True  # type: ignore[arg-type]
    assistida = SimuladorDeCustodiantes([], relogio, fase_1=CustodiaAssincrona())
    assert assistida.solicitar_bloqueio(None) is False  # type: ignore[arg-type]
    assert CustodiaInterna().custodiantes() == []
