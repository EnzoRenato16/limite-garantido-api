"""Apoio dos testes que usam a camada de aplicação direto, sem HTTP."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import Engine

from app.aplicacao import contas as casos_de_conta
from app.aplicacao import parceiros as casos_de_parceiro
from app.aplicacao import trilhos
from app.aplicacao.portas import FabricaDeUnidades
from app.dominio.garantia import Garantia
from app.dominio.parceiro import Parceiro
from app.dominio.politica import TipoAtivo
from app.dominio.regulacao import Produto, Trilho
from app.infra.banco.sessao import criar_fabrica_de_sessoes
from app.infra.banco.unidade import criar_fabrica_de_unidades
from app.infra.contrapartes import PerfilDeContraparte, SimuladorDeContrapartes
from app.infra.infraestrutura_simulada import InfraestruturaSimulada
from app.infra.politica_referencia import carregar_politica_de_referencia
from app.infra.relogio import RelogioFixo
from tests.apoio import reais

TODOS_OS_TRILHOS = (
    Trilho.PORTABILIDADE_CVM_210,
    Trilho.TRANSFERENCIA_DE_CUSTODIA,
    Trilho.TD_GARANTIA,
    Trilho.RES_CONJUNTA_12,
)


def perfil(identificador: str, recusa: str, mudo: str) -> PerfilDeContraparte:
    """Contraparte de teste com destino fixo, que atende todos os trilhos de fora.

    O perfil que atende a Res. Conjunta 12 não pode recusar (art. 18); `teste_recusa` só
    atende os outros três.
    """
    trilhos_atendidos = tuple(
        t for t in TODOS_OS_TRILHOS if not (t is Trilho.RES_CONJUNTA_12 and recusa != "0")
    )
    return PerfilDeContraparte(
        id=identificador,
        nome=f"{identificador} (simulador)",
        descricao="Perfil de teste.",
        trilhos=trilhos_atendidos,
        semente=1,
        latencia=timedelta(minutes=10),
        probabilidade_de_recusa=Decimal(recusa),
        probabilidade_sem_resposta=Decimal(mudo),
        motivos_de_recusa=("ativo_ja_onerado",),
        percentual_de_tributos_informados=Decimal("0.10"),
    )


PERFIS = [
    perfil("teste_confirma", "0", "0"),
    perfil("teste_recusa", "1", "0"),
    perfil("teste_mudo", "0", "1"),
]


def fabrica_de_unidades(engine: Engine, relogio: RelogioFixo) -> FabricaDeUnidades:
    return criar_fabrica_de_unidades(
        criar_fabrica_de_sessoes(engine),
        relogio,
        SimuladorDeContrapartes(PERFIS, relogio),
        InfraestruturaSimulada(),
    )


def novo_parceiro(abrir_unidade: FabricaDeUnidades) -> Parceiro:
    with abrir_unidade() as uow:
        parceiro, _ = casos_de_parceiro.criar_parceiro(
            uow, "Parceiro de Aplicação", carregar_politica_de_referencia(), 600
        )
        uow.commit()
    return parceiro


def nova_conta_do(abrir_unidade: FabricaDeUnidades, parceiro: Parceiro, cliente: str) -> str:
    with abrir_unidade() as uow:
        conta = casos_de_conta.abrir_conta(uow, parceiro, cliente)
        uow.commit()
        return conta.id


def pedir_entrada(
    abrir_unidade: FabricaDeUnidades,
    parceiro: Parceiro,
    conta_id: str,
    trilho: Trilho,
    tipo: TipoAtivo,
    contraparte_id: str,
    valor: str = "10000.00",
    produto: Produto = Produto.EMPRESTIMO,
) -> Garantia:
    with abrir_unidade() as uow:
        extras: dict[str, object] = {}
        if trilho is Trilho.RES_CONJUNTA_12:
            extras = {
                "plano": "pgbl",
                "fim_da_acumulacao": uow.relogio.hoje() + timedelta(days=3650),
            }
        _, garantia = trilhos.receber_de_fora(
            uow,
            parceiro,
            conta_id,
            trilho=trilho,
            tipo=tipo,
            valor=reais(valor),
            produto=produto,
            contraparte_id=contraparte_id,
            liquidez_dias=0 if produto is Produto.CARTAO else None,
            **extras,  # type: ignore[arg-type]
        )
        uow.commit()
        return garantia
