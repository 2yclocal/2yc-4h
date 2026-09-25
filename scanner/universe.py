"""
Symbol universe — index constituent lists, not full-exchange listings.

US  : S&P 500 + S&P MidCap 400 (~900 pre-screened large/mid-cap stocks)
TSX : S&P/TSX 60 (top 60 Canadian large-caps)

Index membership guarantees the $5B+ market cap and $1M+ ADV thresholds,
so no per-symbol ticker.info call is needed. Company names come from
Wikipedia alongside the symbol lists — zero metadata API calls.
"""

from __future__ import annotations

import io
import logging
import requests
import pandas as pd

_HEADERS = {"User-Agent": "2yc-4h/1.0 (github.com/2yclocal/2yc-4h)"}

logger = logging.getLogger(__name__)

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_SP400_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"

# S&P/TSX Composite constituents (~220 stocks). Updated periodically — the
# Composite changes quarterly but major holdings are stable year to year.
_TSX_FALLBACK: list[tuple[str, str]] = [
    # Financials
    ("RY.TO",      "Royal Bank of Canada"),
    ("TD.TO",      "Toronto-Dominion Bank"),
    ("BNS.TO",     "Bank of Nova Scotia"),
    ("BMO.TO",     "Bank of Montreal"),
    ("CM.TO",      "CIBC"),
    ("NA.TO",      "National Bank of Canada"),
    ("SLF.TO",     "Sun Life Financial"),
    ("MFC.TO",     "Manulife Financial"),
    ("GWO.TO",     "Great-West Lifeco"),
    ("IFC.TO",     "Intact Financial Corporation"),
    ("FFH.TO",     "Fairfax Financial Holdings"),
    ("POW.TO",     "Power Corporation of Canada"),   # PWF.TO merged into POW.TO
    ("EQB.TO",     "EQB Inc."),
    ("IAG.TO",     "iA Financial Group"),
    ("X.TO",       "TMX Group"),
    ("BAM.TO",     "Brookfield Asset Management"),
    ("BN.TO",      "Brookfield Corporation"),
    ("BIP-UN.TO",  "Brookfield Infrastructure Partners"),
    ("BEP-UN.TO",  "Brookfield Renewable Partners"),
    ("BEPC.TO",    "Brookfield Renewable Corporation"),
    ("BIPC.TO",    "Brookfield Infrastructure Corporation"),
    ("ONEX.TO",    "Onex Corporation"),
    # Energy
    ("ENB.TO",     "Enbridge"),
    ("TRP.TO",     "TC Energy"),
    ("SU.TO",      "Suncor Energy"),
    ("CNQ.TO",     "Canadian Natural Resources"),
    ("CVE.TO",     "Cenovus Energy"),
    ("IMO.TO",     "Imperial Oil"),
    ("PPL.TO",     "Pembina Pipeline"),
    ("ARX.TO",     "ARC Resources"),
    ("BTE.TO",     "Baytex Energy"),
    ("VET.TO",     "Vermilion Energy"),
    ("TOU.TO",     "Tourmaline Oil"),
    ("BIR.TO",     "Birchcliff Energy"),
    ("GEI.TO",     "Gibson Energy"),
    ("KEL.TO",     "Kelt Exploration"),
    ("SPB.TO",     "Superior Plus"),
    ("WCP.TO",     "Whitecap Resources"),
    ("PEY.TO",     "Peyto Exploration"),
    ("TVE.TO",     "Tamarack Valley Energy"),
    ("TPZ.TO",     "Topaz Energy"),
    ("ATH.TO",     "Athabasca Oil"),
    # Materials / Mining
    ("ABX.TO",     "Barrick Mining Corporation"),
    ("AEM.TO",     "Agnico Eagle Mines"),
    ("WPM.TO",     "Wheaton Precious Metals"),
    ("FM.TO",      "First Quantum Minerals"),
    ("LUN.TO",     "Lundin Mining"),
    ("CCO.TO",     "Cameco Corporation"),
    ("TECK-B.TO",  "Teck Resources Class B"),
    ("AGI.TO",     "Alamos Gold"),
    ("IMG.TO",     "IAMGOLD Corporation"),
    ("OR.TO",      "Osisko Royalties"),
    ("FNV.TO",     "Franco-Nevada Corporation"),
    ("K.TO",       "Kinross Gold"),
    ("PAAS.TO",    "Pan American Silver"),
    ("TXG.TO",     "Torex Gold Resources"),
    ("CS.TO",      "Capstone Copper"),
    ("NTR.TO",     "Nutrien"),
    ("ACO-X.TO",   "ATCO Ltd Class I"),
    ("MG.TO",      "Magna International"),
    # Industrials
    ("CNR.TO",     "Canadian National Railway"),
    ("CP.TO",      "Canadian Pacific Kansas City"),
    ("WCN.TO",     "Waste Connections"),
    ("WSP.TO",     "WSP Global"),
    ("CAE.TO",     "CAE Inc."),
    ("TIH.TO",     "Toromont Industries"),
    ("TFII.TO",    "TFI International"),            # was TFI.TO (wrong ticker)
    ("GFL.TO",     "GFL Environmental"),
    ("STN.TO",     "Stantec Inc."),
    ("ATS.TO",     "ATS Corporation"),
    ("TRI.TO",     "Thomson Reuters"),
    ("FSV.TO",     "FirstService Corporation"),
    ("CIGI.TO",    "Colliers International"),
    ("PBH.TO",     "Premium Brands Holdings"),
    ("NFI.TO",     "NFI Group"),
    ("RBA.TO",     "RB Global"),
    ("MTL.TO",     "Mullen Group"),
    # Consumer Staples
    ("ATD.TO",     "Alimentation Couche-Tard"),
    ("L.TO",       "Loblaw Companies"),
    ("MRU.TO",     "Metro Inc."),
    ("SAP.TO",     "Saputo"),
    ("WN.TO",      "George Weston"),
    ("EMP-A.TO",   "Empire Company"),
    ("GIL.TO",     "Gildan Activewear"),
    # Consumer Discretionary
    ("DOL.TO",     "Dollarama"),
    ("QSR.TO",     "Restaurant Brands International"),
    ("ATZ.TO",     "Aritzia"),
    ("CTC-A.TO",   "Canadian Tire Class A"),
    ("GOOS.TO",    "Canada Goose Holdings"),
    ("MTY.TO",     "MTY Food Group"),
    ("RCH.TO",     "Richelieu Hardware"),
    # Technology
    ("SHOP.TO",    "Shopify"),
    ("CSU.TO",     "Constellation Software"),
    ("BB.TO",      "BlackBerry"),
    ("CLS.TO",     "Celestica"),
    ("OTEX.TO",    "Open Text Corporation"),
    ("ENGH.TO",    "Enghouse Systems"),
    ("DSG.TO",     "Descartes Systems"),
    ("KXS.TO",     "Kinaxis"),
    ("GIB-A.TO",   "CGI Group"),
    ("LSPD.TO",    "Lightspeed Commerce"),
    # Telecoms
    ("BCE.TO",     "BCE Inc."),
    ("T.TO",       "Telus Corporation"),
    ("RCI-B.TO",   "Rogers Communications Class B"),
    ("QBR-B.TO",   "Quebecor Class B"),
    # Utilities
    ("FTS.TO",     "Fortis Inc."),
    ("H.TO",       "Hydro One"),
    ("EMA.TO",     "Emera Inc."),
    ("ALA.TO",     "AltaGas"),
    ("AQN.TO",     "Algonquin Power & Utilities"),
    ("NPI.TO",     "Northland Power"),
    ("CPX.TO",     "Capital Power"),
    ("CU.TO",      "Canadian Utilities Class A"),
    ("TA.TO",      "TransAlta Corporation"),
    ("BLX.TO",     "Boralex"),
    # Real Estate / REITs
    ("DIR-UN.TO",  "Dream Industrial REIT"),
    ("CAR-UN.TO",  "Canadian Apartment Properties REIT"),
    ("SRU-UN.TO",  "SmartCentres REIT"),
    ("REI-UN.TO",  "RioCan REIT"),
    ("IIP-UN.TO",  "InterRent REIT"),
    ("GRT-UN.TO",  "Granite REIT"),
    ("CRT-UN.TO",  "CT REIT"),
    ("AP-UN.TO",   "Allied Properties REIT"),
    ("HR-UN.TO",   "H&R REIT"),
    ("CHP-UN.TO",  "Choice Properties REIT"),
    ("NXR-UN.TO",  "Nexus Industrial REIT"),
    ("PLZ-UN.TO",  "Plaza Retail REIT"),
    ("MRT-UN.TO",  "Morguard REIT"),
    ("D-UN.TO",    "Dream Office REIT"),
    ("FCR-UN.TO",  "First Capital REIT"),
    ("MI-UN.TO",   "Minto Apartment REIT"),
    # Healthcare
    ("WELL.TO",    "WELL Health Technologies"),
    ("GUD.TO",     "Knight Therapeutics"),
    ("SIA.TO",     "Sienna Senior Living"),
]


def _wiki_table(
    url: str,
    sym_hints: list[str],
    name_hints: list[str],
    to_suffix: bool = False,
) -> list[tuple[str, str]]:
    """Parse the first matching constituent table from a Wikipedia page."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        for df in tables:
            cols = [str(c) for c in df.columns]
            sym_col  = next((c for c in cols if any(h.lower() in c.lower() for h in sym_hints)), None)
            name_col = next((c for c in cols if any(h.lower() in c.lower() for h in name_hints)), None)
            if not sym_col or not name_col:
                continue
            pairs: list[tuple[str, str]] = []
            for _, row in df.iterrows():
                sym  = str(row[sym_col]).strip().replace(".", "-")
                name = str(row[name_col]).strip()
                if not sym or sym in ("nan", "-") or len(sym) > 8:
                    continue
                if to_suffix and not sym.endswith(".TO"):
                    sym = sym + ".TO"
                pairs.append((sym, name))
            if pairs:
                logger.info(f"Parsed {len(pairs)} symbols from {url}")
                return pairs
    except Exception as exc:
        logger.warning(f"Wikipedia parse failed ({url}): {exc}")
    return []


def load_us_symbols() -> list[tuple[str, str]]:
    """S&P 500 + S&P MidCap 400 from Wikipedia. Returns (symbol, name) pairs."""
    seen: dict[str, str] = {}

    for url in (_SP500_URL, _SP400_URL):
        pairs = _wiki_table(url, sym_hints=["Symbol", "Ticker"], name_hints=["Security", "Company", "Name"])
        for sym, name in pairs:
            seen.setdefault(sym, name)

    if not seen:
        logger.error("Failed to load US index constituents from Wikipedia")
        return []

    logger.info(f"Loaded {len(seen)} US symbols (S&P 500 + S&P 400)")
    return list(seen.items())


def load_tsx_symbols() -> list[tuple[str, str]]:
    """
    S&P/TSX Composite constituents (~220 large/mid-cap Canadian stocks).
    Returns (symbol, name) pairs. The curated list is the primary source —
    the TSX directory API doesn't filter by Composite membership.
    """
    logger.info(f"Loaded {len(_TSX_FALLBACK)} TSX Composite symbols (curated list)")
    return list(_TSX_FALLBACK)
