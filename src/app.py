"""
data.py — Récupération automatique des données d'une entreprise (yfinance)

Pour n'importe quel ticker Yahoo Finance (SGO.PA, AI.PA, ML.PA, AAPL, ...) :
  1. télécharge les comptes annuels (compte de résultat, bilan, flux de trésorerie)
  2. calcule les ratios historiques (comme l'onglet « Données historique »)
  3. propose des hypothèses par défaut (moyennes historiques)
  4. calcule le bêta contre un indice de référence
  5. prépare les objets attendus par dcf.py

Montants convertis en millions, dans la devise de publication de l'entreprise.
Lancer un test :  python src/data.py SGO.PA
"""

import sys

import pandas as pd
import yfinance as yf

from dcf import DonneesEntreprise, Hypotheses, ParametresWACC, valoriser, wacc

# Indice de référence pour le bêta, selon le suffixe du ticker
INDICES = {".PA": "^FCHI", ".DE": "^GDAXI", ".AS": "^AEX", ".MI": "FTSEMIB.MI",
           ".L": "^FTSE", ".MC": "^IBEX", "": "^GSPC"}

# Note synthétique de Damodaran (janvier 2026), grandes entreprises non financières :
# couverture des intérêts (EBIT / charges d'intérêts) → note → spread de crédit
TABLE_NOTES = [  # (couverture minimale, note, spread)
    (8.5, "Aaa/AAA", 0.0040), (6.5, "Aa2/AA", 0.0055), (5.5, "A1/A+", 0.0070),
    (4.25, "A2/A", 0.0078), (3.0, "A3/A-", 0.0089), (2.5, "Baa2/BBB", 0.0111),
    (2.25, "Ba1/BB+", 0.0138), (2.0, "Ba2/BB", 0.0184), (1.75, "B1/B+", 0.0275),
    (1.5, "B2/B", 0.0321), (1.25, "B3/B-", 0.0509), (0.8, "Caa/CCC", 0.0885),
    (0.65, "Ca2/CC", 0.1261), (0.2, "C2/C", 0.1600), (float("-inf"), "D2/D", 0.1900),
]


def note_synthetique(couverture: float | None) -> tuple[str, float]:
    """Renvoie (note, spread) selon la table de Damodaran. Sans donnée : BBB par défaut."""
    if couverture is None or pd.isna(couverture):
        return "Baa2/BBB", 0.0111
    for seuil, note, spread in TABLE_NOTES:
        if couverture > seuil:
            return note, spread
    return TABLE_NOTES[-1][1], TABLE_NOTES[-1][2]


# Paramètres de marché par défaut (modifiables dans l'appli)
MARCHE_DEFAUT = {
    "taux_sans_risque": 0.0483,   # TEC 10 France
    "prime_risque": 0.0501,       # Damodaran, France
    "spread": 0.0111,             # Damodaran, BBB
}


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _ligne(tableau: pd.DataFrame, *noms: str) -> pd.Series | None:
    """Renvoie la première ligne trouvée parmi plusieurs noms possibles.
    (Yahoo n'utilise pas toujours le même libellé selon les entreprises.)"""
    for nom in noms:
        if nom in tableau.index:
            return tableau.loc[nom]
    return None


def _derniere_valeur(serie: pd.Series) -> float:
    """Dernière valeur disponible d'une ligne (Yahoo laisse parfois la dernière année vide)."""
    serie = serie.dropna()
    if serie.empty:
        raise ValueError("Dette nette introuvable sur Yahoo Finance : à saisir à la main.")
    return float(serie.iloc[-1])


def _indice_pour(ticker: str) -> str:
    for suffixe, indice in INDICES.items():
        if suffixe and ticker.upper().endswith(suffixe):
            return indice
    return INDICES[""]


# ---------------------------------------------------------------------------
# 1. Données historiques
# ---------------------------------------------------------------------------

def historique(ticker: str) -> pd.DataFrame:
    """Tableau historique (une colonne par année, de la plus ancienne à la plus récente),
    en millions."""
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
        "Charges d'intérêts": _ligne(cr, "Interest Expense", "Interest Expense Non Operating"),
        "Résultat avant impôt": _ligne(cr, "Pretax Income"),
        "Dette financière": _ligne(bilan, "Total Debt"),
        "Dette nette Yahoo": _ligne(bilan, "Net Debt"),
        "Trésorerie": _ligne(bilan, "Cash Cash Equivalents And Short Term Investments",
                             "Cash And Cash Equivalents"),
        "Minoritaires": _ligne(bilan, "Minority Interest"),
    }
    manquantes = [k for k in ("CA", "EBIT", "D&A", "Capex") if lignes[k] is None]
    if manquantes:
        raise ValueError(f"Données indispensables absentes pour « {ticker} » : {', '.join(manquantes)}. "
                         "Le DCF FCFF n'est probablement pas adapté à cette entreprise.")
    df = pd.DataFrame({k: v for k, v in lignes.items() if v is not None}).T
    df = df[sorted(df.columns)]                      # années croissantes
    df.columns = [c.year for c in df.columns]
    df = df.dropna(axis=1, how="all") / 1e6          # en millions

    # Conventions de signe : Capex en positif
    for poste in ("Capex", "Charges d'intérêts"):
        if poste in df.index:
            df.loc[poste] = df.loc[poste].abs()

    # BFR d'exploitation = créances + stocks − fournisseurs
    bfr = 0
    for poste, signe in (("Créances clients", 1), ("Stocks", 1), ("Dettes fournisseurs", -1)):
        if poste in df.index:
            bfr = bfr + signe * df.loc[poste].fillna(0)
    df.loc["BFR"] = bfr

    # Dette nette = dette financière − trésorerie. Si une des deux manque pour une année,
    # on ne remplace PAS par 0 (sinon dette nette = −trésorerie, donc faussement négative) :
    # on prend la « Net Debt » de Yahoo, sinon on laisse vide.
    nan = pd.Series(float("nan"), index=df.columns)
    dette = df.loc["Dette financière"] if "Dette financière" in df.index else nan
    treso = df.loc["Trésorerie"] if "Trésorerie" in df.index else nan
    yahoo = df.loc["Dette nette Yahoo"] if "Dette nette Yahoo" in df.index else nan
    df.loc["Dette nette"] = (dette - treso).fillna(yahoo)
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


# ---------------------------------------------------------------------------
# 2. Marché : cours, actions, bêta
# ---------------------------------------------------------------------------

def donnees_marche(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    fi = t.fast_info
    info = t.info
    cours = fi["lastPrice"]
    nb_actions = (info.get("sharesOutstanding") or fi["shares"]) / 1e6
    site = (info.get("website") or "").replace("https://", "").replace("http://", "").strip("/")
    return {
        "nom": info.get("longName", ticker),
        "beta_yahoo": info.get("beta"),
        "logo": f"https://www.google.com/s2/favicons?domain={site}&sz=128" if site else None,
        "devise": info.get("financialCurrency") or fi["currency"],
        "cours": cours,
        "nb_actions": nb_actions,
        "capitalisation": cours * nb_actions,
        "plus_bas_52s": fi["yearLow"],
        "plus_haut_52s": fi["yearHigh"],
    }


def beta(ticker: str, indice: str | None = None, annees: int = 5) -> float:
    """Bêta = Cov(rendements action, rendements indice) / Var(rendements indice),
    sur des cours mensuels ajustés (comme ton onglet « Calcul Béta »)."""
    indice = indice or _indice_pour(ticker)
    prix = yf.download([ticker, indice], period=f"{annees}y", interval="1mo",
                       auto_adjust=True, progress=False)["Close"].dropna()
    rdt = prix.pct_change().dropna()
    return rdt[ticker].cov(rdt[indice]) / rdt[indice].var()


# ---------------------------------------------------------------------------
# 3. Assemblage : tout ce qu'il faut pour dcf.py
# ---------------------------------------------------------------------------

SECTEURS_EXCLUS = ("bank", "banks", "insurance", "capital markets", "credit services",
                    "asset management", "financial conglomerates")


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


def preparer(ticker: str, marche: dict | None = None) -> dict:
    """Récupère tout et renvoie les trois objets de dcf.py, avec des hypothèses
    par défaut = moyennes historiques. L'appli permettra ensuite de les modifier."""
    marche = {**MARCHE_DEFAUT, **(marche or {})}
    verifier_eligibilite(ticker)
    h = historique(ticker)
    r = ratios(h)
    m = donnees_marche(ticker)
    b = beta(ticker)
    derniere = h.columns[-1]

    taux_is = float(r.loc["Taux d'impôt", "Moyenne"])
    interets = h.loc["Charges d'intérêts", derniere] if "Charges d'intérêts" in h.index else None
    couverture = h.loc["EBIT", derniere] / interets if interets else None
    note, spread = note_synthetique(couverture)
    hyp = Hypotheses(
        croissance_ca=float(r.loc["Croissance CA", "Moyenne"]),
        marge_ebit=float(r.loc["Marge EBIT", "Moyenne"]),
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
        dette_nette=_derniere_valeur(h.loc["Dette nette"]),
        minoritaires=float(h.loc["Minoritaires", derniere]) if "Minoritaires" in h.index else 0.0,
        provisions_retraites=0.0,   # pas disponible sur Yahoo : à saisir dans l'appli
        nb_actions=m["nb_actions"],
        cours=m["cours"],
    )
    params = ParametresWACC(
        taux_sans_risque=marche["taux_sans_risque"],
        beta=b,
        prime_risque=marche["prime_risque"],
        kd=marche["taux_sans_risque"] + spread,
        taux_is=taux_is,
        capitalisation=m["capitalisation"],
        dette=max(entreprise.dette_nette, 0.0),
    )
    return {"historique": h, "ratios": r, "marche": m, "beta": b,
            "couverture": couverture, "note": note, "spread": spread,
            "hypotheses": hyp, "entreprise": entreprise, "parametres_wacc": params}


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
    print(f"\n{m['nom']} ({ticker}) — montants en millions de {m['devise']}\n")
    print(d["historique"])
    pd.options.display.float_format = "{:.1%}".format
    print("\nRatios :\n", d["ratios"])
    print(f"\nCours {m['cours']:.2f}  ·  {m['nb_actions']:.1f} M actions  ·  bêta {d['beta']:.2f}"
          f" (Yahoo, vs S&P 500 : {m['beta_yahoo']})")
    cv = d["couverture"]
    print(f"Couverture des intérêts {cv:.1f}x → note synthétique {d['note']} → spread {d['spread']:.2%}"
          if cv else f"Charges d'intérêts indisponibles → note par défaut {d['note']}")

    w = wacc(d["parametres_wacc"])
    res = valoriser(d["hypotheses"], d["entreprise"], w)
    print(f"\nWACC {w:.2%}  ·  VE {res['ve']:,.0f}  ·  part VT {res['part_vt']:.0%}")
    print(f"Valeur par action : {res['valeur_action']:.2f}  (cours {m['cours']:.2f}, "
          f"potentiel {res['potentiel']:.1%})")
    print("\n⚠ Hypothèses par défaut = moyennes historiques, provisions retraites = 0. "
          "À ajuster avant de conclure.")