"""O que a garantia conta em cada estado do gravame e em cada produto."""

from __future__ import annotations

import pytest

from app.dominio.erros import ValorInvalido
from app.dominio.garantia import Garantia
from app.dominio.gravame import EstadoGravame, Gravame
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Infraestrutura, Instrumento, Produto, Trilho
from tests.apoio import AGORA, HOJE, reais

E = EstadoGravame


def _garantia(
    estado: EstadoGravame,
    produto: Produto = Produto.CARTAO,
    *,
    baixa_pedida: bool = False,
    emprestimo_id: str | None = None,
) -> Garantia:
    gravame = Gravame.solicitar(
        infraestrutura=Infraestrutura.B3_BALCAO,
        instrumento=Instrumento.CESSAO_FIDUCIARIA,
        trilho=Trilho.CASA,
        agora=AGORA,
    )
    gravame.estado = estado
    if baixa_pedida:
        gravame.baixa_pedida_em = AGORA
        gravame.baixa_prazo = HOJE
    return Garantia(
        id="gar_1",
        conta_id="cta_1",
        tipo_ativo=TipoAtivo.CDB,
        valor_aplicado=reais("100.00"),
        criada_em=AGORA,
        marcada_em=HOJE,
        produto=produto,
        gravame=gravame,
        emprestimo_id=emprestimo_id,
    )


@pytest.mark.parametrize("estado", list(EstadoGravame))
def test_so_registrado_sem_baixa_gera_limite_no_cartao(estado: EstadoGravame) -> None:
    garantia = _garantia(estado)
    assert garantia.estado is estado
    assert garantia.gera_limite == (estado is E.REGISTRADO)
    assert garantia.cobre_divida == (estado in (E.REGISTRADO, E.LIBERADO_PARA_EXCUSSAO))
    assert not _garantia(estado, baixa_pedida=True).gera_limite
    assert not garantia.disponivel_para_emprestimo


def test_garantia_de_emprestimo_nao_gera_limite_e_fica_disponivel_uma_vez() -> None:
    livre = _garantia(E.REGISTRADO, Produto.EMPRESTIMO)
    assert not livre.gera_limite
    assert livre.disponivel_para_emprestimo
    assert not _garantia(
        E.REGISTRADO, Produto.EMPRESTIMO, emprestimo_id="emp_1"
    ).disponivel_para_emprestimo
    assert not _garantia(E.SOLICITADO, Produto.EMPRESTIMO).disponivel_para_emprestimo


def test_garantia_de_fora_exige_contraparte_e_so_ela() -> None:
    gravame = Gravame.solicitar(
        infraestrutura=Infraestrutura.B3_BALCAO,
        instrumento=Instrumento.CESSAO_FIDUCIARIA,
        trilho=Trilho.TRANSFERENCIA_DE_CUSTODIA,
        agora=AGORA,
    )
    with pytest.raises(ValorInvalido):
        Garantia(
            id="gar_1",
            conta_id="cta_1",
            tipo_ativo=TipoAtivo.CDB,
            valor_aplicado=reais("1.00"),
            criada_em=AGORA,
            marcada_em=HOJE,
            produto=Produto.CARTAO,
            gravame=gravame,
        )
    with pytest.raises(ValorInvalido):
        Garantia(
            id="gar_2",
            conta_id="cta_1",
            tipo_ativo=TipoAtivo.CDB,
            valor_aplicado=reais("1.00"),
            criada_em=AGORA,
            marcada_em=HOJE,
            produto=Produto.CARTAO,
            gravame=_garantia(E.SOLICITADO).gravame,
            contraparte_id="banco_de_origem_simulado",
        )
