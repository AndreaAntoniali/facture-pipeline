"""Tests du serveur MCP (étape 7) : chaque outil est appelé PAR LE PROTOCOLE MCP (client en mémoire)
sur l'entrepôt réel, et son résultat est comparé à une requête DuckDB directe.

Prérequis : silver.py puis entrepot.py init.   Lancement : uv run pytest
"""
import asyncio
from decimal import Decimal

import pytest
from mcp import Client

from entrepot import ENTREPOT, connexion
from serveur_mcp import mcp

pytestmark = pytest.mark.skipif(not ENTREPOT.exists(), reason="entrepôt absent : lancer entrepot.py init")


def appeler(outil: str, **arguments) -> dict:
    async def _appel():
        async with Client(mcp) as client:
            r = await client.call_tool(outil, arguments)
            assert not r.is_error, r.content
            return r.structured_content
    return asyncio.run(_appel())


def direct(sql: str, params=None):
    with connexion() as con:
        return con.execute(sql, params or []).fetchall()


# --- chaque outil ----------------------------------------------------------

def test_liste_des_outils():
    async def _liste():
        async with Client(mcp) as client:
            return {t.name for t in (await client.list_tools()).tools}
    assert asyncio.run(_liste()) == {"lister_factures", "totaux_factures", "detail_facture",
                                     "schema_donnees", "requete_sql"}


def test_lister_factures_filtres():
    r = appeler("lister_factures", fournisseur="maury", statut="retard", limite=500)
    attendu = direct("SELECT count(*) FROM silver.factures WHERE fournisseur ILIKE '%maury%' "
                     "AND statut = 'En retard'")[0][0]
    assert r["nb_lignes"] == attendu > 0
    assert all("Maury" in f["fournisseur"] and f["statut"] == "En retard" for f in r["resultats"])
    assert r["sql"] and "?" in r["sql"][0]                  # SQL transparent et paramétré


def test_lister_factures_limite_et_periode():
    r = appeler("lister_factures", date_debut="2026-01-01", limite=3)
    assert r["nb_lignes"] == 3 and r["tronque"] is True
    assert all(f["date_emission"] >= "2026-01-01" for f in r["resultats"])


def test_totaux_exacts():
    r = appeler("totaux_factures", statut="retard", date_debut="2025-01-01", date_fin="2025-12-31")
    n, ttc = direct("SELECT count(*), sum(total_ttc) FROM silver.factures WHERE statut = 'En retard' "
                    "AND year(date_emission) = 2025")[0]
    assert r["resultats"][0]["nb_factures"] == n
    assert Decimal(r["resultats"][0]["total_ttc"]) == ttc     # montant exact, en chaîne


def test_totaux_regroupes():
    r = appeler("totaux_factures", regrouper_par="statut")
    par_statut = {g["statut"]: Decimal(g["total_ttc"]) for g in r["resultats"]}
    assert par_statut == dict(direct("SELECT statut, sum(total_ttc) FROM silver.factures GROUP BY 1"))


def test_detail_facture():
    r = appeler("detail_facture", numero="A-2025-0001")
    assert r["facture"]["numero"] == "A-2025-0001"
    assert r["nb_lignes"] == direct("SELECT count(*) FROM silver.lignes_facture "
                                    "WHERE numero = 'A-2025-0001'")[0][0] == 3
    assert len(r["sql"]) == 2


def test_schema_donnees():
    r = appeler("schema_donnees")
    vues = {v["vue"]: v for v in r["vues"]}
    assert set(vues) == {"bronze.extractions", "silver.factures", "silver.lignes_facture", "silver.quarantaine"}
    assert {"nom": "total_ttc", "type": "DECIMAL(12,2)"} in vues["silver.factures"]["colonnes"]
    assert vues["silver.factures"]["description"]


def test_requete_sql_lecture():
    r = appeler("requete_sql", sql="WITH t AS (SELECT * FROM silver.factures) SELECT count(*) AS n FROM t")
    assert r["resultats"] == [{"n": 100}] and r["sql"]


# --- sécurité --------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DROP VIEW silver.factures",
    "CREATE TABLE silver.pirate AS SELECT 1",
    "DELETE FROM silver.factures",
    "COPY (SELECT * FROM silver.factures) TO 'data/fuite.csv'",
    "ATTACH 'data/autre.duckdb'",
    "SET enable_external_access = true",
    "INSTALL httpfs",
    "SELECT 1; DROP VIEW silver.factures",
    "SELECT * FROM read_text('.env')",
    "SELECT * FROM read_csv('/etc/passwd')",
])
def test_modifications_et_fuites_refusees(sql):
    r = appeler("requete_sql", sql=sql)
    assert "erreur" in r and r["conseil"]
    assert direct("SELECT count(*) FROM silver.factures")[0][0] == 100    # rien n'a bougé


def test_injection_dans_un_filtre():
    r = appeler("lister_factures", fournisseur="x' OR 1=1 --")
    assert r["nb_lignes"] == 0                             # la valeur est un paramètre, pas du SQL


def test_erreur_exploitable_par_le_llm():
    r = appeler("requete_sql", sql="SELECT montant FROM silver.factures")
    assert "montant" in r["erreur"] and "schema_donnees" in r["conseil"]
    r = appeler("detail_facture", numero="ZZ-0000")
    assert "erreur" in r and "lister_factures" in r["conseil"]
    r = appeler("lister_factures", date_debut="31/12/2025")
    assert "AAAA-MM-JJ" in r["conseil"]
