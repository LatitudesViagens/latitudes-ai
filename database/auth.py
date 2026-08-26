from supabase import Client

from database.client import get_supabase_client


def sign_in(email: str, password: str):
    client: Client = get_supabase_client()

    auth_response = client.auth.sign_in_with_password(
        {
            "email": email,
            "password": password,
        }
    )

    if auth_response.user is None or auth_response.session is None:
        raise RuntimeError("Não foi possível autenticar o usuário.")

    return client, auth_response.user