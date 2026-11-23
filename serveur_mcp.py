"""Étape 7 : serveur MCP qui donne accès à l'entrepôt de factures (lecture seule).

Outils : lister_factures, totaux_factures, detail_facture, schema_donnees, requete_sql.
Chaque réponse contient les requêtes SQL réellement exécutées (clé "sql") et leurs paramètres.

Sécurité (défense en profondeur) :
    1. outils à filtres : valeurs toujours passées en paramètres (?), regroupements en liste blanche ;
    2. outil SQL libre : une seule instruction, de type SELECT (analysée par le parseur de DuckDB) ;
    3. connexion DuckDB en lecture seule, sans accès aux fichiers hors de data/ (ni .env, ni /etc),
       configuration verrouillée ; requêtes interrompues au-delà de 10 s, 500 lignes au plus.

Lancement (transport stdio, normalement fait par le client MCP) :
    uv run python serveur_mcp.py
"""
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import duckdb
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from entrepot import DATA_DIR, connexion

MAX_LIGNES = 500
DELAI_S = 10
LECTURE = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)

mcp = MCPServer(
    "factures",
    instructions=(
        "Accès en lecture seule à l'entrepôt des factures fournisseurs (couches bronze et silver). "
        "Pour les questions sur les factures, préfère lister_factures, totaux_factures et "
        "detail_facture ; utilise schema_donnees puis requete_sql seulement si aucun autre outil "
        "ne convient. Chaque réponse indique le SQL exécuté : cite les chiffres tels quels, "
        "sans les recalculer ni les inventer."
    ),
)


# ---------------------------------------------------------------------------
# Connexion « bac à sable » et format des réponses
# ---------------------------------------------------------------------------

@contextmanager
def connexion_securisee():
    con = connexion()                                   # base en lecture seule
    try:
        con.execute(f"SET allowed_directories = ['{DATA_DIR.resolve()}/']")
        con.execute("SET enable_external_access = false")   # aucun fichier hors de data/
        con.execute("SET lock_configuration = true")         # réglages non modifiables ensuite
        yield con
    finally:
        con.close()


def json_safe(v: Any) -> Any:
    """Montants en chaînes (jamais de float), dates en ISO."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, list):
        return [json_safe(x) for x in v]
    if isinstance(v, dict):
        return {k: json_safe(x) for k, x in v.items()}
    return v


def executer(con, sql: str, params: list | None = None, max_lignes: int = MAX_LIGNES) -> tuple[list[dict], bool]:
    """Exécute avec un délai maximal ; renvoie (lignes, tronqué ?)."""
    minuteur = threading.Timer(DELAI_S, con.interrupt)
    minuteur.start()
    try:
        cur = con.execute(sql, params or [])
        colonnes = [d[0] for d in cur.description]
        lignes = cur.fetchmany(max_lignes + 1)
    finally:
        minuteur.cancel()
    return [json_safe(dict(zip(colonnes, l))) for l in lignes[:max_lignes]], len(lignes) > max_lignes


def conseil(e: Exception) -> str:
    msg = str(e)
    if isinstance(e, duckdb.BinderException) or "does not exist" in msg:
        return "Colonne ou vue inconnue : appelle schema_donnees pour voir les noms exacts."
    if isinstance(e, duckdb.ParserException):
        return "Syntaxe SQL invalide (dialecte DuckDB). Corrige la requête et réessaie."
    if isinstance(e, duckdb.PermissionException):
        return "Accès refusé : seules les vues bronze.* et silver.* sont lisibles."
    if isinstance(e, duckdb.InterruptException):
        return f"Requête interrompue après {DELAI_S} s : ajoute des filtres ou un LIMIT."
    return "Vérifie les paramètres ; schema_donnees décrit les vues et colonnes disponibles."


def erreur(message: str, sql: list[str], aide: str) -> dict[str, Any]:
    return {"erreur": message, "sql": sql, "conseil": aide}


# ---------------------------------------------------------------------------
# Filtres communs (toujours paramétrés : aucune valeur n'est concaténée dans le SQL)
# ---------------------------------------------------------------------------

def where_filtres(fournisseur, client, statut, statut_qualite, categorie, date_debut, date_fin):
    clauses, params = [], []
    for colonne, valeur in [("fournisseur", fournisseur), ("client", client),
                            ("statut", statut), ("categorie", categorie)]:
        if valeur:
            clauses.append(f"{colonne} ILIKE ?")            # recherche partielle, sans casse
            params.append(f"%{valeur}%")
    if statut_qualite:
        clauses.append("statut_qualite = ?")
        params.append(statut_qualite)
    for operateur, valeur in [(">=", date_debut), ("<=", date_fin)]:
        if valeur:
            date.fromisoformat(valeur)                     # lève ValueError si format invalide
            clauses.append(f"date_emission {operateur} CAST(? AS DATE)")
            params.append(valeur)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


Qualite = Literal["ok", "a_verifier"]
DOC_FILTRES = """
    Filtres (tous optionnels, cumulables) :
      fournisseur, client, statut, categorie : recherche partielle insensible à la casse
        (ex. statut="retard" pour « En retard » ; statuts : Payée, En attente, En retard ;
        catégories : Conseil, Fournitures, Informatique, Maintenance) ;
      statut_qualite : "ok" ou "a_verifier" (factures aux montants incohérents) ;
      date_debut, date_fin : bornes incluses sur la date d'émission, au format AAAA-MM-JJ."""


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

@mcp.tool(annotations=LECTURE, description=f"""Liste les factures (une ligne par facture : numéro, dates,
fournisseur, client, catégorie, statut de paiement, montants HT/TVA/TTC, statut qualité), triées par
date d'émission. À utiliser pour afficher ou retrouver des factures précises. Pour des nombres ou des
sommes, utilise plutôt totaux_factures (pas de calcul à la main). {DOC_FILTRES}
    limite : nombre maximal de factures renvoyées (50 par défaut, 500 au plus).""")
def lister_factures(fournisseur: str | None = None, client: str | None = None, statut: str | None = None,
                    statut_qualite: Qualite | None = None, categorie: str | None = None,
                    date_debut: str | None = None, date_fin: str | None = None, limite: int = 50) -> dict[str, Any]:
    sql = []
    try:
        where, params = where_filtres(fournisseur, client, statut, statut_qualite, categorie, date_debut, date_fin)
        limite = max(1, min(int(limite), MAX_LIGNES))
        sql = [("SELECT numero, date_emission, date_echeance, fournisseur, client, categorie, statut, "
                "total_ht, total_tva, total_ttc, statut_qualite, anomalies FROM silver.factures"
                f"{where} ORDER BY date_emission, numero")]
        with connexion_securisee() as con:
            lignes, tronque = executer(con, sql[0], params, limite)
        return {"sql": sql, "parametres": params, "nb_lignes": len(lignes), "tronque": tronque,
                "resultats": lignes}
    except ValueError as e:
        return erreur(str(e), sql, "Les dates doivent être au format AAAA-MM-JJ (ex. 2025-01-31).")
    except duckdb.Error as e:
        return erreur(str(e), sql, conseil(e))


GROUPES = {                                             # liste blanche des regroupements
    "fournisseur": "fournisseur", "client": "client", "statut": "statut", "categorie": "categorie",
    "statut_qualite": "statut_qualite",
    "mois": "strftime(date_emission, '%Y-%m')", "annee": "CAST(year(date_emission) AS VARCHAR)",
}


@mcp.tool(annotations=LECTURE, description=f"""Compte les factures et calcule les sommes HT, TVA et TTC
(montants exacts en euros), éventuellement par groupe. À utiliser pour toute question « combien »,
« quel montant », « total », « par fournisseur / par mois ». Indique aussi combien de factures sont
signalées à vérifier (nb_a_verifier) : mentionne-les dans la réponse. {DOC_FILTRES}
    regrouper_par : optionnel, un de fournisseur, client, statut, categorie, statut_qualite, mois, annee.""")
def totaux_factures(fournisseur: str | None = None, client: str | None = None, statut: str | None = None,
                    statut_qualite: Qualite | None = None, categorie: str | None = None,
                    date_debut: str | None = None, date_fin: str | None = None,
                    regrouper_par: Literal["fournisseur", "client", "statut", "categorie",
                                           "statut_qualite", "mois", "annee"] | None = None) -> dict[str, Any]:
    sql = []
    try:
        where, params = where_filtres(fournisseur, client, statut, statut_qualite, categorie, date_debut, date_fin)
        if regrouper_par is not None and regrouper_par not in GROUPES:
            return erreur(f"regrouper_par invalide : {regrouper_par}", sql,
                          "Valeurs possibles : " + ", ".join(GROUPES))
        groupe = f"{GROUPES[regrouper_par]} AS {regrouper_par}, " if regrouper_par else ""
        sql = [(f"SELECT {groupe}count(*) AS nb_factures, "
                "count(*) FILTER (WHERE statut_qualite = 'a_verifier') AS nb_a_verifier, "
                "coalesce(sum(total_ht), 0) AS total_ht, coalesce(sum(total_tva), 0) AS total_tva, "
                f"coalesce(sum(total_ttc), 0) AS total_ttc FROM silver.factures{where}"
                + (" GROUP BY 1 ORDER BY 1" if regrouper_par else ""))]
        with connexion_securisee() as con:
            lignes, tronque = executer(con, sql[0], params)
        return {"sql": sql, "parametres": params, "nb_lignes": len(lignes), "tronque": tronque,
                "resultats": lignes}
    except ValueError as e:
        return erreur(str(e), sql, "Les dates doivent être au format AAAA-MM-JJ (ex. 2025-01-31).")
    except duckdb.Error as e:
        return erreur(str(e), sql, conseil(e))


@mcp.tool(annotations=LECTURE, description="""Renvoie le détail complet d'une facture à partir de son
numéro exact (ex. "A-2025-0001") : en-tête (fournisseur et client avec SIRET, dates, statut, mode de
paiement, IBAN, totaux, anomalies, fichier source) et toutes ses lignes d'article (désignation,
quantité, prix unitaire HT, taux de TVA, total HT). Si tu ne connais pas le numéro, cherche-le
d'abord avec lister_factures.""")
def detail_facture(numero: str) -> dict[str, Any]:
    sql = ["SELECT * FROM silver.factures WHERE numero = ?",
           "SELECT numero_ligne, designation, quantite, prix_unitaire_ht, taux_tva, total_ht "
           "FROM silver.lignes_facture WHERE numero = ? ORDER BY numero_ligne"]
    try:
        with connexion_securisee() as con:
            facture, _ = executer(con, sql[0], [numero.strip()])
            if not facture:
                return erreur(f"Aucune facture avec le numéro {numero!r}", sql[:1],
                              "Vérifie le numéro exact avec lister_factures (format ex. A-2025-0001).")
            lignes, _ = executer(con, sql[1], [numero.strip()])
        return {"sql": sql, "parametres": [numero.strip()], "facture": facture[0], "lignes": lignes,
                "nb_lignes": len(lignes)}
    except duckdb.Error as e:
        return erreur(str(e), sql, conseil(e))


@mcp.tool(annotations=LECTURE, description="""Décrit les données disponibles : les vues de l'entrepôt
(bronze.extractions, silver.factures, silver.lignes_facture, silver.quarantaine), leur rôle, et pour
chacune ses colonnes avec leur type SQL. À appeler avant d'écrire une requête avec requete_sql.""")
def schema_donnees() -> dict[str, Any]:
    sql = ["SELECT schema_name || '.' || view_name AS vue, comment AS description FROM duckdb_views() "
           "WHERE NOT internal AND schema_name IN ('bronze', 'silver') ORDER BY 1",
           "SELECT schema_name || '.' || table_name AS vue, column_name AS colonne, data_type AS type "
           "FROM duckdb_columns() WHERE schema_name IN ('bronze', 'silver') ORDER BY vue, column_index"]
    try:
        with connexion_securisee() as con:
            vues, _ = executer(con, sql[0])
            colonnes, _ = executer(con, sql[1], max_lignes=10_000)
        for v in vues:
            v["colonnes"] = [{"nom": c["colonne"], "type": c["type"]} for c in colonnes if c["vue"] == v["vue"]]
        return {"sql": sql, "parametres": [], "vues": vues}
    except duckdb.Error as e:
        return erreur(str(e), sql, conseil(e))


@mcp.tool(annotations=LECTURE, description=f"""Exécute UNE requête SQL en lecture seule (dialecte DuckDB)
sur les vues bronze.* et silver.*. Seul SELECT (y compris WITH ... SELECT) est accepté : toute
modification (INSERT, UPDATE, DELETE, DROP, CREATE, COPY, ATTACH, SET...) est refusée. Au plus
{MAX_LIGNES} lignes, {DELAI_S} s maximum. À n'utiliser que si lister_factures, totaux_factures et
detail_facture ne suffisent pas ; appelle schema_donnees d'abord pour connaître les colonnes.""")
def requete_sql(sql: str) -> dict[str, Any]:
    try:
        instructions = duckdb.extract_statements(sql)
    except duckdb.Error as e:
        return erreur(str(e), [sql], conseil(e))
    if len(instructions) != 1:
        return erreur(f"{len(instructions)} instructions reçues : une seule requête est autorisée.", [sql],
                      "Envoie une seule requête SELECT, sans point-virgule entre plusieurs ordres.")
    if instructions[0].type != duckdb.StatementType.SELECT:
        return erreur(f"Instruction {instructions[0].type.name} refusée : lecture seule (SELECT uniquement).",
                      [sql], "Reformule en SELECT ; les données ne peuvent pas être modifiées.")
    try:
        with connexion_securisee() as con:
            lignes, tronque = executer(con, sql)
        return {"sql": [sql], "parametres": [], "nb_lignes": len(lignes), "tronque": tronque,
                "resultats": lignes}
    except duckdb.Error as e:
        return erreur(str(e), [sql], conseil(e))


if __name__ == "__main__":
    mcp.run()          # transport stdio
