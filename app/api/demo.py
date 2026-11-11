"""Página /demo (em construção nesta etapa)."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.recursos import Recursos


def montar_demo(app: FastAPI, recursos: Recursos) -> None:
    del app, recursos
