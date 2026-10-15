"""Testa, só lendo, a lista de contas (API admin) no projeto novo com a chave
service_role do cofre. Mostra só quantas contas vieram ou o tipo do erro."""

import os

import truststore

truststore.inject_into_ssl()

import httpx  # noqa: E402

for lado in ("ORIGEM", "DESTINO"):
    resposta = httpx.get(
        f"{os.environ[f'{lado}_SUPABASE_URL']}/auth/v1/admin/users",
        params={"page": 1, "per_page": 1000},
        headers={
            "apikey": os.environ[f"{lado}_SERVICE_ROLE_KEY"],
            "Authorization": f"Bearer {os.environ[f'{lado}_SERVICE_ROLE_KEY']}",
        },
        timeout=30,
    )

    if resposta.status_code == 200:
        print(f"{lado:8} HTTP 200 | {len(resposta.json().get('users', []))} conta(s)")
    else:
        print(f"{lado:8} HTTP {resposta.status_code} | {resposta.text[:200]}")
