"""Étape 1 : détermine pour chaque PDF s'il a une couche texte ("texte") ou s'il faut l'OCR ("image").

Méthode : on extrait le texte natif avec PyMuPDF ; s'il n'y a rien, c'est un scan.

Sortie : un manifeste CSV (fichier, sha256, type) + un résumé à l'écran.

Lancement :
    uv run --with pymupdf python classify_factures.py                 # dossier par défaut
    uv run --with pymupdf python classify_factures.py data/landing/factures
"""
import csv
import hashlib
import sys
from collections import Counter
from pathlib import Path

import pymupdf

IN_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "data/bronze/factures")
MANIFEST = IN_DIR.parent / "manifest_factures.csv"


def classify(path: Path) -> dict:
    with pymupdf.open(path) as doc:
        text = "".join(page.get_text() for page in doc).strip()
    return {
        "fichier": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "type": "texte" if text else "image",
    }


def main():
    rows = [classify(p) for p in sorted(IN_DIR.rglob("*.pdf"))]
    if not rows:
        sys.exit(f"Aucun PDF dans {IN_DIR}/")

    with MANIFEST.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print("Résumé :", dict(Counter(r["type"] for r in rows)), f"-> {MANIFEST}")


if __name__ == "__main__":
    main()
