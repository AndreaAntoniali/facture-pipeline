"""Étape 6 : l'entrepôt de données.

Séparation stockage / calcul :
    - stockage : les fichiers Parquet écrits par ocr.py (bronze) et silver.py (silver), en un seul exemplaire ;
    - calcul   : DuckDB. La base data/entrepot.duckdb ne contient QUE des vues sur ces Parquet
                 (quelques Ko, aucune donnée). Chaque requête relit les fichiers : les nouvelles
                 données sont visibles sans rien reconstruire.

Accès concurrents : tous les lecteurs (DBeaver, scripts, serveur MCP) ouvrent la base en lecture
seule, ce que DuckDB autorise pour plusieurs processus en même temps. Le pipeline n'écrit jamais
dans la base, seulement des Parquet (de façon atomique).

Chemins : les vues contiennent des chemins absolus calculés depuis DATA_DIR. La base n'est pas
versionnée : chaque environnement (machine, conteneur, collègue) la régénère avec `init`.

    uv run python entrepot.py init                      # (re)crée data/entrepot.duckdb
    uv run python entrepot.py sql "SELECT * FROM silver.factures LIMIT 5"
    DATA_DIR=/data uv run python entrepot.py init       # ex. dans un conteneur
"""
import os
import sys
from pathlib import Path

import duckdb

PROJET = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJET / "data"))  # relatif au projet, pas au dossier courant
ENTREPOT = Path(os.environ.get("ENTREPOT", DATA_DIR / "entrepot.duckdb"))

# (schéma, vue) -> (fichiers Parquet relatifs à DATA_DIR, description)
VUES = {
    ("bronze", "extractions"): (
        "bronze/factures/*.parquet",
        "Couche bronze : texte brut extrait de chaque PDF (natif ou OCR), avec ses métadonnées. "
        "Une ligne par extraction ; un même fichier (sha256) peut avoir plusieurs extractions OCR "
        "à des résolutions différentes (longest_edge). Données immuables."),
    ("silver", "factures"): (
        "silver/factures.parquet",
        "Couche silver : une ligne par facture valide. Montants en DECIMAL(12,2), dates en DATE. "
        "statut = statut de paiement (Payée, En attente, En retard). statut_qualite = 'ok' ou "
        "'a_verifier' (montants incohérents, voir la colonne anomalies). Clé : numero."),
    ("silver", "lignes_facture"): (
        "silver/lignes_facture.parquet",
        "Couche silver : une ligne par article de facture. Jointure avec silver.factures sur numero."),
    ("silver", "quarantaine"): (
        "silver/quarantaine.parquet",
        "Couche silver : documents rejetés (illisibles ou doublons), avec le motif du rejet."),
}


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def init(data_dir: Path = DATA_DIR, entrepot: Path = ENTREPOT) -> None:
    """Crée la base de vues dans un fichier temporaire puis la met en place par renommage :
    les lecteurs déjà connectés ne sont jamais bloqués."""
    data_dir = data_dir.resolve()
    entrepot.parent.mkdir(parents=True, exist_ok=True)
    tmp = entrepot.with_name(entrepot.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        for schema in sorted({s for s, _ in VUES}):
            con.execute(f"CREATE SCHEMA {schema}")
        for (schema, vue), (motif, description) in VUES.items():
            chemin = str(data_dir / motif)
            con.execute(f"CREATE VIEW {schema}.{vue} AS "
                        f"SELECT * FROM read_parquet({sql_str(chemin)}, union_by_name = true)")
            con.execute(f"COMMENT ON VIEW {schema}.{vue} IS {sql_str(description)}")
    finally:
        con.close()
    tmp.replace(entrepot)


def connexion(entrepot: Path = ENTREPOT) -> duckdb.DuckDBPyConnection:
    """Connexion en lecture seule : plusieurs processus peuvent l'ouvrir en même temps,
    et toute tentative d'écriture dans la base est refusée."""
    if not entrepot.exists():
        raise SystemExit(f"{entrepot} n'existe pas : lance d'abord `python entrepot.py init`")
    return duckdb.connect(str(entrepot), read_only=True)


def main():
    commande = sys.argv[1] if len(sys.argv) > 1 else "aide"
    if commande == "init":
        init()
        with connexion() as con:
            print(con.sql("SELECT schema_name || '.' || view_name AS vue, comment FROM duckdb_views() "
                          "WHERE NOT internal ORDER BY 1"))
            for (schema, vue) in VUES:
                n = con.sql(f"SELECT count(*) FROM {schema}.{vue}").fetchall()[0][0]
                print(f"{schema}.{vue} : {n} ligne(s)")
        print(f"-> {ENTREPOT} ({ENTREPOT.stat().st_size // 1024} Ko, vues uniquement)")
    elif commande == "sql" and len(sys.argv) > 2:
        with connexion() as con:
            con.sql(sys.argv[2]).show(max_rows=100, max_width=200)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
