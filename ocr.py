from openai import OpenAI
import os
import pymupdf
import base64
import time
import csv
from pathlib import Path
import json
from datetime import datetime, timezone
client = OpenAI(base_url=os.environ["OCR_BASE_URL"], api_key=os.environ["OCR_API_KEY"])
MODEL = os.environ["OCR_MODEL"]
LONGEST_EDGE = 1540
LANDING = Path(os.environ.get("OUT_DIR", "data/landing/factures"))
MANIFEST = LANDING.parent.parent / "bronze" / "manifest_factures.csv"
OCR_DIR = MANIFEST.parent / "ocr"

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



def factures_image() -> list[dict]:
    with MANIFEST.open() as f:
        return [row for row in csv.DictReader(f) if row["type"] == "image"]

def ocr_facture(row: dict) -> None:
    out = OCR_DIR / f"{row['sha256']}.json"
    if out.exists():                      # déjà traité : on ne refait pas d'appel OCR
        print("déjà fait :", row["fichier"])
        return

    pages = []
    with pymupdf.open(LANDING / row["fichier"]) as doc:
        for i, page in enumerate(doc, start=1):
            texte, duree = ocr_image(render_page(page))
            pages.append({"page": i, "texte": texte, "duree_s": round(duree, 2)})

    out.write_text(json.dumps({
        "fichier": row["fichier"],
        "sha256": row["sha256"],
        "methode": "ocr",
        "modele": MODEL,
        "longest_edge": LONGEST_EDGE,
        "date_extraction": datetime.now(timezone.utc).isoformat(),
        "pages": pages,
    }, ensure_ascii=False, indent=2))
    print("ok :", row["fichier"], [p["duree_s"] for p in pages])

def main():
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    erreurs = []
    a = factures_image()
    # TODO parallélisation : remplacer cette boucle séquentielle (~6 s par page) par un
    # ThreadPoolExecutor(max_workers=N) qui soumet ocr_facture(row) pour chaque facture.
    # Les appels OCR attendent le réseau, donc des threads suffisent, et le serveur vLLM
    # traite ensemble les requêtes qui arrivent en même temps.
    # Garder le try/except pour chaque facture : une erreur ne doit pas arrêter les autres.
    for row in a:
        try:
            ocr_facture(row)
        except Exception as e:            # service OCR indisponible, timeout, PDF illisible…
            print("ERREUR :", row["fichier"], repr(e))
            erreurs.append(row["fichier"])
    print(f"Terminé, {len(erreurs)} erreur(s) : elles seront retraitées au prochain lancement.")

if __name__ == "__main__":
    main()



# response = client.chat.completions.create(
#     model="lightonocr-3-1b-test",
#     messages=[{"role": "user", "content": "Hello, how are you?"}],
#     temperature=0.7,
#     max_tokens=512,
# )
# print(response.choices[0].message.content)