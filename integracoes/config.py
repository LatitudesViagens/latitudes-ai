"""Credenciais e configuração das integrações, só por variáveis de ambiente.

Os valores nunca são impressos nem incluídos em erros.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)

RD_CRM_BASE_URL = "https://crm.rdstation.com/api/v1"

# Forma de autenticar no Envision (a documentação usa OAuth2 com usuário e
# senha em /token para todas as consultas):
#   "senha"            usuário e senha do perfil da ÁGORA no Envision
#   "senha_com_chave"  usuário e senha + a chave como client_id
#   "pura" / "bearer"  só a chave no cabeçalho Authorization
# O nível 1 do script de verificação testa todas e diz qual funcionou.
ENVISION_FORMATOS_CHAVE = ("senha", "senha_com_chave", "pura", "bearer")


def rd_crm_token() -> str | None:
    return os.getenv("RD_CRM_API_TOKEN") or None


def envision_base_url() -> str | None:
    value = (os.getenv("ENVISION_BASE_URL") or "").strip().rstrip("/")
    return value or None


def envision_api_key() -> str | None:
    return os.getenv("ENVISION_API_KEY") or None


def envision_usuario() -> str | None:
    # ENVISION_USERNAME/ENVISION_PASSWORD são os nomes usados pelo programador
    # do formulário de viajantes; os dois jeitos funcionam.
    return os.getenv("ENVISION_USUARIO") or os.getenv("ENVISION_USERNAME") or None


def envision_senha() -> str | None:
    return os.getenv("ENVISION_SENHA") or os.getenv("ENVISION_PASSWORD") or None


def _inteiro(nome: str) -> int | None:
    valor = (os.getenv(nome) or "").strip()
    return int(valor) if valor.isdigit() else None


def envision_contexto() -> dict | None:
    """additionalInfo do Records/Query (agência e conta do sistema), como o
    formulário de viajantes usa. Não são segredos, mas ficam no .env."""
    agencia = _inteiro("ENVISION_TRAVEL_AGENCY_ID")
    conta = (
        _inteiro("ENVISION_TRAVEL_AGENCY_SYSTEM_ACCOUNT_ID")
        or _inteiro("ENVISION_SYSTEM_ACCOUNT_ID")
    )

    if agencia and conta:
        return {"travelAgencyId": agencia, "systemAccountId": conta}

    return None


def envision_formato_chave() -> str:
    padrao = "senha" if envision_usuario() and envision_senha() else "pura"
    value = (os.getenv("ENVISION_AUTH_FORMATO") or padrao).strip().lower()
    return value if value in ENVISION_FORMATOS_CHAVE else padrao
