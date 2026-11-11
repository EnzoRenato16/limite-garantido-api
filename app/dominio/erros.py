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


class FaturaEmAberto(ErroDominio):
    codigo = "fatura_em_aberto"


class SemSaldoParaFaturar(ErroDominio):
    codigo = "sem_saldo_para_faturar"


class VencimentoInvalido(ErroDominio):
    codigo = "vencimento_invalido"


class PagamentoExcedeDivida(ErroDominio):
    codigo = "pagamento_excede_divida"
