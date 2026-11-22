"""Catálogo regulatório: o que cada ativo aceita, por qual trilho, em qual produto.

É a fonte única das regras que vêm da regulação (ou da leitura dela): infraestrutura em
que o gravame é registrado, instrumento jurídico, trilho de entrada, produto permitido,
prazos, LTV de partida, carteira de provisão, retenção na execução e as afirmações que a
documentação mostra, cada uma com a sua categoria. Os números moram em
`app/regulacao.toml`; aqui ficam só o formato, as validações e as consultas.

Unidades usadas nas anotações:
- `{dia_util}`: dia útil bancário nacional (calendário em `app/feriados_nacionais.toml`);
- `{dia_corrido}`: dia do calendário civil;
- `{ano}`: ano até o vencimento do título, em `Decimal`;
- `{1}`: razão adimensional (LTV, alíquota, piso de provisão);
- `{BRL}`: reais, sempre em `Dinheiro`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from app.dominio.dinheiro import Dinheiro
from app.dominio.erros import CombinacaoNaoPermitida, ValorInvalido
from app.dominio.politica import TipoAtivo


class Infraestrutura(StrEnum):
    """Onde o gravame é registrado (o projeto só simula o registro)."""

    B3_BALCAO = "b3_balcao"
    B3_CENTRAL_DEPOSITARIA = "b3_central_depositaria"
    SELIC_TD_GARANTIA = "selic_td_garantia"
    REGISTRADORA_OU_B3_ESCRITURADOR = "registradora_ou_b3_escriturador"
    PLATAFORMA_RES_CONJUNTA_12 = "plataforma_res_conjunta_12"


class Instrumento(StrEnum):
    CESSAO_FIDUCIARIA = "cessao_fiduciaria"
    ALIENACAO_FIDUCIARIA = "alienacao_fiduciaria"
    PENHOR = "penhor"
    DIREITO_DE_RESGATE = "direito_de_resgate"


class Trilho(StrEnum):
    """Como o ativo chega à condição de garantia da SCD do grupo."""

    CASA = "casa"
    PORTABILIDADE_CVM_210 = "portabilidade_cvm_210"
    TRANSFERENCIA_DE_CUSTODIA = "transferencia_de_custodia"
    TD_GARANTIA = "td_garantia"
    RES_CONJUNTA_12 = "res_conjunta_12"


class Produto(StrEnum):
    CARTAO = "cartao"
    EMPRESTIMO = "emprestimo"


class Categoria(StrEnum):
    """De onde vem a afirmação. `norma` exige norma e artigo."""

    NORMA = "norma"
    PRATICA_DE_MERCADO = "pratica_de_mercado"
    INTERPRETACAO = "interpretacao"
    IMPRENSA = "imprensa"
    NAO_VERIFICADO = "nao_verificado"


class CarteiraDeProvisao(StrEnum):
    """Carteiras de provisão mínima para inadimplidos (Res. BCB 352/2023, art. 81)."""

    C1 = "c1"
    C2 = "c2"
    C3 = "c3"
    C4 = "c4"
    C5 = "c5"


class CondicaoNoCartao(StrEnum):
    """O que o ativo precisa cumprir para garantir o cartão (limite rotativo)."""

    LIQUIDEZ_DIARIA = "liquidez_diaria"
    RESGATE_ATE_D1 = "resgate_ate_d1"
    CTVM_AGENTE_DO_TD = "ctvm_agente_do_td"


class TemaDaAfirmacao(StrEnum):
    """Coluna da matriz em que a afirmação aparece (página /regulacao e docs)."""

    O_QUE_FUNCIONA = "o_que_funciona"
    TRILHO = "trilho"
    INSTRUMENTO = "instrumento"
    INFRAESTRUTURA = "infraestrutura"
    PRODUTO = "produto"
    PRAZO = "prazo"
    RISCO_DE_EXECUCAO = "risco_de_execucao"
    RETENCAO = "retencao"
    PROVISAO = "provisao"
    LTV = "ltv"
    GERAL = "geral"


# Temas que todo ativo precisa ter para a matriz não sair com célula vazia.
TEMAS_DA_MATRIZ = frozenset(
    {
        TemaDaAfirmacao.O_QUE_FUNCIONA,
        TemaDaAfirmacao.TRILHO,
        TemaDaAfirmacao.INSTRUMENTO,
        TemaDaAfirmacao.INFRAESTRUTURA,
        TemaDaAfirmacao.PRODUTO,
        TemaDaAfirmacao.PRAZO,
        TemaDaAfirmacao.RISCO_DE_EXECUCAO,
    }
)


class RegraDeRetencao(StrEnum):
    """Como se calcula o que fica retido na execução (o cálculo é da Tarefa 4)."""

    TABELA_REGRESSIVA_RENDA_FIXA = "tabela_regressiva_renda_fixa"  # sobre o rendimento
    ISENTO = "isento"
    SOBRE_A_VENDA = "sobre_a_venda"  # percentual sobre o valor da venda
    PERCENTUAL_ILUSTRATIVO = "percentual_ilustrativo"  # sobre o valor resgatado


def _razao_entre_0_e_1(valor: Decimal, nome: str, *, aceita_zero: bool = False) -> None:
    if not isinstance(valor, Decimal) or not valor.is_finite():
        raise ValorInvalido(f"{nome} precisa ser um Decimal finito")
    if not (0 <= valor <= 1 if aceita_zero else 0 < valor <= 1):
        limite = "[0, 1]" if aceita_zero else "(0, 1]"
        raise ValorInvalido(f"{nome} precisa estar em {limite}: {valor}")


# ------------------------------------------------------------------ dados básicos
@dataclass(frozen=True, slots=True)
class Afirmacao:
    texto: str
    categoria: Categoria
    norma: str | None
    artigo: str | None
    tema: TemaDaAfirmacao = TemaDaAfirmacao.GERAL

    def __post_init__(self) -> None:
        if not self.texto.strip():
            raise ValorInvalido("Afirmação sem texto")
        if self.categoria is Categoria.NORMA:
            if not self.norma:
                raise ValorInvalido(f"Afirmação de categoria norma sem norma: {self.texto!r}")
            if not self.artigo:
                raise ValorInvalido(f"Afirmação de categoria norma sem artigo: {self.texto!r}")


@dataclass(frozen=True, slots=True)
class FaixaDeLtv:
    """LTV de partida {1} para títulos com até `ate_anos` {ano} até o vencimento.

    `ate_anos=None` é a faixa aberta (sem prazo, ou acima da faixa anterior).
    """

    ate_anos: int | None
    minimo: Decimal  # {1}
    maximo: Decimal  # {1}

    def __post_init__(self) -> None:
        _razao_entre_0_e_1(self.minimo, "LTV mínimo da faixa")
        _razao_entre_0_e_1(self.maximo, "LTV máximo da faixa")
        if self.minimo > self.maximo:
            raise ValorInvalido(f"Faixa de LTV com mínimo {self.minimo} acima do máximo")
        if self.ate_anos is not None and self.ate_anos <= 0:
            raise ValorInvalido("ate_anos da faixa de LTV precisa ser positivo")


@dataclass(frozen=True, slots=True)
class FaixaDaTabelaRegressiva:
    """Alíquota {1} de IR sobre o rendimento para aplicações com até tantos {dia_corrido}."""

    ate_dias_corridos: int | None
    aliquota: Decimal  # {1}

    def __post_init__(self) -> None:
        _razao_entre_0_e_1(self.aliquota, "Alíquota da tabela regressiva")
        if self.ate_dias_corridos is not None and self.ate_dias_corridos <= 0:
            raise ValorInvalido("ate_dias_corridos da tabela regressiva precisa ser positivo")


@dataclass(frozen=True, slots=True)
class RetencaoNaExecucao:
    regra: RegraDeRetencao
    percentual: Decimal | None  # {1}; só em sobre_a_venda e percentual_ilustrativo
    afirmacoes: tuple[Afirmacao, ...]

    def __post_init__(self) -> None:
        precisa = self.regra in (
            RegraDeRetencao.SOBRE_A_VENDA,
            RegraDeRetencao.PERCENTUAL_ILUSTRATIVO,
        )
        if precisa and self.percentual is None:
            raise ValorInvalido(f"Retenção {self.regra} precisa de percentual")
        if not precisa and self.percentual is not None:
            raise ValorInvalido(f"Retenção {self.regra} não leva percentual")
        if self.percentual is not None:
            _razao_entre_0_e_1(self.percentual, "Percentual de retenção", aceita_zero=True)
        if not self.afirmacoes:
            raise ValorInvalido("Retenção sem afirmação que a sustente")


@dataclass(frozen=True, slots=True)
class PisoDeProvisao:
    """Provisão mínima {1} sobre o saldo, contada a partir do inadimplemento."""

    primeiro_mes: Decimal  # {1}
    decimo_terceiro_mes: Decimal  # {1}

    def __post_init__(self) -> None:
        _razao_entre_0_e_1(self.primeiro_mes, "Piso do 1º mês", aceita_zero=True)
        _razao_entre_0_e_1(self.decimo_terceiro_mes, "Piso do 13º mês", aceita_zero=True)
        if self.primeiro_mes > self.decimo_terceiro_mes:
            raise ValorInvalido("O piso de provisão não pode cair do 1º para o 13º mês")


# ------------------------------------------------------------------------- trilhos
@dataclass(frozen=True, slots=True)
class RegraDoTrilho:
    trilho: Trilho
    nome: str
    # Prazo do trilho em {dia_util}; None quando não há prazo (gravame no dia) ou quando
    # o prazo depende do ativo (então vem de `prazos_por_ativo`).
    prazo_dias_uteis: int | None
    prazo_e_regulatorio: bool
    pode_recusar: bool
    afirmacoes: tuple[Afirmacao, ...]
    rota: str = ""
    # Prazo por ativo, em {dia_util}, quando ele muda com o ativo (portabilidade).
    prazos_por_ativo: Mapping[TipoAtivo, int] | None = None

    def __post_init__(self) -> None:
        prazos = [self.prazo_dias_uteis, *(self.prazos_por_ativo or {}).values()]
        if any(prazo is not None and prazo < 0 for prazo in prazos):
            raise ValorInvalido(f"Prazo negativo no trilho {self.trilho}")
        if self.prazo_dias_uteis is not None and self.prazos_por_ativo:
            raise ValorInvalido(
                f"Trilho {self.trilho}: use prazo único ou prazo por ativo, não os dois"
            )
        if self.prazo_e_regulatorio and self.prazo_dias_uteis is None and not self.prazos_por_ativo:
            raise ValorInvalido(f"Trilho {self.trilho}: prazo regulatório sem prazo")

    def prazo_para(self, tipo: TipoAtivo) -> int | None:
        """Prazo do trilho para o ativo, em {dia_util}; None quando o trilho não tem prazo."""
        if self.prazos_por_ativo:
            try:
                return self.prazos_por_ativo[tipo]
            except KeyError:
                raise CombinacaoNaoPermitida(
                    "trilho_nao_atende_o_ativo",
                    f"O trilho {self.trilho} não tem prazo para {tipo}",
                ) from None
        return self.prazo_dias_uteis


# -------------------------------------------------------------------------- ativos
@dataclass(frozen=True, slots=True)
class RegraDoAtivo:
    tipo: TipoAtivo
    nome: str
    infraestrutura: Infraestrutura
    # O primeiro instrumento é o padrão.
    instrumentos: tuple[Instrumento, ...]
    trilhos: tuple[Trilho, ...]
    produtos: tuple[Produto, ...]
    condicao_no_cartao: CondicaoNoCartao | None
    # Atraso mínimo, em {dia_corrido}, antes de executar; None = prazo contratual da política.
    dias_minimos_execucao: int | None
    faixas_de_ltv: tuple[FaixaDeLtv, ...]
    carteira_de_provisao: Mapping[Instrumento, CarteiraDeProvisao]
    recomendado: bool
    afirmacoes: tuple[Afirmacao, ...]
    retencao: RetencaoNaExecucao | None = None
    # Prazo de resgate máximo, em {dia_util} (0 = D+0), para entrar no cartão.
    liquidez_maxima_no_cartao_dias_uteis: int | None = None
    # Planos aceitos (só previdência); vazio = o ativo não tem plano.
    planos_aceitos: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        nome = str(self.tipo)
        if len(set(self.instrumentos)) != len(self.instrumentos):
            raise ValorInvalido(f"{nome}: instrumento repetido")
        sem_carteira = set(self.instrumentos) - set(self.carteira_de_provisao)
        if sem_carteira:
            lista = ", ".join(sorted(sem_carteira))
            raise ValorInvalido(f"{nome}: instrumento sem carteira de provisão: {lista}")
        sobrando = set(self.carteira_de_provisao) - set(self.instrumentos)
        if sobrando:
            lista = ", ".join(sorted(sobrando))
            raise ValorInvalido(f"{nome}: carteira de provisão de instrumento não listado: {lista}")
        self._validar_faixas()
        if self.recomendado:
            for campo in ("instrumentos", "trilhos", "produtos", "faixas_de_ltv"):
                if not getattr(self, campo):
                    raise ValorInvalido(f"{nome}: ativo recomendado sem {campo}")
            if self.retencao is None:
                raise ValorInvalido(f"{nome}: ativo recomendado sem regra de retenção")
            faltando = TEMAS_DA_MATRIZ - {a.tema for a in self.afirmacoes}
            if faltando:
                lista = ", ".join(sorted(faltando))
                raise ValorInvalido(f"{nome}: faltam afirmações dos temas {lista}")
        if self.condicao_no_cartao is not None and Produto.CARTAO not in self.produtos:
            raise ValorInvalido(f"{nome}: condição no cartão para ativo que não vai ao cartão")
        exige_liquidez = self.condicao_no_cartao in (
            CondicaoNoCartao.LIQUIDEZ_DIARIA,
            CondicaoNoCartao.RESGATE_ATE_D1,
        )
        if exige_liquidez and self.liquidez_maxima_no_cartao_dias_uteis is None:
            raise ValorInvalido(f"{nome}: condição de liquidez sem liquidez máxima")
        if self.dias_minimos_execucao is not None and self.dias_minimos_execucao < 1:
            raise ValorInvalido(f"{nome}: dias_minimos_execucao precisa ser pelo menos 1")

    def _validar_faixas(self) -> None:
        anterior = 0
        for posicao, faixa in enumerate(self.faixas_de_ltv):
            ultima = posicao == len(self.faixas_de_ltv) - 1
            if faixa.ate_anos is None:
                if not ultima:
                    raise ValorInvalido(f"{self.tipo}: só a última faixa de LTV pode ser aberta")
                continue
            if faixa.ate_anos <= anterior:
                raise ValorInvalido(f"{self.tipo}: faixas de LTV precisam crescer em prazo")
            anterior = faixa.ate_anos

    def faixa_de_ltv(self, anos_ate_o_vencimento: Decimal | None) -> FaixaDeLtv:
        """A faixa que vale para o prazo {ano}. Sem prazo, vale a mais longa (a menor)."""
        if not self.faixas_de_ltv:
            raise ValorInvalido(f"{self.tipo} não tem faixa de LTV")
        if anos_ate_o_vencimento is None:
            return self.faixas_de_ltv[-1]
        if not anos_ate_o_vencimento.is_finite() or anos_ate_o_vencimento < 0:
            raise ValorInvalido(f"Prazo até o vencimento inválido: {anos_ate_o_vencimento}")
        for faixa in self.faixas_de_ltv:
            if faixa.ate_anos is None or anos_ate_o_vencimento <= faixa.ate_anos:
                return faixa
        # Prazo acima da última faixa fechada: o lado seguro é a última faixa (a menor).
        return self.faixas_de_ltv[-1]


# ----------------------------------------------------------------------- catálogo
@dataclass(frozen=True, slots=True)
class CatalogoRegulatorio:
    ativos: Mapping[TipoAtivo, RegraDoAtivo]
    trilhos: Mapping[Trilho, RegraDoTrilho]
    # Não há confirmação pública de que a CTVM do grupo seja agente de custódia do TD.
    ctvm_do_grupo_e_agente_do_td: bool
    prazo_da_baixa_dias_uteis: int  # {dia_util}
    pisos_de_provisao: Mapping[CarteiraDeProvisao, PisoDeProvisao]
    teto_do_fgc: Dinheiro  # {BRL} por titular e conglomerado
    tabela_regressiva_renda_fixa: tuple[FaixaDaTabelaRegressiva, ...] = ()
    rota_do_emprestimo: str = "POST /v1/contas/{conta_id}/emprestimos"
    rota_da_matriz: str = "GET /v1/regulacao/matriz"
    afirmacoes: tuple[Afirmacao, ...] = ()

    def __post_init__(self) -> None:
        faltando = set(TipoAtivo) - set(self.ativos)
        if faltando:
            raise ValorInvalido(f"Tipos de ativo sem regra: {', '.join(sorted(faltando))}")
        sem_trilho = set(Trilho) - set(self.trilhos)
        if sem_trilho:
            raise ValorInvalido(f"Trilhos sem regra: {', '.join(sorted(sem_trilho))}")
        for tipo, regra in self.ativos.items():
            if regra.tipo is not tipo:
                raise ValorInvalido(f"Regra de {regra.tipo} guardada como {tipo}")
        for trilho, regra_do_trilho in self.trilhos.items():
            if regra_do_trilho.trilho is not trilho:
                raise ValorInvalido(f"Regra do trilho {regra_do_trilho.trilho} como {trilho}")
            if not regra_do_trilho.prazos_por_ativo:
                continue
            atendidos = {t for t, r in self.ativos.items() if trilho in r.trilhos}
            sem_prazo = atendidos - set(regra_do_trilho.prazos_por_ativo)
            if sem_prazo:
                lista = ", ".join(sorted(sem_prazo))
                raise ValorInvalido(f"Trilho {trilho} sem prazo para: {lista}")
            sem_trilho_no_ativo = set(regra_do_trilho.prazos_por_ativo) - atendidos
            if sem_trilho_no_ativo:
                lista = ", ".join(sorted(sem_trilho_no_ativo))
                raise ValorInvalido(f"Trilho {trilho} com prazo para ativo que não atende: {lista}")
        if self.prazo_da_baixa_dias_uteis < 0:
            raise ValorInvalido("Prazo da baixa negativo")
        if not self.teto_do_fgc.positivo:
            raise ValorInvalido("O teto do FGC precisa ser positivo")
        self._validar_tabela_regressiva()

    def _validar_tabela_regressiva(self) -> None:
        tabela = self.tabela_regressiva_renda_fixa
        usa = any(
            r.retencao is not None
            and r.retencao.regra is RegraDeRetencao.TABELA_REGRESSIVA_RENDA_FIXA
            for r in self.ativos.values()
        )
        if usa and not tabela:
            raise ValorInvalido("Há ativo com tabela regressiva, mas a tabela está vazia")
        anterior = 0
        for posicao, faixa in enumerate(tabela):
            if faixa.ate_dias_corridos is None:
                if posicao != len(tabela) - 1:
                    raise ValorInvalido("Só a última faixa da tabela regressiva pode ser aberta")
                continue
            if faixa.ate_dias_corridos <= anterior:
                raise ValorInvalido("A tabela regressiva precisa crescer em prazo")
            anterior = faixa.ate_dias_corridos
        if tabela and tabela[-1].ate_dias_corridos is not None:
            raise ValorInvalido("A última faixa da tabela regressiva precisa ser aberta")

    # ------------------------------------------------------------------ consultas
    def regra(self, tipo: TipoAtivo) -> RegraDoAtivo:
        return self.ativos[tipo]

    def com_agente_do_td(self, valor: bool) -> CatalogoRegulatorio:
        """Cópia com a chave da CTVM do grupo como agente de custódia do TD trocada."""
        return replace(self, ctvm_do_grupo_e_agente_do_td=valor)

    def ltv_maximo(self, tipo: TipoAtivo, anos_ate_o_vencimento: Decimal | None) -> Decimal:
        """Teto de LTV {1} para o ativo com esse prazo {ano} até o vencimento."""
        regra = self.regra(tipo)
        if not regra.recomendado:
            raise self._nao_recomendado(regra)
        return regra.faixa_de_ltv(anos_ate_o_vencimento).maximo

    def exigir_combinacao(
        self,
        tipo: TipoAtivo,
        trilho: Trilho,
        produto: Produto,
        *,
        liquidez_dias: int | None,
        plano: str | None = None,
        ja_em_garantia: bool = False,
    ) -> None:
        """Levanta `CombinacaoNaoPermitida` se o catálogo não aceita a combinação.

        `liquidez_dias` é o prazo de resgate em {dia_util} (0 = D+0); None = desconhecido,
        que no cartão é recusado (o lado seguro). `plano` só vale para previdência; None
        não é conferido aqui.
        """
        regra = self.regra(tipo)
        if not regra.recomendado:
            raise self._nao_recomendado(regra)

        if trilho not in regra.trilhos:
            raise CombinacaoNaoPermitida(
                "trilho_nao_atende_o_ativo",
                f"O trilho {trilho} não atende {regra.nome}",
                sugestao=f"Para {regra.nome}, use {self._rotas_de_entrada(regra)}.",
            )

        if ja_em_garantia and trilho is Trilho.PORTABILIDADE_CVM_210:
            rota = self.trilhos[Trilho.PORTABILIDADE_CVM_210].rota
            raise CombinacaoNaoPermitida(
                "ativo_ja_em_garantia",
                "Ativo dado em garantia é impedimento à portabilidade "
                '(Res. CVM 210, art. 19, §1º, IV, "b")',
                sugestao=(
                    "Peça a baixa do gravame no custodiante de origem e depois a "
                    f"portabilidade em {rota}."
                ),
            )

        if regra.planos_aceitos and plano is not None and plano not in regra.planos_aceitos:
            aceitos = ", ".join(regra.planos_aceitos)
            raise CombinacaoNaoPermitida(
                "plano_fora_da_res_conjunta_12",
                f"O plano {plano} fica fora da Res. Conjunta 12 (art. 1º, §1º)",
                sugestao=(
                    f"Use um plano aceito ({aceitos}) em "
                    f"{self.trilhos[Trilho.RES_CONJUNTA_12].rota}."
                ),
            )

        if produto not in regra.produtos:
            entrada = self._rotas_de_entrada(regra)
            if tipo is TipoAtivo.PREVIDENCIA:
                raise CombinacaoNaoPermitida(
                    "previdencia_so_no_emprestimo",
                    "A Res. Conjunta 12 exclui os limites rotativos de conta e de cartão "
                    "(art. 1º, §2º, VII)",
                    sugestao=(
                        f"Constitua a garantia em {entrada} e contrate um empréstimo em "
                        f"{self.rota_do_emprestimo}."
                    ),
                )
            raise CombinacaoNaoPermitida(
                "ativo_so_no_emprestimo",
                f"{regra.nome} só garante empréstimo, não {produto}",
                sugestao=(
                    f"Constitua a garantia em {entrada} e contrate um empréstimo em "
                    f"{self.rota_do_emprestimo}."
                ),
            )

        if produto is Produto.CARTAO:
            self._exigir_condicao_do_cartao(regra, liquidez_dias)

    def _exigir_condicao_do_cartao(self, regra: RegraDoAtivo, liquidez_dias: int | None) -> None:
        condicao = regra.condicao_no_cartao
        emprestimo = f"contrate um empréstimo em {self.rota_do_emprestimo}"
        if condicao is CondicaoNoCartao.CTVM_AGENTE_DO_TD:
            if not self.ctvm_do_grupo_e_agente_do_td:
                raise CombinacaoNaoPermitida(
                    "tesouro_no_cartao_exige_agente_do_td",
                    "Tesouro no cartão só quando a CTVM do grupo é agente de custódia do TD",
                    sugestao=(
                        "Use o Tesouro em garantia de empréstimo: constitua o gravame em "
                        f"{self.trilhos[Trilho.TD_GARANTIA].rota} e {emprestimo}."
                    ),
                )
            return
        if condicao is None:
            return
        maxima = regra.liquidez_maxima_no_cartao_dias_uteis
        dentro = liquidez_dias is not None and maxima is not None and 0 <= liquidez_dias <= maxima
        if dentro:
            return
        if condicao is CondicaoNoCartao.LIQUIDEZ_DIARIA:
            raise CombinacaoNaoPermitida(
                "cartao_exige_liquidez_diaria",
                f"No cartão, {regra.nome} só com liquidez diária",
                sugestao=f"Para este {regra.nome}, {emprestimo}.",
            )
        raise CombinacaoNaoPermitida(
            "fundo_no_cartao_so_ate_d1",
            f"No cartão, {regra.nome} só com resgate em D+0 ou D+1",
            sugestao=f"Para este fundo, {emprestimo}.",
        )

    def _rotas_de_entrada(self, regra: RegraDoAtivo) -> str:
        rotas = [f"{self.trilhos[t].rota} ({self.trilhos[t].nome})" for t in regra.trilhos]
        return " ou ".join(rotas)

    def _nao_recomendado(self, regra: RegraDoAtivo) -> CombinacaoNaoPermitida:
        return CombinacaoNaoPermitida(
            "coe_nao_recomendado" if regra.tipo is TipoAtivo.COE else "ativo_nao_recomendado",
            f"{regra.nome} não é recomendado como garantia",
            sugestao=(f"Use outro ativo; veja o que cada um aceita em {self.rota_da_matriz}."),
        )
