# Conversation avec le serveur MCP « factures » (Claude Code)

Commande : `claude -p "<question>" --mcp-config .mcp.json --strict-mcp-config --allowedTools mcp__factures` (seuls les outils du serveur MCP sont autorisés).

**Question :** Quel est le montant TTC total des factures en retard en 2025 ? Quel fournisseur en concentre le plus ? Donne aussi le détail de la plus grosse facture en retard de ce fournisseur.

Modèle : `claude-opus-5-5` · serveurs MCP : factures (connected)

### Appel d'outil : `totaux_factures`

```json
{
  "statut": "retard",
  "date_debut": "2025-01-01",
  "date_fin": "2025-12-31"
}
```

SQL exécuté (renvoyé par l'outil) :

```sql
SELECT count(*) AS nb_factures, count(*) FILTER (WHERE statut_qualite = 'a_verifier') AS nb_a_verifier, coalesce(sum(total_ht), 0) AS total_ht, coalesce(sum(total_tva), 0) AS total_tva, coalesce(sum(total_ttc), 0) AS total_ttc FROM silver.factures WHERE statut ILIKE ? AND date_emission >= CAST(? AS DATE) AND date_emission <= CAST(? AS DATE)
```

Paramètres : `['%retard%', '2025-01-01', '2025-12-31']`

<details><summary>Réponse JSON complète</summary>

```json
{
  "sql": [
    "SELECT count(*) AS nb_factures, count(*) FILTER (WHERE statut_qualite = 'a_verifier') AS nb_a_verifier, coalesce(sum(total_ht), 0) AS total_ht, coalesce(sum(total_tva), 0) AS total_tva, coalesce(sum(total_ttc), 0) AS total_ttc FROM silver.factures WHERE statut ILIKE ? AND date_emission >= CAST(? AS DATE) AND date_emission <= CAST(? AS DATE)"
  ],
  "parametres": [
    "%retard%",
    "2025-01-01",
    "2025-12-31"
  ],
  "nb_lignes": 1,
  "tronque": false,
  "resultats": [
    {
      "nb_factures": 12,
      "nb_a_verifier": 0,
      "total_ht": "89072.02",
      "total_tva": "17814.41",
      "total_ttc": "106886.43"
    }
  ]
}
```

</details>

### Appel d'outil : `totaux_factures`

```json
{
  "statut": "retard",
  "date_debut": "2025-01-01",
  "date_fin": "2025-12-31",
  "regrouper_par": "fournisseur"
}
```

SQL exécuté (renvoyé par l'outil) :

```sql
SELECT fournisseur AS fournisseur, count(*) AS nb_factures, count(*) FILTER (WHERE statut_qualite = 'a_verifier') AS nb_a_verifier, coalesce(sum(total_ht), 0) AS total_ht, coalesce(sum(total_tva), 0) AS total_tva, coalesce(sum(total_ttc), 0) AS total_ttc FROM silver.factures WHERE statut ILIKE ? AND date_emission >= CAST(? AS DATE) AND date_emission <= CAST(? AS DATE) GROUP BY 1 ORDER BY 1
```

Paramètres : `['%retard%', '2025-01-01', '2025-12-31']`

<details><summary>Réponse JSON complète</summary>

```json
{
  "sql": [
    "SELECT fournisseur AS fournisseur, count(*) AS nb_factures, count(*) FILTER (WHERE statut_qualite = 'a_verifier') AS nb_a_verifier, coalesce(sum(total_ht), 0) AS total_ht, coalesce(sum(total_tva), 0) AS total_tva, coalesce(sum(total_ttc), 0) AS total_ttc FROM silver.factures WHERE statut ILIKE ? AND date_emission >= CAST(? AS DATE) AND date_emission <= CAST(? AS DATE) GROUP BY 1 ORDER BY 1"
  ],
  "parametres": [
    "%retard%",
    "2025-01-01",
    "2025-12-31"
  ],
  "nb_lignes": 6,
  "tronque": false,
  "resultats": [
    {
      "fournisseur": "Aubry",
      "nb_factures": 2,
      "nb_a_verifier": 0,
      "total_ht": "24802.25",
      "total_tva": "4960.45",
      "total_ttc": "29762.70"
    },
    {
      "fournisseur": "Chevalier",
      "nb_factures": 2,
      "nb_a_verifier": 0,
      "total_ht": "5171.84",
      "total_tva": "1034.37",
      "total_ttc": "6206.21"
    },
    {
      "fournisseur": "Gimenez Payet et Fils",
      "nb_factures": 3,
      "nb_a_verifier": 0,
      "total_ht": "31592.10",
      "total_tva": "6318.42",
      "total_ttc": "37910.52"
    },
    {
      "fournisseur": "Maury S.A.R.L.",
      "nb_factures": 2,
      "nb_a_verifier": 0,
      "total_ht": "10941.10",
      "total_tva": "2188.22",
      "total_ttc": "13129.32"
    },
    {
      "fournisseur": "Payet",
      "nb_factures": 1,
      "nb_a_verifier": 0,
      "total_ht": "15897.98",
      "total_tva": "3179.60",
      "total_ttc": "19077.58"
    },
    {
      "fournisseur": "Vasseur",
      "nb_factures": 2,
      "nb_a_verifier": 0,
      "total_ht": "666.75",
      "total_tva": "133.35",
      "total_ttc": "800.10"
    }
  ]
}
```

</details>

### Appel d'outil : `lister_factures`

```json
{
  "statut": "retard",
  "fournisseur": "Gimenez Payet et Fils",
  "date_debut": "2025-01-01",
  "date_fin": "2025-12-31"
}
```

SQL exécuté (renvoyé par l'outil) :

```sql
SELECT numero, date_emission, date_echeance, fournisseur, client, categorie, statut, total_ht, total_tva, total_ttc, statut_qualite, anomalies FROM silver.factures WHERE fournisseur ILIKE ? AND statut ILIKE ? AND date_emission >= CAST(? AS DATE) AND date_emission <= CAST(? AS DATE) ORDER BY date_emission, numero
```

Paramètres : `['%Gimenez Payet et Fils%', '%retard%', '2025-01-01', '2025-12-31']`

<details><summary>Réponse JSON complète</summary>

```json
{
  "sql": [
    "SELECT numero, date_emission, date_echeance, fournisseur, client, categorie, statut, total_ht, total_tva, total_ttc, statut_qualite, anomalies FROM silver.factures WHERE fournisseur ILIKE ? AND statut ILIKE ? AND date_emission >= CAST(? AS DATE) AND date_emission <= CAST(? AS DATE) ORDER BY date_emission, numero"
  ],
  "parametres": [
    "%Gimenez Payet et Fils%",
    "%retard%",
    "2025-01-01",
    "2025-12-31"
  ],
  "nb_lignes": 3,
  "tronque": false,
  "resultats": [
    {
      "numero": "GPE-2025-0007",
      "date_emission": "2025-08-17",
      "date_echeance": "2025-09-01",
      "fournisseur": "Gimenez Payet et Fils",
      "client": "Baron",
      "categorie": "Informatique",
      "statut": "En retard",
      "total_ht": "1395.10",
      "total_tva": "279.02",
      "total_ttc": "1674.12",
      "statut_qualite": "ok",
      "anomalies": []
    },
    {
      "numero": "GPE-2025-0009",
      "date_emission": "2025-08-26",
      "date_echeance": "2025-10-10",
      "fournisseur": "Gimenez Payet et Fils",
      "client": "Martineau",
      "categorie": "Informatique",
      "statut": "En retard",
      "total_ht": "14942.17",
      "total_tva": "2988.43",
      "total_ttc": "17930.60",
      "statut_qualite": "ok",
      "anomalies": []
    },
    {
      "numero": "GPE-2025-0002",
      "date_emission": "2025-09-10",
      "date_echeance": "2025-10-25",
      "fournisseur": "Gimenez Payet et Fils",
      "client": "Vidal",
      "categorie": "Informatique",
      "statut": "En retard",
      "total_ht": "15254.83",
      "total_tva": "3050.97",
      "total_ttc": "18305.80",
      "statut_qualite": "ok",
      "anomalies": []
    }
  ]
}
```

</details>

### Appel d'outil : `detail_facture`

```json
{
  "numero": "GPE-2025-0002"
}
```

SQL exécuté (renvoyé par l'outil) :

```sql
SELECT * FROM silver.factures WHERE numero = ?
SELECT numero_ligne, designation, quantite, prix_unitaire_ht, taux_tva, total_ht FROM silver.lignes_facture WHERE numero = ? ORDER BY numero_ligne
```

Paramètres : `['GPE-2025-0002']`

<details><summary>Réponse JSON complète</summary>

```json
{
  "sql": [
    "SELECT * FROM silver.factures WHERE numero = ?",
    "SELECT numero_ligne, designation, quantite, prix_unitaire_ht, taux_tva, total_ht FROM silver.lignes_facture WHERE numero = ? ORDER BY numero_ligne"
  ],
  "parametres": [
    "GPE-2025-0002"
  ],
  "facture": {
    "numero": "GPE-2025-0002",
    "date_emission": "2025-09-10",
    "date_echeance": "2025-10-25",
    "categorie": "Informatique",
    "statut": "En retard",
    "fournisseur": "Gimenez Payet et Fils",
    "fournisseur_siret": "10518347967010",
    "fournisseur_tva_intracom": "FR65105183479",
    "client": "Vidal",
    "client_siret": "06990162764641",
    "mode_paiement": "Chèque",
    "iban": "FR4513338726247317810801326",
    "taux_tva": "20.00",
    "total_ht": "15254.83",
    "total_tva": "3050.97",
    "total_ttc": "18305.80",
    "statut_qualite": "ok",
    "anomalies": [],
    "sha256": "95ef5bb33aea888341b8045da827527dc8351dc6d7d6e2128c9554648851ec56",
    "fichier": "facture_GPE-2025-0002_texte.pdf",
    "methode_extraction": "texte",
    "date_extraction": "2026-11-23T15:18:28.238846+01:00"
  },
  "lignes": [
    {
      "numero_ligne": 1,
      "designation": "Station d'accueil USB-C",
      "quantite": 9,
      "prix_unitaire_ht": "138.35",
      "taux_tva": "20.00",
      "total_ht": "1245.15"
    },
    {
      "numero_ligne": 2,
      "designation": "Disque SSD externe 2 To",
      "quantite": 1,
      "prix_unitaire_ht": "167.89",
      "taux_tva": "20.00",
      "total_ht": "167.89"
    },
    {
      "numero_ligne": 3,
      "designation": "Écran 27 pouces QHD",
      "quantite": 3,
      "prix_unitaire_ht": "318.21",
      "taux_tva": "20.00",
      "total_ht": "954.63"
    },
    {
      "numero_ligne": 4,
      "designation": "Ordinateur portable Pro 14 pouces",
      "quantite": 11,
      "prix_unitaire_ht": "1152.30",
      "taux_tva": "20.00",
      "total_ht": "12675.30"
    },
    {
      "numero_ligne": 5,
      "designation": "Licence logiciel bureautique annuelle",
      "quantite": 2,
      "prix_unitaire_ht": "105.93",
      "taux_tva": "20.00",
      "total_ht": "211.86"
    }
  ],
  "nb_lignes": 5
}
```

</details>

## Réponse de l'agent

## Factures en retard émises en 2025

**Total TTC : 106 886,43 €** pour 12 factures (89 072,02 € HT et 17 814,41 € de TVA). Aucune facture n'est signalée à vérifier.

**Fournisseur qui en concentre le plus : Gimenez Payet et Fils**, avec 3 factures et **37 910,52 € TTC**, soit environ 35 % du total.

| Fournisseur | Nb | TTC |
|---|---|---|
| Gimenez Payet et Fils | 3 | 37 910,52 € |
| Aubry | 2 | 29 762,70 € |
| Payet | 1 | 19 077,58 € |
| Maury S.A.R.L. | 2 | 13 129,32 € |
| Chevalier | 2 | 6 206,21 € |
| Vasseur | 2 | 800,10 € |

« Payet » apparaît comme un fournisseur à part, avec un autre nom que « Gimenez Payet et Fils ». Je les ai donc comptés séparément.

## Plus grosse facture en retard de Gimenez Payet et Fils : GPE-2025-0002

- **Émise le** 10/09/2025, **échéance** 25/10/2025
- **Client** : Vidal (SIRET 06990162764641)
- **Catégorie** : Informatique
- **Paiement** : chèque
- **Montants** : 15 254,83 € HT + 3 050,97 € de TVA (20 %) = **18 305,80 € TTC**
- **Qualité** : ok, aucune anomalie
- **Fichier source** : `facture_GPE-2025-0002_texte.pdf`, lu par extraction texte

| # | Désignation | Qté | PU HT | Total HT |
|---|---|---|---|---|
| 1 | Station d'accueil USB-C | 9 | 138,35 € | 1 245,15 € |
| 2 | Disque SSD externe 2 To | 1 | 167,89 € | 167,89 € |
| 3 | Écran 27 pouces QHD | 3 | 318,21 € | 954,63 € |
| 4 | Ordinateur portable Pro 14 pouces | 11 | 1 152,30 € | 12 675,30 € |
| 5 | Licence logiciel bureautique annuelle | 2 | 105,93 € | 211,86 € |

Ses deux autres factures en retard sont GPE-2025-0009 (17 930,60 € TTC, client Martineau) et GPE-2025-0007 (1 674,12 € TTC, client Baron).

« En 2025 » se rapporte ici à la date d'émission des factures, et pas à leur date d'échéance.

---
6 tours · coût 0.18 $ · statut success

## Vérification par SQL direct (`entrepot.py sql`)

| Contrôle | Requête | Résultat | Agent |
|---|---|---|---|
| Total en retard 2025 | `SELECT count(*), sum(total_ttc) FROM silver.factures WHERE statut='En retard' AND year(date_emission)=2025` | 12 · 106 886,43 € | ✅ identique |
| 1er fournisseur | `… GROUP BY fournisseur ORDER BY sum(total_ttc) DESC` | Gimenez Payet et Fils · 3 · 37 910,52 € | ✅ identique |
| Plus grosse facture | `… AND fournisseur='Gimenez Payet et Fils' ORDER BY total_ttc DESC` | GPE-2025-0002 · 18 305,80 € | ✅ identique |
