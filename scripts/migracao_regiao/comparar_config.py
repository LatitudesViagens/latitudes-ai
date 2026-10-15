"""Compara as configurações de login (auth) e da Data API do projeto antigo com
as do novo (SOMENTE LEITURA) e mostra o que difere.

Mostra o valor de números e sim/não, os endereços do site e de
redirecionamento por inteiro, só o tamanho dos textos longos (modelos de
e-mail) e NUNCA senhas, chaves ou segredos.
"""

import os
import re

import truststore

truststore.inject_into_ssl()

import httpx  # noqa: E402

API = "https://api.supabase.com/v1/projects"
SENSITIVE = re.compile(r"secret|pass|password|key|token|private", re.IGNORECASE)
SHOW_TEXT = {"site_url", "uri_allow_list", "mailer_otp_exp", "smtp_host", "smtp_port", "smtp_user", "smtp_admin_email", "smtp_sender_name"}


def _get(ref: str, path: str) -> dict:
    response = httpx.get(
        f"{API}/{ref}/{path}",
        headers={"Authorization": f"Bearer {os.environ['SUPABASE_ACCESS_TOKEN']}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _show(name: str, value) -> str:
    if value is None or value == "":
        return "(vazio)"
    if SENSITIVE.search(name):
        return "(definido)" if value else "(vazio)"
    if isinstance(value, (bool, int, float)):
        return str(value)
    if name in SHOW_TEXT:
        return str(value)
    return f"texto ({len(str(value))} caracteres)"


def main() -> None:
    for title, path in (("LOGIN (auth)", "config/auth"), ("DATA API", "postgrest")):
        old = _get(os.environ["ORIGEM_REF"], path)
        new = _get(os.environ["DESTINO_REF"], path)
        names = sorted(set(old) | set(new))
        different = [n for n in names if old.get(n) != new.get(n)]
        print(f"\n== {title}: {len(different)} de {len(names)} opções diferem")

        for name in different:
            print(f"   {name:55} antigo={_show(name, old.get(name))}  |  novo={_show(name, new.get(name))}")


if __name__ == "__main__":
    main()
