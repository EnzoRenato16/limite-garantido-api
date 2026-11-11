"""O demo.py é a vitrine do projeto: este teste garante que ele continua rodando."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent


def test_demo_de_terminal_conta_a_historia_inteira() -> None:
    ambiente = {**os.environ, "COLUMNS": "140", "NO_COLOR": "1"}
    resultado = subprocess.run(
        [sys.executable, "demo.py"],
        cwd=RAIZ,
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    saida = resultado.stdout
    for trecho in (
        "Limite liberado: R$ 4.250,00",  # 5.000 em CDB x 85%
        "HTTP 402",  # a compra negada
        "limite_insuficiente",
        "Idempotent-Replayed: true",
        "Chamada de margem aberta: faltam R$ 175,00",
        "prazo da chamada de margem esgotado: conta bloqueada",
        "7º dia de atraso: a garantia é executada",
        "Vendidos só os R$ 4.000,00 da fatura",
        "disponível para comprar: R$ 425,00",  # sobra de R$ 500 x 85%
        "garantia.executada",
        "fecha",
        "entrariam em chamada de margem",
    ):
        assert trecho in saida, trecho
    assert "NÃO fecha" not in saida
    assert saida.count("confere") == 11  # todos os webhooks com assinatura válida
