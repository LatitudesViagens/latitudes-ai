"""Testa o token de acesso pessoal: lê (só lê) as configurações de login e da
Data API dos dois projetos. Mostra só se funcionou e quantas opções vieram."""

import os

import truststore

truststore.inject_into_ssl()

import httpx  # noqa: E402

API = "https://api.supabase.com/v1/projects"
token = os.environ["SUPABASE_ACCESS_TOKEN"]

for lado in ("ORIGEM", "DESTINO"):
    ref = os.environ[f"{lado}_REF"]

    for nome, caminho in (("login (auth)", "config/auth"), ("Data API", "postgrest")):
        resposta = httpx.get(
            f"{API}/{ref}/{caminho}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        detalhe = f"{len(resposta.json())} opções" if resposta.status_code == 200 else resposta.text[:120]
        print(f"{lado:8} {nome:13} → HTTP {resposta.status_code} | {detalhe}")
