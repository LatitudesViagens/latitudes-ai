"""Compara o inventário do projeto antigo com o do novo (CSV do inventario.sql).

Uso:
    python scripts/migracao_regiao/comparar_inventario.py antigo.csv novo.csv

Mostra só o que difere. Versões de extensões podem diferir sem problema
(o projeto novo vem com versões mais recentes); o resto precisa bater.
"""

import csv
import sys


def ler(caminho: str) -> dict[tuple[str, str], str]:
    with open(caminho, encoding="utf-8-sig", newline="") as arquivo:
        return {(linha["tipo"], linha["nome"]): linha["valor"] for linha in csv.DictReader(arquivo)}


def main() -> int:
    antigo, novo = ler(sys.argv[1]), ler(sys.argv[2])
    faltando = sorted(set(antigo) - set(novo))
    sobrando = sorted(set(novo) - set(antigo))
    diferentes = sorted(k for k in set(antigo) & set(novo) if antigo[k] != novo[k])

    # Extensões padrão do projeto novo que o antigo não usava não são problema.
    sobrando = [k for k in sobrando if k[0] != "extensao"]
    so_versao = [k for k in diferentes if k[0] == "extensao"]
    diferentes = [k for k in diferentes if k[0] != "extensao"]

    print(f"Itens no antigo: {len(antigo)} | no novo: {len(novo)}\n")

    for titulo, chaves, mostrar in (
        ("FALTANDO no novo", faltando, lambda k: antigo[k]),
        ("A MAIS no novo", sobrando, lambda k: novo[k]),
        ("DIFERENTES", diferentes, lambda k: f"antigo={antigo[k]} | novo={novo[k]}"),
    ):
        print(f"== {titulo}: {len(chaves)}")
        for chave in chaves:
            print(f"   {chave[0]:11} {chave[1]}  →  {mostrar(chave)}")
        print()

    if so_versao:
        print(f"(Extensões com versão diferente, normal: {', '.join(k[1] for k in so_versao)})\n")

    ok = not (faltando or sobrando or diferentes)
    print("RESULTADO:", "TUDO IGUAL ✔" if ok else "HÁ DIFERENÇAS — não troque as chaves ainda")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
