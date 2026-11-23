"""Tests du parseur silver sur des textes écrits à la main (un natif, un OCR, un incohérent).

Lancement : uv run pytest
"""
from datetime import date
from decimal import Decimal

from silver import anomalies, motif_rejet, parse_facture

NATIF = """<!-- page 1 -->
Aubry
96, avenue de Meyer
SIRET : 77360260298050
TVA intracom. : FR09773602602
FACTURE
Numéro de facture : A-2025-0001
Date d'émission : 10/01/2025
Date d'échéance : 11/03/2025
Statut : Payée
Facturé à :
Client : Lombard
SIRET client : 20037917087083
Désignation
Qté
Prix unitaire HT
TVA
Total HT
Formation DuckDB
et Parquet
9
1 541,05 €
20 %
13 869,45 €
Atelier de cadrage projet
6
2 221,30 €
20 %
13 327,80 €
Total HT
27 197,25 €
TVA 20 %
5 439,45 €
Total TVA
5 439,45 €
Total TTC
32 636,70 €
Mode de paiement : Carte bancaire
"""

OCR = """<!-- page 1 -->
# FACTURE

**Aubry**
SIRET : 77360260298050

**Numéro de facture** : A-2025-0003
**Date d'émission** : 29/04/2025
**Statut** : En retard

---

<table>
  <thead><tr><th>Désignation</th><th>Qté</th><th>Prix unitaire HT</th><th>TVA</th><th>Total HT</th></tr></thead>
  <tbody>
    <tr>
      <td>Formation DuckDB et Parquet</td>
      <td>5</td>
      <td>1 261,21 €</td>
      <td>20 %</td>
      <td>6 306,05 €</td>
    </tr>
  </tbody>
</table>

**Total HT** 6 306,05 €
**TVA 20 %** 1 261,21 €
**Total TVA** 1 261,21 €
**Total TTC** 7 567,26 €
"""


def test_natif():
    f, lignes = parse_facture(NATIF)
    assert f["numero"] == "A-2025-0001"
    assert f["fournisseur"] == "Aubry"
    assert f["fournisseur_siret"] == "77360260298050"
    assert f["client"] == "Lombard" and f["client_siret"] == "20037917087083"
    assert f["date_emission"] == date(2025, 1, 10)
    assert f["total_ttc"] == Decimal("32636.70")
    assert [l["designation"] for l in lignes] == ["Formation DuckDB et Parquet", "Atelier de cadrage projet"]
    assert lignes[0]["quantite"] == 9 and lignes[0]["prix_unitaire_ht"] == Decimal("1541.05")
    assert motif_rejet(f, lignes) is None
    assert anomalies(f, lignes) == []


def test_ocr():
    f, lignes = parse_facture(OCR)
    assert f["numero"] == "A-2025-0003"
    assert f["fournisseur"] == "Aubry"
    assert f["statut"] == "En retard"
    assert f["total_ht"] == Decimal("6306.05") and f["total_tva"] == Decimal("1261.21")
    assert len(lignes) == 1 and lignes[0]["total_ht"] == Decimal("6306.05")
    assert motif_rejet(f, lignes) is None
    assert anomalies(f, lignes) == []


def test_montants_incoherents_signales():
    f, lignes = parse_facture(OCR.replace("**Total TTC** 7 567,26 €", "**Total TTC** 7 657,26 €"))
    assert motif_rejet(f, lignes) is None          # gardée…
    assert anomalies(f, lignes) == ["HT + TVA ≠ TTC"]  # …mais signalée


def test_champ_illisible_rejete():
    f, lignes = parse_facture(OCR.replace("Numéro de facture", "Nmro de factre"))
    assert motif_rejet(f, lignes) == "champs illisibles : numero"
