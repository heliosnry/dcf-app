"""Tests de l'export Excel : le classeur doit contenir des formules, pas des valeurs figées."""

from io import BytesIO

from openpyxl import load_workbook

from dcf import DonneesEntreprise, Hypotheses, ParametresWACC
from export import exporter_excel


def _classeur(**options):
    h = Hypotheses(**{"croissance_ca": 0.0125, "marge_ebit": 0.115, "da_ca": 0.04,
                      "capex_ca": 0.0425, "bfr_ca": 0.021, "taux_is": 0.26, "g": 0.02,
                      "horizon": 5, **options})
    d = DonneesEntreprise(ca_dernier=46483, bfr_dernier=968, annee_derniere=2025, dette_nette=10356,
                          minoritaires=568, provisions_retraites=1444, nb_actions=489.9, cours=73.20)
    p = ParametresWACC(taux_sans_risque=0.0483, beta=1.428, prime_risque=0.0501, kd=0.0594,
                       taux_is=0.26, capitalisation=489.9 * 73.20, dette=10356)
    return load_workbook(BytesIO(exporter_excel("Saint-Gobain", "EUR", h, d, p, 0.0111)))


def test_onglets():
    assert _classeur().sheetnames[:2] == ["Hypothèses", "DCF"]


def test_formules_et_saisies():
    wb = _classeur()
    assert wb["Hypothèses"]["C4"].value == 0.0125                      # saisie
    assert str(wb["Hypothèses"]["C37"].value).startswith("=")          # WACC calculé
    assert str(wb["DCF"]["C24"].value).startswith("=C23/")             # valeur par action
    assert str(wb["DCF"]["G13"].value).startswith("=G7+G8-G9")         # FCFF 2030


def test_horizon_et_methode():
    wb = _classeur(horizon=7, roic_terminal=0.12, mi_annee=True)
    assert wb["DCF"]["I4"].value == 2032                               # 7 colonnes d'années
    assert wb["Hypothèses"]["C13"].value == 2                          # méthode ROIC
    assert wb["Hypothèses"]["C15"].value == 1                          # mi-année
