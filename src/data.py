"""
data.py — Récupération automatique des données d'une entreprise (yfinance)

Pour n'importe quel ticker Yahoo Finance (SGO.PA, AI.PA, ML.PA, AAPL, ...) :
  1. télécharge les comptes annuels (compte de résultat, bilan, flux de trésorerie)
  2. calcule les ratios historiques (comme l'onglet « Données historique »)
  3. propose des hypothèses par défaut (moyennes historiques, bornées)
  4. calcule le bêta contre l'indice du pays
  5. choisit les paramètres de marché du pays (taux sans risque, prime de risque, impôt)
  6. convertit les comptes dans la devise de cotation si elles diffèrent
  7. signale les points à vérifier (alertes)

Montants en millions, dans la devise de COTATION de l'action.
Lancer un test :  python src/data.py SGO.PA
"""

import sys
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from dcf import DonneesEntreprise, Hypotheses, ParametresWACC, valoriser, wacc

# ---------------------------------------------------------------------------
# Tables de référence
# ---------------------------------------------------------------------------

# Indice de référence pour le bêta, selon le suffixe du ticker
INDICES = {".PA": "^FCHI", ".DE": "^GDAXI", ".AS": "^AEX", ".MI": "FTSEMIB.MI",
           ".L": "^FTSE", ".MC": "^IBEX", ".SW": "^SSMI", ".BR": "^BFX", "": "^GSPC"}

# Paramètres par pays (pays tel que Yahoo l'écrit) :
#   rf    : taux d'État à 10 ans, novembre 2026 (France : TEC 10, comme le modèle Excel)
#   prime : prime de risque totale Damodaran, janvier 2026 (pays + marché mature)
#   impot : taux légal d'impôt sur les sociétés
PAYS = {
    "France":         {"rf": 0.0483, "prime": 0.0501, "impot": 0.2583},
    "Germany":        {"rf": 0.0327, "prime": 0.0423, "impot": 0.2990},
    "Netherlands":    {"rf": 0.0327, "prime": 0.0423, "impot": 0.2580},
    "Italy":          {"rf": 0.0451, "prime": 0.0669, "impot": 0.2790},
    "Spain":          {"rf": 0.0327, "prime": 0.0578, "impot": 0.2500},
    "Belgium":        {"rf": 0.0327, "prime": 0.0501, "impot": 0.2500},
    "Ireland":        {"rf": 0.0327, "prime": 0.0501, "impot": 0.1250},
    "United Kingdom": {"rf": 0.0529, "prime": 0.0501, "impot": 0.2500},
    "Switzerland":    {"rf": 0.0327, "prime": 0.0423, "impot": 0.1970},
    "United States":  {"rf": 0.0487, "prime": 0.0446, "impot": 0.2500},
}
PAYS_DEFAUT = "France"
# Pays de la zone euro sans taux propre dans la table : Bund + prime Damodaran du pays.

# Note synthétique de Damodaran (janvier 2026) : couverture des intérêts → note → spread.
SPREADS = {"Aaa/AAA": 0.0040, "Aa2/AA": 0.0055, "A1/A+": 0.0070, "A2/A": 0.0078,
           "A3/A-": 0.0089, "Baa2/BBB": 0.0111, "Ba1/BB+": 0.0138, "Ba2/BB": 0.0184,
           "B1/B+": 0.0275, "B2/B": 0.0321, "B3/B-": 0.0509, "Caa/CCC": 0.0885,
           "Ca2/CC": 0.1261, "C2/C": 0.1600, "D2/D": 0.1900}
# Seuils pour les grandes entreprises (capitalisation > 5 Md$)
SEUILS_GRANDES = [(8.5, "Aaa/AAA"), (6.5, "Aa2/AA"), (5.5, "A1/A+"), (4.25, "A2/A"),
                  (3.0, "A3/A-"), (2.5, "Baa2/BBB"), (2.25, "Ba1/BB+"), (2.0, "Ba2/BB"),
                  (1.75, "B1/B+"), (1.5, "B2/B"), (1.25, "B3/B-"), (0.8, "Caa/CCC"),
                  (0.65, "Ca2/CC"), (0.2, "C2/C")]
# Seuils plus exigeants pour les petites entreprises (tableur ratings.xls de Damodaran)
SEUILS_PETITES = [(12.5, "Aaa/AAA"), (9.5, "Aa2/AA"), (7.5, "A1/A+"), (6.0, "A2/A"),
                  (4.5, "A3/A-"), (4.0, "Baa2/BBB"), (3.5, "Ba1/BB+"), (3.0, "Ba2/BB"),
                  (2.5, "B1/B+"), (2.0, "B2/B"), (1.5, "B3/B-"), (1.25, "Caa/CCC"),
                  (0.8, "Ca2/CC"), (0.5, "C2/C")]
TABLE_NOTES = [(None, n, s) for n, s in SPREADS.items()]   # compatibilité avec l'appli
SEUIL_PETITE_ENTREPRISE = 5_000   # capitalisation en millions

# Secteurs où les loyers IFRS 16 pèsent lourd
SECTEURS_LOCATIFS = ("grocery", "department stores", "discount stores", "apparel retail",
                     "specialty retail", "lodging", "restaurants", "airlines", "home improvement",
                     "pharmaceutical retailers", "travel services")

SECTEURS_EXCLUS = ("bank", "banks", "insurance", "capital markets", "credit services",
                   "asset management", "financial conglomerates")


def note_synthetique(couverture: float | None, petite: bool = False) -> tuple[str, float]:
    """Renvoie (note, spread) selon la table de Damodaran. Sans donnée : BBB par défaut."""
    if couverture is None or pd.isna(couverture):
        return "Baa2/BBB", SPREADS["Baa2/BBB"]
    for seuil, note in (SEUILS_PETITES if petite else SEUILS_GRANDES):
        if couverture > seuil:
            return note, SPREADS[note]
    return "D2/D", SPREADS["D2/D"]


def parametres_pays(pays: str | None) -> dict:
    """Taux sans risque, prime de risque et impôt légal du pays (France par défaut)."""
    if pays in PAYS:
        return {"pays": pays, **PAYS[pays], "connu": True}
    return {"pays": pays or "inconnu", **PAYS[PAYS_DEFAUT], "connu": False}


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _ligne(tableau: pd.DataFrame, *noms: str) -> pd.Series | None:
    """Première ligne trouvée parmi plusieurs libellés possibles (Yahoo varie)."""
    for nom in noms:
        if nom in tableau.index:
            return tableau.loc[nom]
    return None


def _derniere_valeur(serie: pd.Series, quoi: str = "Dette nette") -> float:
    """Dernière valeur disponible (Yahoo laisse parfois la dernière année vide)."""
    serie = serie.dropna()
    if serie.empty:
        raise ValueError(f"{quoi} introuvable sur Yahoo Finance : à saisir à la main.")
    return float(serie.iloc[-1])


def _indice_pour(ticker: str) -> str:
    for suffixe, indice in INDICES.items():
        if suffixe and ticker.upper().endswith(suffixe):
            return indice
    return INDICES[""]


def _taux_de_change(de: str, vers: str) -> float:
    """Combien de « vers » pour 1 « de » (ex. USD → EUR)."""
    if de == vers:
        return 1.0
    return float(yf.Ticker(f"{de}{vers}=X").fast_info["lastPrice"])


# ---------------------------------------------------------------------------
# 1. Données historiques
# ---------------------------------------------------------------------------

def historique(ticker: str) -> pd.DataFrame:
    """Tableau historique (une colonne par année, de la plus ancienne à la plus récente),
    en millions, dans la devise de PUBLICATION. La date de clôture est dans df.attrs."""
    t = yf.Ticker(ticker)
    cr, bilan, flux = t.income_stmt, t.balance_sheet, t.cashflow
    if cr is None or cr.empty:
        raise ValueError(f"Aucune donnée financière trouvée pour « {ticker} ».")

    lignes = {
        "CA": _ligne(cr, "Total Revenue", "Operating Revenue"),
        "EBIT": _ligne(cr, "EBIT", "Operating Income"),
        "D&A": _ligne(flux, "Depreciation And Amortization", "Depreciation Amortization Depletion",
                      "Reconciled Depreciation"),
        "Capex": _ligne(flux, "Capital Expenditure"),
        "Créances clients": _ligne(bilan, "Accounts Receivable", "Receivables"),
        "Stocks": _ligne(bilan, "Inventory"),
        "Dettes fournisseurs": _ligne(bilan, "Accounts Payable", "Payables"),
        "Impôts": _ligne(cr, "Tax Provision"),
        "Résultat avant impôt": _ligne(cr, "Pretax Income"),
        "Charges d'intérêts": _ligne(cr, "Interest Expense", "Interest Expense Non Operating"),
        "Dette financière": _ligne(bilan, "Total Debt"),
        "dont dettes locatives": _ligne(bilan, "Capital Lease Obligations"),
        "Dette nette Yahoo": _ligne(bilan, "Net Debt"),
        "Trésorerie": _ligne(bilan, "Cash Cash Equivalents And Short Term Investments",
                             "Cash And Cash Equivalents"),
        "Minoritaires": _ligne(bilan, "Minority Interest"),
        "Actions en circulation": _ligne(bilan, "Ordinary Shares Number"),
    }
    manquantes = [k for k in ("CA", "EBIT", "D&A", "Capex") if lignes[k] is None]
    if manquantes:
        raise ValueError(f"Données indispensables absentes pour « {ticker} » : {', '.join(manquantes)}. "
                         "Le DCF FCFF n'est probablement pas adapté à cette entreprise.")
    df = pd.DataFrame({k: v for k, v in lignes.items() if v is not None}).T
    df = df[sorted(df.columns)]                      # années croissantes
    date_cloture = df.columns[-1]
    df.columns = [c.year for c in df.columns]
    df = df.dropna(axis=1, how="all") / 1e6          # en millions

    for poste in ("Capex", "Charges d'intérêts"):    # en positif
        if poste in df.index:
            df.loc[poste] = df.loc[poste].abs()

    # BFR d'exploitation = créances + stocks − fournisseurs
    bfr = 0
    for poste, signe in (("Créances clients", 1), ("Stocks", 1), ("Dettes fournisseurs", -1)):
        if poste in df.index:
            bfr = bfr + signe * df.loc[poste].fillna(0)
    df.loc["BFR"] = bfr

    # Dette nette = dette financière − trésorerie. Une case vide n'est PAS remplacée par 0
    # (sinon dette nette = −trésorerie) : on prend la « Net Debt » de Yahoo à la place.
    nan = pd.Series(float("nan"), index=df.columns)
    dette = df.loc["Dette financière"] if "Dette financière" in df.index else nan
    treso = df.loc["Trésorerie"] if "Trésorerie" in df.index else nan
    yahoo = df.loc["Dette nette Yahoo"] if "Dette nette Yahoo" in df.index else nan
    df.loc["Dette nette"] = (dette - treso).fillna(yahoo)

    df.attrs["date_cloture"] = pd.Timestamp(date_cloture)
    return df


def ratios(h: pd.DataFrame) -> pd.DataFrame:
    """Ratios historiques et leur moyenne (comme les colonnes I à M de ton Excel)."""
    r = pd.DataFrame({
        "Croissance CA": h.loc["CA"].pct_change(),
        "Marge EBIT": h.loc["EBIT"] / h.loc["CA"],
        "D&A/CA": h.loc["D&A"] / h.loc["CA"],
        "Capex/CA": h.loc["Capex"] / h.loc["CA"],
        "BFR/CA": h.loc["BFR"] / h.loc["CA"],
        "Taux d'impôt": h.loc["Impôts"] / h.loc["Résultat avant impôt"],
    }).T
    r["Moyenne"] = r.mean(axis=1)
    return r


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


# ---------------------------------------------------------------------------
# 2. Marché : cours, actions, bêta, dividendes
# ---------------------------------------------------------------------------

def donnees_marche(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    fi, info = t.fast_info, t.info
    devise_cours = fi["currency"]
    cours = float(fi["lastPrice"])
    # Bourse de Londres : cotation en pence (GBp) → on repasse en livres
    if devise_cours == "GBp":
        devise_cours, cours = "GBP", cours / 100
    site = (info.get("website") or "").replace("https://", "").replace("http://", "").strip("/")
    return {
        "nom": info.get("longName", ticker),
        "pays": info.get("country"),
        "secteur": info.get("industry") or "",
        "devise": devise_cours,
        "devise_comptes": info.get("financialCurrency") or devise_cours,
        "cours": cours,
        "actions_yahoo": (info.get("sharesOutstanding") or fi["shares"]) / 1e6,
        "capitalisation_yahoo": (info.get("marketCap") or 0) / 1e6,
        "beta_yahoo": info.get("beta"),
        "plus_bas_52s": float(fi["yearLow"]) / (100 if fi["currency"] == "GBp" else 1),
        "plus_haut_52s": float(fi["yearHigh"]) / (100 if fi["currency"] == "GBp" else 1),
        "logo": f"https://www.google.com/s2/favicons?domain={site}&sz=128" if site else None,
    }


def beta(ticker: str, indice: str | None = None, annees: int = 5) -> float:
    """Bêta = Cov(rendements action, rendements indice) / Var(rendements indice),
    sur des cours mensuels ajustés (comme ton onglet « Calcul Béta »)."""
    indice = indice or _indice_pour(ticker)
    prix = yf.download([ticker, indice], period=f"{annees}y", interval="1mo",
                       auto_adjust=True, progress=False)["Close"].dropna()
    rdt = prix.pct_change().dropna()
    return rdt[ticker].cov(rdt[indice]) / rdt[indice].var()


def beta_blume(b: float) -> float:
    """Ajustement de Blume : les bêtas tendent vers 1 avec le temps."""
    return 0.67 * b + 0.33


def dividendes_depuis(ticker: str, date: pd.Timestamp) -> float:
    """Dividendes par action versés depuis la date de clôture."""
    div = yf.Ticker(ticker).dividends
    if div is None or div.empty:
        return 0.0
    index = div.index.tz_localize(None) if div.index.tz is not None else div.index
    return float(div[index > date].sum())


REGIONS = {"France": "fr", "Germany": "de", "United Kingdom": "gb", "Switzerland": "ch",
           "Netherlands": "nl", "Italy": "it", "Spain": "es", "Belgium": "be", "Ireland": "ie",
           "Sweden": "se", "Denmark": "dk", "Finland": "fi", "Norway": "no", "Austria": "at",
           "Portugal": "pt", "United States": "us", "Canada": "ca", "Japan": "jp"}
EUROPE = ["fr", "de", "gb", "ch", "nl", "it", "es", "be", "ie", "se", "dk", "fi", "no", "at", "pt"]
# Places de cotation secondaires (doublons d'une même entreprise) à écarter
PLACES_SECONDAIRES = (".F", ".BE", ".DU", ".MU", ".SG", ".HM", ".HA", ".VI", ".IL")


def comparables_auto(ticker: str, n: int = 6) -> list[str]:
    """Propose automatiquement des comparables : même secteur d'activité (« industry » Yahoo),
    même zone géographique (Europe ou pays), taille la plus proche possible.
    On élargit la fourchette de taille si on ne trouve pas assez d'entreprises."""
    from yfinance import EquityQuery

    info = yf.Ticker(ticker).info
    industrie, capi = info.get("industry"), info.get("marketCap")
    if not industrie:
        return []
    region = REGIONS.get(info.get("country"), "us")
    regions = EUROPE if region in EUROPE else [region]
    zone = EquityQuery("is-in", ["region", *regions]) if len(regions) > 1 \
        else EquityQuery("eq", ["region", regions[0]])

    trouves: dict[str, tuple[str, float]] = {}          # nom → (ticker, capitalisation)
    for facteur in (5, 20, None):                        # fourchettes de taille de plus en plus larges
        conditions = [EquityQuery("eq", ["industry", industrie]), zone]
        if capi and facteur:
            conditions.append(EquityQuery("btwn", ["intradaymarketcap", capi / facteur, capi * facteur]))
        try:
            reponse = yf.screen(EquityQuery("and", conditions), size=50,
                                sortField="intradaymarketcap", sortAsc=False)
        except Exception:
            continue
        for q in reponse.get("quotes", []):
            symbole = q.get("symbol", "")
            nom = q.get("longName") or q.get("shortName") or symbole
            if (symbole.upper() == ticker.upper() or symbole.endswith(PLACES_SECONDAIRES)
                    or nom == info.get("longName") or nom in trouves):
                continue
            trouves[nom] = (symbole, q.get("marketCap") or 0)
        if len(trouves) >= n:
            break

    if not trouves:                                      # secours : grandes entreprises du secteur
        try:
            top = yf.Industry(info["industryKey"]).top_companies
            return [t for t in top.index if t.upper() != ticker.upper()][:n]
        except Exception:
            return []

    # On garde les entreprises dont la taille est la plus proche
    import math
    candidats = sorted(trouves.values(),
                       key=lambda x: abs(math.log(max(x[1], 1) / capi)) if capi else 0)
    return [symbole for symbole, _ in candidats[:n]]


def comparables(tickers: list[str]) -> pd.DataFrame:
    """Multiples de marché des entreprises comparables (source : Yahoo Finance).
    Les multiples sont sans unité : on peut comparer des entreprises de devises différentes."""
    lignes = []
    for tk in tickers:
        try:
            info = yf.Ticker(tk).info
        except Exception:
            continue
        if not info or info.get("enterpriseToEbitda") is None and info.get("trailingPE") is None:
            continue
        lignes.append({
            "Ticker": tk,
            "Entreprise": info.get("shortName") or info.get("longName") or tk,
            "Pays": info.get("country"),
            "VE / EBITDA": info.get("enterpriseToEbitda"),
            "VE / CA": info.get("enterpriseToRevenue"),
            "PER": info.get("trailingPE"),
        })
    df = pd.DataFrame(lignes, columns=["Ticker", "Entreprise", "Pays", "VE / EBITDA", "VE / CA", "PER"])
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
    info = yf.Ticker(ticker).info
    secteur = (info.get("industry") or "").lower()
    if any(mot in secteur for mot in SECTEURS_EXCLUS):
        raise ValueError(
            f"{info.get('longName', ticker)} est une entreprise financière ({info.get('industry')}). "
            "Le DCF FCFF n'est pas adapté : utiliser plutôt un multiple de fonds propres "
            "(cours / valeur comptable) ou un modèle de dividendes."
        )


def preparer(ticker: str) -> dict:
    """Récupère tout et renvoie les objets de dcf.py, des paramètres par défaut
    et la liste des alertes à vérifier."""
    verifier_eligibilite(ticker)
    alertes = []
    m = donnees_marche(ticker)
    h = historique(ticker)
    date_cloture = h.attrs["date_cloture"]

    # --- Devise : comptes publiés dans une autre devise que la cotation (point 4) ---
    fx = 1.0
    if m["devise_comptes"] != m["devise"]:
        fx = _taux_de_change(m["devise_comptes"], m["devise"])
        h = h * fx
        h.attrs["date_cloture"] = date_cloture
        alertes.append(f"Comptes publiés en {m['devise_comptes']}, action cotée en {m['devise']} : "
                       f"montants convertis au taux de {fx:.4f}.")

    r = ratios(h)
    derniere = h.columns[-1]
    pays = parametres_pays(m["pays"])
    if not pays["connu"]:
        alertes.append(f"Pays « {pays['pays']} » absent de la table : paramètres de la France "
                       "par défaut. Ajuste le taux sans risque et la prime de risque.")

    # --- Impôt (point 6) ---
    taux_is, source_is = taux_impot_defaut(h, pays["impot"])
    if source_is != "taux effectif moyen":
        alertes.append(f"Taux d'impôt effectif aberrant : taux légal retenu ({taux_is:.1%}).")

    # --- Nombre d'actions (point 8) ---
    actions_bilan = (_derniere_valeur(h.loc["Actions en circulation"] / fx, "Actions")
                     if "Actions en circulation" in h.index and h.loc["Actions en circulation"].notna().any()
                     else None)
    nb_actions = actions_bilan or m["actions_yahoo"]
    capi = nb_actions * m["cours"]
    if m["capitalisation_yahoo"] and abs(capi / m["capitalisation_yahoo"] - 1) > 0.05:
        alertes.append(f"Nombre d'actions incertain : {nb_actions:,.1f} M donne une capitalisation de "
                       f"{capi:,.0f}, contre {m['capitalisation_yahoo']:,.0f} selon Yahoo "
                       "(actions autodétenues ou plusieurs catégories d'actions ?).")

    # --- Note de crédit (point 9) ---
    interets = h.loc["Charges d'intérêts", derniere] if "Charges d'intérêts" in h.index else None
    couverture = (h.loc["EBIT", derniere] / interets) if interets and interets > 0 else None
    petite = capi * _taux_de_change(m["devise"], "USD") < SEUIL_PETITE_ENTREPRISE \
        if m["devise"] != "USD" else capi < SEUIL_PETITE_ENTREPRISE
    note, spread = note_synthetique(couverture, petite)

    # --- Dette nette et loyers IFRS 16 (point 5) ---
    dette_nette = _derniere_valeur(h.loc["Dette nette"])
    # Cohérence : une trésorerie nette ne s'accompagne pas de grosses charges d'intérêts
    if dette_nette < 0 and interets and interets > 0.05 * h.loc["EBIT", derniere]:
        detail = ", ".join(f"{k} {h.loc[k].dropna().iloc[-1]:,.0f}" for k in
                           ("Dette financière", "Trésorerie", "Dette nette Yahoo")
                           if k in h.index and h.loc[k].notna().any())
        alertes.append(f"Dette nette négative ({dette_nette:,.0f}) alors que les charges d'intérêts "
                       f"sont élevées ({interets:,.0f}) : donnée Yahoo probablement incomplète "
                       f"({detail}). Saisis l'endettement financier net du rapport annuel.")
    locatives = (float(h.loc["dont dettes locatives"].dropna().iloc[-1])
                 if "dont dettes locatives" in h.index and h.loc["dont dettes locatives"].notna().any()
                 else 0.0)
    if any(mot in m["secteur"].lower() for mot in SECTEURS_LOCATIFS) or \
            (locatives and locatives > 0.25 * max(dette_nette, 1)):
        alertes.append(f"Loyers IFRS 16 importants (dettes locatives : {locatives:,.0f}). "
                       "Les D&A incluent l'amortissement des magasins/bureaux loués mais le Capex "
                       "n'inclut pas les nouveaux contrats : renseigne le curseur « Loyers / CA ».")

    hyp = Hypotheses(
        croissance_ca=float(r.loc["Croissance CA", "Moyenne"]),
        marge_ebit=float(r.loc["Marge EBIT", "Moyenne"]),
        da_ca=float(r.loc["D&A/CA", "Moyenne"]),
        capex_ca=float(r.loc["Capex/CA", "Moyenne"]),
        bfr_ca=float(r.loc["BFR/CA", "Moyenne"]),
        taux_is=taux_is, g=0.02, horizon=5,
    )
    entreprise = DonneesEntreprise(
        ca_dernier=float(h.loc["CA", derniere]),
        bfr_dernier=float(h.loc["BFR", derniere]),
        annee_derniere=int(derniere),
        dette_nette=dette_nette,
        minoritaires=float(h.loc["Minoritaires"].dropna().iloc[-1])
        if "Minoritaires" in h.index and h.loc["Minoritaires"].notna().any() else 0.0,
        provisions_retraites=0.0,   # pas disponible sur Yahoo : à saisir dans l'appli
        nb_actions=nb_actions,
        cours=m["cours"],
    )
    b = beta(ticker)
    params = ParametresWACC(
        taux_sans_risque=pays["rf"], beta=b, prime_risque=pays["prime"],
        kd=pays["rf"] + spread, taux_is=taux_is,
        capitalisation=capi, dette=max(dette_nette, 0.0),
    )
    # --- Date de valorisation (point 2) ---
    aujourd_hui = pd.Timestamp(datetime.now(timezone.utc).date())
    decalage = max((aujourd_hui - date_cloture).days / 365.25, 0.0)
    dividendes = dividendes_depuis(ticker, date_cloture)
    if m["devise"] == "GBP":
        dividendes /= 100          # dividendes londoniens en pence

    return {"historique": h, "ratios": r, "marche": m, "beta": b, "pays": pays,
            "couverture": couverture, "note": note, "spread": spread, "petite": petite,
            "taux_is_source": source_is, "dettes_locatives": locatives,
            "date_cloture": date_cloture, "decalage": decalage, "dividendes": dividendes,
            "hypotheses": hyp, "entreprise": entreprise, "parametres_wacc": params,
            "alertes": alertes}


# ---------------------------------------------------------------------------
# Test : python src/data.py SGO.PA
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SGO.PA"
    try:
        d = preparer(ticker)
    except ValueError as e:
        print(f"\n⚠ {e}\n")
        sys.exit(1)
    m = d["marche"]
    pd.options.display.float_format = "{:,.1f}".format
    print(f"\n{m['nom']} ({ticker}) — {d['pays']['pays']} — montants en millions de {m['devise']}\n")
    print(d["historique"])
    pd.options.display.float_format = "{:.1%}".format
    print("\nRatios :\n", d["ratios"])
    print(f"\nCours {m['cours']:.2f}  ·  {d['entreprise'].nb_actions:.1f} M actions  ·  "
          f"bêta {d['beta']:.2f} (Blume {beta_blume(d['beta']):.2f} ; Yahoo vs S&P 500 : {m['beta_yahoo']})")
    cv = d["couverture"]
    print(f"Couverture des intérêts {cv:.1f}x → note synthétique {d['note']} → spread {d['spread']:.2%}"
          if cv else f"Charges d'intérêts indisponibles → note par défaut {d['note']}")
    print(f"Taux d'impôt : {d['hypotheses'].taux_is:.1%} ({d['taux_is_source']})")
    print(f"Clôture {d['date_cloture']:%d/%m/%Y} · décalage {d['decalage']:.2f} an · "
          f"dividendes versés depuis : {d['dividendes']:.2f}")

    w = wacc(d["parametres_wacc"])
    res = valoriser(d["hypotheses"], d["entreprise"], w, d["decalage"], d["dividendes"])
    print(f"\nWACC {w:.2%}  ·  VE {res['ve']:,.0f}  ·  part VT {res['part_vt']:.0%}  ·  "
          f"ROIC implicite VT {res['roic_implicite']:.0%}")
    print(f"Valeur par action : {res['valeur_action']:.2f}  (cours {m['cours']:.2f}, "
          f"potentiel {res['potentiel']:.1%})")
    for a in d["alertes"]:
        print(f"⚠ {a}")
