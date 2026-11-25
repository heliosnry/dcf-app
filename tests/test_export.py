"""Tests de l'export Excel : le classeur doit contenir des formules, pas des valeurs figées,
et ces formules doivent redonner EXACTEMENT les chiffres de l'application."""

from dataclasses import replace
from io import BytesIO

import pytest
from openpyxl import load_workbook

from dcf import DonneesEntreprise, Hypotheses, ParametresWACC, valoriser, wacc
from export import exporter_excel

SG = DonneesEntreprise(
    ca_dernier=46483,
    bfr_dernier=968,
    annee_derniere=2025,
    dette_nette=10356,
    minoritaires=568,
    provisions_retraites=1444,
    nb_actions=489.9,
    cours=73.20,
)


def _hyp(**options):
    """Hypothèses de l'Excel, modifiées par les options données."""
    return Hypotheses(
        **{
            "croissance_ca": 0.0125,
            "marge_ebit": 0.115,
            "da_ca": 0.04,
            "capex_ca": 0.0425,
            "bfr_ca": 0.021,
            "taux_is": 0.26,
            "g": 0.02,
            "horizon": 5,
            **options,
        }
    )


def _params(d=SG):
    """Paramètres du WACC de l'Excel pour l'entreprise d."""
    return ParametresWACC(
        taux_sans_risque=0.0483,
        beta=1.428,
        prime_risque=0.0501,
        kd=0.0594,
        taux_is=0.26,
        capitalisation=d.nb_actions * d.cours,
        dette=max(d.dette_nette, 0) + d.autres_dettes,
    )


def _octets(h=None, d=SG, decalage=0.0, dividendes=0.0):
    """Contenu du fichier Excel exporté (en octets)."""
    return exporter_excel(
        "Saint-Gobain", "EUR", h or _hyp(), d, _params(d), 0.0111, decalage, dividendes
    )


def _classeur(**options):
    """Classeur exporté, ouvert avec openpyxl (formules non recalculées)."""
    return load_workbook(BytesIO(_octets(_hyp(**options))))


def test_onglets():
    assert _classeur().sheetnames[:2] == ["Hypothèses", "DCF"]


def test_formules_et_saisies():
    wb = _classeur()
    assert wb["Hypothèses"]["C4"].value == 0.0125  # saisie
    assert str(wb["Hypothèses"]["C37"].value).startswith("=")  # WACC calculé
    assert str(wb["DCF"]["C24"].value).startswith("=C23/")  # valeur par action
    assert str(wb["DCF"]["G13"].value).startswith("=G7+G8-G9")  # FCFF 2030


def test_horizon_et_methode():
    wb = _classeur(horizon=7, roic_terminal=0.12, mi_annee=True)
    assert wb["DCF"]["I4"].value == 2032  # 7 colonnes d'années
    assert wb["Hypothèses"]["C13"].value == 2  # méthode ROIC
    assert wb["Hypothèses"]["C15"].value == 1  # mi-année


def test_transition_ajoute_des_colonnes():
    wb = _classeur(annees_transition=3)
    assert wb["DCF"]["J4"].value == 2033  # 5 + 3 années


# --- Recalcul réel du classeur (bibliothèque pycel, facultative) ---------------

CAS = {
    "Excel de référence": (_hyp(), SG, 0.0, 0.0),
    "ROIC + mi-année": (_hyp(roic_terminal=0.12, mi_annee=True), SG, 0.0, 0.0),
    "transition + loyers": (
        _hyp(croissance_ca=0.05, annees_transition=4, loyers_ca=0.01),
        SG,
        0.0,
        0.0,
    ),
    "pont complet + date": (
        _hyp(),
        replace(SG, participations=806, autres_dettes=450, actions_dilutives=4.1),
        0.9,
        2.30,
    ),
}


@pytest.mark.parametrize("nom", list(CAS))
def test_excel_recalcule_comme_l_appli(nom, tmp_path):
    """On ouvre le fichier exporté, on fait calculer toutes ses formules, et on compare la valeur par
    action à celle du moteur Python. Si une formule Excel diverge du code, ce test le voit.
    Ignoré si pycel n'est pas installé (pip install pycel)."""
    pycel = pytest.importorskip("pycel")
    h, d, decalage, dividendes = CAS[nom]
    fichier = tmp_path / "dcf.xlsx"
    fichier.write_bytes(_octets(h, d, decalage, dividendes))
    excel = pycel.ExcelCompiler(filename=str(fichier))
    attendu = valoriser(h, d, wacc(_params(d)), decalage, dividendes)
    assert excel.evaluate("Hypothèses!C37") == pytest.approx(wacc(_params(d)), rel=1e-9)
    assert excel.evaluate("DCF!C24") == pytest.approx(attendu["valeur_action"], rel=1e-9)
    if nom == "Excel de référence":
        assert excel.evaluate("DCF!C24") == pytest.approx(67.93, abs=0.01)
