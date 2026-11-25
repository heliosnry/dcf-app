"""
data.py — Récupération automatique des données d'une entreprise (yfinance)

Pour n'importe quel ticker Yahoo Finance (SGO.PA, AI.PA, ML.PA, AAPL, ...) :
  1. télécharge les comptes annuels (compte de résultat, bilan, flux de trésorerie)
  2. calcule les ratios historiques (comme l'onglet « Données historique »)
  3. propose des hypothèses par défaut (moyennes historiques, bornées)
  4. calcule le bêta contre l'indice du pays (avec sa fiabilité) et le bêta sectoriel Damodaran
  5. choisit le taux sans risque de la DEVISE et la prime de risque du PAYS
  6. convertit les comptes dans la devise de cotation si elles diffèrent
  7. reprend les éléments du passage VE → fonds propres (retraites, participations, hybrides,
     dilution, minoritaires) et signale les points à vérifier (alertes)

Montants en millions, dans la devise de COTATION de l'action.
Lancer un test :  python src/data.py SGO.PA
"""

import math
import sys
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yfinance as yf

from dcf import DonneesEntreprise, Hypotheses, ParametresWACC, reendetter_beta, valoriser, wacc

DOSSIER_DONNEES = Path(__file__).parent / "donnees"

# ---------------------------------------------------------------------------
# Tables de référence
# ---------------------------------------------------------------------------

# Indice de référence pour le bêta, selon le suffixe du ticker
INDICES = {
    ".PA": "^FCHI",
    ".DE": "^GDAXI",
    ".AS": "^AEX",
    ".MI": "FTSEMIB.MI",
    ".L": "^FTSE",
    ".MC": "^IBEX",
    ".SW": "^SSMI",
    ".BR": "^BFX",
    "": "^GSPC",
}

# Taux sans risque = taux d'État à 10 ans de la DEVISE des flux (relevés du 24-25/11/2026).
# Pour l'euro : le Bund allemand, seul grand État de la zone noté Aaa. L'OAT française contient une
# prime de risque de l'État français (≈ 1,4 pt au-dessus du Bund en novembre 2026) : la prendre ET
# ajouter la prime de risque pays de Damodaran compterait deux fois le risque France.
TAUX_SANS_RISQUE = {"EUR": 0.0326, "USD": 0.0488, "GBP": 0.0519, "CAD": 0.0369, "JPY": 0.0310}
DATE_TAUX = "novembre 2026"

# Prime de risque d'un marché mature (S&P 500, implicite) au 1er janvier 2026 — Damodaran
PRIME_MATURE = 0.0423
DATE_PRIMES = "janvier 2026"
# Primes de risque moyennes par grande zone (pondérées par le PIB), Damodaran janvier 2026
PRIMES_ZONES = {
    "Europe de l'Ouest": 0.0527,
    "Amérique du Nord": 0.0445,
    "Asie": 0.0572,
    "Japon": 0.0514,
    "Chine": 0.0514,
    "Inde": 0.0708,
    "Amérique latine": 0.0846,
    "Europe de l'Est": 0.0758,
    "Moyen-Orient": 0.0624,
    "Afrique": 0.1195,
    "Australie / Nouvelle-Zélande": 0.0423,
}
ZONE_DAMODARAN = {
    "Western Europe": "Europe de l'Ouest",
    "North America": "Amérique du Nord",
    "Asia": "Asie",
    "Central and South America": "Amérique latine",
    "Caribbean": "Amérique latine",
    "Eastern Europe": "Europe de l'Est",
    "Middle East": "Moyen-Orient",
    "Africa": "Afrique",
    "Australia & New Zealand": "Australie / Nouvelle-Zélande",
}
PAYS_DEFAUT = "France"

# Note synthétique de Damodaran (janvier 2026) : couverture des intérêts → note → spread.
SPREADS = {
    "Aaa/AAA": 0.0040,
    "Aa2/AA": 0.0055,
    "A1/A+": 0.0070,
    "A2/A": 0.0078,
    "A3/A-": 0.0089,
    "Baa2/BBB": 0.0111,
    "Ba1/BB+": 0.0138,
    "Ba2/BB": 0.0184,
    "B1/B+": 0.0275,
    "B2/B": 0.0321,
    "B3/B-": 0.0509,
    "Caa/CCC": 0.0885,
    "Ca2/CC": 0.1261,
    "C2/C": 0.1600,
    "D2/D": 0.1900,
}
# Seuils pour les grandes entreprises (capitalisation > 5 Md$)
SEUILS_GRANDES = [
    (8.5, "Aaa/AAA"),
    (6.5, "Aa2/AA"),
    (5.5, "A1/A+"),
    (4.25, "A2/A"),
    (3.0, "A3/A-"),
    (2.5, "Baa2/BBB"),
    (2.25, "Ba1/BB+"),
    (2.0, "Ba2/BB"),
    (1.75, "B1/B+"),
    (1.5, "B2/B"),
    (1.25, "B3/B-"),
    (0.8, "Caa/CCC"),
    (0.65, "Ca2/CC"),
    (0.2, "C2/C"),
]
# Seuils plus exigeants pour les petites entreprises (tableur ratings.xls de Damodaran)
SEUILS_PETITES = [
    (12.5, "Aaa/AAA"),
    (9.5, "Aa2/AA"),
    (7.5, "A1/A+"),
    (6.0, "A2/A"),
    (4.5, "A3/A-"),
    (4.0, "Baa2/BBB"),
    (3.5, "Ba1/BB+"),
    (3.0, "Ba2/BB"),
    (2.5, "B1/B+"),
    (2.0, "B2/B"),
    (1.5, "B3/B-"),
    (1.25, "Caa/CCC"),
    (0.8, "Ca2/CC"),
    (0.5, "C2/C"),
]
SEUIL_PETITE_ENTREPRISE = 5_000  # capitalisation en millions

# Secteurs où les loyers IFRS 16 pèsent lourd
SECTEURS_LOCATIFS = (
    "telecom",
    "grocery",
    "department stores",
    "discount stores",
    "apparel retail",
    "specialty retail",
    "lodging",
    "restaurants",
    "airlines",
    "home improvement",
    "pharmaceutical retailers",
    "travel services",
    "luxury",
)

SECTEURS_EXCLUS = (
    "bank",
    "banks",
    "insurance",
    "capital markets",
    "credit services",
    "asset management",
    "financial conglomerates",
)

# Secteur Yahoo (« industry ») → secteur Damodaran (tables betaEurope.xls / betas.xls, janvier 2026)
SECTEURS_DAMODARAN = {
    "Advertising Agencies": "Advertising",
    "Aerospace & Defense": "Aerospace/Defense",
    "Agricultural Inputs": "Farming/Agriculture",
    "Airlines": "Air Transport",
    "Airports & Air Services": "Transportation",
    "Aluminum": "Metals & Mining",
    "Apparel Manufacturing": "Apparel",
    "Apparel Retail": "Retail (Special Lines)",
    "Auto & Truck Dealerships": "Retail (Automotive)",
    "Auto Manufacturers": "Auto & Truck",
    "Auto Parts": "Auto Parts",
    "Beverages - Brewers": "Beverage (Alcoholic)",
    "Beverages - Non-Alcoholic": "Beverage (Soft)",
    "Beverages - Wineries & Distilleries": "Beverage (Alcoholic)",
    "Biotechnology": "Drugs (Biotechnology)",
    "Broadcasting": "Broadcasting",
    "Building Materials": "Building Materials",
    "Building Products & Equipment": "Building Materials",
    "Business Equipment & Supplies": "Office Equipment & Services",
    "Chemicals": "Chemical (Diversified)",
    "Coking Coal": "Coal & Related Energy",
    "Communication Equipment": "Telecom. Equipment",
    "Computer Hardware": "Computers/Peripherals",
    "Confectioners": "Food Processing",
    "Conglomerates": "Diversified",
    "Consulting Services": "Business & Consumer Services",
    "Consumer Electronics": "Electronics (Consumer & Office)",
    "Copper": "Metals & Mining",
    "Department Stores": "Retail (General)",
    "Diagnostics & Research": "Healthcare Products",
    "Discount Stores": "Retail (General)",
    "Drug Manufacturers - General": "Drugs (Pharmaceutical)",
    "Drug Manufacturers - Specialty & Generic": "Drugs (Pharmaceutical)",
    "Education & Training Services": "Education",
    "Electrical Equipment & Parts": "Electrical Equipment",
    "Electronic Components": "Electronics (General)",
    "Electronic Gaming & Multimedia": "Software (Entertainment)",
    "Electronics & Computer Distribution": "Retail (Distributors)",
    "Engineering & Construction": "Engineering/Construction",
    "Entertainment": "Entertainment",
    "Farm & Heavy Construction Machinery": "Machinery",
    "Farm Products": "Farming/Agriculture",
    "Food Distribution": "Food Wholesalers",
    "Footwear & Accessories": "Shoe",
    "Furnishings, Fixtures & Appliances": "Furn/Home Furnishings",
    "Gambling": "Hotel/Gaming",
    "Gold": "Precious Metals",
    "Grocery Stores": "Retail (Grocery and Food)",
    "Health Information Services": "Heathcare Information and Technology",
    "Home Improvement Retail": "Retail (Building Supply)",
    "Household & Personal Products": "Household Products",
    "Industrial Distribution": "Retail (Distributors)",
    "Information Technology Services": "Computer Services",
    "Infrastructure Operations": "Engineering/Construction",
    "Integrated Freight & Logistics": "Transportation",
    "Internet Content & Information": "Software (Internet)",
    "Internet Retail": "Retail (General)",
    "Leisure": "Recreation",
    "Lodging": "Hotel/Gaming",
    "Lumber & Wood Production": "Paper/Forest Products",
    "Luxury Goods": "Apparel",
    "Marine Shipping": "Shipbuilding & Marine",
    "Medical Care Facilities": "Hospitals/Healthcare Facilities",
    "Medical Devices": "Healthcare Products",
    "Medical Distribution": "Healthcare Support Services",
    "Medical Instruments & Supplies": "Healthcare Products",
    "Metal Fabrication": "Steel",
    "Oil & Gas Drilling": "Oilfield Svcs/Equip.",
    "Oil & Gas E&P": "Oil/Gas (Production and Exploration)",
    "Oil & Gas Equipment & Services": "Oilfield Svcs/Equip.",
    "Oil & Gas Integrated": "Oil/Gas (Integrated)",
    "Oil & Gas Midstream": "Oil/Gas Distribution",
    "Oil & Gas Refining & Marketing": "Oil/Gas (Integrated)",
    "Other Industrial Metals & Mining": "Metals & Mining",
    "Other Precious Metals & Mining": "Precious Metals",
    "Packaged Foods": "Food Processing",
    "Packaging & Containers": "Packaging & Container",
    "Paper & Paper Products": "Paper/Forest Products",
    "Personal Services": "Business & Consumer Services",
    "Pharmaceutical Retailers": "Retail (Special Lines)",
    "Pollution & Treatment Controls": "Environmental & Waste Services",
    "Publishing": "Publishing & Newspapers",
    "Railroads": "Transportation (Railroads)",
    "Real Estate - Development": "Real Estate (Development)",
    "Real Estate - Diversified": "Real Estate (General/Diversified)",
    "Real Estate Services": "Real Estate (Operations & Services)",
    "Recreational Vehicles": "Recreation",
    "Rental & Leasing Services": "Business & Consumer Services",
    "Residential Construction": "Homebuilding",
    "Resorts & Casinos": "Hotel/Gaming",
    "Restaurants": "Restaurant/Dining",
    "Scientific & Technical Instruments": "Electronics (General)",
    "Security & Protection Services": "Business & Consumer Services",
    "Semiconductor Equipment & Materials": "Semiconductor Equip",
    "Semiconductors": "Semiconductor",
    "Silver": "Precious Metals",
    "Software - Application": "Software (System & Application)",
    "Software - Infrastructure": "Software (System & Application)",
    "Solar": "Green & Renewable Energy",
    "Specialty Business Services": "Business & Consumer Services",
    "Specialty Chemicals": "Chemical (Specialty)",
    "Specialty Industrial Machinery": "Machinery",
    "Specialty Retail": "Retail (Special Lines)",
    "Staffing & Employment Services": "Business & Consumer Services",
    "Steel": "Steel",
    "Telecom Services": "Telecom. Services",
    "Textile Manufacturing": "Apparel",
    "Thermal Coal": "Coal & Related Energy",
    "Tobacco": "Tobacco",
    "Tools & Accessories": "Machinery",
    "Travel Services": "Recreation",
    "Trucking": "Trucking",
    "Uranium": "Metals & Mining",
    "Utilities - Diversified": "Power",
    "Utilities - Independent Power Producers": "Power",
    "Utilities - Regulated Electric": "Power",
    "Utilities - Regulated Gas": "Oil/Gas Distribution",
    "Utilities - Regulated Water": "Utility (Water)",
    "Utilities - Renewable": "Green & Renewable Energy",
    "Waste Management": "Environmental & Waste Services",
}


@lru_cache(maxsize=1)
def table_primes() -> pd.DataFrame:
    """Prime de risque totale, prime pays et impôt légal par pays (Damodaran, janvier 2026)."""
    return pd.read_csv(DOSSIER_DONNEES / "primes_pays.csv").set_index("pays")


@lru_cache(maxsize=1)
def table_betas() -> pd.DataFrame:
    """Bêtas désendettés corrigés de la trésorerie par secteur (Damodaran, janvier 2026)."""
    return pd.read_csv(DOSSIER_DONNEES / "betas_sectoriels.csv").set_index("industrie")


def note_synthetique(couverture: float | None, petite: bool = False) -> tuple[str, float]:
    """Renvoie (note, spread) selon la table de Damodaran. Sans donnée : BBB par défaut."""
    if couverture is None or pd.isna(couverture):
        return "Baa2/BBB", SPREADS["Baa2/BBB"]
    for seuil, note in SEUILS_PETITES if petite else SEUILS_GRANDES:
        if couverture > seuil:
            return note, SPREADS[note]
    return "D2/D", SPREADS["D2/D"]


def parametres_pays(pays: str | None, devise: str | None = "EUR") -> dict:
    """Taux sans risque de la DEVISE des flux, prime de risque totale et impôt légal du PAYS.

    Règle : le taux sans risque dépend de la monnaie dans laquelle on compte (un flux en euros
    s'actualise avec un taux en euros), la prime de risque dépend du pays (risque pays)."""
    primes = table_primes()
    connu = pays in primes.index
    ligne = primes.loc[pays if connu else PAYS_DEFAUT]
    devise = _devise_iso(devise) or "EUR"
    rf_connu = devise in TAUX_SANS_RISQUE
    return {
        "pays": pays or "inconnu",
        "connu": connu,
        "rf": TAUX_SANS_RISQUE.get(devise, TAUX_SANS_RISQUE["EUR"]),
        "devise_rf": devise,
        "rf_connu": rf_connu,
        "prime": float(ligne["erp"]),
        "prime_pays": float(ligne["crp"]),
        "impot": float(ligne["impot"]),
        "zone": ZONE_DAMODARAN.get(ligne["region"], "Europe de l'Ouest"),
    }


def beta_sectoriel(industrie: str | None, pays: str | None) -> dict | None:
    """Bêta désendetté du secteur (Damodaran) : moyenne de dizaines d'entreprises, donc bien plus
    stable que la régression d'une seule action. Table Europe pour l'Europe de l'Ouest, États-Unis
    sinon. Renvoie None si le secteur Yahoo n'a pas de correspondance."""
    nom = SECTEURS_DAMODARAN.get(industrie or "")
    if nom is None and (industrie or "").startswith("REIT"):
        nom = "R.E.I.T."
    betas = table_betas()
    if nom is None or nom not in betas.index:
        return None
    europe = parametres_pays(pays)["zone"] == "Europe de l'Ouest"
    valeur = betas.loc[nom, "europe" if europe else "us"]
    if pd.isna(valeur):
        valeur, europe = betas.loc[nom, "us" if europe else "europe"], not europe
    return {
        "beta_desendette": float(valeur),
        "secteur": nom,
        "table": "Europe" if europe else "États-Unis",
    }


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------


def _ligne(tableau: pd.DataFrame, *noms: str) -> pd.Series | None:
    """Première ligne trouvée parmi plusieurs libellés possibles (Yahoo varie)."""
    for nom in noms:
        if nom in tableau.index and tableau.loc[nom].notna().any():
            return tableau.loc[nom]
    return None


def _derniere_valeur(serie: pd.Series, quoi: str = "Dette nette") -> float:
    """Dernière valeur disponible (Yahoo laisse parfois la dernière année vide)."""
    serie = serie.dropna()
    if serie.empty:
        raise ValueError(f"{quoi} introuvable sur Yahoo Finance : à saisir à la main.")
    return float(serie.iloc[-1])


def _valeur_annee(h: pd.DataFrame, poste: str, annee) -> float | None:
    """Valeur d'un poste pour UNE année précise (None si absente) : on ne mélange pas les années."""
    if poste not in h.index or annee not in h.columns:
        return None
    v = h.loc[poste, annee]
    return None if pd.isna(v) else float(v)


def _indice_pour(ticker: str) -> str:
    """Indice boursier de référence pour le bêta, déduit du suffixe du ticker (S&P 500 sinon)."""
    for suffixe, indice in INDICES.items():
        if suffixe and ticker.upper().endswith(suffixe):
            return indice
    return INDICES[""]


def _taux_de_change(de: str, vers: str) -> float:
    """Combien de « vers » pour 1 « de » (ex. USD → EUR)."""
    de, vers = _devise_iso(de), _devise_iso(vers)
    if de == vers:
        return 1.0
    return float(_avec_relance(lambda: yf.Ticker(f"{de}{vers}=X").fast_info["lastPrice"]))


def _devise_iso(devise: str | None) -> str | None:
    """Londres cote en pence (« GBp ») : pour les montants agrégés, la devise est la livre."""
    return "GBP" if devise == "GBp" else devise


def _avec_relance(fonction, essais: int = 3, attente: float = 2.0):
    """Yahoo limite le nombre de requêtes (surtout depuis un serveur partagé comme Streamlit
    Cloud) : on réessaie après une courte pause plutôt que d'échouer au premier refus."""
    for i in range(essais):
        try:
            return fonction()
        except Exception as e:  # limite de requêtes, coupure réseau…
            if i == essais - 1:
                raise
            if "Too Many Requests" in str(e) or "Rate" in type(e).__name__:
                time.sleep(attente * (i + 1) * 2)
            else:
                time.sleep(attente)


# ---------------------------------------------------------------------------
# 1. Données historiques
# ---------------------------------------------------------------------------

# Postes qui ne sont pas des montants en devise : jamais convertis
POSTES_SANS_DEVISE = ("Actions en circulation", "Actions moyennes", "Actions diluées moyennes")


def _reconstituer_dette(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute « Dette financière retenue » et « Dette nette » à un bilan (une colonne par date).

    Yahoo ne remplit pas toujours « Total Debt » de façon complète : on reconstitue la dette
    à partir de ses composantes et on garde, chaque date, le montant le plus complet.
    Une case vide n'est PAS remplacée par 0 (sinon dette nette = −trésorerie)."""
    nan = pd.Series(float("nan"), index=df.columns)

    def _somme(*postes):
        """Somme des postes, ou NaN si l'un d'eux manque au bilan."""
        if not all(p in df.index for p in postes):
            return nan
        return sum(df.loc[p] for p in postes)

    candidats = pd.DataFrame(
        {
            "total": df.loc["Dette financière"] if "Dette financière" in df.index else nan,
            "lt+ct avec loyers": _somme("Dette LT (avec loyers)", "Dette CT (avec loyers)"),
            "lt+ct+loyers": _somme("Dette LT", "Dette CT")
            + (
                df.loc["dont dettes locatives"].fillna(0)
                if "dont dettes locatives" in df.index
                else 0
            ),
        }
    )
    dette = candidats.max(axis=1, skipna=True)
    df.loc["Dette financière retenue"] = dette
    treso = df.loc["Trésorerie"] if "Trésorerie" in df.index else nan
    yahoo = df.loc["Dette nette Yahoo"] if "Dette nette Yahoo" in df.index else nan
    df.loc["Dette nette"] = (dette - treso).fillna(yahoo)
    return df


LIGNES_BILAN = {
    "Dette financière": ("Total Debt",),
    "Dette LT (avec loyers)": ("Long Term Debt And Capital Lease Obligation",),
    "Dette CT (avec loyers)": ("Current Debt And Capital Lease Obligation",),
    "Dette LT": ("Long Term Debt",),
    "Dette CT": ("Current Debt",),
    "dont dettes locatives": ("Capital Lease Obligations",),
    "Dette nette Yahoo": ("Net Debt",),
    "Trésorerie": ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"),
}


def _lignes_bilan(bilan: pd.DataFrame) -> dict:
    """Lignes de dette et de trésorerie d'un bilan Yahoo, sous leur libellé français."""
    return {k: _ligne(bilan, *noms) for k, noms in LIGNES_BILAN.items()}


def historique(ticker: str) -> pd.DataFrame:
    """Tableau historique (une colonne par année, de la plus ancienne à la plus récente),
    en millions, dans la devise de PUBLICATION. La date de clôture est dans df.attrs."""
    t = yf.Ticker(ticker)
    cr, bilan, flux = _avec_relance(lambda: (t.income_stmt, t.balance_sheet, t.cashflow))
    if cr is None or cr.empty:
        raise ValueError(f"Aucune donnée financière trouvée pour « {ticker} ».")

    lignes = {
        "CA": _ligne(cr, "Total Revenue", "Operating Revenue"),
        # EBIT d'EXPLOITATION : « Operating Income » de Yahoo (hors éléments exceptionnels et hors
        # quote-part des sociétés mises en équivalence). L'« EBIT » de Yahoo, lui, vaut résultat avant
        # impôt + intérêts : il contient dépréciations, plus-values, pertes sur participations
        # (Renault 2025 : −9 878 avec la perte Nissan, contre +3 632 en exploitation).
        "EBIT": _ligne(cr, "Operating Income", "EBIT"),
        "EBIT Yahoo (avec exceptionnels)": _ligne(cr, "EBIT"),
        "D&A": _ligne(
            flux,
            "Depreciation And Amortization",
            "Depreciation Amortization Depletion",
            "Reconciled Depreciation",
        ),
        "Capex": _ligne(flux, "Capital Expenditure"),
        "Créances clients": _ligne(bilan, "Accounts Receivable", "Receivables"),
        "Stocks": _ligne(bilan, "Inventory"),
        "Dettes fournisseurs": _ligne(bilan, "Accounts Payable", "Payables"),
        "Impôts": _ligne(cr, "Tax Provision"),
        "Résultat avant impôt": _ligne(cr, "Pretax Income"),
        "Charges d'intérêts": _ligne(cr, "Interest Expense", "Interest Expense Non Operating"),
        **_lignes_bilan(bilan),
        "Minoritaires": _ligne(bilan, "Minority Interest"),
        "Capitaux propres part du groupe": _ligne(bilan, "Stockholders Equity"),
        "Provisions retraites": _ligne(
            bilan, "Non Current Pension And Other Postretirement Benefit Plans"
        ),
        "Participations (mises en équivalence)": _ligne(bilan, "Long Term Equity Investment"),
        "Hybrides et autres instruments": _ligne(bilan, "Other Equity Interest"),
        "Actions en circulation": _ligne(bilan, "Ordinary Shares Number"),
        "Actions moyennes": _ligne(cr, "Basic Average Shares"),
        "Actions diluées moyennes": _ligne(cr, "Diluted Average Shares"),
    }
    manquantes = [k for k in ("CA", "EBIT", "D&A", "Capex") if lignes[k] is None]
    if manquantes:
        raise ValueError(
            f"Données indispensables absentes pour « {ticker} » : {', '.join(manquantes)}. "
            "Le DCF FCFF n'est probablement pas adapté à cette entreprise."
        )
    df = pd.DataFrame({k: v for k, v in lignes.items() if v is not None}).T
    df = df[sorted(df.columns)]  # années croissantes
    date_cloture = df.columns[-1]
    df.columns = [c.year for c in df.columns]
    df = df.dropna(axis=1, how="all") / 1e6  # en millions

    for poste in ("Capex", "Charges d'intérêts"):  # en positif
        if poste in df.index:
            df.loc[poste] = df.loc[poste].abs()

    # BFR d'exploitation = créances + stocks − fournisseurs
    bfr = 0
    for poste, signe in (("Créances clients", 1), ("Stocks", 1), ("Dettes fournisseurs", -1)):
        if poste in df.index:
            bfr = bfr + signe * df.loc[poste].fillna(0)
    df.loc["BFR"] = bfr

    df = _reconstituer_dette(df)
    df.attrs["date_cloture"] = pd.Timestamp(date_cloture)
    return df


def convertir(h: pd.DataFrame, fx: float) -> pd.DataFrame:
    """Convertit les montants dans une autre devise, SAUF les nombres d'actions."""
    montants = [i for i in h.index if i not in POSTES_SANS_DEVISE]
    h2 = h.copy()
    h2.loc[montants] = h2.loc[montants] * fx
    h2.attrs = dict(h.attrs)
    return h2


def ratios(h: pd.DataFrame) -> pd.DataFrame:
    """Ratios historiques (une colonne par année) et leur moyenne, comme dans l'Excel."""
    r = pd.DataFrame(
        {
            "Croissance CA": h.loc["CA"].pct_change(),
            "Marge EBIT": h.loc["EBIT"] / h.loc["CA"],
            "D&A/CA": h.loc["D&A"] / h.loc["CA"],
            "Capex/CA": h.loc["Capex"] / h.loc["CA"],
            "BFR/CA": h.loc["BFR"] / h.loc["CA"],
            "Taux d'impôt": h.loc["Impôts"] / h.loc["Résultat avant impôt"],
        }
    ).T
    r["Moyenne"] = r.mean(axis=1)
    return r


TAUX_IMPLICITE_MAX = 0.12  # au-delà, les charges d'intérêts ne collent pas avec la dette


def dette_nette_fiable(
    h: pd.DataFrame, interets: float | None, kd: float
) -> tuple[float, str | None]:
    """Dette nette retenue et, si elle a été corrigée, l'explication.

    Contrôle de cohérence : taux d'intérêt implicite = charges d'intérêts / dette brute.
    S'il dépasse 12 %, la dette brute est incomplète : Yahoo a perdu les obligations et emprunts
    et ne garde que les dettes locatives (cas d'Orange depuis 2023 : 7,5 Md€ de « dette » pour
    1,3 Md€ d'intérêts, soit 18 %). La dette nette calculée, souvent négative, est alors fausse.

    Correction, dans l'ordre :
      1. dette nette publiée par Yahoo pour la MÊME année (hors loyers) + dettes locatives de cette
         année. Une donnée plus ancienne est refusée : le périmètre a pu changer (Orange : sortie
         de l'Espagne en 2024, la dette 2022 surestimait l'endettement de 9 Md€) ;
      2. sinon, estimation grossière : intérêts / Kd − trésorerie, au moins 0.
    Dans les deux cas, le message demande de saisir le chiffre publié.

    Charges d'intérêts absentes (NaN) : on ne peut pas contrôler, on garde la dette nette telle
    quelle (une donnée manquante n'est pas une incohérence).

    Une dette nette négative COHÉRENTE (intérêts faibles, comme Hermès) est conservée : c'est une
    vraie trésorerie nette, qui appartient aux actionnaires et s'ajoute à la valeur.
    """
    dette_nette = _derniere_valeur(h.loc["Dette nette"])
    annee = h.loc["Dette nette"].dropna().index[-1]
    if interets is None or pd.isna(interets) or interets <= 0:
        return dette_nette, None

    brute = _valeur_annee(h, "Dette financière retenue", annee) or 0.0
    locatives = _valeur_annee(h, "dont dettes locatives", annee) or 0.0
    treso = _valeur_annee(h, "Trésorerie", annee) or 0.0
    taux_implicite = interets / brute if brute > 0 else float("inf")
    if taux_implicite <= TAUX_IMPLICITE_MAX:
        return dette_nette, None

    diagnostic = (
        f"Dette nette Yahoo incohérente ({dette_nette:,.0f}) : {interets:,.0f} de charges "
        f"d'intérêts pour {brute:,.0f} de dette brute, soit un taux implicite de "
        f"{taux_implicite:.0%}. Yahoo a perdu une partie de la dette financière "
        f"(obligations, emprunts). "
    )
    yahoo = _valeur_annee(h, "Dette nette Yahoo", annee)
    if yahoo is not None and yahoo > 0:
        estimation = yahoo + locatives
        methode = (
            f"Estimation retenue : {estimation:,.0f} = dette nette financière publiée par Yahoo "
            f"({annee}, {yahoo:,.0f}) + dettes locatives ({locatives:,.0f}). "
        )
    else:
        estimation = max(interets / kd - treso, 0.0)
        methode = (
            f"Estimation GROSSIÈRE : {estimation:,.0f} = intérêts / coût de la dette "
            f"({interets:,.0f} / {kd:.2%}) − trésorerie ({treso:,.0f}). Le coût moyen d'une dette "
            "ancienne est souvent plus bas que le Kd actuel : l'estimation peut être très fausse. "
        )
    return (
        estimation,
        diagnostic + methode + "SAISIS l'endettement financier net du rapport annuel.",
    )


def taux_impot_defaut(h: pd.DataFrame, impot_legal: float) -> tuple[float, str]:
    """Taux effectif moyen s'il est plausible (15-35 %), sinon taux légal du pays.
    Une année de perte ou de résultat quasi nul rend le taux effectif absurde :
    on ne garde que les années bénéficiaires avec un taux entre 0 et 60 %."""
    avant_impot, impots = h.loc["Résultat avant impôt"], h.loc["Impôts"]
    taux = (impots / avant_impot)[(avant_impot > 0) & (impots >= 0)]
    taux = taux[taux < 0.6]
    moyen = float(taux.mean()) if not taux.empty else float("nan")
    if 0.15 <= moyen <= 0.35:
        return moyen, "taux effectif moyen"
    return impot_legal, "taux légal du pays (taux effectif aberrant)"


def couverture_interets(h: pd.DataFrame, annees: int = 3) -> float | None:
    """Couverture des intérêts (EBIT / charges d'intérêts) : MÉDIANE des 3 derniers exercices,
    pour qu'une seule année exceptionnelle ne fasse pas basculer la note de crédit."""
    if "Charges d'intérêts" not in h.index:
        return None
    c = (
        h.loc["EBIT"] / h.loc["Charges d'intérêts"].where(h.loc["Charges d'intérêts"] > 0)
    ).dropna()
    return float(c.iloc[-annees:].median()) if not c.empty else None


# ---------------------------------------------------------------------------
# 2. Marché : cours, actions, bêta, dividendes, consensus
# ---------------------------------------------------------------------------

PAYS_PAR_SUFFIXE = {
    ".PA": "France",
    ".DE": "Germany",
    ".AS": "Netherlands",
    ".MI": "Italy",
    ".L": "United Kingdom",
    ".MC": "Spain",
    ".SW": "Switzerland",
    ".BR": "Belgium",
    ".LS": "Portugal",
    ".IR": "Ireland",
    ".ST": "Sweden",
    ".CO": "Denmark",
    ".HE": "Finland",
    ".OL": "Norway",
    ".VI": "Austria",
}


def donnees_marche(ticker: str) -> dict:
    """Données de marché Yahoo : nom, pays, secteur, devises, cours, nombre d'actions, bêta Yahoo,
    EBITDA des 12 derniers mois, plus bas / plus haut sur 52 semaines et logo. Renvoie un dict."""
    t = yf.Ticker(ticker)

    def lire():
        """Lit les deux sources Yahoo ; échoue (et donc relance) si le cours manque."""
        fi_, info_ = t.fast_info, t.info
        return fi_, info_, fi_["currency"], float(fi_["lastPrice"])  # lève une erreur si incomplet

    fi, info, devise_cours, cours = _avec_relance(lire)
    pays = info.get("country") or next(
        (p for s, p in PAYS_PAR_SUFFIXE.items() if ticker.upper().endswith(s)), None
    )
    # Bourse de Londres : cotation en pence (GBp) → on repasse en livres
    if devise_cours == "GBp":
        devise_cours, cours = "GBP", cours / 100
    site = (info.get("website") or "").replace("https://", "").replace("http://", "").strip("/")
    return {
        "nom": info.get("longName", ticker),
        "pays": pays,  # Yahoo l'oublie parfois (Michelin) : déduit de la place de cotation
        "secteur": info.get("industry") or "",
        "devise": devise_cours,
        "devise_comptes": info.get("financialCurrency") or devise_cours,
        "cours": cours,
        "actions_yahoo": (info.get("sharesOutstanding") or fi["shares"]) / 1e6,
        "capitalisation_yahoo": (info.get("marketCap") or 0) / 1e6,
        "beta_yahoo": info.get("beta"),
        # EBITDA des 12 derniers mois selon Yahoo, dans la devise des COMPTES : même définition et
        # même période que les multiples des comparables (à convertir dans la devise de cotation)
        "ebitda_ttm": (info.get("ebitda") or 0) / 1e6 or None,
        "plus_bas_52s": float(fi["yearLow"]) / (100 if fi["currency"] == "GBp" else 1),
        "plus_haut_52s": float(fi["yearHigh"]) / (100 if fi["currency"] == "GBp" else 1),
        "logo": f"https://www.google.com/s2/favicons?domain={site}&sz=128" if site else None,
    }


def beta_regression(ticker: str, indice: str | None = None, annees: int = 5) -> dict:
    """Bêta = Cov(rendements action, rendements indice) / Var(rendements indice),
    sur des cours mensuels ajustés (comme l'onglet « Calcul Béta » de l'Excel).

    Renvoie aussi sa fiabilité :
      - R² : part des variations de l'action expliquée par le marché (0 = aucune) ;
      - erreur type : incertitude sur le bêta. Avec 60 mois, ±2 erreurs types donnent un
        intervalle de confiance à 95 % souvent large (ex. 0,33 ± 0,30)."""
    indice = indice or _indice_pour(ticker)
    prix = _avec_relance(
        lambda: yf.download(
            [ticker, indice], period=f"{annees}y", interval="1mo", auto_adjust=True, progress=False
        )["Close"].dropna()
    )
    rdt = prix.pct_change().dropna()
    x, y = rdt[indice], rdt[ticker]
    b = y.cov(x) / x.var()
    r2 = y.corr(x) ** 2
    n = len(rdt)
    residus = y - y.mean() - b * (x - x.mean())
    erreur = (
        math.sqrt((residus**2).sum() / (n - 2) / ((x - x.mean()) ** 2).sum())
        if n > 2
        else float("nan")
    )
    return {
        "beta": float(b),
        "r2": float(r2),
        "erreur_type": float(erreur),
        "n": n,
        "indice": indice,
    }


def beta_blume(b: float) -> float:
    """Ajustement de Blume : les bêtas tendent vers 1 avec le temps."""
    return 0.67 * b + 0.33


R2_MIN, BETA_MIN, BETA_MAX = 0.20, 0.6, 1.6


def beta_fiable(stats: dict) -> tuple[bool, str]:
    """La régression est jugée fiable si le marché explique au moins 20 % des variations de
    l'action et si le bêta reste dans une zone plausible (0,6 à 1,6)."""
    raisons = []
    if stats["r2"] < R2_MIN:
        raisons.append(
            f"R² de {stats['r2']:.0%} : le CAC/l'indice explique peu les variations de l'action"
        )
    if not BETA_MIN <= stats["beta"] <= BETA_MAX:
        raisons.append(
            f"bêta de {stats['beta']:.2f} hors de la zone plausible {BETA_MIN}-{BETA_MAX}"
        )
    return (not raisons), " ; ".join(raisons)


def dividendes_depuis(ticker: str, date: pd.Timestamp) -> float:
    """Dividendes par action versés depuis la date de clôture."""
    div = yf.Ticker(ticker).dividends
    if div is None or div.empty:
        return 0.0
    index = div.index.tz_localize(None) if div.index.tz is not None else div.index
    return float(div[index > date].sum())


def croissance_consensus(ticker: str) -> float | None:
    """Croissance moyenne du CA attendue par les analystes (exercice en cours et suivant,
    Yahoo). Attention : elle inclut les acquisitions annoncées (Orange 2026 : MasOrange)."""
    try:
        estimations = yf.Ticker(ticker).revenue_estimate
        periodes = [p for p in ("0y", "+1y") if p in estimations.index]
        g = estimations.loc[periodes, "growth"].dropna()
        return float(g.mean()) if not g.empty else None
    except Exception:
        return None


def dette_nette_moyenne(ticker: str, fx: float, dette_brute_cloture: float) -> dict | None:
    """Dette nette des bilans intermédiaires (semestriels ou trimestriels) des 12 derniers mois.

    Pourquoi : le BFR varie dans l'année. Un distributeur encaisse ses clients tout de suite et paie
    ses fournisseurs plus tard : au 31/12, après les fêtes, sa trésorerie est au plus haut et sa dette
    nette au plus bas. La dette nette MOYENNE représente mieux ce que l'entreprise doit vraiment.
    Les dates où la dette brute paraît incomplète (moins de la moitié de celle de clôture) sont
    écartées."""
    try:
        bilans = yf.Ticker(ticker).quarterly_balance_sheet
    except Exception:
        return None
    if bilans is None or bilans.empty:
        return None
    df = pd.DataFrame({k: v for k, v in _lignes_bilan(bilans).items() if v is not None}).T
    if df.empty:
        return None
    df = _reconstituer_dette(df[sorted(df.columns)] / 1e6 * fx)
    dn = df.loc["Dette nette"].dropna()
    brute = df.loc["Dette financière retenue"]
    dn = (
        dn[[brute[c] >= 0.5 * dette_brute_cloture for c in dn.index]]
        if dette_brute_cloture > 0
        else dn
    )
    if dn.empty:
        return None
    dernier = dn.index[-1]
    dn = dn[dn.index > dernier - pd.Timedelta(days=366)]
    if len(dn) < 2:
        return None
    return {"moyenne": float(dn.mean()), "points": {c.date(): float(v) for c, v in dn.items()}}


REGIONS = {
    "France": "fr",
    "Germany": "de",
    "United Kingdom": "gb",
    "Switzerland": "ch",
    "Netherlands": "nl",
    "Italy": "it",
    "Spain": "es",
    "Belgium": "be",
    "Ireland": "ie",
    "Sweden": "se",
    "Denmark": "dk",
    "Finland": "fi",
    "Norway": "no",
    "Austria": "at",
    "Portugal": "pt",
    "Luxembourg": "lu",
    "Poland": "pl",
    "Greece": "gr",
    "United States": "us",
    "Canada": "ca",
    "Japan": "jp",
}
EUROPE = [
    "fr",
    "de",
    "gb",
    "ch",
    "nl",
    "it",
    "es",
    "be",
    "ie",
    "se",
    "dk",
    "fi",
    "no",
    "at",
    "pt",
    "lu",
    "pl",
    "gr",
]
# Places de cotation secondaires (doublons d'une même entreprise) à écarter
PLACES_SECONDAIRES = (
    ".F",
    ".BE",
    ".DU",
    ".MU",
    ".SG",
    ".HM",
    ".HA",
    ".VI",
    ".IL",
    ".XC",
    ".XD",
    ".TI",
)


def comparables_auto(ticker: str, n: int = 6) -> list[str]:
    """Propose automatiquement des comparables : même secteur d'activité (« industry » Yahoo),
    même zone géographique (Europe ou pays), taille la plus proche possible.
    On élargit la fourchette de taille si on ne trouve pas assez d'entreprises."""
    from yfinance import EquityQuery

    info = yf.Ticker(ticker).info
    industrie, capi = info.get("industry"), info.get("marketCap")
    if not industrie:
        return []
    pays_cible = info.get("country") or next(
        (p for s, p in PAYS_PAR_SUFFIXE.items() if ticker.upper().endswith(s)), None
    )
    region = REGIONS.get(pays_cible, "us")
    regions = EUROPE if region in EUROPE else [region]
    # Le screener Yahoo ne connaît pas toutes les régions (ex. Luxembourg « lu ») : une seule
    # région inconnue fait échouer TOUTE la requête. On ne garde que celles qu'il accepte.
    acceptees = EquityQuery("eq", ["region", "us"]).valid_values.get("region", regions)
    regions = [r for r in regions if r in acceptees] or ["us"]
    zone = (
        EquityQuery("is-in", ["region", *regions])
        if len(regions) > 1
        else EquityQuery("eq", ["region", regions[0]])
    )

    # symbole → (nom, capitalisation, marché de cotation « fr_market », « de_market »…)
    trouves: dict[str, tuple[str, float, str]] = {}
    for facteur in (5, 20, None):  # fourchettes de taille de plus en plus larges
        conditions = [EquityQuery("eq", ["industry", industrie]), zone]
        if capi and facteur:
            conditions.append(
                EquityQuery("btwn", ["intradaymarketcap", capi / facteur, capi * facteur])
            )
        try:
            reponse = yf.screen(
                EquityQuery("and", conditions),
                size=50,
                sortField="intradaymarketcap",
                sortAsc=False,
            )
        except Exception:
            continue
        for q in reponse.get("quotes", []):
            symbole = q.get("symbol", "")
            nom = q.get("longName") or q.get("shortName") or symbole
            if (
                symbole.upper() == ticker.upper()
                or symbole.upper().endswith(PLACES_SECONDAIRES)
                or q.get("market") == "dr_market"
                or nom == info.get("longName")
            ):
                continue
            trouves[symbole] = (nom, q.get("marketCap") or 0, q.get("market") or "")
        if len({nom for nom, _, _ in trouves.values()}) >= 2 * n:
            break

    if not trouves:  # secours : grandes entreprises du secteur
        try:
            leaders = yf.Industry(info["industryKey"]).top_companies
            return [t for t in leaders.index if t.upper() != ticker.upper()][:n]
        except Exception:
            return []

    # On parcourt les candidats du plus proche au plus éloigné en taille, en écartant les
    # cotations SECONDAIRES : une entreprise américaine ou suisse cotée aussi à Francfort apparaît
    # dans le filtre « Europe », mais elle publie dans une autre devise et n'est pas de la zone.
    # Règle : le pays de l'entreprise doit être CONNU, dans la zone, ET correspondre à la place de
    # cotation. Un pays inconnu de la table (Inde, Corée, Chine…) est écarté : c'est ainsi que
    # Mahindra (Renault) ou les ADR de Samsung et Xiaomi (Apple) passaient le filtre.
    candidats = sorted(
        trouves.items(), key=lambda x: abs(math.log(max(x[1][1], 1) / capi)) if capi else 0
    )
    retenus, noms_retenus = [], set()
    for symbole, (nom, _, marche) in candidats:
        if len(retenus) >= n:
            break
        if nom in noms_retenus:
            continue
        try:
            pays = yf.Ticker(symbole).info.get("country")
        except Exception:
            continue
        code = REGIONS.get(pays)
        if code is None or code not in regions or (marche and not marche.startswith(code)):
            continue
        retenus.append(symbole)
        noms_retenus.add(nom)
    return retenus


def comparables(tickers: list[str]) -> pd.DataFrame:
    """Multiples de marché des entreprises comparables (source : Yahoo Finance).

    Piège des devises : Yahoo calcule ses multiples en divisant la VE (dans la devise de COTATION)
    par l'EBITDA (dans la devise des COMPTES) sans conversion. Rockwool, cotée en couronnes
    danoises mais publiant en euros, ressort ainsi à 63x au lieu de 8x (63 / 7,46).
    On corrige en convertissant le dénominateur dans la devise de cotation."""
    lignes, taux = [], {}
    for tk in tickers:
        try:
            info = yf.Ticker(tk).info
        except Exception:
            continue
        if not info or info.get("enterpriseToEbitda") is None and info.get("trailingPE") is None:
            continue
        cotation = _devise_iso(info.get("currency"))
        comptes = info.get("financialCurrency") or cotation
        fx = 1.0
        if cotation and comptes and comptes != cotation:
            if (comptes, cotation) not in taux:
                try:
                    taux[(comptes, cotation)] = _taux_de_change(comptes, cotation)
                except Exception:
                    taux[(comptes, cotation)] = float("nan")  # multiple inexploitable
            fx = taux[(comptes, cotation)]

        multiples = {
            colonne: None if info.get(cle) is None else info.get(cle) / fx
            for colonne, cle in (
                ("VE / EBITDA", "enterpriseToEbitda"),
                ("VE / CA", "enterpriseToRevenue"),
                ("PER", "trailingPE"),
            )
        }
        lignes.append(
            {
                "Ticker": tk,
                "Entreprise": (info.get("shortName") or info.get("longName") or tk).strip(),
                "Pays": info.get("country"),
                "Devises": cotation if comptes == cotation else f"{cotation} / comptes {comptes}",
                **multiples,
            }
        )
    df = pd.DataFrame(
        lignes, columns=["Ticker", "Entreprise", "Pays", "Devises", "VE / EBITDA", "VE / CA", "PER"]
    )
    for col in ("VE / EBITDA", "VE / CA", "PER"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        # multiples négatifs ou extrêmes : sans signification économique
        df.loc[(df[col] <= 0) | (df[col] > 100), col] = float("nan")
    return df


# ---------------------------------------------------------------------------
# 3. Assemblage : tout ce qu'il faut pour dcf.py
# ---------------------------------------------------------------------------


def verifier_eligibilite(ticker: str) -> None:
    """Le DCF FCFF n'a pas de sens pour les banques et assurances :
    leur dette est leur matière première, il n'y a ni EBIT, ni BFR, ni Capex au sens industriel."""
    info = _avec_relance(lambda: yf.Ticker(ticker).info)
    secteur = (info.get("industry") or "").lower()
    if any(mot in secteur for mot in SECTEURS_EXCLUS):
        raise ValueError(
            f"{info.get('longName', ticker)} est une entreprise financière ({info.get('industry')}). "
            "Le DCF FCFF n'est pas adapté : utiliser plutôt un multiple de fonds propres "
            "(cours / valeur comptable) ou un modèle de dividendes."
        )


CROISSANCE_MIN, CROISSANCE_MAX = 0.0, 0.05  # borne de la croissance par défaut
SEUIL_FINANCEMENT_CAPTIF = 0.40  # créances clients > 40 % du CA


def preparer(ticker: str) -> dict:
    """Récupère tout et renvoie les objets de dcf.py, des paramètres par défaut
    et la liste des alertes à vérifier."""
    verifier_eligibilite(ticker)
    alertes = []
    m = donnees_marche(ticker)
    h = historique(ticker)
    date_cloture = h.attrs["date_cloture"]

    # --- Devise : comptes publiés dans une autre devise que la cotation ---
    fx = 1.0
    if m["devise_comptes"] != m["devise"]:
        fx = _taux_de_change(m["devise_comptes"], m["devise"])
        h = convertir(h, fx)
        alertes.append(
            f"Comptes publiés en {m['devise_comptes']}, action cotée en {m['devise']} : "
            f"montants convertis au taux de {fx:.4f}."
        )

    r = ratios(h)
    derniere = h.columns[-1]
    pays = parametres_pays(m["pays"], m["devise"])
    if not pays["connu"]:
        alertes.append(
            f"Pays « {pays['pays']} » absent de la table Damodaran : prime de risque de la "
            "France par défaut. Ajuste la prime de risque."
        )
    if not pays["rf_connu"]:
        alertes.append(
            f"Pas de taux sans risque enregistré pour le {pays['devise_rf']} : taux du Bund "
            "(euro) par défaut. Saisis le taux d'État à 10 ans de cette devise."
        )

    # --- Impôt ---
    taux_is, source_is = taux_impot_defaut(h, pays["impot"])
    if source_is != "taux effectif moyen":
        alertes.append(f"Taux d'impôt effectif aberrant : taux légal retenu ({taux_is:.1%}).")

    # --- Nombre d'actions et dilution ---
    actions_bilan = (
        _derniere_valeur(h.loc["Actions en circulation"], "Actions")
        if "Actions en circulation" in h.index and h.loc["Actions en circulation"].notna().any()
        else None
    )
    nb_actions = actions_bilan or m["actions_yahoo"]
    capi = nb_actions * m["cours"]
    if m["capitalisation_yahoo"] and abs(capi / m["capitalisation_yahoo"] - 1) > 0.05:
        alertes.append(
            f"Nombre d'actions incertain : {nb_actions:,.1f} M donne une capitalisation de "
            f"{capi:,.0f}, contre {m['capitalisation_yahoo']:,.0f} selon Yahoo "
            "(actions autodétenues ou plusieurs catégories d'actions ?)."
        )
    # Dilution : écart entre actions diluées et actions de base (stock-options, actions gratuites,
    # convertibles « dans la monnaie »), d'après le dernier compte de résultat
    dilue, base = (
        _valeur_annee(h, "Actions diluées moyennes", derniere),
        _valeur_annee(h, "Actions moyennes", derniere),
    )
    actions_dilutives = max(dilue - base, 0.0) if dilue and base else 0.0

    # --- Note de crédit : couverture médiane des 3 derniers exercices ---
    interets = _valeur_annee(h, "Charges d'intérêts", derniere)
    couverture = couverture_interets(h)
    petite = (
        capi * _taux_de_change(m["devise"], "USD") < SEUIL_PETITE_ENTREPRISE
        if m["devise"] != "USD"
        else capi < SEUIL_PETITE_ENTREPRISE
    )
    note, spread = note_synthetique(couverture, petite)

    # --- Dette nette et loyers IFRS 16 ---
    dette_nette, correction = dette_nette_fiable(h, interets, pays["rf"] + spread)
    dette_a_saisir = correction is not None
    if correction:
        alertes.append(correction)
    elif dette_nette < 0:
        alertes.append(
            f"Trésorerie nette de {-dette_nette:,.0f} (dette nette négative), cohérente avec "
            "des charges d'intérêts faibles : elle s'ajoute à la valeur des fonds propres. "
            "Vérifie qu'elle n'est pas bloquée (filiales, trésorerie opérationnelle)."
        )
    annee_dn = h.loc["Dette nette"].dropna().index[-1]
    locatives = _valeur_annee(h, "dont dettes locatives", annee_dn) or 0.0
    loyers_importants = any(mot in m["secteur"].lower() for mot in SECTEURS_LOCATIFS) or (
        locatives > 0.25 * max(dette_nette, 1)
    )
    # Loyers annuels estimés ≈ dette locative / 5 (durée résiduelle moyenne des contrats ≈ 5 ans ;
    # à remplacer par les « remboursements de dettes locatives » du tableau des flux si connus)
    loyers_estimes = locatives / 5 / float(h.loc["CA", derniere]) if locatives else 0.0

    # --- Saisonnalité : dette nette moyenne des bilans intermédiaires ---
    moyenne = (
        None
        if dette_a_saisir
        else dette_nette_moyenne(
            ticker, fx, _valeur_annee(h, "Dette financière retenue", annee_dn) or 0.0
        )
    )
    if moyenne and moyenne["moyenne"] > dette_nette + 0.15 * max(abs(dette_nette), 1):
        alertes.append(
            f"Saisonnalité : la dette nette moyenne des 12 derniers mois "
            f"({moyenne['moyenne']:,.0f}) dépasse celle de clôture ({dette_nette:,.0f}). "
            "Le BFR gonfle en cours d'année : la dette de fin d'exercice flatte la valeur. "
            "Option « dette nette moyenne » dans le passage aux fonds propres."
        )

    # --- Éléments du passage aux fonds propres (bilan de clôture) ---
    def poste(nom: str) -> float:
        """Montant d'un poste au dernier bilan, 0 s'il est absent."""
        return _valeur_annee(h, nom, derniere) or 0.0

    retraites, participations, hybrides = (
        poste("Provisions retraites"),
        poste("Participations (mises en équivalence)"),
        max(poste("Hybrides et autres instruments"), 0.0),
    )
    if hybrides < 0.002 * capi:  # montant négligeable : bruit de classement Yahoo
        hybrides = 0.0
    if not retraites:
        alertes.append(
            "Provisions retraites absentes de Yahoo : saisis-les à partir du rapport annuel "
            "(engagements nets de retraite)."
        )
    minoritaires = poste("Minoritaires")
    fp_groupe = poste("Capitaux propres part du groupe")
    # Minoritaires « au prix du marché » : on applique aux minoritaires le multiple
    # capitalisation / capitaux propres du groupe (les actionnaires minoritaires détiennent une part
    # de filiales qui vaut plus que leur valeur comptable si le groupe vaut plus que ses fonds propres)
    minoritaires_marche = (
        minoritaires * capi / fp_groupe if fp_groupe > 0 and minoritaires else minoritaires
    )

    # --- Contrôles d'exploitation ---
    marge_der = float(r.loc["Marge EBIT", derniere])
    marge_moy = float(r.loc["Marge EBIT", "Moyenne"])
    if marge_der < 0 or (marge_moy > 0 and abs(marge_der / marge_moy - 1) > 0.4):
        alertes.append(
            f"Dernier exercice inhabituel : marge EBIT de {marge_der:.1%} contre {marge_moy:.1%} "
            "en moyenne. Vérifie s'il s'agit d'un creux de cycle ou d'éléments exceptionnels "
            "avant de projeter."
        )
    creances = _valeur_annee(h, "Créances clients", derniere) or 0.0
    financement_captif = creances > SEUIL_FINANCEMENT_CAPTIF * float(h.loc["CA", derniere])
    if financement_captif:
        alertes.append(
            f"Créances clients = {creances / float(h.loc['CA', derniere]):.0%} du CA : activité de "
            "financement consolidée (banque captive, comme Mobilize pour Renault). Ses crédits "
            "gonflent le BFR et ses dettes la dette nette : le DCF consolidé n'a pas de sens. "
            "Valorise la branche industrielle seule (BFR et dette nette hors financement, "
            "rapport annuel) et ajoute la banque à sa valeur comptable."
        )
    if loyers_importants:
        alertes.append(
            f"Loyers IFRS 16 importants (dettes locatives : {locatives:,.0f}). Choisis UN "
            "traitement dans « Méthode » (loyers = dette par défaut, ou loyers = charge) et "
            f"vérifie le montant annuel pré-rempli ({loyers_estimes:.1%} du CA ≈ dette locative / 5)."
        )

    croissance_hist = float(r.loc["Croissance CA", "Moyenne"])
    croissance = min(max(croissance_hist, CROISSANCE_MIN), CROISSANCE_MAX)
    capex_net = float(r.loc["Capex/CA", "Moyenne"] - r.loc["D&A/CA", "Moyenne"])
    if capex_net > 0.03 and croissance <= 0.01:
        alertes.append(
            f"Capex supérieur aux D&A de {capex_net:.1%} du CA, mais croissance retenue de "
            f"{croissance:.1%} : l'entreprise paierait des investissements de croissance sans en "
            "tirer de croissance. Revois la croissance (consensus) ou le Capex."
        )
    hyp = Hypotheses(
        croissance_ca=croissance,
        marge_ebit=marge_moy,
        da_ca=float(r.loc["D&A/CA", "Moyenne"]),
        capex_ca=float(r.loc["Capex/CA", "Moyenne"]),
        bfr_ca=float(r.loc["BFR/CA", "Moyenne"]),
        taux_is=taux_is,
        g=0.02,
        horizon=5,
    )
    entreprise = DonneesEntreprise(
        ca_dernier=float(h.loc["CA", derniere]),
        bfr_dernier=float(h.loc["BFR", derniere]),
        annee_derniere=int(derniere),
        dette_nette=dette_nette,
        minoritaires=minoritaires,
        provisions_retraites=retraites,
        nb_actions=nb_actions,
        cours=m["cours"],
        participations=participations,
        autres_dettes=hybrides,
        actions_dilutives=actions_dilutives,
    )

    # --- Bêta : régression (avec sa fiabilité) et bêta sectoriel réendetté ---
    stats = beta_regression(ticker)
    fiable, raison = beta_fiable(stats)
    secteur = beta_sectoriel(m["secteur"], m["pays"])
    d_sur_e = max(dette_nette, 0.0) / capi
    beta_secteur = (
        reendetter_beta(secteur["beta_desendette"], d_sur_e, taux_is) if secteur else None
    )
    if not fiable:
        alertes.append(
            f"Bêta de régression peu fiable ({raison}). "
            + (
                f"Bêta sectoriel retenu par défaut : {beta_secteur:.2f} (secteur Damodaran "
                f"« {secteur['secteur']} », désendetté {secteur['beta_desendette']:.2f}, réendetté "
                f"avec D/E = {d_sur_e:.0%})."
                if secteur
                else "Secteur sans équivalent Damodaran : ajuste le bêta à la main."
            )
        )
    b_defaut = stats["beta"] if fiable or beta_secteur is None else beta_secteur

    # ROIC historique (dernier exercice) = NOPAT / capitaux investis
    # capitaux investis = fonds propres + minoritaires + dette nette + retraites − participations
    capital_investi = fp_groupe + minoritaires + dette_nette + retraites - participations
    nopat_der = float(h.loc["EBIT", derniere]) * (1 - taux_is)
    roic_hist = nopat_der / capital_investi if capital_investi > 0 else None

    params = ParametresWACC(
        taux_sans_risque=pays["rf"],
        beta=b_defaut,
        prime_risque=pays["prime"],
        kd=pays["rf"] + spread,
        taux_is=taux_is,
        capitalisation=capi,
        dette=max(dette_nette, 0.0),
    )
    # --- Date de valorisation ---
    aujourd_hui = pd.Timestamp(datetime.now(UTC).date())
    decalage = max((aujourd_hui - date_cloture).days / 365.25, 0.0)
    dividendes = dividendes_depuis(ticker, date_cloture)
    if m["devise"] == "GBP":
        dividendes /= 100  # dividendes londoniens en pence

    m["ebitda_ttm"] = m["ebitda_ttm"] * fx if m["ebitda_ttm"] else None  # devise de cotation
    return {
        "historique": h,
        "ratios": r,
        "marche": m,
        "beta": stats["beta"],
        "beta_stats": stats,
        "beta_fiable": fiable,
        "beta_raison": raison,
        "beta_secteur": secteur,
        "beta_defaut": b_defaut,
        "pays": pays,
        "couverture": couverture,
        "note": note,
        "spread": spread,
        "petite": petite,
        "taux_is_source": source_is,
        "dettes_locatives": locatives,
        "loyers_estimes": loyers_estimes,
        "loyers_importants": loyers_importants,
        "dette_a_saisir": dette_a_saisir,
        "dette_nette_moyenne": moyenne,
        "minoritaires_marche": minoritaires_marche,
        "financement_captif": financement_captif,
        "roic_historique": roic_hist,
        "croissance_historique": croissance_hist,
        "croissance_consensus": croissance_consensus(ticker),
        "date_cloture": date_cloture,
        "decalage": decalage,
        "dividendes": dividendes,
        "hypotheses": hyp,
        "entreprise": entreprise,
        "parametres_wacc": params,
        "alertes": alertes,
    }


# ---------------------------------------------------------------------------
# Test : python src/data.py SGO.PA
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # console Windows : caractères « → », « ⚠ »
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SGO.PA"
    try:
        d = preparer(ticker)
    except ValueError as e:
        print(f"\n⚠ {e}\n")
        sys.exit(1)
    m, e = d["marche"], d["entreprise"]
    pd.options.display.float_format = "{:,.1f}".format
    print(
        f"\n{m['nom']} ({ticker}) — {d['pays']['pays']} — montants en millions de {m['devise']}\n"
    )
    print(d["historique"])
    pd.options.display.float_format = "{:.1%}".format
    print("\nRatios :\n", d["ratios"])
    s = d["beta_stats"]
    print(
        f"\nCours {m['cours']:.2f}  ·  {e.nb_actions:.1f} M actions (+ {e.actions_dilutives:.1f} M dilutives)"
    )
    print(
        f"Bêta régression {s['beta']:.2f} (R² {s['r2']:.0%}, erreur type {s['erreur_type']:.2f}, "
        f"{'fiable' if d['beta_fiable'] else 'PEU FIABLE'}) · Blume {beta_blume(s['beta']):.2f} · "
        f"Yahoo vs S&P 500 : {m['beta_yahoo']}"
    )
    if d["beta_secteur"]:
        print(
            f"Bêta sectoriel : {d['beta_secteur']['secteur']} ({d['beta_secteur']['table']}) "
            f"désendetté {d['beta_secteur']['beta_desendette']:.2f} · retenu par défaut : {d['beta_defaut']:.2f}"
        )
    cv = d["couverture"]
    print(
        f"Couverture des intérêts (médiane 3 ans) {cv:.1f}x → note {d['note']} → spread {d['spread']:.2%}"
        if cv
        else f"Charges d'intérêts indisponibles → note par défaut {d['note']}"
    )
    p = d["pays"]
    print(
        f"Taux sans risque {p['rf']:.2%} ({p['devise_rf']}) · prime {p['prime']:.2%} (dont pays "
        f"{p['prime_pays']:.2%}) · impôt {d['hypotheses'].taux_is:.1%} ({d['taux_is_source']})"
    )
    print(
        f"Croissance : historique {d['croissance_historique']:.1%}, retenue {d['hypotheses'].croissance_ca:.1%}"
        + (
            f", consensus {d['croissance_consensus']:.1%}"
            if d["croissance_consensus"] is not None
            else ""
        )
    )
    print(
        f"Retraites {e.provisions_retraites:,.0f} · participations {e.participations:,.0f} · hybrides "
        f"{e.autres_dettes:,.0f} · minoritaires {e.minoritaires:,.0f} (marché {d['minoritaires_marche']:,.0f})"
    )
    if d["roic_historique"] is not None:
        print(f"ROIC historique {d['roic_historique']:.1%}")
    print(
        f"Clôture {d['date_cloture']:%d/%m/%Y} · décalage {d['decalage']:.2f} an · "
        f"dividendes versés depuis : {d['dividendes']:.2f}"
    )

    w = wacc(d["parametres_wacc"])
    res = valoriser(d["hypotheses"], e, w, d["decalage"], d["dividendes"])
    print(
        f"\nWACC {w:.2%}  ·  VE {res['ve']:,.0f}  ·  part VT {res['part_vt']:.0%}  ·  "
        f"ROIC implicite VT {res['roic_implicite']:.0%}"
    )
    print(
        f"Valeur par action (Gordon, options de l'appli non appliquées) : {res['valeur_action']:.2f}  "
        f"(cours {m['cours']:.2f}, potentiel {res['potentiel']:.1%})"
    )
    for a in d["alertes"]:
        print(f"⚠ {a}")
