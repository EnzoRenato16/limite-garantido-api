"""Validação da URL de destino do webhook.

O parceiro escolhe a URL e é o nosso worker que faz a requisição. Sem cuidado, isso vira
SSRF: alguém cadastra `http://169.254.169.254/...` ou um serviço interno e usa a
plataforma como ponte. Por isso, fora de desenvolvimento, só aceitamos https e recusamos
qualquer nome que resolva para endereço privado, de loopback ou reservado.

A checagem roda no cadastro e de novo a cada envio (o DNS pode mudar entre um e outro).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.aplicacao.erros import UrlDeWebhookInvalida

TAMANHO_MAXIMO_DA_URL = 500


def _endereco_interno(endereco: str) -> bool:
    ip = ipaddress.ip_address(endereco)
    return not ip.is_global or ip.is_multicast


def validar_destino(url: str, *, permitir_rede_interna: bool) -> None:
    if len(url) > TAMANHO_MAXIMO_DA_URL:
        raise UrlDeWebhookInvalida("A URL do webhook é longa demais")
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise UrlDeWebhookInvalida("A URL do webhook precisa ser http(s) e ter um host")
    if partes.username or partes.password:
        raise UrlDeWebhookInvalida("A URL do webhook não pode conter usuário e senha")
    if permitir_rede_interna:
        return
    if partes.scheme != "https":
        raise UrlDeWebhookInvalida("A URL do webhook precisa usar https")
    try:
        porta = partes.port or 443
        enderecos = {info[4][0] for info in socket.getaddrinfo(partes.hostname, porta)}
    except (socket.gaierror, ValueError) as erro:
        raise UrlDeWebhookInvalida("Não foi possível resolver o host do webhook") from erro
    if not enderecos or any(_endereco_interno(str(endereco)) for endereco in enderecos):
        raise UrlDeWebhookInvalida("A URL do webhook não pode apontar para rede interna")
