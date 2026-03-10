"""Seed sector/ticker mappings. In production, pull from a full dataset like Finnhub or SEC."""

from sqlalchemy.orm import Session

from src.db.models import Sector

# Common tickers by sector (subset — enrich with full dataset via Finnhub API later)
SECTOR_DATA = [
    # Defense / Aerospace
    ("LMT", "Defense", "Aerospace & Defense", "Lockheed Martin"),
    ("RTX", "Defense", "Aerospace & Defense", "RTX Corporation"),
    ("NOC", "Defense", "Aerospace & Defense", "Northrop Grumman"),
    ("GD", "Defense", "Aerospace & Defense", "General Dynamics"),
    ("BA", "Defense", "Aerospace & Defense", "Boeing"),
    ("LHX", "Defense", "Aerospace & Defense", "L3Harris Technologies"),
    ("HII", "Defense", "Aerospace & Defense", "Huntington Ingalls"),
    ("LDOS", "Defense", "Aerospace & Defense", "Leidos Holdings"),
    ("PLTR", "Defense", "Software - Infrastructure", "Palantir Technologies"),

    # Healthcare / Pharma
    ("JNJ", "Healthcare", "Drug Manufacturers", "Johnson & Johnson"),
    ("UNH", "Healthcare", "Health Care Plans", "UnitedHealth Group"),
    ("PFE", "Healthcare", "Drug Manufacturers", "Pfizer"),
    ("ABBV", "Healthcare", "Drug Manufacturers", "AbbVie"),
    ("MRK", "Healthcare", "Drug Manufacturers", "Merck"),
    ("LLY", "Healthcare", "Drug Manufacturers", "Eli Lilly"),
    ("TMO", "Healthcare", "Diagnostics & Research", "Thermo Fisher"),
    ("ABT", "Healthcare", "Medical Devices", "Abbott Laboratories"),
    ("MRNA", "Healthcare", "Biotechnology", "Moderna"),
    ("ISRG", "Healthcare", "Medical Instruments", "Intuitive Surgical"),
    ("HCA", "Healthcare", "Medical Care Facilities", "HCA Healthcare"),
    ("CVS", "Healthcare", "Health Care Plans", "CVS Health"),
    ("CI", "Healthcare", "Health Care Plans", "Cigna Group"),
    ("HUM", "Healthcare", "Health Care Plans", "Humana"),

    # Technology
    ("AAPL", "Technology", "Consumer Electronics", "Apple"),
    ("MSFT", "Technology", "Software - Infrastructure", "Microsoft"),
    ("GOOGL", "Technology", "Internet Content", "Alphabet"),
    ("GOOG", "Technology", "Internet Content", "Alphabet"),
    ("META", "Technology", "Internet Content", "Meta Platforms"),
    ("AMZN", "Technology", "Internet Retail", "Amazon"),
    ("NVDA", "Technology", "Semiconductors", "NVIDIA"),
    ("TSM", "Technology", "Semiconductors", "Taiwan Semiconductor"),
    ("AVGO", "Technology", "Semiconductors", "Broadcom"),
    ("AMD", "Technology", "Semiconductors", "Advanced Micro Devices"),
    ("INTC", "Technology", "Semiconductors", "Intel"),
    ("CRM", "Technology", "Software - Application", "Salesforce"),
    ("ORCL", "Technology", "Software - Infrastructure", "Oracle"),
    ("CSCO", "Technology", "Communication Equipment", "Cisco Systems"),
    ("ADBE", "Technology", "Software - Application", "Adobe"),

    # Energy
    ("XOM", "Energy", "Oil & Gas Integrated", "Exxon Mobil"),
    ("CVX", "Energy", "Oil & Gas Integrated", "Chevron"),
    ("COP", "Energy", "Oil & Gas E&P", "ConocoPhillips"),
    ("SLB", "Energy", "Oil & Gas Equipment", "Schlumberger"),
    ("EOG", "Energy", "Oil & Gas E&P", "EOG Resources"),
    ("OXY", "Energy", "Oil & Gas E&P", "Occidental Petroleum"),
    ("HAL", "Energy", "Oil & Gas Equipment", "Halliburton"),

    # Finance
    ("JPM", "Finance", "Banks - Diversified", "JPMorgan Chase"),
    ("BAC", "Finance", "Banks - Diversified", "Bank of America"),
    ("WFC", "Finance", "Banks - Diversified", "Wells Fargo"),
    ("GS", "Finance", "Capital Markets", "Goldman Sachs"),
    ("MS", "Finance", "Capital Markets", "Morgan Stanley"),
    ("BLK", "Finance", "Asset Management", "BlackRock"),
    ("V", "Finance", "Credit Services", "Visa"),
    ("MA", "Finance", "Credit Services", "Mastercard"),

    # Communications
    ("DIS", "Communications", "Entertainment", "Walt Disney"),
    ("NFLX", "Communications", "Entertainment", "Netflix"),
    ("CMCSA", "Communications", "Entertainment", "Comcast"),
    ("T", "Communications", "Telecom Services", "AT&T"),
    ("VZ", "Communications", "Telecom Services", "Verizon"),
    ("TMUS", "Communications", "Telecom Services", "T-Mobile US"),

    # Consumer
    ("WMT", "Consumer", "Discount Stores", "Walmart"),
    ("COST", "Consumer", "Discount Stores", "Costco"),
    ("HD", "Consumer", "Home Improvement", "Home Depot"),
    ("MCD", "Consumer", "Restaurants", "McDonald's"),
    ("SBUX", "Consumer", "Restaurants", "Starbucks"),
    ("NKE", "Consumer", "Footwear & Accessories", "Nike"),
    ("KO", "Consumer", "Beverages - Non-Alcoholic", "Coca-Cola"),
    ("PEP", "Consumer", "Beverages - Non-Alcoholic", "PepsiCo"),
    ("PG", "Consumer", "Household Products", "Procter & Gamble"),
    ("TSLA", "Consumer", "Auto Manufacturers", "Tesla"),

    # Industrials
    ("CAT", "Industrials", "Farm & Heavy Machinery", "Caterpillar"),
    ("DE", "Industrials", "Farm & Heavy Machinery", "Deere & Company"),
    ("UPS", "Industrials", "Integrated Freight", "United Parcel Service"),
    ("HON", "Industrials", "Conglomerates", "Honeywell"),
    ("UNP", "Industrials", "Railroads", "Union Pacific"),
    ("GE", "Industrials", "Specialty Industrial Machinery", "GE Aerospace"),

    # Real Estate
    ("AMT", "Real Estate", "REIT - Specialty", "American Tower"),
    ("PLD", "Real Estate", "REIT - Industrial", "Prologis"),
    ("SPG", "Real Estate", "REIT - Retail", "Simon Property Group"),

    # Utilities
    ("NEE", "Utilities", "Utilities - Regulated Electric", "NextEra Energy"),
    ("DUK", "Utilities", "Utilities - Regulated Electric", "Duke Energy"),
    ("SO", "Utilities", "Utilities - Regulated Electric", "Southern Company"),
]


def seed_sectors(db: Session):
    existing = {s.ticker for s in db.query(Sector.ticker).all()}
    new_sectors = []
    for ticker, sector, industry, company_name in SECTOR_DATA:
        if ticker not in existing:
            new_sectors.append(
                Sector(ticker=ticker, sector=sector, industry=industry, company_name=company_name)
            )
    if new_sectors:
        db.add_all(new_sectors)
        db.commit()
        print(f"Seeded {len(new_sectors)} sector mappings")
    else:
        print("Sector data already seeded")
