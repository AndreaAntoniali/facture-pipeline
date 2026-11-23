# Rapport : de la facture scannée à l'agent IA

> État : étapes 1 à 7 réalisées. Les étapes 8 (agent) et 9 (industrialisation) restent à compléter.
> Tous les chiffres de ce rapport sont reproductibles avec les commandes du [README](README.md).

## 1. Architecture

```
                 ┌──────────────────────── stockage : fichiers Parquet (un seul exemplaire) ───────────────────────┐
 S3 (Hyperfluid) │                                                                                                  │
 s3://factures ──┼─► landing ──────► bronze ───────────────────► silver ──────────────────┐                        │
  100 PDF        │   PDF bruts       texte brut + métadonnées    factures, lignes,        │                        │
                 │   + manifeste     (natif ou OCR)              quarantaine (typées)     │                        │
                 └──────────────────────────────────────────────────────────────────────┼────────────────────────┘
   download_factures.py   classify_factures.py   ocr.py ──► LightOnOCR-3      silver.py │
                                                         (API Hyperfluid)               ▼
                                              calcul : DuckDB (entrepot.duckdb = vues seulement)
                                                     │                    │
                                           DBeaver (lecture seule)   serveur_mcp.py ──► client MCP / agent
```

| Brique | Choix | Justification |
|---|---|---|
| Langage, dépendances | Python 3.12, **uv** (`pyproject.toml` + `uv.lock`) | Une commande (`uv sync`) installe exactement les mêmes versions chez tout le monde. |
| Téléchargement | **boto3** sur la passerelle S3 Hyperfluid | Client S3 standard. Il faut le style d'adressage `path`, car la passerelle n'est pas AWS. |
| Détection texte / image, rendu des pages | **PyMuPDF** | Rapide, sans dépendance système, et il fait à la fois l'extraction native et le rendu PNG des pages. |
| OCR | **LightOnOCR-3**, appelé via le client `openai` | L'API Hyperfluid est compatible OpenAI. Le code OCR tient dans 2 fonctions isolées (`render_page`, `ocr_image`) dans `ocr.py`. |
| Format bronze / silver | **Parquet** (pyarrow) | Format colonne, typé (`DECIMAL`, `DATE`), compressé, lisible par tous les moteurs. Fichiers immuables, écrits de façon atomique. |
| Entrepôt | **DuckDB**, avec une base qui ne contient que des **vues** | Il interroge les Parquet directement : le stockage et le calcul sont séparés, sans copie ni serveur à administrer. |
| Accès IA | **SDK officiel `mcp`** (Python, transport stdio) | Standard du protocole. Lancé par le client lui-même, sans port réseau ouvert. |
| Tests | **pytest** (31 tests) | Ils couvrent le parseur silver, l'entrepôt et le serveur MCP, appelé par le protocole. |

## 2. Réponses aux questions, étape par étape

### Étape 1 : récupérer les factures

- `download_factures.py` télécharge les PDF du bucket dans la **zone d'arrivée** `data/landing/factures/`. Le script est incrémental : un fichier déjà présent avec la même taille est sauté. Une relance télécharge donc uniquement les nouvelles factures.
- `classify_factures.py` extrait le texte natif de chaque PDF avec PyMuPDF. **Sans texte, c'est une image** qu'il faut OCRiser. Le résultat (fichier, sha256, type) est écrit dans `data/landing/manifest_factures.csv`.
- Résultat : **50 « texte » et 50 « image »**. C'est cohérent à 100 % avec les suffixes `_texte` et `_scan` des noms de fichiers. Ces suffixes servent uniquement à vérifier la détection, **jamais à décider** : de vraies factures n'auraient pas ce suffixe.

### Étape 2 : appeler le modèle d'OCR

**Format de requête.** On envoie un message `user` qui ne contient **que l'image** de la page, en PNG encodé en base64 dans une `data:` URL, sans consigne textuelle. L'appel passe par l'API *chat completions* compatible OpenAI, avec `temperature=0.1` et `max_tokens=4096`. Le modèle est spécialisé : il n'a pas besoin de prompt.

**Résolution.** La fiche du modèle recommande de rendre la page avec un **côté le plus long de 1540 px**. Si on ne respecte pas cette recommandation, les petits caractères se dégradent. À 800 px, on a mesuré des chiffres perdus dans les identifiants : voir l'étape 5.

**Format de sortie.** Du **Markdown** :
- titres `#`, gras `**…**`, séparateurs `---` ;
- **tableaux en HTML** (`<table><tr><td>`) ;
- le rendu n'est **pas stable d'une facture à l'autre**. Les étiquettes sont parfois en gras (`**Statut** : …`), et les totaux sont tantôt en lignes de texte, tantôt dans un **second tableau HTML** (11 scans sur 50). La casse peut aussi changer (« TVA **I**ntracom. »).

**Temps par page** (appels séquentiels, mesurés sur les 50 scans) :

| Résolution | Temps moyen par page |
|---|---|
| 800 px | 5,1 s |
| 1540 px | 7,4 s |

Pour comparer : l'extraction native d'une facture texte prend moins de 10 ms.

**Compromis taille / vitesse / qualité.** Une image plus grande coûte environ 45 % de temps en plus, mais supprime les erreurs sur les longues suites de chiffres. Pour des factures, c'est le bon compromis : un SIRET faux coûte plus cher que 2 secondes.

**Indépendance vis-à-vis du fournisseur.** Tout l'OCR passe par `ocr_image(png) -> (texte, durée)`, et l'URL, la clé et le modèle sont dans le `.env`. Changer de modèle compatible OpenAI ne demande qu'un changement de configuration. Changer de moteur (Tesseract, par exemple) revient à réécrire cette seule fonction.

**Clé d'API.** Elle est dans le `.env`, qui est **ignoré par git** (`.gitignore`). Le dépôt ne contient que `.env.example`, sans valeurs. Les scripts la lisent avec `uv run --env-file .env`.

### Étape 3 : couche bronze

- **Contenu.** `data/bronze/factures/run_<date>.parquet` contient une ligne par extraction. Colonnes : `fichier`, `sha256`, `source` (s3://…), `taille_octets`, `nb_pages`, `methode` (texte ou ocr), `modele`, `longest_edge`, `texte` (pages séparées par `<!-- page N -->`), `duree_s` et `date_extraction`.
- **Incrémental.** La clé d'une extraction est **(sha256, méthode, résolution)**. Au démarrage, `ocr.py` relit tout bronze et saute ce qui y est déjà. Lancé deux fois de suite, il affiche à la seconde exécution : `0 nouvelle(s), 100 déjà en bronze, 0 appel(s) OCR`.
- **Panne de l'OCR.** Chaque facture est traitée dans un `try/except`. Testé avec une URL d'OCR invalide : les factures texte passent, les scans sont signalés en erreur, et le pipeline va au bout. Une facture en erreur n'a pas de ligne en bronze, donc elle est **retraitée automatiquement** au lancement suivant. L'écriture se fait dans un `finally` : même après un Ctrl+C, les extractions déjà faites sont sauvegardées.

**« Déjà vu » : le nom de fichier suffit-il ?** Non.
- Un fichier renommé serait retraité pour rien (un OCR inutile).
- Un fichier corrigé mais gardant le même nom ne serait jamais retraité.
- Le **sha256 du contenu** identifie le document lui-même.
- La **résolution** fait aussi partie de la clé : relancer à 1540 px ajoute des extractions sans écraser celles à 800 px, ce qui a permis l'étape 5.

**Métadonnées gardées pour la traçabilité.**
- La source S3 et le sha256 disent de quel fichier vient une valeur.
- La méthode, le modèle et la résolution disent comment elle a été extraite.
- La date d'extraction dit quand.
- La silver reprend `sha256`, `fichier`, `methode_extraction` et `date_extraction` : toute valeur peut être remontée jusqu'au PDF.

**Pourquoi ne jamais modifier bronze ?** C'est la seule trace de ce que le système a réellement lu.
- Si le parseur silver a un bug, on le corrige et on **recalcule** silver depuis bronze, sans refaire d'OCR (qui est lent et coûteux).
- Si on modifiait bronze, on perdrait la preuve de l'origine d'une erreur et la possibilité de rejouer.
- Concrètement, chaque lancement **ajoute** un nouveau Parquet, écrit en `.tmp` puis renommé (atomique) : on ne réécrit jamais un fichier existant.

### Étape 4 : couche silver

- **Tables** (`data/silver/`) :
  - `factures` : 100 lignes, avec les dates, les montants HT/TVA/TTC, le fournisseur et le client (SIRET), le mode de paiement, l'IBAN, `statut_qualite`, `anomalies` et la traçabilité ;
  - `lignes_facture` : 271 lignes ;
  - `quarantaine`.
- **Choix d'extraction.** Pour chaque scan, silver prend l'OCR à `SILVER_LONGEST_EDGE` (1540 par défaut). Le choix est explicite et reproductible.
- **Contrôles.**
  - **Rejet → quarantaine** si le numéro, la date d'émission ou un total est illisible, s'il n'y a aucune ligne d'article, ou si le numéro est déjà présent dans un autre fichier (doublon).
  - **Signalement** (`a_verifier`) si quantité × prix ≠ total de la ligne, si la somme des lignes ≠ total HT, si HT × taux ≠ TVA, si HT + TVA ≠ TTC, ou si l'échéance est antérieure à l'émission. La tolérance est de 0,01 €.
- **Résultat.**
  - Aucune facture perdue : 100 en silver et 0 en quarantaine pour 100 documents.
  - Aucun doublon : 100 numéros distincts.
  - Une facture signalée : **V-2025-0001** (HT 148,26 € + TVA 29,65 €, mais TTC imprimé 99 999,99 €).

**Quel type pour les montants ?** `Decimal` en Python, `DECIMAL(12,2)` en Parquet et en SQL. Un `float` est binaire : `0.1 + 0.2 = 0.30000000000000004`, et sur des sommes de factures, les écarts de centimes s'accumulent et faussent les contrôles d'égalité.

**Un parseur robuste aux deux formats (natif et OCR).** On **normalise d'abord, on parse ensuite** :
- suppression du Markdown (`**`, `#`, `---`) ;
- conversion de **tous** les tableaux HTML en lignes de texte ;
- étiquettes recherchées sans tenir compte de la casse.

Ensuite, un **seul** parseur sert pour les deux sources. Les articles sont reconnus par leur forme (quantité entière, prix en €, taux en %, total en €), pas par leur position. Ce sont les vraies données qui ont révélé les variantes : la première version mettait 11 scans en quarantaine, parce que leurs totaux étaient dans un second tableau. Quatre tests unitaires (`test_silver.py`) figent ces cas.

**Faut-il corriger automatiquement un montant mal lu ?** Non.
- Une valeur illisible devient `NULL`, et la facture est rejetée ou signalée. Une correction automatique transformerait une **erreur visible** en **donnée fausse silencieuse**.
- Le jeu en donne un exemple. La facture V-2025-0001 affiche **réellement** 99 999,99 € de TTC : c'est une anomalie volontaire, vérifiée sur le PDF. Le pipeline de référence fourni dans le bucket l'a « corrigée » en 177,91 €, ce qui masque une facture potentiellement frauduleuse.
- Notre pipeline la garde telle quelle, signalée `HT + TVA ≠ TTC`, pour qu'un humain tranche.

**Rejetée ou signalée ?**
- **Rejetée** : le document est **inexploitable** (champ clé illisible, doublon). Il sort des tables et va en quarantaine avec son motif et son texte brut.
- **Signalée** : le document est **exploitable mais suspect**. Il reste dans les tables, compte dans les totaux et porte la liste de ses anomalies, pour que le métier et l'agent puissent en tenir compte.

### Étape 5 : mesurer la qualité de l'OCR

Méthode et résultats : voir la section 3.

**Une vérité terrain fiable sans y passer des heures.**
- 20 scans sont tirés au hasard avec une graine fixe (`evaluation.py preparer`), et rendus en PNG à 2000 px.
- Les valeurs ont été saisies **depuis les images, sans regarder les sorties de LightOnOCR**, par un modèle de vision indépendant (Claude). On obtient 16 champs × 20 factures = 320 valeurs, dans `eval/verite_terrain.csv` (versionné).
- **Contrôle humain** : 2 factures tirées au hasard ont été comparées au PDF champ par champ, soit 32 valeurs et 0 erreur.
- Le script signale aussi toute case où la vérité terrain contredit **à la fois** les deux réglages OCR. Ce sont les seuls endroits où l'annotateur pourrait s'être trompé, et il n'y en a aucun.

**Quelle taille d'échantillon ?**
- Avec 20 factures, la granularité est de **5 %** par champ. Un écart de 95 % contre 100 % repose donc sur **une** erreur : il n'est pas statistiquement significatif à lui seul.
- La conclusion tient parce qu'elle est **confirmée sur les 50 scans**. Les deux réglages ne divergent que sur 3 identifiants, et 1540 px a raison les 3 fois.
- Pour estimer une précision de l'ordre de 99 % avec une marge de ±2 %, il faudrait plusieurs centaines de factures. Le générateur de fausses factures, qui produirait sa vérité terrain automatiquement, serait la bonne approche (c'est un bonus).

### Étape 6 : l'entrepôt

- `data/entrepot.duckdb` contient **uniquement 4 vues** (`bronze.extractions`, `silver.factures`, `silver.lignes_facture`, `silver.quarantaine`) sur les Parquet, avec une description par vue.
- **Les données n'existent qu'une fois**, dans les Parquet. DuckDB est le calcul, les Parquet sont le stockage.
- Chaque requête relit les fichiers : une nouvelle exécution de `silver.py` est visible immédiatement, sans rien reconstruire.
- Accès : `entrepot.py sql "…"` en ligne de commande, `entrepot.connexion()` en Python, et DBeaver (pilote DuckDB, propriété `duckdb.read_only=true`).

**Plusieurs programmes en même temps.** DuckDB autorise **plusieurs processus en lecture seule ou un seul en écriture**. Notre conception :
1. Tous les consommateurs (DBeaver, scripts, serveur MCP) ouvrent la base **en lecture seule**, donc ils ne se bloquent jamais.
2. Le pipeline **n'écrit jamais dans la base**, seulement des Parquet, de façon atomique : un lecteur voit l'ancien fichier ou le nouveau, jamais un fichier à moitié écrit.
3. Régénérer les vues se fait dans un fichier temporaire, puis par renommage.

Testé : un lecteur est resté connecté pendant `silver.py`, une autre requête et `entrepot.py init`. Rien n'a bloqué (`test_entrepot.py`).

**Des références valides partout.** Les vues contiennent des chemins absolus calculés depuis `DATA_DIR`, qui par défaut est le dossier `data/` du projet. La base **n'est pas versionnée** : chaque environnement la régénère avec `entrepot.py init`.
- Sur ta machine : le dossier `data/` du projet.
- Dans un conteneur : `DATA_DIR=/data`, sur un volume monté.
- Chez un collègue : son propre chemin, après `git clone`.

Le chemin devient une configuration, plus une valeur codée en dur.

### Étape 7 : le serveur MCP

- **Outils** (`serveur_mcp.py`) :

  | Outil | Rôle |
  |---|---|
  | `lister_factures` | liste les factures selon des filtres |
  | `totaux_factures` | nombre et sommes HT, TVA et TTC, avec regroupement optionnel |
  | `detail_facture` | une facture et ses lignes |
  | `schema_donnees` | les vues, colonnes, types et descriptions |
  | `requete_sql` | SQL libre en lecture seule |

- **Transparence.** Chaque réponse contient `sql` (les requêtes réellement exécutées) et `parametres`.
- **Tests.** `test_serveur_mcp.py` passe par le protocole MCP (20 tests). Chaque outil est comparé à une requête directe, et 10 tentatives d'écriture ou de fuite sont refusées.
- **Conversation** avec Claude Code, enregistrée dans `eval/conversation_mcp.md`. À la question « montant TTC des factures en retard en 2025 ? », l'agent a enchaîné 4 outils et répondu **106 886,43 € sur 12 factures**, avec Gimenez Payet et Fils en tête. Les chiffres sont identiques à ceux d'une requête SQL directe.

**Nom et description des outils.**
- Des noms d'**actions métier** en français (`totaux_factures` plutôt que `query`).
- Des descriptions qui disent **quand** utiliser l'outil et **quand ne pas** l'utiliser (« pour des sommes, utilise totaux_factures, pas de calcul à la main »), avec les valeurs possibles des filtres.
- Des instructions du serveur qui demandent de privilégier les outils spécialisés et de citer les chiffres tels quels.
- Des annotations `read_only_hint`. Le schéma de `regrouper_par` est une énumération fermée.

**Éviter l'injection SQL.** Les arguments viennent d'un LLM, donc d'une source non fiable. On a trois barrières :
1. **Outils à filtres** : les valeurs sont **toujours passées en paramètres** (`?`), jamais concaténées. `regrouper_par` est pris dans une liste blanche. Avec `fournisseur = "x' OR 1=1 --"`, on obtient 0 ligne.
2. **SQL libre** : il doit contenir **une seule instruction**, et uniquement de type `SELECT`. C'est le parseur de DuckDB qui l'analyse (`extract_statements`), pas une regex qu'on pourrait contourner.
3. **Bac à sable sur la connexion** :
   - base en lecture seule ;
   - `enable_external_access=false` avec `allowed_directories=[data/]` : aucun fichier lisible hors des données, donc ni `.env` ni `/etc/passwd` ;
   - `lock_configuration=true` : on ne peut pas désactiver ces réglages par une requête ;
   - 10 s et 500 lignes au maximum.

**Que renvoyer en cas d'erreur ?** L'outil **ne lève pas d'exception**. Il renvoie `{"erreur", "sql", "conseil"}` :
- le message exact de DuckDB, qui propose souvent la bonne colonne (« Candidate bindings: total_ttc… ») ;
- le SQL tenté ;
- un conseil d'action (« appelle schema_donnees », « format AAAA-MM-JJ », « vérifie le numéro avec lister_factures »).

Le LLM peut ainsi se corriger seul au tour suivant.

## 3. Évaluation de l'OCR

Le tableau ci-dessous est reproductible avec `uv run python evaluation.py evaluer`, qui écrit `eval/resultats.md`. Il porte sur 20 scans et 16 champs, comparés à la vérité terrain.

| Champ | 800 px | 1540 px | Pipeline de référence (bucket) |
|---|---|---|---|
| numéro, dates, fournisseur, client, catégorie, statut | 100 % | 100 % | 100 % |
| mode de paiement, IBAN, SIRET client, TVA intracom. | 100 % | 100 % | — |
| **SIRET fournisseur** | **95 %** (1 err.) | 100 % | — |
| total HT / TVA / TTC | 100 % | 100 % | 85 % / 90 % / 80 % |
| nombre de lignes | 100 % | 100 % | — |
| **Facture entièrement correcte** | **95 %** (19/20) | **100 %** (20/20) | 80 % (16/20) |
| Temps moyen par page | 4,9 s | 7,2 s | — |

**Analyse : quels champs sont fragiles, et pourquoi ?**
1. **Les identifiants** (SIRET, TVA intracom.) : ce sont de longues suites de chiffres, sans structure ni redondance. À 800 px, l'OCR a lu `3884969066978` au lieu de `38849696066978` : un chiffre a sauté dans une séquence répétitive. Sur les 50 scans, 800 et 1540 px ne divergent **que** sur 3 identifiants, et 1540 px a raison chaque fois. Un montant faux est rattrapé par les contrôles de cohérence de silver ; **un SIRET faux ne l'est pas**. C'est donc l'erreur la plus dangereuse. Un contrôle de clé de Luhn sur les SIRET serait une bonne parade.
2. **La mise en forme, plus que les caractères.** Les champs texte et les montants sont lus parfaitement aux deux résolutions. La seconde erreur observée à 800 px venait en réalité du **parseur** : la valeur était juste, mais l'étiquette était écrite « TVA Intracom. » avec une majuscule. Une mesure de bout en bout évalue à la fois l'OCR et le parseur.
3. **Le pipeline de référence** (un autre moteur, de type Tesseract) confond les lignes d'article et les totaux. Son HT vaut par exemple celui de la première ligne. Il a aussi « corrigé » le TTC de V-2025-0001.

**Conclusion.** 1540 px, la valeur recommandée par la fiche du modèle, est retenu par défaut, pour environ 2,3 s de plus par page. Limites : l'échantillon est petit (granularité de 5 %), et le contenu des lignes d'article (désignation, prix) n'est évalué que par leur nombre.

## 4. Difficultés, limites et pistes

| Difficulté ou limite | Ce qu'on a fait, ou ce qu'il faudrait faire |
|---|---|
| Sortie OCR **non déterministe dans sa forme** (gras, tableaux, casse) | Normalisation avant le parsing, et tests sur les cas rencontrés. Piste : demander une sortie JSON structurée à un LLM, en contrôlant ses valeurs. |
| OCR **séquentiel** (environ 7 s par page) | Paralléliser les appels (pool de threads : l'API est limitée par le réseau). Il y a un `TODO` dans `ocr.py`. |
| Les **erreurs d'OCR** sont affichées, pas journalisées | Elles sont bien retraitées au lancement suivant. Piste : un journal `errors.jsonl` (date, fichier, exception, nombre de tentatives). |
| Le filtre `fournisseur` fait une **recherche partielle** | « Payet » trouve aussi « Gimenez Payet et Fils ». À documenter pour l'agent, ou à faire suivre d'une recherche exacte. |
| Les **SIRET ne sont pas validés** | Ajouter la clé de Luhn aux contrôles silver (signalement). |
| Vérité terrain de **20 factures** | Un générateur de factures synthétiques avec leur vérité terrain automatique (bonus). |
| Une seule commande par étape, pas encore d'orchestration | Étape 9 : enchaîner le tout (Makefile, puis un orchestrateur). |

## 5. À compléter

- **Étape 8 (agent).** LLM interchangeable par configuration, outils MCP uniquement, affichage des appels et du SQL, puis comparaison d'un modèle local et d'un modèle hébergé.
- **Étape 9 (industrialisation).** Déploiement, orchestration, montée en charge de l'OCR, secrets, supervision et coûts.
