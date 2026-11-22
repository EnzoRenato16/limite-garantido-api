"""Contrapartes simuladas dos trilhos: determinísticas, guiadas pelo relógio."""

from __future__ import annotations

import hashlib
import re
import tomllib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.aplicacao.portas import (
    ContraparteDesconhecida,
    PedidoDeEntrada,
    StatusDaResposta,
)
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Trilho
from app.infra.contrapartes import (
    ARQUIVO_DE_PERFIS,
    PerfilDeContraparte,
    SemContrapartes,
    SimuladorDeContrapartes,
    carregar_perfis,
    montar_contraparte,
)
from app.infra.infraestrutura_simulada import InfraestruturaSimulada
from app.infra.relogio import RelogioFixo
from tests.apoio import AGORA, custodiar, nova_conta, reais

# Palavras que não podem aparecer nos perfis: SHA-256 da palavra em minúsculas. Os nomes
# não ficam escritos no código nem no TOML; o teste só compara os resumos.
RESUMOS_PROIBIDOS = frozenset(
    {
        "ef0c9930fdbe965132c55d5be98f0a51ace24809b15458361319d3872a9c0b94",
        "4946469c1604ec9159a7479f6d29cab8949971832b7553cb8aa109f79d71e623",
        "6774021d89fd4fd1f95833e8fedf662fb66923b98db11b389fc2e15f47a09d54",
    }
)


def _perfil(**mudancas: object) -> PerfilDeContraparte:
    valores: dict[str, object] = {
        "id": "contraparte_teste",
        "nome": "Contraparte de Teste (simulador)",
        "descricao": "Para os testes.",
        "trilhos": (Trilho.TRANSFERENCIA_DE_CUSTODIA,),
        "semente": 7,
        "latencia": timedelta(minutes=10),
        "probabilidade_de_recusa": Decimal("0.2"),
        "probabilidade_sem_resposta": Decimal("0.1"),
        "motivos_de_recusa": ("ativo_nao_portavel", "titularidade_divergente"),
    }
    valores.update(mudancas)
    return PerfilDeContraparte(**valores)  # type: ignore[arg-type]


def _pedido(
    solicitacao_id: str = "sol_1",
    trilho: Trilho = Trilho.TRANSFERENCIA_DE_CUSTODIA,
    contraparte_id: str = "contraparte_teste",
    valor: str = "1000.00",
) -> PedidoDeEntrada:
    return PedidoDeEntrada(
        solicitacao_id=solicitacao_id,
        contraparte_id=contraparte_id,
        trilho=trilho,
        garantia_id="gar_1",
        tipo_ativo=TipoAtivo.CDB,
        valor=reais(valor),
        criado_em=AGORA,
    )


def _simulador(relogio: RelogioFixo, **mudancas: object) -> SimuladorDeContrapartes:
    return SimuladorDeContrapartes([_perfil(**mudancas)], relogio)


def test_antes_da_latencia_o_pedido_esta_pendente_e_depois_conclui() -> None:
    relogio = RelogioFixo(AGORA)
    simulador = _simulador(
        relogio, probabilidade_de_recusa=Decimal(0), probabilidade_sem_resposta=Decimal(0)
    )
    resposta = simulador.solicitar_entrada(_pedido())
    assert resposta.status is StatusDaResposta.PENDENTE
    assert resposta.evento_id is None

    relogio.avancar(minutes=10)
    resposta = simulador.consultar(_pedido())
    assert resposta.status is StatusDaResposta.CONCLUIDA
    assert resposta.evento_id is not None


def test_mesma_semente_e_mesmo_pedido_dao_a_mesma_resposta() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=2))
    primeiro = _simulador(relogio)
    segundo = _simulador(relogio)
    for numero in range(50):
        pedido = _pedido(f"sol_{numero}")
        assert primeiro.consultar(pedido) == segundo.consultar(pedido)
        assert primeiro.consultar(pedido) == primeiro.solicitar_entrada(pedido)
    # Outra semente, outro sorteio: o determinismo vem da semente, não de um valor fixo.
    outro = _simulador(relogio, semente=8)
    assert [primeiro.consultar(_pedido(f"sol_{n}")).status for n in range(50)] != [
        outro.consultar(_pedido(f"sol_{n}")).status for n in range(50)
    ]


def test_sem_resposta_nunca_responde() -> None:
    relogio = RelogioFixo(AGORA)
    simulador = _simulador(
        relogio, probabilidade_de_recusa=Decimal(0), probabilidade_sem_resposta=Decimal(1)
    )
    relogio.avancar(days=365)
    assert simulador.consultar(_pedido()).status is StatusDaResposta.PENDENTE


def test_recusa_vem_com_motivo_do_perfil_e_vira_impedimento_so_na_portabilidade() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=1))
    simulador = _simulador(
        relogio,
        trilhos=(Trilho.TRANSFERENCIA_DE_CUSTODIA, Trilho.PORTABILIDADE_CVM_210),
        probabilidade_de_recusa=Decimal(1),
        probabilidade_sem_resposta=Decimal(0),
    )
    recusa = simulador.consultar(_pedido())
    assert recusa.status is StatusDaResposta.RECUSADA
    assert recusa.motivo in ("ativo_nao_portavel", "titularidade_divergente")
    impedimento = simulador.consultar(_pedido(trilho=Trilho.PORTABILIDADE_CVM_210))
    assert impedimento.status is StatusDaResposta.IMPEDIMENTO  # CVM 210, art. 19


def test_a_operadora_informa_valor_e_tributos_e_nao_recusa() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=3))
    perfis = {p.id: p for p in carregar_perfis(ARQUIVO_DE_PERFIS)}
    simulador = SimuladorDeContrapartes(perfis.values(), relogio)
    respondidas = 0
    for numero in range(300):
        resposta = simulador.consultar(
            _pedido(
                f"sol_{numero}",
                Trilho.RES_CONJUNTA_12,
                "entidade_operadora_simulada",
                valor="20000.00",
            )
        )
        assert resposta.status in (StatusDaResposta.PENDENTE, StatusDaResposta.CONCLUIDA)
        if resposta.status is StatusDaResposta.CONCLUIDA:
            respondidas += 1
            assert resposta.valor_disponivel == reais("20000.00")
            assert resposta.tributos_informados == reais("2000.00")
    assert respondidas > 200


def test_a_corretora_de_origem_nunca_recusa_por_sorteio() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=5))
    simulador = montar_contraparte("simulador", relogio)
    assert isinstance(simulador, SimuladorDeContrapartes)
    for numero in range(300):
        pedido = _pedido(
            f"sol_{numero}", Trilho.PORTABILIDADE_CVM_210, "corretora_de_origem_simulada"
        )
        status = simulador.consultar(pedido).status
        assert status in (StatusDaResposta.PENDENTE, StatusDaResposta.CONCLUIDA)


def test_o_agente_sem_servico_recusa_sempre_e_o_banco_pode_recusar() -> None:
    relogio = RelogioFixo(AGORA + timedelta(days=1))
    simulador = montar_contraparte("simulador", relogio)
    assert isinstance(simulador, SimuladorDeContrapartes)
    for numero in range(30):
        resposta = simulador.consultar(
            _pedido(f"sol_{numero}", Trilho.TD_GARANTIA, "agente_td_sem_servico_simulado")
        )
        assert resposta.status is StatusDaResposta.RECUSADA
        assert resposta.motivo == "agente_nao_opera_td_garantia"
    estados = {
        simulador.consultar(_pedido(f"b_{n}", contraparte_id="banco_de_origem_simulado")).status
        for n in range(200)
    }
    assert StatusDaResposta.RECUSADA in estados


def test_as_proporcoes_seguem_o_perfil() -> None:
    relogio = RelogioFixo(AGORA + timedelta(hours=1))
    simulador = _simulador(relogio)
    contagem = dict.fromkeys(StatusDaResposta, 0)
    for numero in range(2000):
        contagem[simulador.consultar(_pedido(f"sol_{numero}")).status] += 1
    assert 300 <= contagem[StatusDaResposta.RECUSADA] <= 500  # 20%
    assert 120 <= contagem[StatusDaResposta.PENDENTE] <= 280  # 10%


def test_contraparte_desconhecida_e_desligada() -> None:
    simulador = _simulador(RelogioFixo(AGORA))
    with pytest.raises(ContraparteDesconhecida):
        simulador.consultar(_pedido(contraparte_id="nao_existe"))
    assert simulador.conhece("contraparte_teste")
    assert not simulador.conhece("nao_existe")
    desligada = montar_contraparte("desligada", RelogioFixo(AGORA))
    assert isinstance(desligada, SemContrapartes)
    assert desligada.contrapartes() == []
    with pytest.raises(ContraparteDesconhecida):
        desligada.solicitar_entrada(_pedido())


@pytest.mark.parametrize(
    "mudanca",
    [
        {"probabilidade_de_recusa": Decimal("1.1")},
        {"probabilidade_sem_resposta": Decimal("-0.1")},
        {"probabilidade_de_recusa": Decimal("0.6"), "probabilidade_sem_resposta": Decimal("0.5")},
        {"latencia": timedelta(seconds=-1)},
        {"motivos_de_recusa": ()},
        {"trilhos": ()},
        {"trilhos": (Trilho.CASA,)},
        {"nome": "Contraparte de Teste"},  # sem "(simulador)"
        {"simulado": False},
        # Res. Conjunta 12, art. 18: a operadora não recusa pedido que siga as normas.
        {"trilhos": (Trilho.RES_CONJUNTA_12,), "probabilidade_de_recusa": Decimal("0.1")},
    ],
)
def test_perfil_invalido_e_recusado(mudanca: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="inválido"):
        _perfil(**mudanca)


def test_arquivo_tem_as_cinco_contrapartes_simuladas_e_nenhum_nome_proibido() -> None:
    bruto = ARQUIVO_DE_PERFIS.read_text(encoding="utf-8")
    dados = tomllib.loads(bruto)
    assert list(dados) == [
        "corretora_de_origem_simulada",
        "banco_de_origem_simulado",
        "agente_td_simulado",
        "agente_td_sem_servico_simulado",
        "entidade_operadora_simulada",
    ]
    for contraparte_id, campos in dados.items():
        assert campos["simulado"] is True, contraparte_id
        assert campos["nome"].endswith("(simulador)"), contraparte_id
    # Nenhuma palavra do arquivo inteiro (nomes, descrições, comentários) é proibida.
    palavras = set(re.findall(r"[a-zà-ú0-9]+", bruto.lower()))
    achadas = {p for p in palavras if hashlib.sha256(p.encode()).hexdigest() in RESUMOS_PROIBIDOS}
    assert not achadas
    perfis = carregar_perfis(ARQUIVO_DE_PERFIS)
    assert [p.simulado for p in perfis] == [True] * 5


def test_arquivo_invalido_e_recusado(tmp_path: Path) -> None:
    arquivo = tmp_path / "perfis.toml"
    arquivo.write_text('[x]\nnome = "X"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="incompleto"):
        carregar_perfis(arquivo)


def test_a_infraestrutura_simulada_da_protocolo_vende_ao_mercado_e_baixa() -> None:
    conta = nova_conta()
    garantia = custodiar(conta, TipoAtivo.CDB, "1000.00")
    infraestrutura = InfraestruturaSimulada()
    protocolo = infraestrutura.registrar(garantia)
    assert protocolo == f"SIM-{garantia.gravame.infraestrutura}-{garantia.id}"
    assert protocolo.startswith("SIM-b3_balcao-")
    assert infraestrutura.registrar(garantia) == protocolo  # estável
    assert infraestrutura.executar(garantia, reais("1234.56")) == reais("1234.56")
    assert infraestrutura.baixar(garantia) is None
    assert infraestrutura.confirma_na_hora
    assert not InfraestruturaSimulada(confirma_na_hora=False).confirma_na_hora
