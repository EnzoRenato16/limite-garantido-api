"""Serviço da versão 0.1: tudo em memória, sem banco."""

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

from app.regras import (
    DIAS_PARA_EXECUCAO,
    LTV_POR_ATIVO,
    ORDEM_DE_LIQUIDEZ,
    arredondar_para_baixo,
    limite_da_garantia,
)

ZERO = Decimal("0.00")


class ErroDeNegocio(Exception):
    """Erro de regra de negócio, com código estável e status HTTP sugerido."""

    def __init__(self, codigo: str, mensagem: str, status: int = 409) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.status = status


@dataclass
class Garantia:
    id: str
    cliente_id: str
    tipo_ativo: str
    valor_de_mercado: Decimal
    estado: str = "bloqueada"  # bloqueada | liberada | executada


@dataclass
class Cliente:
    id: str
    divida: Decimal = ZERO
    dias_em_atraso: int = 0


@dataclass
class Servico:
    garantias: dict[str, Garantia] = field(default_factory=dict)
    clientes: dict[str, Cliente] = field(default_factory=dict)
    eventos: list[dict[str, object]] = field(default_factory=list)

    # ------------------------------------------------------------ consultas
    def _cliente(self, cliente_id: str) -> Cliente:
        return self.clientes.setdefault(cliente_id, Cliente(id=cliente_id))

    def _garantias_bloqueadas(self, cliente_id: str) -> list[Garantia]:
        return [
            g
            for g in self.garantias.values()
            if g.cliente_id == cliente_id and g.estado == "bloqueada"
        ]

    def limite_total(self, cliente_id: str) -> Decimal:
        return sum(
            (
                limite_da_garantia(g.tipo_ativo, g.valor_de_mercado)
                for g in self._garantias_bloqueadas(cliente_id)
            ),
            ZERO,
        )

    def posicao(self, cliente_id: str) -> dict[str, object]:
        cliente = self._cliente(cliente_id)
        total = self.limite_total(cliente_id)
        return {
            "cliente_id": cliente_id,
            "limite_total": total,
            "divida": cliente.divida,
            "limite_disponivel": max(total - cliente.divida, ZERO),
            "dias_em_atraso": cliente.dias_em_atraso,
        }

    def _registrar(self, tipo: str, **dados: object) -> None:
        self.eventos.append({"tipo": tipo, **dados})

    # ------------------------------------------------------------ garantias
    def bloquear_garantia(self, cliente_id: str, tipo_ativo: str, valor: Decimal) -> Garantia:
        if tipo_ativo not in LTV_POR_ATIVO:
            raise ErroDeNegocio("ativo_nao_aceito", f"Ativo não aceito: {tipo_ativo}", 422)
        if valor <= ZERO:
            raise ErroDeNegocio("valor_invalido", "O valor precisa ser positivo", 422)
        self._cliente(cliente_id)
        garantia = Garantia(
            id=str(uuid4()),
            cliente_id=cliente_id,
            tipo_ativo=tipo_ativo,
            valor_de_mercado=arredondar_para_baixo(valor),
        )
        self.garantias[garantia.id] = garantia
        self._registrar("garantia.bloqueada", garantia_id=garantia.id, cliente_id=cliente_id)
        self._registrar("limite.atualizado", **self.posicao(cliente_id))
        return garantia

    def _garantia(self, garantia_id: str) -> Garantia:
        if garantia_id not in self.garantias:
            raise ErroDeNegocio("garantia_nao_encontrada", "Garantia não encontrada", 404)
        return self.garantias[garantia_id]

    def remarcar(self, garantia_id: str, novo_valor: Decimal) -> Garantia:
        garantia = self._garantia(garantia_id)
        if garantia.estado != "bloqueada":
            raise ErroDeNegocio("garantia_nao_bloqueada", "Só dá para remarcar garantia bloqueada")
        garantia.valor_de_mercado = arredondar_para_baixo(novo_valor)
        posicao = self.posicao(garantia.cliente_id)
        self._registrar("limite.atualizado", **posicao)
        cliente = self._cliente(garantia.cliente_id)
        limite = self.limite_total(garantia.cliente_id)
        if cliente.divida > limite:
            self._registrar(
                "chamada_de_margem",
                cliente_id=cliente.id,
                deficit=cliente.divida - limite,
            )
        return garantia

    def liberar(self, garantia_id: str) -> Garantia:
        garantia = self._garantia(garantia_id)
        if garantia.estado != "bloqueada":
            raise ErroDeNegocio("garantia_nao_bloqueada", "A garantia não está bloqueada")
        cliente = self._cliente(garantia.cliente_id)
        limite_sem_ela = self.limite_total(cliente.id) - limite_da_garantia(
            garantia.tipo_ativo, garantia.valor_de_mercado
        )
        if cliente.divida > limite_sem_ela:
            raise ErroDeNegocio(
                "garantia_cobrindo_divida",
                "A garantia está cobrindo dívida em aberto e não pode ser liberada",
            )
        garantia.estado = "liberada"
        self._registrar("garantia.liberada", garantia_id=garantia.id, cliente_id=cliente.id)
        self._registrar("limite.atualizado", **self.posicao(cliente.id))
        return garantia

    # --------------------------------------------------------------- compras
    def autorizar_compra(self, cliente_id: str, valor: Decimal) -> dict[str, object]:
        if valor <= ZERO:
            raise ErroDeNegocio("valor_invalido", "O valor precisa ser positivo", 422)
        cliente = self._cliente(cliente_id)
        disponivel = max(self.limite_total(cliente_id) - cliente.divida, ZERO)
        if valor > disponivel:
            self._registrar("compra.negada", cliente_id=cliente_id, valor=valor)
            raise ErroDeNegocio(
                "limite_insuficiente",
                f"Limite disponível de R$ {disponivel} é menor que a compra de R$ {valor}",
                402,
            )
        cliente.divida += valor
        self._registrar("compra.aprovada", cliente_id=cliente_id, valor=valor)
        self._registrar("limite.atualizado", **self.posicao(cliente_id))
        return {"id": str(uuid4()), "cliente_id": cliente_id, "valor": valor, "estado": "aprovada"}

    # ---------------------------------------------------- pagamento e atraso
    def pagar(self, cliente_id: str, valor: Decimal) -> dict[str, object]:
        cliente = self._cliente(cliente_id)
        if valor <= ZERO or valor > cliente.divida:
            raise ErroDeNegocio("valor_invalido", "Pagamento precisa estar entre 0 e a dívida", 422)
        cliente.divida -= valor
        if cliente.divida == ZERO:
            cliente.dias_em_atraso = 0
            self._registrar("fatura.paga", cliente_id=cliente_id)
        self._registrar("limite.atualizado", **self.posicao(cliente_id))
        return self.posicao(cliente_id)

    def registrar_atraso(self, cliente_id: str, dias: int) -> dict[str, object]:
        cliente = self._cliente(cliente_id)
        if cliente.divida == ZERO:
            raise ErroDeNegocio("sem_divida", "Cliente sem dívida não entra em atraso")
        cliente.dias_em_atraso = dias
        self._registrar("fatura.em_atraso", cliente_id=cliente_id, dias=dias)
        if dias >= DIAS_PARA_EXECUCAO:
            self._executar(cliente)
        return self.posicao(cliente_id)

    def _executar(self, cliente: Cliente) -> None:
        """Vende garantias, das mais líquidas para as menos, até cobrir a dívida.

        O que sobrar da última garantia vendida volta a ficar bloqueado.
        """
        fila = sorted(
            self._garantias_bloqueadas(cliente.id),
            key=lambda g: ORDEM_DE_LIQUIDEZ.index(g.tipo_ativo),
        )
        for garantia in fila:
            if cliente.divida == ZERO:
                break
            usado = min(garantia.valor_de_mercado, cliente.divida)
            sobra = garantia.valor_de_mercado - usado
            cliente.divida -= usado
            garantia.estado = "executada"
            self._registrar(
                "garantia.executada",
                garantia_id=garantia.id,
                cliente_id=cliente.id,
                valor_executado=usado,
                sobra=sobra,
            )
            if sobra > ZERO:
                nova = Garantia(
                    id=str(uuid4()),
                    cliente_id=cliente.id,
                    tipo_ativo=garantia.tipo_ativo,
                    valor_de_mercado=sobra,
                )
                self.garantias[nova.id] = nova
                self._registrar("garantia.bloqueada", garantia_id=nova.id, cliente_id=cliente.id)
        if cliente.divida == ZERO:
            cliente.dias_em_atraso = 0
        self._registrar("limite.atualizado", **self.posicao(cliente.id))
