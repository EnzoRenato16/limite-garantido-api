"""Rate limit simples por parceiro: janela fixa de um minuto, em memória.

Serve para uma instância. Com várias, o contador precisa ir para um lugar compartilhado
(Redis, por exemplo); a interface `permitir` continua a mesma.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

JANELA_EM_SEGUNDOS = 60


@dataclass(frozen=True, slots=True)
class Veredito:
    permitido: bool
    limite: int
    restantes: int
    segundos_para_reiniciar: int

    def cabecalhos(self) -> dict[str, str]:
        cabecalhos = {
            "RateLimit-Limit": str(self.limite),
            "RateLimit-Remaining": str(self.restantes),
            "RateLimit-Reset": str(self.segundos_para_reiniciar),
        }
        if not self.permitido:
            cabecalhos["Retry-After"] = str(self.segundos_para_reiniciar)
        return cabecalhos


class LimitadorDeRequisicoes:
    def __init__(self, relogio: Callable[[], float] = time.monotonic) -> None:
        self._relogio = relogio
        self._trava = threading.Lock()
        # parceiro_id -> (número da janela, requisições contadas nela)
        self._janelas: dict[str, tuple[int, int]] = {}

    def permitir(self, parceiro_id: str, limite_por_minuto: int) -> Veredito:
        agora = self._relogio()
        janela = int(agora // JANELA_EM_SEGUNDOS)
        faltam = max(int((janela + 1) * JANELA_EM_SEGUNDOS - agora), 1)
        with self._trava:
            janela_guardada, contagem = self._janelas.get(parceiro_id, (janela, 0))
            if janela_guardada != janela:
                contagem = 0
            if contagem >= limite_por_minuto:
                self._janelas[parceiro_id] = (janela, contagem)
                return Veredito(False, limite_por_minuto, 0, faltam)
            self._janelas[parceiro_id] = (janela, contagem + 1)
            return Veredito(True, limite_por_minuto, limite_por_minuto - contagem - 1, faltam)
