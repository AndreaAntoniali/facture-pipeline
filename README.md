# De la facture scannée à l'agent IA

Chaîne de traitement de factures fournisseurs PDF : téléchargement depuis S3, extraction du texte
(natif ou OCR LightOnOCR-3), couches **bronze** et **silver** en Parquet, entrepôt SQL DuckDB.

```
S3 ──► landing (PDF) ──► bronze (texte brut, Parquet) ──► silver (tables typées, Parquet) ──► entrepôt DuckDB (vues)
```

## Installation

Prérequis : [uv](https://docs.astral.sh/uv/) (il installe Python et les dépendances).

```bash
git clone <ce dépôt> && cd projet
uv sync
cp .env.example .env      # puis remplir les identifiants S3 et la clé d'API OCR
```

## Lancer le pipeline

Toutes les commandes se lancent depuis le dossier `projet/`.

| Étape | Commande | Résultat |
|---|---|---|
| 1. Télécharger les factures | `uv run --env-file .env python download_factures.py` | PDF dans `data/landing/factures/` |
| 1. Détecter texte / image | `uv run --env-file .env python classify_factures.py` | `data/landing/manifest_factures.csv` |
| 2. Tester l'OCR | voir `ocr.py` (`render_page`, `ocr_image`) | |
| 3. Bronze (extraction) | `uv run --env-file .env python ocr.py` | `data/bronze/factures/run_*.parquet` |
| 4. Silver | `uv run python silver.py` | `data/silver/{factures,lignes_facture,quarantaine}.parquet` |
| 5. Évaluation OCR | `uv run python evaluation.py evaluer` | `eval/resultats.md` |
| 6. Entrepôt | `uv run python entrepot.py init` | `data/entrepot.duckdb` (vues) |
| 7. Serveur MCP | lancé par le client MCP (voir plus bas) | outils sur l'entrepôt |

Chaque étape est **incrémentale ou idempotente** : on peut tout relancer sans risque. Bronze ne
retraite (ni ne ré-OCRise) aucun fichier déjà ingéré ; silver et l'entrepôt sont recalculés.

Réglages (variables d'environnement) : `OCR_LONGEST_EDGE` (résolution envoyée à l'OCR, 1540 par
défaut), `SILVER_LONGEST_EDGE` (réglage OCR retenu pour silver, 1540 par défaut), `DATA_DIR`
(dossier des données, `data` par défaut).

Tests : `uv run pytest`

## Interroger l'entrepôt (étape 6)

L'entrepôt est une base DuckDB qui ne contient **que des vues** sur les fichiers Parquet : les
données n'existent qu'en un exemplaire (les Parquet), et chaque requête voit les dernières données
sans rien reconstruire.

| Vue | Contenu |
|---|---|
| `bronze.extractions` | texte brut de chaque extraction (natif ou OCR, toutes résolutions) |
| `silver.factures` | une ligne par facture (montants `DECIMAL(12,2)`, dates `DATE`, `statut_qualite`) |
| `silver.lignes_facture` | une ligne par article (jointure sur `numero`) |
| `silver.quarantaine` | documents rejetés avec leur motif |

**En ligne de commande :**

```bash
uv run python entrepot.py sql "SELECT statut, count(*), sum(total_ttc) FROM silver.factures GROUP BY 1"
```

**Depuis un programme Python :**

```python
from entrepot import connexion
with connexion() as con:                     # connexion en lecture seule
    print(con.sql("SELECT * FROM silver.factures WHERE statut = 'En retard'"))
```

**Depuis DBeaver :**

1. Installer DBeaver Community (`flatpak install flathub io.dbeaver.DBeaverCommunity`).
2. *Nouvelle connexion* → **DuckDB** (DBeaver propose de télécharger le pilote).
3. *Path* : le chemin absolu de `projet/data/entrepot.duckdb`.
4. Onglet *Driver properties* : ajouter **`duckdb.read_only` = `true`**.
5. Tester avec `SELECT * FROM silver.factures LIMIT 10;`

> **Pourquoi en lecture seule ?** DuckDB autorise plusieurs processus en lecture seule en même
> temps, mais un seul en écriture. En lecture seule, DBeaver ouvert ne bloque ni le pipeline (qui
> n'écrit que des Parquet, jamais dans la base), ni les autres programmes, ni `entrepot.py init`.

**Sur une autre machine ou dans un conteneur :** les vues contiennent des chemins absolus. La base
n'est donc pas versionnée : on la régénère dans chaque environnement, par exemple
`DATA_DIR=/data uv run python entrepot.py init` dans un conteneur dont les données sont montées sur `/data`.

## Serveur MCP (étape 7)

`serveur_mcp.py` expose l'entrepôt à une IA via le [Model Context Protocol](https://modelcontextprotocol.io/)
(transport stdio, SDK officiel `mcp`). Toutes les réponses contiennent le **SQL réellement exécuté**
(`sql`) et ses paramètres ; les montants sont renvoyés en chaînes exactes, jamais en flottants.

| Outil | Rôle |
|---|---|
| `lister_factures` | factures filtrées (fournisseur, client, statut, catégorie, statut qualité, période) |
| `totaux_factures` | nombre et sommes HT / TVA / TTC, mêmes filtres, regroupement optionnel (fournisseur, mois…) |
| `detail_facture` | une facture et toutes ses lignes |
| `schema_donnees` | vues, colonnes, types et descriptions |
| `requete_sql` | SQL libre en lecture seule (500 lignes, 10 s maximum) |

**Sécurité :** valeurs des filtres toujours passées en paramètres (pas d'injection SQL) ; l'outil SQL
libre n'accepte qu'**une** instruction `SELECT` (analysée par le parseur DuckDB) ; la connexion est en
lecture seule, sans accès aux fichiers hors de `data/` (ni `.env`, ni `/etc`), configuration verrouillée.
En cas d'erreur, l'outil renvoie `erreur` + `conseil` pour que le LLM se corrige.

**Brancher sur Claude Code :** le fichier `.mcp.json` du projet déclare le serveur. Lancer `claude`
dans `projet/`, accepter le serveur « factures », vérifier avec `/mcp`, puis poser une question.
Exemple de conversation enregistrée : [`eval/conversation_mcp.md`](eval/conversation_mcp.md).

**Brancher sur Claude Desktop :** dans `claude_desktop_config.json` :

```json
{"mcpServers": {"factures": {"command": "uv",
  "args": ["run", "--directory", "/chemin/absolu/vers/projet", "python", "serveur_mcp.py"]}}}
```

**Tester :** `uv run pytest test_serveur_mcp.py` appelle chaque outil par le protocole MCP et vérifie
les résultats et les refus (DROP, COPY, ATTACH, lecture de `.env`, requêtes multiples, injection).
