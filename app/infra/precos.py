"""Provedor de preços fake e determinístico.

Para o mesmo ativo e o mesmo dia o preço é sempre o mesmo, em qualquer máquina: ele sai
de um hash, não de um gerador aleatório. Isso deixa a marcação a mercado testável e
reproduzível. Para usar preço de verdade (B3, Anbima, um vendor), basta escrever outra
classe com o mesmo método `preco`.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import date, timedelta
from decimal import Decimal

from app.dominio.politica import TipoAtivo

_DIA_ZERO = date(2024, 1, 1)
_PRECO_INICIAL = Decimal(100)

# (carrego diário, oscilação diária máxima), em fração do preço. Valores só para a
# simulação fazer sentido: depósito parado, renda fixa pós andando devagar para cima,
# prefixado e IPCA+ balançando com juros, ações balançando bastante.
_COMPORTAMENTO: dict[TipoAtivo, tuple[Decimal, Decimal]] = {
    TipoAtivo.DEPOSITO: (Decimal(0), Decimal(0)),
    TipoAtivo.FUNDO_DI: (Decimal("0.0004"), Decimal("0.0001")),
    TipoAtivo.TESOURO_SELIC: (Decimal("0.0004"), Decimal("0.0001")),
    TipoAtivo.CDB: (Decimal("0.0004"), Decimal("0.0002")),
    TipoAtivo.TESOURO_PREFIXADO: (Decimal("0.0004"), Decimal("0.006")),
    TipoAtivo.TESOURO_IPCA: (Decimal("0.0004"), Decimal("0.008")),
    TipoAtivo.ACOES: (Decimal("0.0003"), Decimal("0.025")),
}

_ESCALA_DO_SORTEIO = 10**6


_OITO_CASAS = Decimal("0.00000001")


class ProvedorDePrecosFake:
    def __init__(self, semente: str = "limite-garantido") -> None:
        self._semente = semente
        # Série diária de cada ativo desde o dia zero, calculada sob demanda.
        self._series: dict[TipoAtivo, list[Decimal]] = {}
        self._trava = threading.Lock()

    def preco(self, tipo_ativo: TipoAtivo, dia: date) -> Decimal:
        indice = max((dia - _DIA_ZERO).days, 0)
        with self._trava:
            serie = self._series.setdefault(tipo_ativo, [_PRECO_INICIAL])
            while len(serie) <= indice:
                proximo_dia = _DIA_ZERO + timedelta(days=len(serie))
                retorno = self._retorno_do_dia(tipo_ativo, proximo_dia)
                # {BRL/unidade} * (1 + retorno {1}) -> {BRL/unidade}
                serie.append((serie[-1] * (Decimal(1) + retorno)).quantize(_OITO_CASAS))
            return serie[indice]

    def _retorno_do_dia(self, tipo_ativo: TipoAtivo, dia: date) -> Decimal:
        carrego, oscilacao = _COMPORTAMENTO[tipo_ativo]
        resumo = hashlib.sha256(
            f"{self._semente}:{tipo_ativo.value}:{dia.isoformat()}".encode()
        ).digest()
        # Número uniforme em [-1, 1), com seis casas, tirado do hash.
        sorteio = Decimal(int.from_bytes(resumo[:8], "big") % (2 * _ESCALA_DO_SORTEIO)) / Decimal(
            _ESCALA_DO_SORTEIO
        ) - Decimal(1)
        return carrego + oscilacao * sorteio
