"""Étape 5 : mesurer la qualité de l'OCR par rapport à une vérité terrain.

    uv run --env-file .env python evaluation.py preparer
        Tire 20 factures scannées (graine fixe), rend leurs pages en PNG dans data/eval/images/
        et télécharge le pipeline de référence du bucket (gold/factures.parquet) dans data/reference/.
        La vérité terrain est ensuite saisie à partir des images dans eval/verite_terrain.csv.

    uv run python evaluation.py evaluer
        Re-parse les extractions bronze de chaque réglage OCR avec le parseur de silver,
        compare champ par champ à la vérité terrain et écrit eval/resultats.md.
"""
import csv
import os
import random
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pymupdf

from silver import BRONZE_DIR, lire_bronze, parse_facture, to_date, to_decimal

MANIFEST = Path("data/landing/manifest_factures.csv")
LANDING = Path(os.environ.get("OUT_DIR", "data/landing/factures"))
IMG_DIR = Path("data/eval/images")
REFERENCE = Path("data/reference/gold_factures.parquet")
VERITE = Path("eval/verite_terrain.csv")
RESULTATS = Path("eval/resultats.md")
GRAINE, TAILLE = 42, 20
REGLAGES = [800, 1540]                    # résolutions OCR comparées (longest_edge)

CHAMPS = ["numero", "date_emission", "date_echeance", "fournisseur", "fournisseur_siret",
          "fournisseur_tva_intracom", "client", "client_siret", "categorie",
          "statut", "mode_paiement", "iban", "total_ht", "total_tva", "total_ttc", "nb_lignes"]
DATES = {"date_emission", "date_echeance"}
MONTANTS = {"total_ht", "total_tva", "total_ttc"}


def echantillon() -> list[str]:
    with MANIFEST.open() as f:
        scans = sorted(r["fichier"] for r in csv.DictReader(f) if r["type"] == "image")
    return sorted(random.Random(GRAINE).sample(scans, TAILLE))


# ---------------------------------------------------------------------------
# preparer
# ---------------------------------------------------------------------------

def telecharger_reference() -> None:
    if REFERENCE.exists():
        return
    import boto3
    from botocore.config import Config
    s3 = boto3.client("s3", endpoint_url=os.environ["AWS_ENDPOINT_URL"],
                      region_name=os.environ.get("AWS_DEFAULT_REGION", "default"),
                      config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(os.environ.get("S3_BUCKET", "factures"), "gold/factures.parquet", str(REFERENCE))


def preparer() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for fichier in echantillon():
        with pymupdf.open(LANDING / fichier) as doc:
            for i, page in enumerate(doc, start=1):
                zoom = 2000 / max(page.rect.width, page.rect.height)
                page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(IMG_DIR / f"{Path(fichier).stem}_p{i}.png")
        print("image :", fichier)
    telecharger_reference()
    print(f"-> {IMG_DIR}/ ; saisir la vérité terrain dans {VERITE} (colonnes : fichier, {', '.join(CHAMPS)})")


# ---------------------------------------------------------------------------
# evaluer
# ---------------------------------------------------------------------------

def normaliser(champ: str, v):
    """Met une valeur sous une forme comparable (types de silver)."""
    if v is None or v == "":
        return None
    if champ in DATES:
        return v if isinstance(v, date) else to_date(str(v))
    if champ in MONTANTS:
        return v.quantize(Decimal("0.01")) if isinstance(v, Decimal) else to_decimal(str(v).replace(".", ","))
    if champ == "nb_lignes":
        return int(v)
    return " ".join(str(v).split())


def lire_verite() -> dict[str, dict]:
    with VERITE.open() as f:
        return {r["fichier"]: {c: normaliser(c, r[c]) for c in CHAMPS} for r in csv.DictReader(f)}


def extractions_ocr(longest_edge: int, fichiers: set[str]) -> dict[str, dict]:
    """Champs extraits par notre parseur silver depuis l'OCR bronze à cette résolution."""
    out = {}
    for d in lire_bronze(longest_edge):
        if d["fichier"] in fichiers and d["methode"] == "ocr":
            facture, lignes = parse_facture(d["texte"])
            out[d["fichier"]] = {**{c: normaliser(c, facture.get(c)) for c in CHAMPS if c != "nb_lignes"},
                                 "nb_lignes": len(lignes), "_duree": d["duree_s"] / d["nb_pages"]}
    return out


def extractions_reference(fichiers: set[str]) -> dict[str, dict]:
    if not REFERENCE.exists():
        return {}
    noms = {"numero": "numero_facture", "date_emission": "date_facture", "total_ht": "montant_ht",
            "total_tva": "montant_tva", "total_ttc": "montant_ttc"}
    out = {}
    for r in pq.read_table(REFERENCE).to_pylist():
        if r["filename"] in fichiers:
            out[r["filename"]] = {c: normaliser(c, r.get(noms.get(c, c))) for c in CHAMPS
                                  if noms.get(c, c) in r}
    return out


def evaluer() -> None:
    verite = lire_verite()
    fichiers = set(verite)
    sources = {f"LightOnOCR {px} px": extractions_ocr(px, fichiers) for px in REGLAGES}
    sources["Référence du bucket"] = extractions_reference(fichiers)
    for nom, ext in sources.items():
        manquants = fichiers - set(ext)
        if manquants and ext:
            sys.exit(f"{nom} : pas d'extraction pour {sorted(manquants)} (lancer ocr.py avec ce réglage)")

    n = len(verite)
    entete = "| Champ | " + " | ".join(sources) + " |"
    lignes = [f"# Évaluation OCR : précision champ par champ ({n} factures scannées)", "",
              entete, "|---|" + "---|" * len(sources)]
    erreurs = []
    for champ in CHAMPS:
        cellules = []
        for nom, ext in sources.items():
            if not ext or champ not in next(iter(ext.values())):
                cellules.append("—")
                continue
            ko = [(f, verite[f][champ], ext[f][champ]) for f in sorted(fichiers) if ext[f][champ] != verite[f][champ]]
            erreurs += [(nom, f, champ, attendu, obtenu) for f, attendu, obtenu in ko]
            cellules.append(f"{100 * (n - len(ko)) / n:.0f} % ({len(ko)} err.)")
        lignes.append(f"| {champ} | " + " | ".join(cellules) + " |")

    completes, durees = [], []
    for nom, ext in sources.items():
        if not ext:
            completes.append("—"); durees.append("—")
            continue
        ok = sum(all(ext[f].get(c, verite[f][c]) == verite[f][c] for c in CHAMPS) for f in fichiers)
        completes.append(f"{100 * ok / n:.0f} % ({ok}/{n})")
        d = [e["_duree"] for e in ext.values() if "_duree" in e]
        durees.append(f"{sum(d) / len(d):.2f} s" if d else "—")
    lignes += ["| **Facture entièrement correcte** | " + " | ".join(completes) + " |",
               "| *Temps moyen par page* | " + " | ".join(durees) + " |", ""]

    lignes += ["## Erreurs", "", "| Source | Fichier | Champ | Attendu | Obtenu |", "|---|---|---|---|---|"]
    lignes += [f"| {s} | {f} | {c} | {a} | {o} |" for s, f, c, a, o in erreurs]

    # Ce qu'un humain doit contrôler : 2 factures au hasard + les cases où la vérité
    # terrain contredit à la fois l'OCR 800 px et l'OCR 1540 px.
    a_controler = sorted(random.Random(GRAINE + 1).sample(sorted(fichiers), 2))
    ocr = [sources[f"LightOnOCR {px} px"] for px in REGLAGES]
    suspects = [(f, c, verite[f][c], ocr[0][f][c], ocr[1][f][c]) for f in sorted(fichiers) for c in CHAMPS
                if all(o[f][c] != verite[f][c] for o in ocr)]
    lignes += ["", "## À contrôler à la main", "",
               "Factures à comparer au PDF, champ par champ : " + ", ".join(a_controler), "",
               "Cases où la vérité terrain contredit les deux réglages OCR :", "",
               "| Fichier | Champ | Vérité terrain | 800 px | 1540 px |", "|---|---|---|---|---|"]
    lignes += [f"| {f} | {c} | {v} | {a} | {b} |" for f, c, v, a, b in suspects]

    RESULTATS.write_text("\n".join(lignes) + "\n")
    print("\n".join(lignes[:len(CHAMPS) + 7]))
    print(f"\n{len(erreurs)} erreur(s), {len(suspects)} case(s) à contrôler -> {RESULTATS}")


if __name__ == "__main__":
    {"preparer": preparer, "evaluer": evaluer}[sys.argv[1] if len(sys.argv) > 1 else "evaluer"]()
