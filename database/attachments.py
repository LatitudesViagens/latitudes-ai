from io import BytesIO
from pathlib import Path
from posixpath import normpath
import re
from typing import Iterable
from uuid import uuid4
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageOps
from supabase import Client


BUCKET_NAME = "chat-attachments"
MAX_FILES_PER_MESSAGE = 5
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_DOCX_XML_SIZE = 5 * 1024 * 1024
MAX_XLSX_XML_SIZE = 12 * 1024 * 1024
MAX_EXTRACTED_TEXT_SIZE = 2 * 1024 * 1024
MAX_MODEL_TEXT_SIZE = 240 * 1024
MAX_MODEL_IMAGE_DIMENSION = 1280
MODEL_IMAGE_JPEG_QUALITY = 82
DOCX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)
XLSX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "spreadsheetml.sheet"
)
CSV_MIME_TYPE = "text/csv"

ALLOWED_EXTENSIONS = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".docx": DOCX_MIME_TYPE,
    ".xlsx": XLSX_MIME_TYPE,
    ".csv": CSV_MIME_TYPE,
    ".txt": "text/plain",
    ".webp": "image/webp",
}


def _safe_filename(filename: str) -> str:
    original_name = Path(filename).name.strip()
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix.lower()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")

    return f"{safe_stem or 'arquivo'}{suffix}"


def _validate_file(
    filename: str,
    declared_mime_type: str,
    data: bytes,
) -> tuple[str, str]:
    safe_name = _safe_filename(filename)
    extension = Path(safe_name).suffix.lower()
    expected_mime_type = ALLOWED_EXTENSIONS.get(extension)

    if expected_mime_type is None:
        raise ValueError(
            f"O formato de {filename} não é permitido."
        )

    if not data:
        raise ValueError(f"O arquivo {filename} está vazio.")

    if len(data) > MAX_FILE_SIZE:
        raise ValueError(
            f"O arquivo {filename} ultrapassa o limite de 10 MB."
        )

    clean_declared_type = declared_mime_type.split(";", 1)[0].strip().lower()

    accepted_declared_types = {expected_mime_type}

    if expected_mime_type == CSV_MIME_TYPE:
        accepted_declared_types.update(
            {"application/csv", "application/vnd.ms-excel", "text/plain"}
        )

    if (
        clean_declared_type
        and clean_declared_type not in accepted_declared_types
    ):
        raise ValueError(
            f"O conteúdo de {filename} não corresponde à extensão."
        )

    if expected_mime_type.startswith("image/"):
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
        except Exception as error:
            raise ValueError(
                f"A imagem {filename} é inválida ou está corrompida."
            ) from error
    elif expected_mime_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError(f"O PDF {filename} é inválido.")
    elif expected_mime_type == "text/plain":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(
                f"O arquivo {filename} deve estar em UTF-8."
            ) from error
    elif expected_mime_type == DOCX_MIME_TYPE:
        _extract_docx_text(data, filename)
    elif expected_mime_type == XLSX_MIME_TYPE:
        _extract_xlsx_text(data, filename)
    elif expected_mime_type == CSV_MIME_TYPE:
        _extract_csv_text(data, filename)

    return safe_name, expected_mime_type


def _extract_docx_text(data: bytes, filename: str) -> str:
    try:
        with ZipFile(BytesIO(data)) as document:
            document_info = document.getinfo("word/document.xml")

            if document_info.file_size > MAX_DOCX_XML_SIZE:
                raise ValueError(
                    f"O conteúdo textual de {filename} é muito grande."
                )

            document_xml = document.read(document_info)
    except (BadZipFile, KeyError) as error:
        raise ValueError(
            f"O documento Word {filename} é inválido ou está corrompido."
        ) from error

    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as error:
        raise ValueError(
            f"Não foi possível ler o documento Word {filename}."
        ) from error

    text_parts = []

    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]

        if local_name == "t" and element.text:
            text_parts.append(element.text)
        elif local_name in {"br", "cr"}:
            text_parts.append("\n")
        elif local_name == "tab":
            text_parts.append("\t")
        elif local_name == "p":
            text_parts.append("\n")
        elif local_name == "tc":
            text_parts.append("\t")

    extracted_text = "".join(text_parts).strip()

    if not extracted_text:
        raise ValueError(
            f"O documento Word {filename} não possui texto legível."
        )

    return extracted_text


def _extract_csv_text(data: bytes, filename: str) -> str:
    try:
        extracted_text = data.decode("utf-8-sig").strip()
    except UnicodeDecodeError as error:
        raise ValueError(
            f"A planilha CSV {filename} deve estar em UTF-8."
        ) from error

    if not extracted_text:
        raise ValueError(f"A planilha CSV {filename} está vazia.")

    if len(extracted_text.encode("utf-8")) > MAX_EXTRACTED_TEXT_SIZE:
        raise ValueError(
            f"O conteúdo textual de {filename} é muito grande."
        )

    return extracted_text


def _prepare_image_for_model(data: bytes, filename: str) -> bytes:
    try:
        with Image.open(BytesIO(data)) as original_image:
            image = ImageOps.exif_transpose(original_image)
            image.thumbnail(
                (MAX_MODEL_IMAGE_DIMENSION, MAX_MODEL_IMAGE_DIMENSION),
                Image.Resampling.LANCZOS,
            )

            if image.mode in {"RGBA", "LA"}:
                rgba_image = image.convert("RGBA")
                background = Image.new(
                    "RGB",
                    rgba_image.size,
                    (255, 255, 255),
                )
                background.paste(
                    rgba_image,
                    mask=rgba_image.getchannel("A"),
                )
                image = background
            else:
                image = image.convert("RGB")

            image_buffer = BytesIO()
            image.save(
                image_buffer,
                format="JPEG",
                quality=MODEL_IMAGE_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
    except Exception as error:
        raise ValueError(
            f"Não foi possível preparar a imagem {filename} para análise."
        ) from error

    return image_buffer.getvalue()


def _prepare_text_for_model(
    content: str,
    filename: str,
) -> bytes:
    encoded_content = content.encode("utf-8")

    if len(encoded_content) <= MAX_MODEL_TEXT_SIZE:
        return encoded_content

    truncated_content = encoded_content[:MAX_MODEL_TEXT_SIZE].decode(
        "utf-8",
        errors="ignore",
    )

    return (
        f"{truncated_content}\n\n"
        f"[Conteúdo de {filename} limitado para processamento.]"
    ).encode("utf-8")

def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xlsx_cell_text(
    cell: ET.Element,
    shared_strings: list[str],
) -> str:
    cell_type = cell.attrib.get("t", "")

    if cell_type == "inlineStr":
        return "".join(
            element.text or ""
            for element in cell.iter()
            if _xml_local_name(element.tag) == "t"
        )

    value = next(
        (
            element.text or ""
            for element in cell
            if _xml_local_name(element.tag) == "v"
        ),
        "",
    )

    if cell_type == "s" and value.isdigit():
        index = int(value)

        if 0 <= index < len(shared_strings):
            return shared_strings[index]

    if cell_type == "b":
        return "VERDADEIRO" if value == "1" else "FALSO"

    return value


def _extract_xlsx_text(data: bytes, filename: str) -> str:
    try:
        with ZipFile(BytesIO(data)) as workbook:
            workbook_xml = workbook.read("xl/workbook.xml")
            relationships_xml = workbook.read(
                "xl/_rels/workbook.xml.rels"
            )

            xml_size = len(workbook_xml) + len(relationships_xml)
            shared_strings: list[str] = []

            if "xl/sharedStrings.xml" in workbook.namelist():
                shared_xml = workbook.read("xl/sharedStrings.xml")
                xml_size += len(shared_xml)
                shared_root = ET.fromstring(shared_xml)

                for item in shared_root:
                    shared_strings.append(
                        "".join(
                            element.text or ""
                            for element in item.iter()
                            if _xml_local_name(element.tag) == "t"
                        )
                    )

            if xml_size > MAX_XLSX_XML_SIZE:
                raise ValueError(
                    f"O conteúdo de {filename} é muito grande."
                )

            workbook_root = ET.fromstring(workbook_xml)
            relationships_root = ET.fromstring(relationships_xml)
            relationship_targets = {
                relationship.attrib.get("Id", ""):
                relationship.attrib.get("Target", "")
                for relationship in relationships_root
            }
            sheet_descriptors = []

            for element in workbook_root.iter():
                if _xml_local_name(element.tag) != "sheet":
                    continue

                relationship_id = next(
                    (
                        value
                        for key, value in element.attrib.items()
                        if _xml_local_name(key) == "id"
                    ),
                    "",
                )
                target = relationship_targets.get(relationship_id, "")

                if not target:
                    continue

                if target.startswith("/"):
                    sheet_path = normpath(target.lstrip("/"))
                else:
                    sheet_path = normpath(f"xl/{target}")

                if not sheet_path.startswith("xl/"):
                    raise ValueError(
                        f"A planilha {filename} possui estrutura inválida."
                    )

                sheet_descriptors.append(
                    (element.attrib.get("name", "Planilha"), sheet_path)
                )

            output_parts = []

            for sheet_name, sheet_path in sheet_descriptors:
                sheet_xml = workbook.read(sheet_path)
                xml_size += len(sheet_xml)

                if xml_size > MAX_XLSX_XML_SIZE:
                    raise ValueError(
                        f"O conteúdo de {filename} é muito grande."
                    )

                sheet_root = ET.fromstring(sheet_xml)
                rows = []

                for row in sheet_root.iter():
                    if _xml_local_name(row.tag) != "row":
                        continue

                    values = [
                        _xlsx_cell_text(cell, shared_strings)
                        for cell in row
                        if _xml_local_name(cell.tag) == "c"
                    ]
                    rows.append("\t".join(values).rstrip())

                output_parts.append(
                    f"Planilha: {sheet_name}\n" + "\n".join(rows).strip()
                )
    except ValueError:
        raise
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError(
            f"A planilha Excel {filename} é inválida ou está corrompida."
        ) from error

    extracted_text = "\n\n".join(output_parts).strip()

    if not extracted_text:
        raise ValueError(
            f"A planilha Excel {filename} não possui dados legíveis."
        )

    if len(extracted_text.encode("utf-8")) > MAX_EXTRACTED_TEXT_SIZE:
        raise ValueError(
            f"O conteúdo textual de {filename} é muito grande."
        )

    return extracted_text


def upload_chat_attachments(
    client: Client,
    user_id: str,
    conversation_id: str,
    uploaded_files: Iterable,
) -> list[dict]:
    files = list(uploaded_files)

    if len(files) > MAX_FILES_PER_MESSAGE:
        raise ValueError("Envie no máximo 5 arquivos por mensagem.")

    uploaded_metadata = []
    uploaded_paths = []

    try:
        for uploaded_file in files:
            data = uploaded_file.getvalue()
            safe_name, mime_type = _validate_file(
                filename=uploaded_file.name,
                declared_mime_type=uploaded_file.type or "",
                data=data,
            )
            storage_path = (
                f"{user_id}/{conversation_id}/"
                f"{uuid4()}-{safe_name}"
            )

            client.storage.from_(BUCKET_NAME).upload(
                path=storage_path,
                file=data,
                file_options={
                    "content-type": mime_type,
                    "upsert": "false",
                },
            )

            uploaded_paths.append(storage_path)
            uploaded_metadata.append(
                {
                    "name": safe_name,
                    "path": storage_path,
                    "mime_type": mime_type,
                    "size": len(data),
                }
            )
    except Exception:
        if uploaded_paths:
            try:
                client.storage.from_(BUCKET_NAME).remove(uploaded_paths)
            except Exception:
                pass

        raise

    return uploaded_metadata


def upload_generated_files(
    client: Client,
    user_id: str,
    conversation_id: str,
    files: list[dict],
) -> list[dict]:
    """Salva arquivos gerados pela ÁGORA na pasta privada do usuário.

    Usa o mesmo caminho dos anexos ({user_id}/{conversation_id}/...), coberto
    pelas políticas de Storage existentes.
    """
    uploaded_metadata = []
    uploaded_paths = []

    try:
        for generated_file in files:
            safe_name = _safe_filename(str(generated_file["name"]))
            data = generated_file["data"]
            mime_type = str(generated_file["mime_type"])
            storage_path = (
                f"{user_id}/{conversation_id}/"
                f"{uuid4()}-{safe_name}"
            )

            client.storage.from_(BUCKET_NAME).upload(
                path=storage_path,
                file=data,
                file_options={
                    "content-type": mime_type,
                    "upsert": "false",
                },
            )

            uploaded_paths.append(storage_path)
            uploaded_metadata.append(
                {
                    "name": safe_name,
                    "path": storage_path,
                    "mime_type": mime_type,
                    "size": len(data),
                    "generated": True,
                }
            )
    except Exception:
        if uploaded_paths:
            try:
                client.storage.from_(BUCKET_NAME).remove(uploaded_paths)
            except Exception:
                pass

        raise

    return uploaded_metadata


def download_chat_attachment(
    client: Client,
    attachment: dict,
) -> bytes:
    storage_path = str(attachment.get("path", "")).strip()

    if not storage_path:
        raise ValueError("O anexo não possui um caminho válido.")

    return client.storage.from_(BUCKET_NAME).download(storage_path)


def hydrate_chat_attachments(
    client: Client,
    attachments: list[dict] | None,
) -> list[dict]:
    hydrated = []

    for attachment in attachments or []:
        data = download_chat_attachment(
            client=client,
            attachment=attachment,
        )
        hydrated_attachment = {
            **attachment,
            "data": data,
        }

        if str(attachment.get("mime_type", "")).startswith("image/"):
            hydrated_attachment["data"] = _prepare_image_for_model(
                data=data,
                filename=str(attachment.get("name", "imagem")),
            )
            hydrated_attachment["mime_type"] = "image/jpeg"
        elif attachment.get("mime_type") == DOCX_MIME_TYPE:
            extracted_text = _extract_docx_text(
                data=data,
                filename=str(attachment.get("name", "documento.docx")),
            )
            hydrated_attachment["data"] = _prepare_text_for_model(
                f"Documento Word: {attachment.get('name', '')}\n\n"
                f"{extracted_text}",
                filename=str(attachment.get("name", "documento.docx")),
            )
            hydrated_attachment["mime_type"] = "text/plain"
        elif attachment.get("mime_type") == XLSX_MIME_TYPE:
            extracted_text = _extract_xlsx_text(
                data=data,
                filename=str(attachment.get("name", "planilha.xlsx")),
            )
            hydrated_attachment["data"] = _prepare_text_for_model(
                f"Planilha Excel: {attachment.get('name', '')}\n\n"
                f"{extracted_text}",
                filename=str(attachment.get("name", "planilha.xlsx")),
            )
            hydrated_attachment["mime_type"] = "text/plain"
        elif attachment.get("mime_type") == CSV_MIME_TYPE:
            extracted_text = _extract_csv_text(
                data=data,
                filename=str(attachment.get("name", "planilha.csv")),
            )
            hydrated_attachment["data"] = _prepare_text_for_model(
                f"Planilha CSV: {attachment.get('name', '')}\n\n"
                f"{extracted_text}",
                filename=str(attachment.get("name", "planilha.csv")),
            )
            hydrated_attachment["mime_type"] = "text/plain"
        elif attachment.get("mime_type") == "text/plain":
            text_content = data.decode("utf-8", errors="replace")
            hydrated_attachment["data"] = _prepare_text_for_model(
                text_content,
                filename=str(attachment.get("name", "arquivo.txt")),
            )

        hydrated.append(hydrated_attachment)

    return hydrated


def remove_attachments_from_messages(
    client: Client,
    messages: list[dict],
) -> None:
    paths = [
        str(attachment.get("path", "")).strip()
        for message in messages
        for attachment in message.get("attachments") or []
        if attachment.get("path")
    ]

    if paths:
        client.storage.from_(BUCKET_NAME).remove(paths)
