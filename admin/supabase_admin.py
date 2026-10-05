"""Acesso administrativo ao Supabase (chave service_role).

A chave fica só no servidor: é lida aqui, usada numa conexão criada para
cada operação e nunca guardada em st.session_state, cookies ou logs. Toda
função pública exige o cliente de quem pediu e confere o papel TI no banco
antes de usar a chave.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client
from supabase.client import ClientOptions

from database.roles import is_ti


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "latitudes_agent" / ".env"

load_dotenv(dotenv_path=ENV_FILE)

SERVICE_ROLE_ENV = "SUPABASE_SERVICE_ROLE_KEY"


class NotAuthorizedError(PermissionError):
    """Quem pediu não tem o papel TI."""


class AdminNotConfiguredError(RuntimeError):
    """SUPABASE_SERVICE_ROLE_KEY não foi configurada no servidor."""


def is_configured() -> bool:
    return bool(os.getenv(SERVICE_ROLE_ENV))


def require_ti(client: Client) -> None:
    if not is_ti(client):
        raise NotAuthorizedError("Acesso restrito ao TI.")


def _service_client(requester: Client) -> Client:
    require_ti(requester)
    return _create_service_client()


def _create_service_client() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv(SERVICE_ROLE_ENV)

    if not supabase_url or not service_key:
        raise AdminNotConfiguredError(
            f"Defina {SERVICE_ROLE_ENV} no servidor para usar esta função."
        )

    # Conexão nova a cada operação: o cliente usa HTTP/2 e não pode ser
    # compartilhado entre sessões/threads.
    return create_client(
        supabase_url,
        service_key,
        options=ClientOptions(
            auto_refresh_token=False,
            persist_session=False,
        ),
    )


def list_user_emails(requester: Client) -> dict[str, str]:
    """{id do usuário: e-mail} de todas as contas (para os painéis do TI)."""
    service = _service_client(requester)
    emails: dict[str, str] = {}
    page = 1

    while True:
        users = service.auth.admin.list_users(
            page=page,
            per_page=1000,
        )

        for user in users:
            emails[str(user.id)] = user.email or str(user.id)

        if len(users) < 1000:
            return emails

        page += 1


def set_temporary_password(
    requester: Client,
    target_user_id: str,
    temporary_password: str,
) -> None:
    """Troca a senha de alguém (só TI). A obrigação de trocar e o registro
    do reset são gravados por quem chamou, com o login do TI."""
    service = _service_client(requester)
    service.auth.admin.update_user_by_id(
        str(target_user_id),
        {"password": temporary_password},
    )


def clear_own_password_requirement(client: Client) -> None:
    """Remove a obrigação de trocar a senha de quem está logado.

    Chamar só depois que a troca de senha deu certo. A pessoa não tem
    permissão para apagar a obrigação no banco; o servidor apaga com a
    service_role, usando o ID confirmado pelo Supabase a partir do login
    (nunca um ID vindo da tela).
    """
    user_response = client.auth.get_user()
    user = user_response.user if user_response else None

    if user is None:
        raise NotAuthorizedError("Sessão inválida.")

    service = _create_service_client()
    service.table("password_change_required").delete().eq(
        "user_id",
        str(user.id),
    ).execute()
