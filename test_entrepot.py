"""Tests de l'entrepôt (étape 6) sur un jeu de Parquet minuscule construit dans un dossier temporaire.

Lancement : uv run pytest
"""
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from entrepot import connexion, init


def ecrire(chemin, **colonnes):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(colonnes), chemin)


@pytest.fixture
def entrepot(tmp_path):
    data = tmp_path / "data"
    ecrire(data / "bronze/factures/run_1.parquet", sha256=["a", "b"], methode=["texte", "ocr"])
    ecrire(data / "silver/factures.parquet", numero=["F-1", "F-2"], total_ttc=[10, 20])
    ecrire(data / "silver/lignes_facture.parquet", numero=["F-1", "F-1", "F-2"], numero_ligne=[1, 2, 1])
    ecrire(data / "silver/quarantaine.parquet", sha256=["c"], motif=["doublon"])
    base = data / "entrepot.duckdb"
    init(data, base)
    return data, base


def compter(con, vue):
    return con.sql(f"SELECT count(*) FROM {vue}").fetchall()[0][0]


def test_vues(entrepot):
    _, base = entrepot
    with connexion(base) as con:
        assert compter(con, "bronze.extractions") == 2
        assert compter(con, "silver.factures") == 2
        assert compter(con, "silver.lignes_facture") == 3
        assert compter(con, "silver.quarantaine") == 1


def test_plusieurs_lecteurs_simultanes(entrepot):
    _, base = entrepot
    with connexion(base) as dbeaver, connexion(base) as programme:   # ex. DBeaver + serveur MCP
        assert compter(dbeaver, "silver.factures") == compter(programme, "silver.factures") == 2


@pytest.mark.parametrize("ordre", [
    "CREATE TABLE silver.pirate (x INT)",
    "DROP VIEW silver.factures",
    "CREATE OR REPLACE VIEW silver.factures AS SELECT 1",
])
def test_ecriture_refusee(entrepot, ordre):
    _, base = entrepot
    with connexion(base) as con, pytest.raises(duckdb.Error, match="read-only"):
        con.execute(ordre)


def test_nouvelles_donnees_visibles_sans_init(entrepot):
    data, base = entrepot
    with connexion(base) as con:                      # un lecteur reste connecté…
        ecrire(data / "silver/factures.parquet", numero=["F-1", "F-2", "F-3"], total_ttc=[10, 20, 30])
        ecrire(data / "bronze/factures/run_2.parquet", sha256=["d"], methode=["ocr"])
        assert compter(con, "silver.factures") == 3   # …et voit les nouvelles données
        assert compter(con, "bronze.extractions") == 3


def test_init_pendant_une_lecture(entrepot):
    data, base = entrepot
    with connexion(base) as con:                      # DBeaver ouvert pendant une régénération
        init(data, base)
        assert compter(con, "silver.factures") == 2
