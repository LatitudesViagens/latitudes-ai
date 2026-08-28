from supabase import Client

from database.client import get_supabase_client


def sign_in(email: str, password: str):
    client: Client = get_supabase_client()

    auth_response = client.auth.sign_in_with_password(
        {
            "email": email.strip().lower(),
            "password": password,
        }
    )

    if auth_response.user is None or auth_response.session is None:
        raise RuntimeError("Não foi possível autenticar o usuário.")

    return client, auth_response.user


def request_password_reset(email: str) -> None:
    clean_email = email.strip().lower()

    if not clean_email:
        raise ValueError("Informe o e-mail corporativo.")

    client: Client = get_supabase_client()
    client.auth.reset_password_email(clean_email)


def reset_password_with_code(
    email: str,
    code: str,
    new_password: str,
) -> None:
    clean_email = email.strip().lower()
    clean_code = code.strip()

    if not clean_email or not clean_code:
        raise ValueError("Informe o e-mail e o código recebido.")

    if len(new_password) < 8:
        raise ValueError("A nova senha deve possuir pelo menos 8 caracteres.")

    client: Client = get_supabase_client()

    verification_response = client.auth.verify_otp(
        {
            "email": clean_email,
            "token": clean_code,
            "type": "recovery",
        }
    )

    if (
        verification_response.user is None
        or verification_response.session is None
    ):
        raise RuntimeError("O código informado é inválido ou expirou.")

    update_response = client.auth.update_user(
        {
            "password": new_password,
        }
    )

    if update_response.user is None:
        raise RuntimeError("Não foi possível atualizar a senha.")

    client.auth.sign_out()