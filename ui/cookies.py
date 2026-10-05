"""Leitura dos cookies de login direto da conexão (st.context.cookies).

O componente de cookies (streamlit-cookies-manager) precisa carregar uma
janela escondida no navegador e rodar a tela de novo antes de entregar os
cookies: ~3 s a mais em cada abertura. Os mesmos cookies já chegam ao
servidor junto com a conexão, então a abertura usa esta leitura direta. O
componente continua sendo o único que GRAVA cookies (login, troca de token,
saída) e passa a valer assim que fica pronto.

Os valores continuam criptografados do mesmo jeito (mesma senha, mesmos
parâmetros), e o login restaurado continua sendo conferido no Supabase.
"""

import base64
from functools import lru_cache
from urllib.parse import unquote

from cryptography.fernet import Fernet, InvalidToken
import streamlit as st
from streamlit_cookies_manager.encrypted_cookie_manager import key_from_parameters

KEY_PARAMS_COOKIE = "EncryptedCookieManager.key_params"


def _request_cookies() -> dict[str, str]:
    """Cookies da conexão, com nome e valor decodificados como o componente
    os grava (encodeURIComponent)."""
    try:
        raw_cookies = dict(st.context.cookies)
    except Exception:
        return {}

    return {
        unquote(name): unquote(value)
        for name, value in raw_cookies.items()
    }


@lru_cache(maxsize=256)
def _fernet(
    salt: bytes,
    iterations: int,
    password: str,
) -> Fernet:
    # A derivação da chave é lenta de propósito (390 mil iterações); o
    # resultado fica em memória para não repetir a cada abertura.
    return Fernet(
        key_from_parameters(
            salt=salt,
            iterations=iterations,
            password=password,
        )
    )


def read_encrypted_cookie(
    name: str,
    prefix: str,
    password: str,
) -> str | None:
    """Valor descriptografado de um cookie do EncryptedCookieManager, lido da
    conexão. None se não existir ou não puder ser lido."""
    cookies = _request_cookies()
    encrypted_value = cookies.get(prefix + name)
    raw_key_params = cookies.get(prefix + KEY_PARAMS_COOKIE)

    if not encrypted_value or not raw_key_params:
        return None

    try:
        raw_salt, raw_iterations, _ = raw_key_params.split(":")
        fernet = _fernet(
            salt=base64.b64decode(raw_salt),
            iterations=int(raw_iterations),
            password=password,
        )
        return fernet.decrypt(encrypted_value.encode()).decode()
    except (ValueError, TypeError, InvalidToken):
        return None
