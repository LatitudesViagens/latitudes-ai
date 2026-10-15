"""Copia os arquivos do Storage (bucket chat-attachments) do projeto antigo
para o novo, mantendo os mesmos caminhos ({user_id}/{conversation_id}/arquivo).

A cópia do banco não leva os arquivos, só o registro deles; aqui eles são
baixados do antigo e enviados ao novo. As chaves vêm das variáveis de ambiente
e nunca são impressas. Mostra só contagens (nomes de arquivo podem conter
nomes de clientes); os caminhos que falharem vão para falhas_arquivos.txt,
neste computador, para rodar de novo só eles.

Variáveis (definir no terminal antes de rodar):
    ORIGEM_SUPABASE_URL, ORIGEM_SERVICE_ROLE_KEY   (projeto de São Paulo)
    DESTINO_SUPABASE_URL, DESTINO_SERVICE_ROLE_KEY (projeto novo nos EUA)

Uso:
    python scripts/migracao_regiao/copiar_arquivos.py --contar   # só conta, não copia
    python scripts/migracao_regiao/copiar_arquivos.py            # copia e confere
    python scripts/migracao_regiao/copiar_arquivos.py --falhas   # repete só as falhas
"""

import os
from pathlib import Path
import sys

import truststore

truststore.inject_into_ssl()

from supabase import create_client  # noqa: E402

BUCKET = "chat-attachments"
PAGE = 1000
FAILURES_FILE = Path("falhas_arquivos.txt")


def _client(prefix: str):
    url = os.getenv(f"{prefix}_SUPABASE_URL", "").strip()
    key = os.getenv(f"{prefix}_SERVICE_ROLE_KEY", "").strip()

    if not url or not key:
        raise SystemExit(f"Defina {prefix}_SUPABASE_URL e {prefix}_SERVICE_ROLE_KEY no terminal.")

    return create_client(url, key)


def _walk(client, prefix: str = "") -> list[dict]:
    """Todos os arquivos do bucket: [{"path", "size", "mimetype"}]."""
    found = []
    offset = 0

    while True:
        items = client.storage.from_(BUCKET).list(
            prefix,
            {"limit": PAGE, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
        )

        for item in items:
            path = f"{prefix}/{item['name']}" if prefix else item["name"]

            if item.get("id") is None:  # pasta
                found.extend(_walk(client, path))
            else:
                metadata = item.get("metadata") or {}
                found.append(
                    {
                        "path": path,
                        "size": int(metadata.get("size") or 0),
                        "mimetype": metadata.get("mimetype") or "application/octet-stream",
                    }
                )

        if len(items) < PAGE:
            return found

        offset += PAGE


def _summary(files: list[dict]) -> str:
    return f"{len(files)} arquivo(s), {sum(f['size'] for f in files) / (1024 * 1024):.1f} MB"


def main() -> int:
    only_count = "--contar" in sys.argv
    only_failures = "--falhas" in sys.argv
    source = _client("ORIGEM")
    target = _client("DESTINO")

    source_files = _walk(source)
    print(f"Projeto antigo: {_summary(source_files)}")
    print(f"Projeto novo:   {_summary(_walk(target))}")

    if only_count:
        return 0

    if only_failures:
        wanted = set(FAILURES_FILE.read_text(encoding="utf-8").splitlines()) if FAILURES_FILE.exists() else set()
        source_files = [f for f in source_files if f["path"] in wanted]
        print(f"Repetindo {len(source_files)} falha(s).")

    failures = []

    for number, item in enumerate(source_files, start=1):
        try:
            data = source.storage.from_(BUCKET).download(item["path"])
            target.storage.from_(BUCKET).upload(
                item["path"],
                data,
                {"content-type": item["mimetype"], "upsert": "true"},
            )
        except Exception as error:
            failures.append(item["path"])
            print(f"   falha no arquivo {number}: {type(error).__name__}", flush=True)

        if number % 25 == 0:
            print(f"   {number}/{len(source_files)} copiados", flush=True)

    FAILURES_FILE.write_text("\n".join(failures), encoding="utf-8")
    print(f"\nFalhas: {len(failures)}" + (f" (lista em {FAILURES_FILE.resolve()})" if failures else ""))

    # Conferência final: quantidade e tamanho iguais nos dois projetos.
    target_files = _walk(target)
    print(f"\nConferência — antigo: {_summary(_walk(source))} | novo: {_summary(target_files)}")
    same = {f['path']: f['size'] for f in _walk(source)} == {f['path']: f['size'] for f in target_files}
    print("RESULTADO:", "ARQUIVOS IGUAIS ✔" if same else "HÁ DIFERENÇAS — rode de novo com --falhas")
    return 0 if same and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
