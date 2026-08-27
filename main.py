import asyncio
from getpass import getpass

from database.auth import sign_in
from database.conversations import (
    create_conversation,
    list_conversations,
)
from database.messages import list_messages
from services.chat_service import process_message


def choose_conversation(
    client,
    user_id: str,
) -> dict:
    while True:
        conversations = list_conversations(
            client=client,
            user_id=user_id,
        )

        print("\nConversas disponíveis:")

        if conversations:
            for index, conversation in enumerate(
                conversations,
                start=1,
            ):
                print(f"{index}. {conversation['title']}")
        else:
            print("Nenhuma conversa encontrada.")

        print("N. Criar nova conversa")

        choice = input("\nEscolha uma opção: ").strip().lower()

        if choice == "n":
            title = input("Título da conversa: ").strip()

            return create_conversation(
                client=client,
                user_id=user_id,
                title=title or "Nova conversa",
            )

        if choice.isdigit():
            selected_index = int(choice) - 1

            if 0 <= selected_index < len(conversations):
                return conversations[selected_index]

        print("Opção inválida. Tente novamente.")


def show_history(
    client,
    conversation_id: str,
) -> None:
    messages = list_messages(
        client=client,
        conversation_id=conversation_id,
    )

    if not messages:
        print("\nEsta conversa ainda não possui mensagens.")
        return

    print("\nHistórico:")

    role_names = {
        "user": "Você",
        "assistant": "Assistente",
        "system": "Sistema",
        "tool": "Ferramenta",
    }

    for message in messages:
        role_name = role_names.get(
            message["role"],
            message["role"],
        )

        print(f"\n{role_name}: {message['content']}")


async def chat_loop(
    client,
    user_id: str,
    conversation: dict,
) -> None:
    print(f"\nConversa: {conversation['title']}")

    show_history(
        client=client,
        conversation_id=conversation["id"],
    )

    print("\nDigite /sair para encerrar.")

    while True:
        content = input("\nVocê: ").strip()

        if content.lower() in {"/sair", "sair"}:
            print("Conversa encerrada.")
            return

        if not content:
            print("Digite uma mensagem antes de enviar.")
            continue

        try:
            result = await process_message(
                client=client,
                user_id=user_id,
                conversation_id=conversation["id"],
                content=content,
            )
        except Exception as error:
            print(
                "\nNão foi possível gerar a resposta. "
                "Você pode enviar a mesma mensagem novamente."
            )
            print(f"Detalhes: {error}")
            continue

        print(
            "\nAssistente:",
            result["assistant_message"]["content"],
        )


async def main() -> None:
    print("Latitudes AI")
    print("------------")

    email = input("Email: ").strip()
    password = getpass("Senha: ")

    try:
        client, user = sign_in(
            email=email,
            password=password,
        )
    except Exception as error:
        print(f"\nNão foi possível realizar o login: {error}")
        return

    print(f"\nLogin realizado: {user.email}")

    conversation = choose_conversation(
        client=client,
        user_id=str(user.id),
    )

    await chat_loop(
        client=client,
        user_id=str(user.id),
        conversation=conversation,
    )


if __name__ == "__main__":
    asyncio.run(main())