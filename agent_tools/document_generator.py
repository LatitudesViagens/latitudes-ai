from contextvars import ContextVar
import re

from services.document_export import (
    export_csv,
    export_docx,
    export_file_name,
    export_pdf,
    export_xlsx,
    has_table,
    remove_advice_sections,
)


# Arquivos gerados durante uma tentativa do agente. services/agent_runner.py
# cria uma lista nova a cada tentativa; services/chat_service.py salva os
# arquivos no Storage com o cliente autenticado do usuário.
GENERATED_FILES: ContextVar[list[dict] | None] = ContextVar(
    "generated_files",
    default=None,
)

# Trava contra arquivos não pedidos: só há geração quando a mensagem atual
# do usuário pede um arquivo. services/agent_runner.py define o valor.
FILE_REQUESTED: ContextVar[bool] = ContextVar(
    "file_requested",
    default=False,
)

FILE_REQUEST_PATTERN = re.compile(
    r"\b(?:pdf|docs?|docx|word|planilhas?|excel|xlsx|csv|arquivos?|"
    r"documentos?|export\w*|baix\w*|download)\b",
    flags=re.IGNORECASE,
)


def user_requested_file(message: str) -> bool:
    return FILE_REQUEST_PATTERN.search(message or "") is not None


MAX_GENERATED_FILES = 3
MAX_FILE_SIZE = 10 * 1024 * 1024

FORMATS = {
    "pdf": (
        "application/pdf",
        lambda content, title: export_pdf(content, title),
    ),
    "docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document",
        lambda content, title: export_docx(content, title),
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet",
        lambda content, title: export_xlsx(content, title),
    ),
    "csv": (
        "text/csv",
        lambda content, title: export_csv(content),
    ),
}

FORMAT_ALIASES = {
    "word": "docx",
    "doc": "docx",
    "docs": "docx",
    "google docs": "docx",
    "documento": "docx",
    "excel": "xlsx",
    "planilha": "xlsx",
    "xls": "xlsx",
}


async def generate_document(
    formato: str,
    titulo: str,
    conteudo: str,
) -> dict:
    """Gera um arquivo com a identidade visual da Latitudes para o usuário baixar.

    Use quando o usuário pedir um PDF, documento Word, planilha Excel ou CSV.
    O arquivo aparece para o usuário logo abaixo da sua resposta.

    Args:
        formato: "pdf", "docx" (Word), "xlsx" (planilha Excel) ou "csv".
        titulo: título curto do documento, por exemplo "Roteiro do Egito".
        conteudo: somente o roteiro (ou a tabela pedida) em Markdown, em tom
            de documento para o cliente: sem saudações, sem falar de si
            mesma, sem perguntas e sem seções de recomendações, observações,
            dicas, notas ou fontes, que ficam na conversa.
            Para "xlsx" e "csv", inclua uma ou mais tabelas em Markdown com
            linha de cabeçalho; cada tabela vira uma aba da planilha.
    """
    collector = GENERATED_FILES.get()

    if not FILE_REQUESTED.get():
        return {
            "status": "erro",
            "mensagem": (
                "Nenhum arquivo foi gerado: o usuário não pediu um arquivo "
                "nesta mensagem. Não diga que existe arquivo pronto. "
                "Responda com o conteúdo no chat e, se fizer sentido, "
                "pergunte se ele quer o arquivo."
            ),
        }

    if collector is None:
        return {
            "status": "erro",
            "mensagem": "A geração de arquivos não está disponível agora.",
        }

    if len(collector) >= MAX_GENERATED_FILES:
        return {
            "status": "erro",
            "mensagem": "Limite de arquivos por resposta atingido.",
        }

    format_key = str(formato).strip().lower().lstrip(".")
    format_key = FORMAT_ALIASES.get(format_key, format_key)

    if format_key not in FORMATS:
        return {
            "status": "erro",
            "mensagem": "Formato inválido. Use pdf, docx, xlsx ou csv.",
        }

    clean_title = " ".join(str(titulo).split())[:120] or "Documento Latitudes"
    # O documento leva só o roteiro; recomendações ficam na conversa.
    clean_content = remove_advice_sections(str(conteudo).strip())

    if not clean_content:
        return {
            "status": "erro",
            "mensagem": "O conteúdo do documento está vazio.",
        }

    if format_key in {"xlsx", "csv"} and not has_table(clean_content):
        return {
            "status": "erro",
            "mensagem": (
                "Planilhas precisam de pelo menos uma tabela em Markdown "
                "com linha de cabeçalho. Gere novamente com a tabela."
            ),
        }

    mime_type, build = FORMATS[format_key]

    try:
        data = build(clean_content, clean_title)
    except Exception as error:
        print(
            f"[DOCUMENT] format={format_key} error={type(error).__name__}",
            flush=True,
        )
        return {
            "status": "erro",
            "mensagem": "Não foi possível montar o arquivo.",
        }

    if len(data) > MAX_FILE_SIZE:
        return {
            "status": "erro",
            "mensagem": "O arquivo ficou grande demais. Reduza o conteúdo.",
        }

    file_name = export_file_name(clean_title, format_key)
    collector.append(
        {
            "name": file_name,
            "mime_type": mime_type,
            "data": data,
        }
    )

    return {
        "status": "ok",
        "arquivo": file_name,
        "mensagem": (
            "Arquivo gerado. Ele já aparece para o usuário baixar logo "
            "abaixo da sua resposta. Responda em uma ou duas frases, sem "
            "repetir o conteúdo do arquivo."
        ),
    }
