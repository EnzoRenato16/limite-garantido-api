"""Logs estruturados em JSON, uma linha por evento.

Toda linha carrega `request_id` e `parceiro_id` quando existe uma requisição em curso:
é o que permite seguir uma operação de ponta a ponta num agregador de logs.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Um dicionário por requisição. É mutável de propósito: quem autentica o parceiro roda
# em outra thread e preenche `parceiro_id` no mesmo objeto que o middleware criou.
contexto_da_requisicao: ContextVar[dict[str, Any] | None] = ContextVar(
    "contexto_da_requisicao", default=None
)

_CAMPOS_PADRAO_DO_LOGGING = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def contexto_atual() -> dict[str, Any]:
    contexto = contexto_da_requisicao.get()
    return contexto if contexto is not None else {}


_fabrica_original = logging.getLogRecordFactory()


def _fabrica_com_contexto(*args: Any, **kwargs: Any) -> logging.LogRecord:
    """Carimba request_id e parceiro_id no registro na hora em que ele nasce.

    Assim o dado certo acompanha a linha mesmo que ela só seja formatada depois, fora da
    requisição (um handler com fila, por exemplo).
    """
    registro = _fabrica_original(*args, **kwargs)
    contexto = contexto_atual()
    registro.request_id = contexto.get("request_id")
    registro.parceiro_id = contexto.get("parceiro_id")
    return registro


class FormatadorJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        linha: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "nivel": record.levelname,
            "logger": record.name,
            "mensagem": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "parceiro_id": getattr(record, "parceiro_id", None),
        }
        for chave, valor in record.__dict__.items():
            if chave not in _CAMPOS_PADRAO_DO_LOGGING and not chave.startswith("_"):
                linha[chave] = valor
        if record.exc_info:
            linha["excecao"] = self.formatException(record.exc_info)
        return json.dumps(linha, ensure_ascii=False, default=str)


def configurar_logs(nivel: str = "INFO") -> None:
    logging.setLogRecordFactory(_fabrica_com_contexto)
    saida = logging.StreamHandler(sys.stdout)
    saida.setFormatter(FormatadorJson())
    raiz = logging.getLogger()
    raiz.handlers = [saida]
    raiz.setLevel(nivel.upper())
    # O log de acesso é nosso (com request_id e parceiro_id); o do uvicorn seria repetido.
    logging.getLogger("uvicorn.access").disabled = True
    for nome in ("uvicorn", "uvicorn.error"):
        logging.getLogger(nome).handlers = []
        logging.getLogger(nome).propagate = True
