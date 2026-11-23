from openai import OpenAI
import os
import pymupdf
import base64
import time
import csv
from pathlib import Path
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

client = OpenAI(base_url=os.environ["OCR_BASE_URL"], api_key=os.environ["OCR_API_KEY"])
MODEL = os.environ["OCR_MODEL"]
LONGEST_EDGE = 800
BUCKET = os.environ.get("S3_BUCKET", "factures")
LANDING = Path(os.environ.get("OUT_DIR", "data/landing/factures"))
MANIFEST = LANDING.parent / "manifest_factures.csv"      # écrit par classify_factures.py
BRONZE_DIR = Path(os.environ.get("BRONZE_DIR", "data/bronze/factures"))
LIMIT = int(os.environ.get("LIMIT", 0))                  # pour tester sur N factures

SCHEMA = pa.schema([
    ("fichier", pa.string()),
    ("sha256", pa.string()),
    ("source", pa.string()),
    ("taille_octets", pa.int64()),
    ("nb_pages", pa.int32()),
    ("methode", pa.string()),
    ("modele", pa.string()),
    ("longest_edge", pa.int32()),
    ("texte", pa.string()),
    ("duree_s", pa.float64()),
    ("date_extraction", pa.timestamp("us", tz="UTC")),
])

def render_page(page: pymupdf.Page) -> bytes:
    zoom = LONGEST_EDGE / max(page.rect.width, page.rect.height)
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")



def ocr_image(png: bytes) -> tuple[str, float]:
    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
    start = time.perf_counter()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        temperature=0.1,
        max_tokens=4096,
    )
    return response.choices[0].message.content, time.perf_counter() - start



def factures() -> list[dict]:
    with MANIFEST.open() as f:
        return list(csv.DictReader(f))          # toutes, plus seulement "image"


def cle(sha256: str, methode: str, longest_edge: int | None) -> tuple:
    """Identifie une extraction : même fichier (contenu) + même méthode + mêmes réglages."""
    return (sha256, methode, longest_edge)


def deja_traitees() -> set[tuple]:
    """Relit toute la couche bronze pour savoir ce qui a déjà été extrait."""
    fichiers = sorted(BRONZE_DIR.glob("*.parquet"))
    if not fichiers:
        return set()
    t = pq.ParquetDataset(fichiers).read(columns=["sha256", "methode", "longest_edge"])
    return {cle(*r.values()) for r in t.to_pylist()}


def extract_facture(row: dict) -> tuple[dict, int]:
    """Extrait une facture. Renvoie la ligne bronze et le nombre d'appels OCR faits."""
    natif = row["type"] == "texte"
    path = LANDING / row["fichier"]
    textes, appels_ocr, debut = [], 0, time.perf_counter()
    with pymupdf.open(path) as doc:
        nb_pages = doc.page_count
        for i, page in enumerate(doc, start=1):
            if natif:
                texte = page.get_text()             # extraction native, aucun appel réseau
            else:
                texte, _ = ocr_image(render_page(page))
                appels_ocr += 1
            textes.append(f"<!-- page {i} -->\n{texte}")

    return {
        "fichier": row["fichier"],
        "sha256": row["sha256"],
        "source": f"s3://{BUCKET}/{row['fichier']}",
        "taille_octets": path.stat().st_size,
        "nb_pages": nb_pages,
        "methode": "texte" if natif else "ocr",
        "modele": None if natif else MODEL,
        "longest_edge": None if natif else LONGEST_EDGE,
        "texte": "\n\n".join(textes),
        "duree_s": round(time.perf_counter() - debut, 2),
        "date_extraction": datetime.now(timezone.utc),
    }, appels_ocr


def ecrire_bronze(lignes: list[dict]) -> Path | None:
    """Ajoute un nouveau fichier Parquet (on ne modifie jamais les existants)."""
    if not lignes:
        return None
    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    out = BRONZE_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.parquet"
    tmp = out.with_suffix(".tmp")
    pq.write_table(pa.Table.from_pylist(lignes, schema=SCHEMA), tmp)
    tmp.replace(out)                      # renommage atomique : jamais de Parquet à moitié écrit
    return out


def main():
    deja = deja_traitees()
    a_traiter = factures()[:LIMIT] if LIMIT else factures()
    nouvelles, erreurs, n_deja, n_ocr = [], [], 0, 0
    # TODO parallélisation : remplacer cette boucle séquentielle (~6 s par page) par un
    # ThreadPoolExecutor(max_workers=N) qui soumet extract_facture(row) pour chaque facture.
    # Les appels OCR attendent le réseau, donc des threads suffisent, et le serveur vLLM
    # traite ensemble les requêtes qui arrivent en même temps.
    # Garder le try/except pour chaque facture : une erreur ne doit pas arrêter les autres.
    try:
        for row in a_traiter:
            natif = row["type"] == "texte"
            if cle(row["sha256"], "texte" if natif else "ocr", None if natif else LONGEST_EDGE) in deja:
                n_deja += 1                   # déjà en bronze : aucun appel OCR
                continue
            try:
                ligne, appels = extract_facture(row)
                nouvelles.append(ligne)
                n_ocr += appels
                print("ok :", row["fichier"], ligne["methode"], f"{ligne['duree_s']} s")
            except Exception as e:            # service OCR indisponible, timeout, PDF illisible…
                print("ERREUR :", row["fichier"], repr(e))
                erreurs.append(row["fichier"])
    finally:                              # même après Ctrl+C, on sauvegarde ce qui a été extrait
        out = ecrire_bronze(nouvelles)
        print(f"\n{len(nouvelles)} nouvelle(s), {n_deja} déjà en bronze, {n_ocr} appel(s) OCR, "
              f"{len(erreurs)} erreur(s) (retraitées au prochain lancement)")
        print("Écrit :", out or "rien (aucune nouvelle facture)")


if __name__ == "__main__":
    main()



# response = client.chat.completions.create(
#     model="lightonocr-3-1b-test",
#     messages=[{"role": "user", "content": "Hello, how are you?"}],
#     temperature=0.7,
#     max_tokens=512,
# )
# print(response.choices[0].message.content)