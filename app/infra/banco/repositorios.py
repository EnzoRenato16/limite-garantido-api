"""Repositórios SQL: traduzem entre o domínio (puro) e as tabelas."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

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

from app.aplicacao.erros import ConflitoDeConcorrencia, ContaJaExiste, RespostaDuplicada
from app.aplicacao.portas import LocalDaSolicitacao, StatusDaResposta
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
from app.dominio.emprestimo import Emprestimo, EstadoEmprestimo, Parcela
from app.dominio.garantia import (
    Garantia,
    InformacoesDaOperadora,
    Solicitacao,
    TipoSolicitacao,
)
from app.dominio.gravame import ESTADOS_FINAIS, EstadoGravame, Gravame
from app.dominio.ids import novo_id
from app.dominio.ledger import ChaveDeSaldo, ContaContabil, Saldos, Sentido
from app.dominio.parceiro import Parceiro
from app.dominio.politica import PoliticaRisco, TipoAtivo
from app.dominio.regulacao import Infraestrutura, Instrumento, Produto, Trilho
from app.dominio.stress import ContaNaCarteira, GarantiaNaCarteira
from app.infra.banco.modelos import (
    ChamadaDeMargemModelo,
    ChaveDeIdempotenciaModelo,
    CompraModelo,
    ContaModelo,
    EmprestimoModelo,
    EntregaDeWebhookModelo,
    EventoModelo,
    FaturaModelo,
    GarantiaModelo,
    GravameModelo,
    LancamentoModelo,
    PagamentoModelo,
    ParceiroModelo,
    ParcelaModelo,
    PartidaModelo,
    RespostaDeCustodianteModelo,
    SolicitacaoDeCustodiaModelo,
)

ESTADO_DE_ENTREGA_PENDENTE = "pendente"
ESTADO_DE_ENTREGA_ENTREGUE = "entregue"
ESTADO_DE_ENTREGA_MORTA = "morta"

# Tudo o que ainda pode mudar entra no agregado; os gravames encerrados ficam de fora.
_GRAVAMES_VIVOS = [e.value for e in EstadoGravame if e not in ESTADOS_FINAIS]
# Garantia que ainda onera o ativo a favor da SCD (vigente ou liberada para excussão).
_GRAVAMES_ONERADOS = [EstadoGravame.REGISTRADO.value, EstadoGravame.LIBERADO_PARA_EXCUSSAO.value]
_EMPRESTIMOS_ABERTOS = [EstadoEmprestimo.ATIVO.value, EstadoEmprestimo.EM_ATRASO.value]
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
            gravame = self._sessao.scalars(
                select(GravameModelo).where(GravameModelo.garantia_id == garantia_id)
            ).one_or_none()
            if modelo is not None and gravame is not None:
                conta.garantias.append(_garantia_do_modelo(modelo, gravame, {}))
        return conta

    def _carregar(self, consulta: Select[ContaModelo]) -> Conta | None:
        # populate_existing: vale o que está no banco, não um objeto que a sessão já tinha.
        modelo = self._sessao.execute(
            consulta.execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if modelo is None:
            return None
        garantias = self._sessao.execute(
            select(GarantiaModelo, GravameModelo)
            .join(GravameModelo, GravameModelo.garantia_id == GarantiaModelo.id)
            .where(GarantiaModelo.conta_id == modelo.id, GravameModelo.estado.in_(_GRAVAMES_VIVOS))
            .order_by(GarantiaModelo.id)
        ).all()
        ids_de_pedidos = [g.solicitacao_id for g, _ in garantias if g.solicitacao_id]
        pedidos = (
            {
                pedido.id: pedido
                for pedido in self._sessao.scalars(
                    select(SolicitacaoDeCustodiaModelo).where(
                        SolicitacaoDeCustodiaModelo.id.in_(ids_de_pedidos)
                    )
                )
            }
            if ids_de_pedidos
            else {}
        )
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
            garantias=[_garantia_do_modelo(g, v, pedidos) for g, v in garantias],
            faturas=[_fatura_do_modelo(f) for f in faturas],
            chamadas=[_chamada_do_modelo(c) for c in chamadas],
            emprestimos=self._emprestimos_abertos(modelo.id),
            saldos=self._saldos(modelo.id),
            versao=modelo.versao,
        )

    def _emprestimos_abertos(self, conta_id: str) -> list[Emprestimo]:
        modelos = self._sessao.scalars(
            select(EmprestimoModelo)
            .where(
                EmprestimoModelo.conta_id == conta_id,
                EmprestimoModelo.estado.in_(_EMPRESTIMOS_ABERTOS),
            )
            .order_by(EmprestimoModelo.id)
        ).all()
        if not modelos:
            return []
        ids = [m.id for m in modelos]
        parcelas = self._sessao.scalars(
            select(ParcelaModelo)
            .where(ParcelaModelo.emprestimo_id.in_(ids))
            .order_by(ParcelaModelo.emprestimo_id, ParcelaModelo.numero)
        ).all()
        garantias = self._sessao.execute(
            select(GarantiaModelo.emprestimo_id, GarantiaModelo.id)
            .where(GarantiaModelo.emprestimo_id.in_(ids))
            .order_by(GarantiaModelo.id)
        ).all()
        return [
            Emprestimo(
                id=m.id,
                conta_id=m.conta_id,
                principal=m.principal,
                taxa_ao_mes=Decimal(m.taxa_ao_mes),
                parcelas=[_parcela_do_modelo(p) for p in parcelas if p.emprestimo_id == m.id],
                garantia_ids=tuple(g_id for e_id, g_id in garantias if e_id == m.id),
                contratado_em=m.contratado_em,
                estado=EstadoEmprestimo(m.estado),
                dias_minimos_execucao=m.dias_minimos_execucao,
                aviso_45_em=m.aviso_45_em,
                aviso_10_em=m.aviso_10_em,
            )
            for m in modelos
        ]

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

        for emprestimo in conta.emprestimos:
            self._sessao.merge(_modelo_do_emprestimo(emprestimo, conta.parceiro_id))
            for parcela in emprestimo.parcelas:
                self._sessao.merge(_modelo_da_parcela(parcela, emprestimo.id))
        self._sessao.flush()
        for garantia in conta.garantias:
            self._sessao.merge(_modelo_da_garantia(garantia, conta.parceiro_id))
        self._sessao.flush()
        for garantia in conta.garantias:
            self._sessao.merge(_modelo_do_gravame(garantia, conta.parceiro_id))
        self._sessao.flush()
        for garantia in conta.garantias:
            # O pedido é gravado uma vez, quando nasce, e não muda mais.
            pedido = garantia.solicitacao
            if (
                pedido is not None
                and self._sessao.get(SolicitacaoDeCustodiaModelo, pedido.id) is None
            ):
                assert garantia.contraparte_id is not None  # noqa: S101 - só a de fora pede
                self._sessao.add(
                    SolicitacaoDeCustodiaModelo(
                        id=pedido.id,
                        garantia_id=garantia.id,
                        conta_id=conta.id,
                        parceiro_id=conta.parceiro_id,
                        custodiante_id=garantia.contraparte_id,
                        tipo=pedido.tipo.value,
                        criada_em=pedido.criada_em,
                        prazo_ate=pedido.prazo_ate,
                    )
                )
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
            .join(GravameModelo, GravameModelo.garantia_id == GarantiaModelo.id)
            .where(
                GarantiaModelo.parceiro_id == parceiro_id,
                GarantiaModelo.produto == Produto.CARTAO.value,
                GravameModelo.estado == EstadoGravame.REGISTRADO.value,
                GravameModelo.baixa_pedida_em.is_(None),
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
            .join(GravameModelo, GravameModelo.garantia_id == GarantiaModelo.id)
            .where(
                GravameModelo.estado == EstadoGravame.REGISTRADO.value,
                GravameModelo.baixa_pedida_em.is_(None),
                GarantiaModelo.marcada_em < hoje,
            )
            .distinct()
            .order_by(GarantiaModelo.conta_id)
        )
        if parceiro_id is not None:
            consulta = consulta.where(GarantiaModelo.parceiro_id == parceiro_id)
        return [(linha[0], linha[1]) for linha in self._sessao.execute(consulta)]

    def contas_com_baixa_pendente(self) -> list[tuple[str, str]]:
        """Pares (parceiro_id, conta_id) com baixa de gravame pedida e ainda não feita."""
        consulta = (
            select(GravameModelo.parceiro_id, GravameModelo.conta_id)
            .where(
                GravameModelo.baixa_pedida_em.is_not(None),
                GravameModelo.estado.in_(
                    [EstadoGravame.REGISTRADO.value, EstadoGravame.EXECUTADO.value]
                ),
            )
            .distinct()
            .order_by(GravameModelo.parceiro_id, GravameModelo.conta_id)
        )
        return [(linha[0], linha[1]) for linha in self._sessao.execute(consulta)]

    def contas_com_emprestimo_a_avaliar(
        self, hoje: date, parceiro_id: str | None = None
    ) -> list[tuple[str, str]]:
        """Pares (parceiro_id, conta_id) com empréstimo aberto e parcela vencida em aberto."""
        consulta = (
            select(EmprestimoModelo.parceiro_id, EmprestimoModelo.conta_id)
            .join(ParcelaModelo, ParcelaModelo.emprestimo_id == EmprestimoModelo.id)
            .where(
                EmprestimoModelo.estado.in_(_EMPRESTIMOS_ABERTOS),
                ParcelaModelo.paga_em.is_(None),
                ParcelaModelo.vencimento < hoje,
            )
            .distinct()
            .order_by(EmprestimoModelo.parceiro_id, EmprestimoModelo.conta_id)
        )
        if parceiro_id is not None:
            consulta = consulta.where(EmprestimoModelo.parceiro_id == parceiro_id)
        return [(linha[0], linha[1]) for linha in self._sessao.execute(consulta)]

    # ------------------------------------------------------------- custódia
    def localizar_solicitacao(self, solicitacao_id: str) -> LocalDaSolicitacao | None:
        modelo = self._sessao.get(SolicitacaoDeCustodiaModelo, solicitacao_id)
        if modelo is None or modelo.tipo != TipoSolicitacao.ENTRADA.value:
            # Pedidos de liberação e execução de antes da migração 0003 são só histórico.
            return None
        return LocalDaSolicitacao(
            solicitacao_id=modelo.id,
            parceiro_id=modelo.parceiro_id,
            conta_id=modelo.conta_id,
            garantia_id=modelo.garantia_id,
            custodiante_id=modelo.custodiante_id,
            tipo=TipoSolicitacao(modelo.tipo),
        )

    def garantias_aguardando_contraparte(
        self, limite: int, parceiro_id: str | None = None
    ) -> list[tuple[str, str, str]]:
        """Trios (parceiro_id, conta_id, garantia_id) com gravame `solicitado`, os mais
        antigos primeiro, até `limite`."""
        consulta = (
            select(GravameModelo.parceiro_id, GravameModelo.conta_id, GravameModelo.garantia_id)
            .where(GravameModelo.estado == EstadoGravame.SOLICITADO.value)
            .order_by(GravameModelo.solicitado_em, GravameModelo.id)
            .limit(limite)
        )
        if parceiro_id is not None:
            consulta = consulta.where(GravameModelo.parceiro_id == parceiro_id)
        return [(linha[0], linha[1], linha[2]) for linha in self._sessao.execute(consulta)]

    def antecipar_prazo(self, solicitacao_id: str, prazo_ate: datetime) -> None:
        self._sessao.execute(
            update(SolicitacaoDeCustodiaModelo)
            .where(SolicitacaoDeCustodiaModelo.id == solicitacao_id)
            .values(prazo_ate=prazo_ate)
        )

    def resposta_ja_registrada(self, custodiante_id: str, evento_id: str) -> bool:
        return (
            self._sessao.get(RespostaDeCustodianteModelo, (custodiante_id, evento_id)) is not None
        )

    def registrar_resposta(
        self,
        *,
        custodiante_id: str,
        evento_id: str,
        solicitacao_id: str,
        garantia_id: str,
        parceiro_id: str,
        status: StatusDaResposta,
        canal: str,
        resultado: str,
        recebida_em: datetime,
        valor_obtido: Dinheiro | None = None,
        motivo: str | None = None,
    ) -> None:
        """Grava a resposta. Levanta `RespostaDuplicada` se o evento já foi registrado.

        A inserção roda num savepoint: a duplicata desfaz só ela, e a transação segue.
        """
        try:
            with self._sessao.begin_nested():
                self._sessao.add(
                    RespostaDeCustodianteModelo(
                        custodiante_id=custodiante_id,
                        evento_id=evento_id,
                        solicitacao_id=solicitacao_id,
                        garantia_id=garantia_id,
                        parceiro_id=parceiro_id,
                        status=status.value,
                        canal=canal,
                        resultado=resultado,
                        valor_obtido=valor_obtido,
                        motivo=motivo,
                        recebida_em=recebida_em,
                    )
                )
        except IntegrityError:
            # Só a chave (custodiante, evento) repetida é duplicata. Qualquer outra
            # violação (pedido inexistente, campo inválido) é erro e sobe como erro.
            if self.resposta_ja_registrada(custodiante_id, evento_id):
                raise RespostaDuplicada(
                    "Esta resposta do custodiante já foi recebida", evento_id=evento_id
                ) from None
            raise

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


def _garantia_do_modelo(
    modelo: GarantiaModelo,
    gravame: GravameModelo,
    pedidos: dict[str, SolicitacaoDeCustodiaModelo],
) -> Garantia:
    pedido = pedidos.get(modelo.solicitacao_id) if modelo.solicitacao_id else None
    informacoes = None
    if modelo.informacoes_valor_disponivel is not None:
        assert modelo.informacoes_tributos is not None  # noqa: S101 - gravados juntos
        assert modelo.informacoes_recebidas_em is not None  # noqa: S101
        informacoes = InformacoesDaOperadora(
            modelo.informacoes_valor_disponivel,
            modelo.informacoes_tributos,
            modelo.informacoes_recebidas_em,
        )
    return Garantia(
        id=modelo.id,
        conta_id=modelo.conta_id,
        tipo_ativo=TipoAtivo(modelo.tipo_ativo),
        valor_aplicado=modelo.valor_aplicado,
        criada_em=modelo.criada_em,
        marcada_em=modelo.marcada_em,
        produto=Produto(modelo.produto),
        gravame=_gravame_do_modelo(gravame),
        emissor=modelo.emissor,
        vencimento=modelo.vencimento,
        liquidez_dias=modelo.liquidez_dias,
        contraparte_id=modelo.contraparte_id,
        solicitacao=None
        if pedido is None
        else Solicitacao(
            id=pedido.id,
            tipo=TipoSolicitacao(pedido.tipo),
            criada_em=pedido.criada_em,
            prazo_ate=pedido.prazo_ate,
            enviada_em=modelo.solicitacao_enviada_em,
        ),
        emprestimo_id=modelo.emprestimo_id,
        plano=modelo.plano,
        fim_da_acumulacao=modelo.fim_da_acumulacao,
        informacoes=informacoes,
        aplicado_em=modelo.aplicado_em,
    )


def _modelo_da_garantia(garantia: Garantia, parceiro_id: str) -> GarantiaModelo:
    informacoes = garantia.informacoes
    return GarantiaModelo(
        id=garantia.id,
        conta_id=garantia.conta_id,
        parceiro_id=parceiro_id,
        tipo_ativo=garantia.tipo_ativo.value,
        valor_aplicado=garantia.valor_aplicado,
        marcada_em=garantia.marcada_em,
        criada_em=garantia.criada_em,
        produto=garantia.produto.value,
        emissor=garantia.emissor,
        vencimento=garantia.vencimento,
        liquidez_dias=garantia.liquidez_dias,
        contraparte_id=garantia.contraparte_id,
        plano=garantia.plano,
        fim_da_acumulacao=garantia.fim_da_acumulacao,
        aplicado_em=garantia.aplicado_em,
        emprestimo_id=garantia.emprestimo_id,
        informacoes_valor_disponivel=informacoes.valor_disponivel if informacoes else None,
        informacoes_tributos=informacoes.tributos_informados if informacoes else None,
        informacoes_recebidas_em=informacoes.recebidas_em if informacoes else None,
        solicitacao_id=garantia.solicitacao.id if garantia.solicitacao else None,
        solicitacao_enviada_em=garantia.solicitacao.enviada_em if garantia.solicitacao else None,
    )


def _gravame_do_modelo(modelo: GravameModelo) -> Gravame:
    return Gravame(
        id=modelo.id,
        infraestrutura=Infraestrutura(modelo.infraestrutura),
        instrumento=Instrumento(modelo.instrumento),
        trilho=Trilho(modelo.trilho),
        estado=EstadoGravame(modelo.estado),
        solicitado_em=modelo.solicitado_em,
        registrado_em=modelo.registrado_em,
        protocolo=modelo.protocolo,
        baixa_pedida_em=modelo.baixa_pedida_em,
        baixa_prazo=modelo.baixa_prazo,
        encerrado_em=modelo.encerrado_em,
        motivo=modelo.motivo,
    )


def _modelo_do_gravame(garantia: Garantia, parceiro_id: str) -> GravameModelo:
    gravame = garantia.gravame
    return GravameModelo(
        id=gravame.id,
        garantia_id=garantia.id,
        conta_id=garantia.conta_id,
        parceiro_id=parceiro_id,
        infraestrutura=gravame.infraestrutura.value,
        instrumento=gravame.instrumento.value,
        trilho=gravame.trilho.value,
        estado=gravame.estado.value,
        solicitado_em=gravame.solicitado_em,
        registrado_em=gravame.registrado_em,
        protocolo=gravame.protocolo,
        baixa_pedida_em=gravame.baixa_pedida_em,
        baixa_prazo=gravame.baixa_prazo,
        encerrado_em=gravame.encerrado_em,
        motivo=gravame.motivo,
    )


def _parcela_do_modelo(modelo: ParcelaModelo) -> Parcela:
    return Parcela(
        numero=modelo.numero,
        vencimento=modelo.vencimento,
        valor=modelo.valor,
        juros=modelo.juros,
        amortizacao=modelo.amortizacao,
        paga_em=modelo.paga_em,
        juros_pago=modelo.juros_pago,
        amortizacao_paga=modelo.amortizacao_paga,
        inicio=modelo.inicio,
    )


def _modelo_da_parcela(parcela: Parcela, emprestimo_id: str) -> ParcelaModelo:
    return ParcelaModelo(
        emprestimo_id=emprestimo_id,
        numero=parcela.numero,
        vencimento=parcela.vencimento,
        inicio=parcela.inicio_do_periodo,
        valor=parcela.valor,
        juros=parcela.juros,
        amortizacao=parcela.amortizacao,
        juros_pago=parcela.juros_pago,
        amortizacao_paga=parcela.amortizacao_paga,
        paga_em=parcela.paga_em,
    )


def _modelo_do_emprestimo(emprestimo: Emprestimo, parceiro_id: str) -> EmprestimoModelo:
    return EmprestimoModelo(
        id=emprestimo.id,
        conta_id=emprestimo.conta_id,
        parceiro_id=parceiro_id,
        principal=emprestimo.principal,
        taxa_ao_mes=str(emprestimo.taxa_ao_mes),
        estado=emprestimo.estado.value,
        contratado_em=emprestimo.contratado_em,
        dias_minimos_execucao=emprestimo.dias_minimos_execucao,
        aviso_45_em=emprestimo.aviso_45_em,
        aviso_10_em=emprestimo.aviso_10_em,
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
