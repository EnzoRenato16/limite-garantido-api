"""Configuração por variáveis de ambiente (veja `.env.example`).

Os padrões são os seguros: sandbox, demo e acesso a rede interna vêm desligados, e a API
administrativa só existe se houver um token. Em produção a aplicação se recusa a subir
com configuração de desenvolvimento.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
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
    # "assincrona": modo legado; a garantia interna fica pendente até uma confirmação
    # administrativa e expira depois de CUSTODIA_PRAZO_LEGADO_MINUTOS.
    custodia_modo: Literal["interna", "assincrona"] = "interna"
    custodia_prazo_legado_minutos: int = Field(default=1440, ge=1)

    # Trilhos de fora (portabilidade, transferência de custódia, TD Garantia, Res. Conjunta 12).
    # Não há integração real: "simulador" liga as contrapartes fictícias de
    # app/contrapartes_simuladas.toml. Sem valor explícito, o simulador fica ligado fora de
    # produção e desligado em produção.
    contrapartes: Literal["simulador", "desligada"] | None = None
    contrapartes_perfis_arquivo: Path | None = None
    # Onde o gravame seria registrado (B3, Selic, plataforma da Res. Conjunta 12). Também só
    # há simulação: em produção ela é recusada, e "desligada" responde 503
    # `infraestrutura_indisponivel` em vez de fingir um registro.
    infraestrutura_de_registro: Literal["simulada", "desligada"] | None = None
    # Segredo HMAC de cada contraparte para assinar callbacks, em JSON:
    # CONTRAPARTES_SEGREDOS='{"banco_de_origem_simulado": "...", ...}'
    contrapartes_segredos: dict[str, SecretStr] = Field(default_factory=dict)
    # Janela contra reapresentação de callback (diferença máxima do timestamp assinado).
    contrapartes_tolerancia_segundos: int = Field(default=300, ge=1, le=900)
    # De quanto em quanto tempo o worker consulta pedidos sem resposta (polling).
    contrapartes_intervalo_segundos: float = Field(default=2.0, gt=0)

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
    def _recusar_segredo_de_callback_fraco(self) -> Configuracao:
        # Vale em qualquer ambiente: com segredo vazio ou curto, qualquer um forja a
        # confirmação de um gravame, e isso vira limite sem garantia.
        fracos = [
            contraparte_id
            for contraparte_id, segredo in self.contrapartes_segredos.items()
            if len(segredo.get_secret_value()) < TAMANHO_MINIMO_DE_SEGREDO
        ]
        if fracos:
            raise ValueError(
                f"segredo de callback de {', '.join(sorted(fracos))} precisa ter "
                f"{TAMANHO_MINIMO_DE_SEGREDO}+ caracteres"
            )
        return self

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
        if self.contrapartes == "simulador":
            problemas.append("CONTRAPARTES=simulador não roda em produção: não há contraparte real")
        if self.infraestrutura_de_registro == "simulada":
            problemas.append(
                "INFRAESTRUTURA_DE_REGISTRO=simulada não roda em produção: "
                "não há registro real de gravame"
            )
        for contraparte_id, segredo in self.contrapartes_segredos.items():
            valor = segredo.get_secret_value()
            if len(valor) < TAMANHO_MINIMO_DE_SEGREDO or MARCA_DE_SEGREDO_DE_EXEMPLO in valor:
                problemas.append(
                    f"segredo de callback de {contraparte_id} precisa ter "
                    f"{TAMANHO_MINIMO_DE_SEGREDO}+ caracteres e não pode ser o do .env.example"
                )
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

    @property
    def infraestrutura_efetiva(self) -> Literal["simulada", "desligada"]:
        if self.infraestrutura_de_registro is not None:
            return self.infraestrutura_de_registro
        return "desligada" if self.em_producao else "simulada"

    @property
    def contrapartes_efetivas(self) -> Literal["simulador", "desligada"]:
        if self.contrapartes is not None:
            return self.contrapartes
        return "desligada" if self.em_producao else "simulador"


@lru_cache
def obter_configuracao() -> Configuracao:
    return Configuracao()
