"""Repositórios SQL: traduzem entre o domínio (puro) e as tabelas."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Select,
    case,
    delete,
    func,
    select,
    type_coerce,
    union,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.aplicacao.erros import ConflitoDeConcorrencia, ContaJaExiste
from app.dominio.conta import (
    ChamadaDeMargem,
    Conta,
    EstadoChamada,
    EstadoConta,
    EstadoFatura,
    Fatura,
    MotivoBloqueio,
)
from app.dominio.dinheiro import Dinheiro
from app.dominio.garantia import ESTADOS_ATIVOS, EstadoGarantia, Garantia
from app.dominio.ids import novo_id
from app.dominio.ledger import ChaveDeSaldo, ContaContabil, Saldos, Sentido
from app.dominio.parceiro import Parceiro
from app.dominio.politica import PoliticaRisco, TipoAtivo
from app.dominio.stress import ContaNaCarteira, GarantiaNaCarteira
from app.infra.banco.modelos import (
    ChamadaDeMargemModelo,
    ChaveDeIdempotenciaModelo,
    CompraModelo,
    ContaModelo,
    EntregaDeWebhookModelo,
    EventoModelo,
    FaturaModelo,
    GarantiaModelo,
    LancamentoModelo,
    PagamentoModelo,
    ParceiroModelo,
    PartidaModelo,
)

ESTADO_DE_ENTREGA_PENDENTE = "pendente"
ESTADO_DE_ENTREGA_ENTREGUE = "entregue"
ESTADO_DE_ENTREGA_MORTA = "morta"

_GARANTIAS_VIVAS = [
    EstadoGarantia.PENDENTE.value,
    EstadoGarantia.BLOQUEADA.value,
    EstadoGarantia.PARCIALMENTE_EXECUTADA.value,
]
_GARANTIAS_ATIVAS = [estado.value for estado in ESTADOS_ATIVOS]
_FATURAS_EM_ABERTO = [EstadoFatura.FECHADA.value, EstadoFatura.EM_ATRASO.value]
_RECEBIVEIS = [
    ContaContabil.RECEBIVEIS_A_FATURAR.value,
    ContaContabil.RECEBIVEIS_FATURADOS.value,
]

# Débitos menos créditos, em centavos, direto no banco.
_valor_em_centavos = type_coerce(PartidaModelo.valor, BigInteger)
_DEBITOS_MENOS_CREDITOS = func.coalesce(
    func.sum(
        case(
            (PartidaModelo.sentido == Sentido.DEBITO.value, _valor_em_centavos),
            else_=-_valor_em_centavos,
        )
    ),
    0,
)


class RepositorioDeContasSql:
    def __init__(self, sessao: Session, eventos_gravados: list[str] | None = None) -> None:
        self._sessao = sessao
        self._eventos_gravados = eventos_gravados if eventos_gravados is not None else []

    # --------------------------------------------------------------- leitura
    def obter(self, parceiro_id: str, conta_id: str) -> Conta | None:
        return self._carregar(
            select(ContaModelo).where(
                ContaModelo.id == conta_id, ContaModelo.parceiro_id == parceiro_id
            )
        )

    def obter_para_atualizar(self, parceiro_id: str, conta_id: str) -> Conta | None:
        # Trava de linha: a segunda transação espera a primeira terminar e só então lê o
        # saldo. No Postgres vira FOR NO KEY UPDATE, que é o modo de quem não vai mudar a
        # chave: bloqueia outra escrita na conta, mas não as checagens de chave
        # estrangeira. No SQLite a cláusula é ignorada; lá quem protege é a versão.
        return self._carregar(
            select(ContaModelo)
            .where(ContaModelo.id == conta_id, ContaModelo.parceiro_id == parceiro_id)
            .with_for_update(key_share=True)
        )

    def obter_pela_garantia_para_atualizar(
        self, garantia_id: str, parceiro_id: str | None = None
    ) -> Conta | None:
        consulta = select(GarantiaModelo.conta_id, GarantiaModelo.parceiro_id).where(
            GarantiaModelo.id == garantia_id
        )
        if parceiro_id is not None:
            consulta = consulta.where(GarantiaModelo.parceiro_id == parceiro_id)
        linha = self._sessao.execute(consulta).first()
        if linha is None:
            return None
        conta = self.obter_para_atualizar(linha.parceiro_id, linha.conta_id)
        if conta is None:
            return None
        if not any(garantia.id == garantia_id for garantia in conta.garantias):
            # Garantia já encerrada (liberada ou executada): entra só para o domínio
            # poder responder com o erro de transição certo.
            modelo = self._sessao.get(GarantiaModelo, garantia_id)
            if modelo is not None:
                conta.garantias.append(_garantia_do_modelo(modelo))
        return conta

    def _carregar(self, consulta: Select[ContaModelo]) -> Conta | None:
        # populate_existing: vale o que está no banco, não um objeto que a sessão já tinha.
        modelo = self._sessao.execute(
            consulta.execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if modelo is None:
            return None
        garantias = self._sessao.scalars(
            select(GarantiaModelo)
            .where(
                GarantiaModelo.conta_id == modelo.id, GarantiaModelo.estado.in_(_GARANTIAS_VIVAS)
            )
            .order_by(GarantiaModelo.id)
        ).all()
        faturas = self._sessao.scalars(
            select(FaturaModelo).where(
                FaturaModelo.conta_id == modelo.id, FaturaModelo.estado.in_(_FATURAS_EM_ABERTO)
            )
        ).all()
        chamadas = self._sessao.scalars(
            select(ChamadaDeMargemModelo).where(
                ChamadaDeMargemModelo.conta_id == modelo.id,
                ChamadaDeMargemModelo.estado == EstadoChamada.ABERTA.value,
            )
        ).all()
        return Conta(
            id=modelo.id,
            parceiro_id=modelo.parceiro_id,
            cliente_ref=modelo.cliente_ref,
            criada_em=modelo.criada_em,
            estado=EstadoConta(modelo.estado),
            motivo_bloqueio=MotivoBloqueio(modelo.motivo_bloqueio)
            if modelo.motivo_bloqueio
            else None,
            garantias=[_garantia_do_modelo(g) for g in garantias],
            faturas=[_fatura_do_modelo(f) for f in faturas],
            chamadas=[_chamada_do_modelo(c) for c in chamadas],
            saldos=self._saldos(modelo.id),
            versao=modelo.versao,
        )

    def _saldos(self, conta_id: str) -> Saldos:
        linhas = self._sessao.execute(
            select(PartidaModelo.conta_contabil, PartidaModelo.garantia_id, _DEBITOS_MENOS_CREDITOS)
            .where(PartidaModelo.conta_id == conta_id)
            .group_by(PartidaModelo.conta_contabil, PartidaModelo.garantia_id)
        ).all()
        liquido: dict[ChaveDeSaldo, Dinheiro] = {
            (ContaContabil(conta_contabil), garantia_id): Dinheiro(int(total))
            for conta_contabil, garantia_id, total in linhas
        }
        return Saldos(liquido)

    # --------------------------------------------------------------- escrita
    def adicionar(self, conta: Conta) -> None:
        self._sessao.add(
            ContaModelo(
                id=conta.id,
                parceiro_id=conta.parceiro_id,
                cliente_ref=conta.cliente_ref,
                estado=conta.estado.value,
                motivo_bloqueio=None,
                versao=conta.versao,
                criada_em=conta.criada_em,
            )
        )
        try:
            self._sessao.flush()
        except IntegrityError as erro:
            raise ContaJaExiste("Já existe uma conta para este cliente") from erro

    def salvar(self, conta: Conta, parceiro: Parceiro) -> None:
        # Trava otimista: só grava se ninguém mexeu na conta desde a leitura.
        resultado = self._sessao.execute(
            update(ContaModelo)
            .where(ContaModelo.id == conta.id, ContaModelo.versao == conta.versao)
            .values(
                estado=conta.estado.value,
                motivo_bloqueio=conta.motivo_bloqueio.value if conta.motivo_bloqueio else None,
                versao=conta.versao + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if resultado.rowcount != 1:  # type: ignore[attr-defined]
            raise ConflitoDeConcorrencia(
                "A conta foi alterada por outra operação ao mesmo tempo. Tente de novo.",
                conta_id=conta.id,
            )
        conta.versao += 1

        for garantia in conta.garantias:
            self._sessao.merge(_modelo_da_garantia(garantia, conta.parceiro_id))
        for fatura in conta.faturas:
            self._sessao.merge(_modelo_da_fatura(fatura, conta.parceiro_id))
        for chamada in conta.chamadas:
            self._sessao.merge(_modelo_da_chamada(chamada, conta.parceiro_id))
        # Garantias, faturas e pagamentos precisam existir antes das partidas que os citam.
        self._sessao.flush()

        pendencias = conta.pendencias
        for compra in pendencias.compras:
            self._sessao.add(
                CompraModelo(
                    id=compra.id,
                    conta_id=conta.id,
                    parceiro_id=conta.parceiro_id,
                    valor=compra.valor,
                    descricao=compra.descricao,
                    estado=compra.estado.value,
                    motivo_negacao=compra.motivo_negacao.value if compra.motivo_negacao else None,
                    criada_em=compra.criada_em,
                )
            )
        for pagamento in pendencias.pagamentos:
            self._sessao.add(
                PagamentoModelo(
                    id=pagamento.id,
                    conta_id=conta.id,
                    parceiro_id=conta.parceiro_id,
                    fatura_id=pagamento.fatura_id,
                    valor=pagamento.valor,
                    valor_na_fatura=pagamento.valor_na_fatura,
                    valor_antecipado=pagamento.valor_antecipado,
                    criado_em=pagamento.criado_em,
                )
            )
        for lancamento in pendencias.lancamentos:
            cabecalho = LancamentoModelo(
                id=lancamento.id,
                conta_id=conta.id,
                parceiro_id=conta.parceiro_id,
                tipo=lancamento.tipo.value,
                referencia_id=lancamento.referencia_id,
                descricao=lancamento.descricao,
                criado_em=lancamento.criado_em,
            )
            self._sessao.add(cabecalho)
            self._sessao.flush()  # para obter a sequência
            self._sessao.add_all(
                PartidaModelo(
                    lancamento_sequencia=cabecalho.sequencia,
                    conta_id=conta.id,
                    parceiro_id=conta.parceiro_id,
                    conta_contabil=partida.conta_contabil.value,
                    sentido=partida.sentido.value,
                    valor=partida.valor,
                    garantia_id=partida.garantia_id,
                )
                for partida in lancamento.partidas
            )
        # Outbox: o evento e a entrega do webhook entram na mesma transação da operação.
        for evento in pendencias.eventos:
            self._sessao.add(
                EventoModelo(
                    id=evento.id,
                    parceiro_id=conta.parceiro_id,
                    conta_id=conta.id,
                    tipo=evento.tipo.value,
                    dados=dict(evento.dados),
                    ocorrido_em=evento.ocorrido_em,
                )
            )
            if parceiro.webhook_url:
                self._sessao.flush()
                self._sessao.add(
                    EntregaDeWebhookModelo(
                        id=novo_id("ent"),
                        evento_id=evento.id,
                        parceiro_id=conta.parceiro_id,
                        url=parceiro.webhook_url,
                        estado=ESTADO_DE_ENTREGA_PENDENTE,
                        tentativas=0,
                        proxima_tentativa_em=evento.ocorrido_em,
                        criado_em=evento.ocorrido_em,
                    )
                )
        self._sessao.flush()
        self._eventos_gravados.extend(evento.tipo.value for evento in pendencias.eventos)
        pendencias.limpar()

    # ---------------------------------------------------------- agregações
    def carteira_do_parceiro(self, parceiro_id: str) -> Dinheiro:
        total = self._sessao.execute(
            select(_DEBITOS_MENOS_CREDITOS).where(
                PartidaModelo.parceiro_id == parceiro_id,
                PartidaModelo.conta_contabil.in_(_RECEBIVEIS),
            )
        ).scalar_one()
        return Dinheiro(int(total))

    def carteira_para_stress(self, parceiro_id: str) -> list[ContaNaCarteira]:
        dividas: dict[str, int] = {
            conta_id: int(total)
            for conta_id, total in self._sessao.execute(
                select(PartidaModelo.conta_id, _DEBITOS_MENOS_CREDITOS)
                .where(
                    PartidaModelo.parceiro_id == parceiro_id,
                    PartidaModelo.conta_contabil.in_(_RECEBIVEIS),
                )
                .group_by(PartidaModelo.conta_id)
            )
        }
        garantias: dict[str, list[GarantiaNaCarteira]] = {}
        linhas = self._sessao.execute(
            select(GarantiaModelo.conta_id, GarantiaModelo.tipo_ativo, _DEBITOS_MENOS_CREDITOS)
            .join(PartidaModelo, PartidaModelo.garantia_id == GarantiaModelo.id)
            .where(
                GarantiaModelo.parceiro_id == parceiro_id,
                GarantiaModelo.estado.in_(_GARANTIAS_ATIVAS),
                PartidaModelo.conta_contabil == ContaContabil.GARANTIAS_CUSTODIADAS.value,
            )
            .group_by(GarantiaModelo.id, GarantiaModelo.conta_id, GarantiaModelo.tipo_ativo)
            .order_by(GarantiaModelo.id)
        ).all()
        for conta_id, tipo_ativo, valor in linhas:
            garantias.setdefault(conta_id, []).append(
                GarantiaNaCarteira(TipoAtivo(tipo_ativo), Dinheiro(int(valor)))
            )
        return [
            ContaNaCarteira(
                conta_id=conta_id,
                divida=Dinheiro(dividas.get(conta_id, 0)),
                garantias=tuple(garantias.get(conta_id, ())),
            )
            for conta_id in sorted(set(dividas) | set(garantias))
        ]

    def valores_de_mercado(self, garantia_ids: Sequence[str]) -> dict[str, Dinheiro]:
        if not garantia_ids:
            return {}
        linhas = self._sessao.execute(
            select(PartidaModelo.garantia_id, _DEBITOS_MENOS_CREDITOS)
            .where(
                PartidaModelo.garantia_id.in_(garantia_ids),
                PartidaModelo.conta_contabil == ContaContabil.GARANTIAS_CUSTODIADAS.value,
            )
            .group_by(PartidaModelo.garantia_id)
        ).all()
        valores = {garantia_id: Dinheiro(int(total)) for garantia_id, total in linhas}
        return {
            garantia_id: valores.get(garantia_id, Dinheiro.zero()) for garantia_id in garantia_ids
        }

    def contas_com_garantia_a_marcar(
        self, hoje: date, parceiro_id: str | None = None
    ) -> Sequence[tuple[str, str]]:
        consulta = (
            select(GarantiaModelo.parceiro_id, GarantiaModelo.conta_id)
            .where(GarantiaModelo.estado.in_(_GARANTIAS_ATIVAS), GarantiaModelo.marcada_em < hoje)
            .distinct()
            .order_by(GarantiaModelo.conta_id)
        )
        if parceiro_id is not None:
            consulta = consulta.where(GarantiaModelo.parceiro_id == parceiro_id)
        return [(linha[0], linha[1]) for linha in self._sessao.execute(consulta)]

    def contas_com_prazo_vencido(
        self, hoje: date, parceiro_id: str | None = None
    ) -> Sequence[tuple[str, str]]:
        faturas = select(FaturaModelo.parceiro_id, FaturaModelo.conta_id).where(
            FaturaModelo.estado.in_(_FATURAS_EM_ABERTO), FaturaModelo.vencimento < hoje
        )
        chamadas = select(ChamadaDeMargemModelo.parceiro_id, ChamadaDeMargemModelo.conta_id).where(
            ChamadaDeMargemModelo.estado == EstadoChamada.ABERTA.value,
            ChamadaDeMargemModelo.prazo_ate < hoje,
        )
        if parceiro_id is not None:
            faturas = faturas.where(FaturaModelo.parceiro_id == parceiro_id)
            chamadas = chamadas.where(ChamadaDeMargemModelo.parceiro_id == parceiro_id)
        linhas = self._sessao.execute(union(faturas, chamadas)).all()
        return sorted((linha[0], linha[1]) for linha in linhas)


class RepositorioDeParceirosSql:
    def __init__(self, sessao: Session) -> None:
        self._sessao = sessao

    def adicionar(self, parceiro: Parceiro, chave_prefixo: str, chave_hash: str) -> None:
        self._sessao.add(
            ParceiroModelo(
                id=parceiro.id,
                nome=parceiro.nome,
                chave_prefixo=chave_prefixo,
                chave_hash=chave_hash,
                politica=parceiro.politica.para_dict(),
                webhook_url=parceiro.webhook_url,
                webhook_segredo=parceiro.webhook_segredo,
                limite_de_requisicoes_por_minuto=parceiro.limite_de_requisicoes_por_minuto,
                ativo=parceiro.ativo,
                criado_em=parceiro.criado_em,
            )
        )
        self._sessao.flush()

    def obter(self, parceiro_id: str) -> Parceiro | None:
        modelo = self._sessao.get(ParceiroModelo, parceiro_id)
        return None if modelo is None else _parceiro_do_modelo(modelo)

    def obter_pelo_prefixo(self, chave_prefixo: str) -> tuple[Parceiro, str] | None:
        """Devolve o parceiro e o hash da chave dele, para quem autentica conferir."""
        modelo = self._sessao.execute(
            select(ParceiroModelo).where(ParceiroModelo.chave_prefixo == chave_prefixo)
        ).scalar_one_or_none()
        return None if modelo is None else (_parceiro_do_modelo(modelo), modelo.chave_hash)

    def listar(self) -> list[Parceiro]:
        modelos = self._sessao.scalars(select(ParceiroModelo).order_by(ParceiroModelo.id)).all()
        return [_parceiro_do_modelo(modelo) for modelo in modelos]

    def salvar(self, parceiro: Parceiro) -> None:
        modelo = self._sessao.get(ParceiroModelo, parceiro.id)
        if modelo is None:
            raise LookupError(f"Parceiro {parceiro.id} não existe")
        modelo.nome = parceiro.nome
        modelo.politica = parceiro.politica.para_dict()
        modelo.webhook_url = parceiro.webhook_url
        modelo.webhook_segredo = parceiro.webhook_segredo
        modelo.limite_de_requisicoes_por_minuto = parceiro.limite_de_requisicoes_por_minuto
        modelo.ativo = parceiro.ativo
        self._sessao.flush()

    def trocar_chave(self, parceiro_id: str, chave_prefixo: str, chave_hash: str) -> None:
        self._sessao.execute(
            update(ParceiroModelo)
            .where(ParceiroModelo.id == parceiro_id)
            .values(chave_prefixo=chave_prefixo, chave_hash=chave_hash)
        )

    def travar(self, parceiro_id: str) -> None:
        # FOR NO KEY UPDATE, e não FOR UPDATE: a transação já tem um KEY SHARE nesta
        # linha (a chave de idempotência aponta para o parceiro). Pedir FOR UPDATE por
        # cima disso faz duas compras simultâneas se travarem uma na outra (deadlock).
        self._sessao.execute(
            select(ParceiroModelo.id)
            .where(ParceiroModelo.id == parceiro_id)
            .with_for_update(key_share=True)
        )


class RepositorioDeIdempotencia:
    def __init__(self, sessao: Session) -> None:
        self._sessao = sessao

    def obter(self, parceiro_id: str, chave: str) -> ChaveDeIdempotenciaModelo | None:
        modelo = self._sessao.get(ChaveDeIdempotenciaModelo, (parceiro_id, chave))
        if modelo is not None:
            self._sessao.refresh(modelo)
        return modelo

    def reservar(
        self,
        parceiro_id: str,
        chave: str,
        impressao_digital: str,
        agora: datetime,
        expira_em: datetime,
    ) -> ChaveDeIdempotenciaModelo:
        """Insere a chave como "em andamento". Levanta IntegrityError se já existir."""
        modelo = ChaveDeIdempotenciaModelo(
            parceiro_id=parceiro_id,
            chave=chave,
            impressao_digital=impressao_digital,
            status_http=None,
            corpo=None,
            criado_em=agora,
            expira_em=expira_em,
        )
        self._sessao.add(modelo)
        self._sessao.flush()
        return modelo

    def apagar(self, modelo: ChaveDeIdempotenciaModelo) -> None:
        self._sessao.delete(modelo)
        self._sessao.flush()

    def apagar_expiradas(self, agora: datetime) -> int:
        resultado = self._sessao.execute(
            delete(ChaveDeIdempotenciaModelo).where(ChaveDeIdempotenciaModelo.expira_em <= agora)
        )
        return int(resultado.rowcount)  # type: ignore[attr-defined]


# ------------------------------------------------------------------ tradutores
def _parceiro_do_modelo(modelo: ParceiroModelo) -> Parceiro:
    return Parceiro(
        id=modelo.id,
        nome=modelo.nome,
        politica=PoliticaRisco.de_dict(modelo.politica),
        limite_de_requisicoes_por_minuto=modelo.limite_de_requisicoes_por_minuto,
        criado_em=modelo.criado_em,
        webhook_url=modelo.webhook_url,
        webhook_segredo=modelo.webhook_segredo,
        ativo=modelo.ativo,
    )


def _garantia_do_modelo(modelo: GarantiaModelo) -> Garantia:
    return Garantia(
        id=modelo.id,
        conta_id=modelo.conta_id,
        tipo_ativo=TipoAtivo(modelo.tipo_ativo),
        valor_aplicado=modelo.valor_aplicado,
        estado=EstadoGarantia(modelo.estado),
        criada_em=modelo.criada_em,
        marcada_em=modelo.marcada_em,
    )


def _modelo_da_garantia(garantia: Garantia, parceiro_id: str) -> GarantiaModelo:
    return GarantiaModelo(
        id=garantia.id,
        conta_id=garantia.conta_id,
        parceiro_id=parceiro_id,
        tipo_ativo=garantia.tipo_ativo.value,
        valor_aplicado=garantia.valor_aplicado,
        estado=garantia.estado.value,
        marcada_em=garantia.marcada_em,
        criada_em=garantia.criada_em,
    )


def _fatura_do_modelo(modelo: FaturaModelo) -> Fatura:
    return Fatura(
        id=modelo.id,
        conta_id=modelo.conta_id,
        valor_fechado=modelo.valor_fechado,
        fechada_em=modelo.fechada_em,
        vencimento=modelo.vencimento,
        estado=EstadoFatura(modelo.estado),
        encerrada_em=modelo.encerrada_em,
    )


def _modelo_da_fatura(fatura: Fatura, parceiro_id: str) -> FaturaModelo:
    return FaturaModelo(
        id=fatura.id,
        conta_id=fatura.conta_id,
        parceiro_id=parceiro_id,
        valor_fechado=fatura.valor_fechado,
        vencimento=fatura.vencimento,
        estado=fatura.estado.value,
        fechada_em=fatura.fechada_em,
        encerrada_em=fatura.encerrada_em,
    )


def _chamada_do_modelo(modelo: ChamadaDeMargemModelo) -> ChamadaDeMargem:
    return ChamadaDeMargem(
        id=modelo.id,
        conta_id=modelo.conta_id,
        deficit_inicial=modelo.deficit_inicial,
        aberta_em=modelo.aberta_em,
        prazo_ate=modelo.prazo_ate,
        estado=EstadoChamada(modelo.estado),
        encerrada_em=modelo.encerrada_em,
    )


def _modelo_da_chamada(chamada: ChamadaDeMargem, parceiro_id: str) -> ChamadaDeMargemModelo:
    return ChamadaDeMargemModelo(
        id=chamada.id,
        conta_id=chamada.conta_id,
        parceiro_id=parceiro_id,
        deficit_inicial=chamada.deficit_inicial,
        prazo_ate=chamada.prazo_ate,
        estado=chamada.estado.value,
        aberta_em=chamada.aberta_em,
        encerrada_em=chamada.encerrada_em,
    )
