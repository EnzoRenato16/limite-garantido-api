"""Erros de negócio. Cada um tem um código estável, que a API expõe sem tradução."""

from __future__ import annotations

from typing import ClassVar


class ErroDominio(Exception):
    """Base dos erros de regra de negócio."""

    codigo: ClassVar[str] = "erro_de_negocio"

    def __init__(self, mensagem: str, **detalhes: object) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.detalhes = detalhes


class ValorInvalido(ErroDominio):
    codigo = "valor_invalido"


class PoliticaInvalida(ErroDominio):
    codigo = "politica_invalida"


class AtivoNaoAceito(ErroDominio):
    codigo = "ativo_nao_aceito"


class LancamentoDesbalanceado(ErroDominio):
    codigo = "lancamento_desbalanceado"


class TransicaoInvalida(ErroDominio):
    codigo = "transicao_de_estado_invalida"


class GarantiaNaoEncontrada(ErroDominio):
    codigo = "garantia_nao_encontrada"


class GarantiaCobrindoDivida(ErroDominio):
    codigo = "garantia_cobrindo_divida"


class EmprestimoNaoEncontrado(ErroDominio):
    codigo = "emprestimo_nao_encontrado"


class FaturaEmAberto(ErroDominio):
    codigo = "fatura_em_aberto"


class SemSaldoParaFaturar(ErroDominio):
    codigo = "sem_saldo_para_faturar"


class VencimentoInvalido(ErroDominio):
    codigo = "vencimento_invalido"


class PagamentoExcedeDivida(ErroDominio):
    codigo = "pagamento_excede_divida"


class SolicitacaoDivergente(ErroDominio):
    """A resposta do custodiante não corresponde ao pedido em aberto da garantia."""

    codigo = "solicitacao_divergente"


class SolicitacaoNoPrazo(ErroDominio):
    codigo = "solicitacao_no_prazo"


class CombinacaoNaoPermitida(ErroDominio):
    """Ativo, trilho e produto que o catálogo regulatório não aceita juntos.

    O código varia com o motivo (`previdencia_so_no_emprestimo`, `coe_nao_recomendado`...)
    e é estável. A `sugestao` cita a rota certa e também vai nos detalhes, para a API
    devolvê-la no problem+json.
    """

    def __init__(
        self,
        codigo: str,
        mensagem: str | None = None,
        *,
        sugestao: str = "",
        **detalhes: object,
    ) -> None:
        super().__init__(mensagem or sugestao or codigo, sugestao=sugestao, **detalhes)
        self._codigo = codigo
        self.sugestao = sugestao

    @property
    def codigo(self) -> str:  # type: ignore[override]
        return self._codigo


class ForaDaCobertura(ErroDominio):
    """A data ou o prazo em dias úteis sai do período coberto pelo calendário de feriados.

    É falta de configuração (o calendário precisa ser estendido), não entrada do usuário:
    melhor um erro claro do que um prazo errado em silêncio.
    """

    codigo = "data_fora_da_cobertura"


class EmprestimoNegado(ErroDominio):
    """A conta não pode contratar empréstimo agora. O código varia com o motivo."""

    def __init__(self, codigo: str, mensagem: str, **detalhes: object) -> None:
        super().__init__(mensagem, **detalhes)
        self._codigo = codigo

    @property
    def codigo(self) -> str:  # type: ignore[override]
        return self._codigo
