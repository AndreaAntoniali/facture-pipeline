# De la facture scannée à l'agent IA

Chaîne de traitement de factures fournisseurs PDF, de bout en bout :

```
S3 ──► landing (PDF) ──► bronze (texte brut) ──► silver (tables typées) ──► entrepôt DuckDB ──► serveur MCP ──► IA
        étape 1           étapes 2-3 (OCR)        étape 4                    étape 6            étape 7
```

- Les factures **texte** sont lues directement. Les factures **scannées** passent par l'OCR **LightOnOCR-3** (Hyperfluid).
- Les données sont stockées en **Parquet** (couches bronze et silver) et interrogées en SQL avec **DuckDB**.
- Un **serveur MCP** en lecture seule les expose à une IA (Claude Code, Claude Desktop…).
- Choix techniques, résultats et réponses aux questions de la consigne : voir **[RAPPORT.md](RAPPORT.md)**.

---

## 1. Prérequis

| Outil | Pourquoi | Installation |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | installe Python 3.12 et toutes les dépendances | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Identifiants S3 Hyperfluid | télécharger les factures | fournis par l'enseignant |
| Clé d'API Hyperfluid | appeler LightOnOCR-3 | console Hyperfluid → page du modèle → créer une clé |
| *(facultatif)* DBeaver | consulter l'entrepôt en graphique | `flatpak install flathub io.dbeaver.DBeaverCommunity` |
| *(facultatif)* Claude Code | discuter avec les données via MCP | [claude.com/claude-code](https://claude.com/claude-code) |

## 2. Installation

```bash
git clone <url-du-depot>
cd projet
uv sync                       # crée .venv avec les versions exactes de uv.lock
cp .env.example .env          # puis remplir le fichier .env
```

Variables du `.env` (le fichier n'est **jamais commité**) :

| Variable | Valeur |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | identifiants S3 fournis |
| `AWS_ENDPOINT_URL` | `https://dams-5.s3-gateway.console.hyperfluid.cloud` (déjà rempli) |
| `AWS_DEFAULT_REGION`, `S3_BUCKET` | `default`, `factures` (déjà remplis) |
| `OUT_DIR` | `data/landing/factures` (déjà rempli) |
| `OCR_BASE_URL` | URL du point d'accès, indiquée sur la page du modèle LightOnOCR-3 (se termine par `/v1`) |
| `OCR_API_KEY` | ta clé d'API Hyperfluid |
| `OCR_MODEL` | nom du modèle indiqué sur la page (ex. `lightonocr-3-1b-test`) |

## 3. Lancer tout le pipeline

Lance ces commandes **depuis le dossier `projet/`**, dans l'ordre. Le premier lancement prend environ **6 minutes**, essentiellement pour l'OCR des 50 scans.

```bash
uv run --env-file .env python download_factures.py     # 1. télécharge les PDF       -> data/landing/factures/
uv run --env-file .env python classify_factures.py     # 1. texte ou image ?          -> data/landing/manifest_factures.csv
uv run --env-file .env python ocr.py                   # 3. extraction + OCR (bronze) -> data/bronze/factures/run_*.parquet
uv run python silver.py                                # 4. tables typées + qualité   -> data/silver/*.parquet
uv run python entrepot.py init                         # 6. vues SQL                  -> data/entrepot.duckdb
```

Chaque commande peut être **relancée sans risque** :

| Commande | Comportement à la relance |
|---|---|
| `download_factures.py` | ne télécharge que les nouveaux fichiers |
| `ocr.py` | ne retraite (et ne ré-OCRise) aucune facture déjà en bronze |
| `silver.py` et `entrepot.py init` | recalculent tout, en quelques secondes et sans appel réseau |

Quand on relance `ocr.py`, la dernière ligne doit afficher `0 nouvelle(s), 100 déjà en bronze, 0 appel(s) OCR`.

### Vérifier que tout fonctionne

```bash
uv run pytest                                          # 31 tests : silver, entrepôt, serveur MCP
uv run python entrepot.py sql "SELECT statut, count(*) AS n, sum(total_ttc) AS ttc FROM silver.factures GROUP BY 1"
```

Résultat attendu : 100 factures au total (58 « Payée », 21 « En attente », 21 « En retard »).

## 4. Détail des étapes

### Étape 1 : récupérer et classer les factures
- `download_factures.py` : `--list` liste le bucket sans rien télécharger.
- `classify_factures.py` : une facture sans texte natif est classée « image » et devra passer par l'OCR.

### Étape 2 : tester l'OCR sur une page
Pour envoyer une page au modèle et afficher le texte reconnu et le temps de traitement :
```bash
uv run --env-file .env python -c "
import pymupdf, ocr
with pymupdf.open('data/landing/factures/facture_A-2025-0003_scan.pdf') as doc:
    texte, duree = ocr.ocr_image(ocr.render_page(doc[0]))
print(texte); print(f'--- {duree:.2f} s')"
```

### Étape 3 : bronze
`ocr.py` extrait chaque facture, en texte natif ou en OCR, et ajoute un fichier `run_<date>.parquet` par lancement. Les fichiers existants ne sont jamais modifiés.

Variables utiles :

| Variable | Rôle |
|---|---|
| `OCR_LONGEST_EDGE` | résolution envoyée à l'OCR (1540 par défaut) |
| `LIMIT=5` | ne traiter que 5 factures, pour un essai |
| `BRONZE_DIR` | écrire ailleurs, pour un test |

Si l'OCR est indisponible, les factures concernées sont affichées en erreur et **retraitées au lancement suivant**.

### Étape 4 : silver
`silver.py` produit `factures`, `lignes_facture` et `quarantaine` :
- montants en `DECIMAL(12,2)` ;
- colonne `statut_qualite` à `ok` ou `a_verifier`, avec la liste des `anomalies` ;
- `SILVER_LONGEST_EDGE` choisit la résolution OCR utilisée pour les scans (1540 par défaut).

### Étape 5 : évaluer l'OCR
```bash
uv run --env-file .env python evaluation.py preparer   # tire 20 scans, crée leurs images (déjà fait, résultat versionné)
uv run python evaluation.py evaluer                    # précision champ par champ -> eval/resultats.md
```
`evaluer` compare 800 px et 1540 px. Il faut donc que les deux résolutions soient en bronze. Pour obtenir les extractions à 800 px :
```bash
OCR_LONGEST_EDGE=800 uv run --env-file .env python ocr.py
```

La vérité terrain est dans `eval/verite_terrain.csv`.

### Étape 6 : interroger l'entrepôt

L'entrepôt est une base DuckDB qui ne contient **que des vues** sur les fichiers Parquet. Les données n'existent qu'en un exemplaire, et chaque requête voit les dernières données.

| Vue | Contenu |
|---|---|
| `bronze.extractions` | texte brut de chaque extraction (natif ou OCR, toutes résolutions) |
| `silver.factures` | une ligne par facture |
| `silver.lignes_facture` | une ligne par article (jointure sur `numero`) |
| `silver.quarantaine` | documents rejetés, avec leur motif |

**En ligne de commande :**
```bash
uv run python entrepot.py sql "SELECT * FROM silver.factures WHERE statut_qualite = 'a_verifier'"
```

**Depuis Python :**
```python
from entrepot import connexion
with connexion() as con:                     # toujours en lecture seule
    print(con.sql("SELECT fournisseur, sum(total_ttc) FROM silver.factures GROUP BY 1"))
```

**Depuis DBeaver :**
1. *Nouvelle connexion* → **DuckDB**. DBeaver propose de télécharger le pilote : accepte.
2. *Path* : le chemin absolu de `projet/data/entrepot.duckdb`.
3. Onglet *Driver properties* : ajoute **`duckdb.read_only` = `true`**. C'est indispensable pour ne pas bloquer les autres programmes.
4. Déplie `bronze` ou `silver`, puis *Vues*. Ou ouvre un éditeur SQL : `SELECT * FROM silver.factures;`

**Sur une autre machine ou dans un conteneur :** les vues contiennent des chemins absolus, et la base n'est pas versionnée. Régénère-la sur place, par exemple :
```bash
DATA_DIR=/data uv run python entrepot.py init
```

### Étape 7 : le serveur MCP

`serveur_mcp.py` expose l'entrepôt en **lecture seule**. Chaque réponse contient le **SQL réellement exécuté**.

| Outil | Rôle |
|---|---|
| `lister_factures` | factures filtrées (fournisseur, client, statut, catégorie, statut qualité, période) |
| `totaux_factures` | nombre et sommes HT / TVA / TTC, avec regroupement optionnel (fournisseur, mois…) |
| `detail_facture` | une facture et toutes ses lignes |
| `schema_donnees` | vues, colonnes, types et descriptions |
| `requete_sql` | SQL libre, un seul `SELECT` (500 lignes et 10 s au maximum) |

**Avec Claude Code :** le fichier `.mcp.json` du projet déclare déjà le serveur.
```bash
claude                        # lancé dans projet/ ; accepter le serveur « factures »
```
Vérifie avec `/mcp` que tu vois `factures · connected`, puis pose une question, par exemple « Quel est le montant TTC des factures en retard en 2025 ? ». `Ctrl+O` affiche le détail des appels et le SQL. Une conversation enregistrée est disponible dans [`eval/conversation_mcp.md`](eval/conversation_mcp.md).

**Avec Claude Desktop :** ajoute ceci dans `claude_desktop_config.json`, puis redémarre l'application :
```json
{"mcpServers": {"factures": {"command": "uv",
  "args": ["run", "--directory", "/chemin/absolu/vers/projet", "python", "serveur_mcp.py"]}}}
```

**Sans LLM, avec l'inspecteur MCP officiel (Node.js requis) :**
```bash
npx @modelcontextprotocol/inspector uv run python serveur_mcp.py
```

## 5. Structure du dépôt

```
projet/
├── download_factures.py   étape 1 : S3 -> data/landing/
├── classify_factures.py   étape 1 : détection texte / image
├── ocr.py                 étapes 2-3 : rendu des pages, appel OCR, bronze incrémental
├── silver.py              étape 4 : normalisation, parsing, contrôles qualité, quarantaine
├── evaluation.py          étape 5 : échantillon, comparaison à la vérité terrain
├── entrepot.py            étape 6 : vues DuckDB, connexion en lecture seule
├── serveur_mcp.py         étape 7 : serveur MCP
├── test_*.py              tests (uv run pytest)
├── eval/                  vérité terrain, résultats, conversation MCP (versionnés)
├── data/                  données générées (non versionnées) : landing, bronze, silver, entrepot.duckdb
├── .env.example           modèle de configuration (copier en .env)
├── .mcp.json              déclaration du serveur MCP pour Claude Code
├── CONSIGNES.md           sujet du projet
└── RAPPORT.md             rapport : choix, réponses aux questions, résultats
```

## 6. Dépannage

| Symptôme | Solution |
|---|---|
| `KeyError: 'OCR_BASE_URL'` (ou une autre variable) | Il manque `--env-file .env`, ou la variable est vide dans `.env`. |
| `ocr.py` : « No such file … manifest_factures.csv » | Lance d'abord `classify_factures.py`. |
| `ocr.py` : `APIConnectionError` ou `401` | Vérifie l'URL et la clé de l'OCR. Les factures en erreur seront retraitées à la relance. |
| « entrepot.duckdb n'existe pas » | `uv run python entrepot.py init` |
| `No files found that match the pattern` | Les Parquet ont été déplacés, ou la base vient d'une autre machine : relance `entrepot.py init`. |
| DBeaver : `Could not set lock on file` | Ajoute la propriété `duckdb.read_only = true` à la connexion. |
| `/mcp` affiche `failed` | Lance `uv run python serveur_mcp.py` à la main pour voir l'erreur. Vérifie aussi que l'entrepôt existe. |
| Le serveur MCP n'apparaît pas dans Claude Code | Lance `claude` **dans `projet/`**. Si tu l'as refusé, utilise `claude mcp reset-project-choices`. |
