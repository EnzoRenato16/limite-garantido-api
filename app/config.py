"""Configuração por variáveis de ambiente (veja `.env.example`).

Os padrões são os seguros: sandbox, demo e acesso a rede interna vêm desligados, e a API
administrativa só existe se houver um token. Em produção a aplicação se recusa a subir
com configuração de desenvolvimento.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TAMANHO_MINIMO_DE_SEGREDO = 32
MARCA_DE_SEGREDO_DE_EXEMPLO = "exemplo"


class Ambiente(StrEnum):
    DESENVOLVIMENTO = "desenvolvimento"
    TESTE = "teste"
    PRODUCAO = "producao"


class Configuracao(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    ambiente: Ambiente = Ambiente.DESENVOLVIMENTO
    database_url: str = "sqlite:///./limite_garantido.db"
    banco_pool_tamanho: int = Field(default=10, ge=1)
    banco_pool_excedente: int = Field(default=20, ge=0)
    log_nivel: str = "INFO"
    fuso_de_negocio: str = "America/Sao_Paulo"

    # Token da API administrativa. Sem ele, /v1/admin responde 404.
    admin_token: SecretStr | None = None

    idempotencia_ttl_horas: int = Field(default=24, ge=1)
    limite_de_requisicoes_por_minuto: int = Field(default=120, ge=1)

    # "interna": o gravame é confirmado na hora (fase 1, investimento dentro de casa).
    # "assincrona": a garantia fica pendente até a custódia confirmar (fases 2 e 3).
    custodia_modo: Literal["interna", "assincrona"] = "interna"

    webhook_timeout_segundos: float = Field(default=5.0, gt=0)
    webhook_max_tentativas: int = Field(default=8, ge=1)
    webhook_backoff_base_segundos: int = Field(default=30, ge=1)
    webhook_backoff_teto_segundos: int = Field(default=3600, ge=1)
    webhook_tolerancia_segundos: int = Field(default=300, ge=1)
    # Em produção o worker não entrega para IP privado, loopback ou http sem TLS (SSRF).
    webhook_permitir_rede_interna: bool = False
    worker_intervalo_segundos: float = Field(default=1.0, gt=0)

    # Endpoints /v1/sandbox (simular queda de ativo e passagem de dias).
    sandbox_habilitado: bool = False
    # Página /demo, com um parceiro de exemplo. A chave abaixo é entregue ao navegador.
    demo_habilitado: bool = False
    demo_api_key: SecretStr | None = None
    demo_webhook_segredo: SecretStr | None = None
    demo_webhook_url: str = "http://localhost:8000/demo/webhooks"

    @model_validator(mode="after")
    def _recusar_configuracao_insegura_em_producao(self) -> Configuracao:
        if self.ambiente is not Ambiente.PRODUCAO:
            return self
        problemas = []
        if self.demo_habilitado:
            problemas.append("DEMO_HABILITADO precisa ser false")
        if self.sandbox_habilitado:
            problemas.append("SANDBOX_HABILITADO precisa ser false")
        if self.webhook_permitir_rede_interna:
            problemas.append("WEBHOOK_PERMITIR_REDE_INTERNA precisa ser false")
        if self.database_url.startswith("sqlite"):
            problemas.append("DATABASE_URL precisa apontar para o Postgres")
        if self.admin_token is not None:
            token = self.admin_token.get_secret_value()
            if len(token) < TAMANHO_MINIMO_DE_SEGREDO or MARCA_DE_SEGREDO_DE_EXEMPLO in token:
                problemas.append(
                    f"ADMIN_TOKEN precisa ter {TAMANHO_MINIMO_DE_SEGREDO}+ caracteres "
                    "e não pode ser o do .env.example"
                )
        if problemas:
            raise ValueError("Configuração insegura para produção: " + "; ".join(problemas))
        return self

    @property
    def em_producao(self) -> bool:
        return self.ambiente is Ambiente.PRODUCAO


@lru_cache
def obter_configuracao() -> Configuracao:
    return Configuracao()
