import json
import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import argparse

# --- Database Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.getenv('DB_FILE_PATH', os.path.join(BASE_DIR, 'finance.db'))
DATABASE_URL = f"sqlite:///{DB_FILE}"

Base = declarative_base()

class Asset(Base):
    __tablename__ = 'assets'
    id = Column(Integer, primary_key=True)
    owner = Column(String, nullable=False, default="Vivek")
    name = Column(String, nullable=False)
    dp_name = Column(String, nullable=True) 
    asset_type = Column(String, nullable=False)
    currency = Column(String, nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    isin = Column(String, nullable=True)
    ticker = Column(String, nullable=True)
    last_updated = Column(DateTime, nullable=True)

engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

ISIN_MAP = {
    # ... (Keep existing map if needed, or load from config) ...
    # Ideally this should be centralized, but for now we keep it simple
    "INE674K01013": "ABCAPITAL.NS",
    "INE009A01021": "INFY.NS",
    "INE467B01029": "TCS.NS",
    "INE040A01034": "HDFCBANK.NS",
    "INF204KB17I5": "GOLDBEES.NS",
    "INF789F01XA0": "0P0000XVU2.BO", # UTI Nifty 50
}

def guess_ticker(name, isin):
    if isin and isin.strip() in ISIN_MAP:
        return ISIN_MAP[isin.strip()]
    if not name: return None
    clean_name = name.upper()
    if "#" in clean_name: clean_name = clean_name.split("#")[0]
    if "-" in clean_name: clean_name = clean_name.split("-")[0]
    for suffix in [" LIMITED", " LTD", " PVT", " PRIVATE", " EQUITY SHARES", " S.A.", " INC", " CORP", " CORPORATION", " INDIA", " NEW EQUITY SHARES", " EQUITY SHARES"]:
        clean_name = clean_name.replace(suffix, "")
    clean_name = clean_name.strip()
    if clean_name:
        first_word = clean_name.split(' ')[0]
        if len(first_word) > 2:
            return f"{first_word}.NS"
    return None

import shutil
import time

def backup_database():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{DB_FILE}.{timestamp}.bak"
    try:
        shutil.copy2(DB_FILE, backup_file)
        print(f"Database backup created: {backup_file}")
        return True
    except Exception as e:
        print(f"Error creating backup: {e}")
        return False

def import_data(file_path, target_owner):
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return

    # Create backup before modifying DB
    if not backup_database():
        print("Aborting import due to backup failure.")
        return

    print(f"Reading data from {file_path}...")
    with open(file_path, 'r') as f:
        data = json.load(f)
    
    session = SessionLocal()
    
    # CRITICAL: Delete existing assets for this owner to prevent duplicates
    print(f"Clearing existing assets for owner: '{target_owner}'...")
    deleted_count = session.query(Asset).filter(Asset.owner == target_owner).delete()
    print(f"Deleted {deleted_count} existing records.")
    
    count = 0
    
    def add(name, type_, qty, val, isin=None, dp_name=None):
        nonlocal count
        if qty > 0:
            unit_price = val / qty if qty else 0
            ticker = guess_ticker(name, isin) if type_ == 'Stock' else None
            
            # Special case for known MFs if ticker not found
            if type_ == 'MF' and isin in ISIN_MAP:
                ticker = ISIN_MAP[isin]

            asset = Asset(
                owner=target_owner,
                name=name,
                dp_name=dp_name,
                asset_type=type_,
                currency='INR',
                quantity=qty,
                unit_price=unit_price,
                isin=isin,
                ticker=ticker,
                last_updated=datetime.datetime.now()
            )
            session.add(asset)
            count += 1

    # Logic to parse parsed_cdsl.json structure
    if 'accounts' in data and isinstance(data['accounts'], list):
        # Handle parsed_cdsl.json format
        print("Detected parsed_cdsl.json format")
        for acc in data['accounts']:
            dp = acc.get('name')
            # Equities
            if 'equities' in acc:
                for eq in acc['equities']:
                    add(eq.get('name', 'Unknown'), 'Stock', float(eq.get('num_shares', 0)), float(eq.get('value', 0)), eq.get('isin'), dp_name=dp)
            # Mutual Funds
            if 'mutual_funds' in acc:
                for mf in acc['mutual_funds']:
                    add(mf.get('name', 'Unknown'), 'MF', float(mf.get('balance', 0)), float(mf.get('value', 0)), mf.get('isin'), dp_name=dp)

    # Logic to parse old data.json.txt structure
    elif 'demat_accounts' in data or 'mutual_funds' in data:
        print("Detected data.json.txt format")
        if 'demat_accounts' in data:
            for account in data['demat_accounts']:
                dp = account.get('dp_name')
                h = account.get('holdings', {})
                for i in h.get('equities', []): add(i.get('name'), 'Stock', float(i.get('units',0)), float(i.get('value',0)), i.get('isin'), dp_name=dp)
                for i in h.get('demat_mutual_funds', []): add(i.get('name'), 'MF', float(i.get('units',0)), float(i.get('value',0)), i.get('isin'), dp_name=dp)
                for i in h.get('corporate_bonds', []): add(i.get('name'), 'Bond', float(i.get('units',0)), float(i.get('value',0)), i.get('isin'), dp_name=dp)
                for i in h.get('government_securities', []): add(i.get('name'), 'Govt Sec', float(i.get('units',0)), float(i.get('value',0)), i.get('isin'), dp_name=dp)

        if 'mutual_funds' in data:
            for mf in data['mutual_funds']:
                amc = mf.get('amc')
                for s in mf.get('schemes', []):
                    add(s.get('name'), 'MF', float(s.get('units',0)), float(s.get('value',0)), s.get('isin'), dp_name=amc)

    # Logic to parse custom_cdsl_parser.py output (simple dict with equities/mf_demat keys)
    elif 'equities' in data and isinstance(data['equities'], list):
        print("Detected custom_cdsl_parser format")
        for eq in data['equities']:
             add(eq.get('name'), 'Stock', float(eq.get('units', 0)), float(eq.get('value', 0)), eq.get('isin'), dp_name="CDSL")
        if 'mf_demat' in data:
            for mf in data['mf_demat']:
                add(mf.get('name'), 'MF', float(mf.get('units', 0)), float(mf.get('value', 0)), mf.get('isin'), dp_name="CDSL")
    
    session.commit()
    session.close()
    print(f"Successfully imported {count} assets for owner '{target_owner}'.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import assets from JSON to Finance DB")
    parser.add_argument("file", help="Path to the JSON file")
    parser.add_argument("owner", help="Owner name (Vivek, Father, Wife, Mother)")
    
    args = parser.parse_args()
    import_data(args.file, args.owner)
