import pytest

from app.dominio.erros import TransicaoInvalida
from app.dominio.garantia import TRANSICOES_PERMITIDAS, EstadoGarantia, Garantia
from app.dominio.politica import TipoAtivo
from tests.apoio import AGORA, HOJE, reais


def _garantia(estado: EstadoGarantia) -> Garantia:
    return Garantia(
        id="gar_1",
        conta_id="cta_1",
        tipo_ativo=TipoAtivo.CDB,
        valor_aplicado=reais("5000.00"),
        estado=estado,
        criada_em=AGORA,
        marcada_em=HOJE,
    )


TODOS = list(EstadoGarantia)
VALIDAS = [(de, para) for de, destinos in TRANSICOES_PERMITIDAS.items() for para in destinos]
INVALIDAS = [(de, para) for de in TODOS for para in TODOS if (de, para) not in VALIDAS]


@pytest.mark.parametrize(("de", "para"), VALIDAS)
def test_transicoes_validas(de: EstadoGarantia, para: EstadoGarantia) -> None:
    garantia = _garantia(de)
    garantia.transicionar(para)
    assert garantia.estado is para


@pytest.mark.parametrize(("de", "para"), INVALIDAS)
def test_transicoes_invalidas_dao_erro_claro(de: EstadoGarantia, para: EstadoGarantia) -> None:
    garantia = _garantia(de)
    with pytest.raises(TransicaoInvalida) as erro:
        garantia.transicionar(para)
    assert garantia.estado is de
    assert erro.value.codigo == "transicao_de_estado_invalida"
    assert erro.value.detalhes == {
        "garantia_id": "gar_1",
        "estado_atual": str(de),
        "estado_pedido": str(para),
    }


def test_o_caminho_do_enunciado_esta_no_mapa() -> None:
    assert (EstadoGarantia.PENDENTE, EstadoGarantia.BLOQUEADA) in VALIDAS
    for destino in (
        EstadoGarantia.LIBERADA,
        EstadoGarantia.EXECUTADA,
        EstadoGarantia.PARCIALMENTE_EXECUTADA,
    ):
        assert (EstadoGarantia.BLOQUEADA, destino) in VALIDAS
    # Estados finais não saem do lugar; pendente não pula etapa.
    assert not TRANSICOES_PERMITIDAS[EstadoGarantia.LIBERADA]
    assert not TRANSICOES_PERMITIDAS[EstadoGarantia.EXECUTADA]
    assert (EstadoGarantia.PENDENTE, EstadoGarantia.LIBERADA) in INVALIDAS


def test_so_garantia_bloqueada_conta_como_ativa() -> None:
    assert _garantia(EstadoGarantia.BLOQUEADA).ativa
    assert _garantia(EstadoGarantia.PARCIALMENTE_EXECUTADA).ativa
    for estado in (EstadoGarantia.PENDENTE, EstadoGarantia.LIBERADA, EstadoGarantia.EXECUTADA):
        garantia = _garantia(estado)
        assert not garantia.ativa
        with pytest.raises(TransicaoInvalida):
            garantia.exigir_ativa("remarcar")
