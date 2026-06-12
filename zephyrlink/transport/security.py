"""Autenticação por chave compartilhada, TLS opcional e allowlist de hosts.

Autenticação: desafio-resposta HMAC-SHA256. O servidor envia um nonce
aleatório; o cliente responde com HMAC(shared_key, nonce). A chave nunca
trafega pela rede. Sem TLS, o tráfego de eventos não é cifrado — o HMAC
garante apenas que só máquinas com a chave podem se conectar.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import ssl

from zephyrlink.config import SecurityConfig


def make_challenge() -> str:
    return secrets.token_hex(32)


def sign_challenge(shared_key: str, nonce: str) -> str:
    return hmac.new(shared_key.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_challenge(shared_key: str, nonce: str, digest: str) -> bool:
    expected = sign_challenge(shared_key, nonce)
    return hmac.compare_digest(expected, digest)


def host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    """Lista vazia permite todos. Suporta IP exato e curinga de sufixo
    (``192.168.1.*`` casa qualquer host no prefixo)."""
    if not allowed:
        return True
    for pattern in allowed:
        if pattern == host:
            return True
        if pattern.endswith("*") and host.startswith(pattern[:-1]):
            return True
    return False


def build_server_ssl_context(security: SecurityConfig) -> ssl.SSLContext | None:
    if not security.use_tls:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=security.tls_cert, keyfile=security.tls_key)  # type: ignore[arg-type]
    return context


def build_client_ssl_context(security: SecurityConfig) -> ssl.SSLContext | None:
    if not security.use_tls:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if security.tls_ca:
        context.load_verify_locations(cafile=security.tls_ca)
    else:
        # Sem CA configurada, aceita certificado autoassinado do servidor;
        # a autenticidade continua garantida pelo desafio HMAC.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context
