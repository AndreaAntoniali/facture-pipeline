"""Étape 4 : couche silver.

Lit la couche bronze (texte brut extrait des factures), le transforme en tables typées,
contrôle la qualité et isole les rejets :
    data/silver/factures.parquet         une ligne par facture (statut_qualite : ok / a_verifier)
    data/silver/lignes_facture.parquet   une ligne par article
    data/silver/quarantaine.parquet      les documents écartés, avec le motif

Silver est recalculée entièrement à chaque lancement (rapide, sans appel réseau, déterministe).

Lancement :
    uv run python silver.py
"""
import html
import os
import re
from collections import Counter
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

BRONZE_DIR = Path(os.environ.get("BRONZE_DIR", "data/bronze/factures"))
SILVER_DIR = Path(os.environ.get("SILVER_DIR", "data/silver"))
SILVER_LONGEST_EDGE = int(os.environ.get("SILVER_LONGEST_EDGE", 800))  # réglage OCR retenu pour silver
CENT = Decimal("0.01")
TOLERANCE = Decimal("0.01")

MONTANT = r"(-?[\d\s  ]+,\d{2})\s*€"


# ---------------------------------------------------------------------------
# 1. Conversion des valeurs (aucune « correction » : illisible -> None)
# ---------------------------------------------------------------------------

def to_decimal(s: str | None) -> Decimal | None:
    """'1 541,05 €' -> Decimal('1541.05'). Jamais de float pour des montants."""
    if s is None:
        return None
    s = re.sub(r"[\s  €%]", "", s).replace(",", ".")
    try:
        return Decimal(s).quantize(CENT)
    except InvalidOperation:
        return None


def to_date(s: str | None) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").date() if s else None
    except ValueError:
        return None


def to_int(s: str | None) -> int | None:
    try:
        return int(s.strip()) if s else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 2. Normalisation : le texte natif et le texte OCR deviennent un même format
# ---------------------------------------------------------------------------

def nettoyer(texte: str) -> str:
    """Retire le balisage Markdown de l'OCR et les marqueurs de page."""
    texte = re.sub(r"<!-- page \d+ -->", "", texte)
    texte = texte.replace("**", "")
    lignes = []
    for ligne in texte.splitlines():
        ligne = re.sub(r"^#+\s*", "", ligne).strip()
        if ligne and not re.fullmatch(r"-{3,}", ligne):
            lignes.append(ligne)
    return "\n".join(lignes)


def cellules(tr: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)]


def articles_html(texte: str) -> list[list[str]]:
    """Tableaux HTML (sortie OCR) -> lignes [désignation, qté, PU, TVA, total].
    Seules les lignes à 5 cellules <td> sont des articles (l'en-tête est en <th>,
    et l'OCR met parfois les totaux dans un second tableau à 2 colonnes)."""
    rows = [cellules(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", texte, flags=re.S)]
    return [r for r in rows if len(r) == 5]


def html_en_texte(texte: str) -> str:
    """Remplace chaque tableau HTML par une ligne de texte par rangée :
    '<tr><td>Total HT</td><td>148,26 €</td></tr>' -> 'Total HT 148,26 €'."""
    def tableau(m: re.Match) -> str:
        rows = [" ".join(cellules(tr)) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), flags=re.S)]
        return "\n" + "\n".join(r for r in rows if r) + "\n"
    return re.sub(r"<table.*?</table>", tableau, texte, flags=re.S)


def articles_natif(texte: str) -> list[list[str]]:
    """Texte natif : une cellule par ligne. On repère chaque article par sa fin
    (quantité, prix, taux, total) ; la désignation peut tenir sur plusieurs lignes."""
    lignes = texte.splitlines()
    try:
        debut = lignes.index("Désignation") + 5    # saute l'en-tête de 5 colonnes
    except ValueError:
        return []
    rows, designation, i = [], [], debut
    while i < len(lignes) and lignes[i] != "Total HT":
        fin = lignes[i + 1:i + 5]
        if (len(fin) == 4 and re.fullmatch(r"\d+", fin[0]) and re.fullmatch(MONTANT, fin[1])
                and fin[2].endswith("%") and re.fullmatch(MONTANT, fin[3])):
            rows.append([" ".join(designation + [lignes[i]]), *fin])
            designation, i = [], i + 5
        else:
            designation.append(lignes[i])
            i += 1
    return rows


# ---------------------------------------------------------------------------
# 3. Parsing
# ---------------------------------------------------------------------------

def champ(texte: str, label: str) -> str | None:
    # Insensible à la casse : l'OCR écrit parfois « TVA Intracom. » au lieu de « TVA intracom. »
    m = re.search(rf"^{label}\s*:\s*(.+)$", texte, flags=re.M | re.I)
    return m.group(1).strip() if m else None


def total(texte: str, label: str) -> Decimal | None:
    # Natif : montant à la ligne suivante ; OCR : sur la même ligne.
    m = re.search(rf"^{label}\s*\n?\s*{MONTANT}\s*$", texte, flags=re.M)
    return to_decimal(m.group(1)) if m else None


def parse_facture(texte_brut: str) -> tuple[dict, list[dict]]:
    """Texte bronze (natif ou OCR) -> (facture, lignes). Les valeurs illisibles valent None."""
    a_tableau_html = "<table" in texte_brut
    rows = articles_html(texte_brut) if a_tableau_html else []
    texte = nettoyer(html_en_texte(texte_brut))
    if not a_tableau_html:
        rows = articles_natif(texte)

    en_tete = [l for l in texte.splitlines() if l.upper() != "FACTURE"]
    taux = re.search(r"^TVA (\d+(?:,\d+)?) %", texte, flags=re.M)
    facture = {
        "numero": champ(texte, "Numéro de facture"),
        "date_emission": to_date(champ(texte, "Date d'émission")),
        "date_echeance": to_date(champ(texte, "Date d'échéance")),
        "categorie": champ(texte, "Catégorie"),
        "statut": champ(texte, "Statut"),
        "fournisseur": en_tete[0] if en_tete else None,
        "fournisseur_siret": champ(texte, "SIRET"),
        "fournisseur_tva_intracom": champ(texte, r"TVA intracom\."),
        "client": champ(texte, "Client"),
        "client_siret": champ(texte, "SIRET client"),
        "mode_paiement": champ(texte, "Mode de paiement"),
        "iban": champ(texte, "IBAN"),
        "taux_tva": to_decimal(taux.group(1)) if taux else None,
        "total_ht": total(texte, "Total HT"),
        "total_tva": total(texte, "Total TVA"),
        "total_ttc": total(texte, "Total TTC"),
    }
    lignes = [{
        "numero_ligne": n,
        "designation": r[0] if len(r) > 0 else None,
        "quantite": to_int(r[1]) if len(r) > 1 else None,
        "prix_unitaire_ht": to_decimal(r[2]) if len(r) > 2 else None,
        "taux_tva": to_decimal(r[3]) if len(r) > 3 else None,
        "total_ht": to_decimal(r[4]) if len(r) > 4 else None,
    } for n, r in enumerate(rows, start=1)]
    return facture, lignes


# ---------------------------------------------------------------------------
# 4. Contrôles qualité
# ---------------------------------------------------------------------------

OBLIGATOIRES = ["numero", "date_emission", "total_ht", "total_tva", "total_ttc"]


def motif_rejet(facture: dict, lignes: list[dict]) -> str | None:
    """Rejet = document inutilisable -> quarantaine."""
    manquants = [c for c in OBLIGATOIRES if facture[c] is None]
    if manquants:
        return "champs illisibles : " + ", ".join(manquants)
    if not lignes:
        return "aucune ligne d'article"
    return None


def ecart(a: Decimal | None, b: Decimal | None) -> bool:
    return a is not None and b is not None and abs(a - b) > TOLERANCE


def anomalies(facture: dict, lignes: list[dict]) -> list[str]:
    """Signalement = facture gardée mais à vérifier. On ne corrige rien."""
    out = []
    for l in lignes:
        if None in (l["quantite"], l["prix_unitaire_ht"], l["total_ht"]):
            out.append(f"ligne {l['numero_ligne']} illisible")
        elif ecart((l["quantite"] * l["prix_unitaire_ht"]).quantize(CENT, ROUND_HALF_UP), l["total_ht"]):
            out.append(f"ligne {l['numero_ligne']} : qté × PU ≠ total")
    totaux_lignes = [l["total_ht"] for l in lignes if l["total_ht"] is not None]
    if len(totaux_lignes) == len(lignes) and ecart(sum(totaux_lignes), facture["total_ht"]):
        out.append("somme des lignes ≠ total HT")
    if facture["taux_tva"] is not None and ecart(
            (facture["total_ht"] * facture["taux_tva"] / 100).quantize(CENT, ROUND_HALF_UP),
            facture["total_tva"]):
        out.append("total HT × taux ≠ TVA")
    if ecart(facture["total_ht"] + facture["total_tva"], facture["total_ttc"]):
        out.append("HT + TVA ≠ TTC")
    if facture["date_echeance"] and facture["date_echeance"] < facture["date_emission"]:
        out.append("échéance avant émission")
    return out


# ---------------------------------------------------------------------------
# 5. Lecture bronze, écriture silver
# ---------------------------------------------------------------------------

DEC = pa.decimal128(12, 2)
SCHEMA_FACTURES = pa.schema([
    ("numero", pa.string()), ("date_emission", pa.date32()), ("date_echeance", pa.date32()),
    ("categorie", pa.string()), ("statut", pa.string()),
    ("fournisseur", pa.string()), ("fournisseur_siret", pa.string()),
    ("fournisseur_tva_intracom", pa.string()),
    ("client", pa.string()), ("client_siret", pa.string()),
    ("mode_paiement", pa.string()), ("iban", pa.string()),
    ("taux_tva", pa.decimal128(5, 2)), ("total_ht", DEC), ("total_tva", DEC), ("total_ttc", DEC),
    ("statut_qualite", pa.string()), ("anomalies", pa.list_(pa.string())),
    ("sha256", pa.string()), ("fichier", pa.string()), ("methode_extraction", pa.string()),
    ("date_extraction", pa.timestamp("us", tz="UTC")),
])
SCHEMA_LIGNES = pa.schema([
    ("numero", pa.string()), ("numero_ligne", pa.int32()), ("designation", pa.string()),
    ("quantite", pa.int32()), ("prix_unitaire_ht", DEC), ("taux_tva", pa.decimal128(5, 2)),
    ("total_ht", DEC),
])
SCHEMA_QUARANTAINE = pa.schema([
    ("sha256", pa.string()), ("fichier", pa.string()), ("methode_extraction", pa.string()),
    ("numero", pa.string()), ("motif", pa.string()), ("texte", pa.string()),
    ("date_rejet", pa.timestamp("us", tz="UTC")),
])


def lire_bronze(longest_edge: int = SILVER_LONGEST_EDGE) -> list[dict]:
    """Une extraction par fichier (sha256) : l'extraction native pour les PDF texte,
    l'OCR à la résolution `longest_edge` pour les scans (plusieurs réglages cohabitent en bronze)."""
    fichiers = sorted(BRONZE_DIR.glob("*.parquet"))
    if not fichiers:
        raise SystemExit(f"Aucun Parquet dans {BRONZE_DIR}/ : lance d'abord ocr.py")
    docs = {}
    for d in pq.ParquetDataset(fichiers).read().to_pylist():
        if d["methode"] == "ocr" and d["longest_edge"] != longest_edge:
            continue
        if d["sha256"] not in docs or d["date_extraction"] > docs[d["sha256"]]["date_extraction"]:
            docs[d["sha256"]] = d
    # Ordre stable : premier ingéré d'abord (c'est lui qu'on garde en cas de doublon)
    return sorted(docs.values(), key=lambda d: (d["date_extraction"], d["fichier"]))


def ecrire(rows: list[dict], schema: pa.Schema, nom: str) -> None:
    SILVER_DIR.mkdir(parents=True, exist_ok=True)
    out = SILVER_DIR / nom
    tmp = out.with_suffix(".tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), tmp)
    tmp.replace(out)


def main():
    maintenant = datetime.now(timezone.utc)
    factures, lignes, quarantaine, vus = [], [], [], {}

    for doc in lire_bronze():
        facture, articles = parse_facture(doc["texte"])
        motif = motif_rejet(facture, articles)
        if motif is None and facture["numero"] in vus:
            motif = f"doublon du numéro {facture['numero']} (déjà vu dans {vus[facture['numero']]})"
        if motif:
            quarantaine.append({"sha256": doc["sha256"], "fichier": doc["fichier"],
                                "methode_extraction": doc["methode"], "numero": facture["numero"],
                                "motif": motif, "texte": doc["texte"], "date_rejet": maintenant})
            continue
        vus[facture["numero"]] = doc["fichier"]
        anos = anomalies(facture, articles)
        factures.append({**facture, "statut_qualite": "a_verifier" if anos else "ok",
                         "anomalies": anos, "sha256": doc["sha256"], "fichier": doc["fichier"],
                         "methode_extraction": doc["methode"],
                         "date_extraction": doc["date_extraction"]})
        lignes += [{"numero": facture["numero"], **a} for a in articles]

    factures.sort(key=lambda f: f["numero"])
    lignes.sort(key=lambda l: (l["numero"], l["numero_ligne"]))
    quarantaine.sort(key=lambda q: q["fichier"])
    ecrire(factures, SCHEMA_FACTURES, "factures.parquet")
    ecrire(lignes, SCHEMA_LIGNES, "lignes_facture.parquet")
    ecrire(quarantaine, SCHEMA_QUARANTAINE, "quarantaine.parquet")

    qualite = Counter(f["statut_qualite"] for f in factures)
    print(f"{len(factures)} facture(s) en silver : {qualite['ok']} ok, {qualite['a_verifier']} à vérifier"
          f" | {len(lignes)} ligne(s) | {len(quarantaine)} en quarantaine")
    for ano, n in Counter(a for f in factures for a in f["anomalies"]).most_common():
        print(f"  à vérifier : {ano} ({n})")
    for q in quarantaine:
        print(f"  quarantaine : {q['fichier']} -> {q['motif']}")
    print(f"-> {SILVER_DIR}/")


if __name__ == "__main__":
    main()
