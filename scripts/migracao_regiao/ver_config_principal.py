"""Mostra as opções principais de login dos dois projetos (só leitura, sem segredos)."""

import os

import truststore

truststore.inject_into_ssl()

import httpx  # noqa: E402

CAMPOS = (
    "site_url",
    "uri_allow_list",
    "disable_signup",
    "external_email_enabled",
    "mailer_autoconfirm",
    "password_min_length",
    "password_required_characters",
    "mailer_otp_exp",
    "mailer_otp_length",
    "jwt_exp",
    "smtp_host",
    "smtp_admin_email",
    "rate_limit_email_sent",
)

for lado in ("ORIGEM", "DESTINO"):
    ref = os.environ[f"{lado}_REF"]
    dados = httpx.get(
        f"https://api.supabase.com/v1/projects/{ref}/config/auth",
        headers={"Authorization": f"Bearer {os.environ['SUPABASE_ACCESS_TOKEN']}"},
        timeout=30,
    ).json()
    print(f"\n{lado} ({ref[:8]}...)")
    for campo in CAMPOS:
        print(f"   {campo:32} {dados.get(campo)!r}")
