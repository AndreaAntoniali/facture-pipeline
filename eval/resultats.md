# Évaluation OCR : précision champ par champ (20 factures scannées)

| Champ | LightOnOCR 800 px | LightOnOCR 1540 px | Référence du bucket |
|---|---|---|---|
| numero | 100 % (0 err.) | 100 % (0 err.) | 100 % (0 err.) |
| date_emission | 100 % (0 err.) | 100 % (0 err.) | 100 % (0 err.) |
| date_echeance | 100 % (0 err.) | 100 % (0 err.) | 100 % (0 err.) |
| fournisseur | 100 % (0 err.) | 100 % (0 err.) | 100 % (0 err.) |
| fournisseur_siret | 95 % (1 err.) | 100 % (0 err.) | — |
| fournisseur_tva_intracom | 100 % (0 err.) | 100 % (0 err.) | — |
| client | 100 % (0 err.) | 100 % (0 err.) | 100 % (0 err.) |
| client_siret | 100 % (0 err.) | 100 % (0 err.) | — |
| categorie | 100 % (0 err.) | 100 % (0 err.) | 100 % (0 err.) |
| statut | 100 % (0 err.) | 100 % (0 err.) | 100 % (0 err.) |
| mode_paiement | 100 % (0 err.) | 100 % (0 err.) | — |
| iban | 100 % (0 err.) | 100 % (0 err.) | — |
| total_ht | 100 % (0 err.) | 100 % (0 err.) | 85 % (3 err.) |
| total_tva | 100 % (0 err.) | 100 % (0 err.) | 90 % (2 err.) |
| total_ttc | 100 % (0 err.) | 100 % (0 err.) | 80 % (4 err.) |
| nb_lignes | 100 % (0 err.) | 100 % (0 err.) | — |
| **Facture entièrement correcte** | 95 % (19/20) | 100 % (20/20) | 80 % (16/20) |
| *Temps moyen par page* | 4.94 s | 7.22 s | — |

## Erreurs

| Source | Fichier | Champ | Attendu | Obtenu |
|---|---|---|---|---|
| LightOnOCR 800 px | facture_V-2025-0009_scan.pdf | fournisseur_siret | 38849696066978 | 3884969066978 |
| Référence du bucket | facture_A-2026-0004_scan.pdf | total_ht | 10911.61 | 6355.10 |
| Référence du bucket | facture_C-2026-0007_scan.pdf | total_ht | 2549.56 | 14215.00 |
| Référence du bucket | facture_MS-2025-0006_scan.pdf | total_ht | 10630.54 | 9110.32 |
| Référence du bucket | facture_A-2026-0004_scan.pdf | total_tva | 2182.32 | 4556.51 |
| Référence du bucket | facture_MS-2025-0006_scan.pdf | total_tva | 2126.11 | 463.83 |
| Référence du bucket | facture_A-2026-0004_scan.pdf | total_ttc | 13093.93 | 10911.61 |
| Référence du bucket | facture_C-2026-0007_scan.pdf | total_ttc | 3059.47 | 14724.91 |
| Référence du bucket | facture_MS-2025-0006_scan.pdf | total_ttc | 12756.65 | 9574.15 |
| Référence du bucket | facture_V-2025-0001_scan.pdf | total_ttc | 99999.99 | 177.91 |

## À contrôler à la main

Factures à comparer au PDF, champ par champ : facture_A-2026-0004_scan.pdf, facture_C-2026-0014_scan.pdf

Cases où la vérité terrain contredit les deux réglages OCR :

| Fichier | Champ | Vérité terrain | 800 px | 1540 px |
|---|---|---|---|---|
