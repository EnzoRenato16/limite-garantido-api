"""Ponto de entrada: `uvicorn app.main:app`."""

from app.api.fabrica import criar_app

app = criar_app()
