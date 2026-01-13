import streamlit as st
import pandas as pd
import requests
import sqlalchemy
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import plotly.express as px
import time
import json
import datetime
import yfinance as yf
import os
import re
from streamlit_sortables import sort_items
import subprocess
from groq import Groq
import sys
try:
    import casparser
except ImportError:
    casparser = None

import shutil
import time

# --- Database Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.getenv('DB_FILE_PATH', os.path.join(BASE_DIR, 'finance.db'))
DATABASE_URL = f"sqlite:///{DB_FILE}"

def backup_database():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{DB_FILE}.{timestamp}.bak"
    try:
        shutil.copy2(DB_FILE, backup_file)
        return backup_file
    except Exception as e:
        print(f"Error creating backup: {e}")
        return None

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
    original_currency = Column(String, nullable=True)
    original_unit_price = Column(Float, nullable=True)
    daily_change_pct = Column(Float, nullable=True)
    avg_buy_price = Column(Float, nullable=True)
    price_30d = Column(Float, nullable=True)

class PortfolioHistory(Base):
    __tablename__ = 'portfolio_history'
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    total_value = Column(Float, nullable=False)

class AppSettings(Base):
    __tablename__ = 'app_settings'
    id = Column(Integer, primary_key=True)
    smtp_server = Column(String, default="smtp.gmail.com")
    smtp_port = Column(Integer, default=587)
    sender_email = Column(String, nullable=True)
    sender_password = Column(String, nullable=True)
    receiver_email = Column(String, nullable=True)
    report_enabled = Column(sqlalchemy.Boolean, default=False)
    report_time = Column(String, default="18:00")
    last_run_date = Column(Date, nullable=True)
    gemini_api_key = Column(String, nullable=True)
    groq_api_key = Column(String, nullable=True)
    ai_context_columns = Column(String, default="name,ticker,quantity,unit_price,Value (INR),daily_change_pct")
    gotify_url = Column(String, nullable=True)
    gotify_token = Column(String, nullable=True)
    gotify_enabled = Column(sqlalchemy.Boolean, default=False)

class InvestmentTransaction(Base):
    __tablename__ = 'investment_transactions'
    id = Column(Integer, primary_key=True)
    date = Column(Date, default=datetime.date.today)
    asset_name = Column(String, nullable=False)
    ticker = Column(String, nullable=True)
    transaction_type = Column(String, default="BUY") # BUY, SELL, ADJUSTMENT
    quantity_change = Column(Float, nullable=False)
    price_per_unit = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    owner = Column(String, default="Vivek")

# Increase timeout to 30s to handle concurrent writes (background updater + app)
engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})

# Enable Write-Ahead Logging (WAL) for better concurrency
try:
    with engine.connect() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL;"))
except Exception as e:
    print(f"Could not set WAL mode: {e}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@st.cache_resource
def init_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    
    # Check if settings exist
    if not session.query(AppSettings).first():
        session.add(AppSettings(id=1))
        session.commit()
    
    # Schema Migration: Check if gemini_api_key exists
    try:
        session.execute(sqlalchemy.text("SELECT gemini_api_key FROM app_settings LIMIT 1"))
    except Exception:
        try:
            session.execute(sqlalchemy.text("ALTER TABLE app_settings ADD COLUMN gemini_api_key VARCHAR"))
            session.commit()
        except: pass

    # Migration: groq_api_key
    try:
        session.execute(sqlalchemy.text("SELECT groq_api_key FROM app_settings LIMIT 1"))
    except Exception:
        try:
            session.execute(sqlalchemy.text("ALTER TABLE app_settings ADD COLUMN groq_api_key VARCHAR"))
            session.commit()
        except: pass

    # Migration: ai_context_columns
    try:
        session.execute(sqlalchemy.text("SELECT ai_context_columns FROM app_settings LIMIT 1"))
    except Exception:
        try:
            default_cols = "name,ticker,quantity,unit_price,Value (INR),daily_change_pct"
            session.execute(sqlalchemy.text(f"ALTER TABLE app_settings ADD COLUMN ai_context_columns VARCHAR DEFAULT '{default_cols}'"))
            session.commit()
        except: pass

    # Migration: Gotify
    try:
        session.execute(sqlalchemy.text("SELECT gotify_url FROM app_settings LIMIT 1"))
    except Exception:
        try:
            session.execute(sqlalchemy.text("ALTER TABLE app_settings ADD COLUMN gotify_url VARCHAR"))
            session.execute(sqlalchemy.text("ALTER TABLE app_settings ADD COLUMN gotify_token VARCHAR"))
            session.execute(sqlalchemy.text("ALTER TABLE app_settings ADD COLUMN gotify_enabled BOOLEAN DEFAULT 0"))
            session.commit()
        except: pass

    # Migration: price_30d
    try:
        session.execute(sqlalchemy.text("SELECT price_30d FROM assets LIMIT 1"))
    except Exception:
        try:
            session.execute(sqlalchemy.text("ALTER TABLE assets ADD COLUMN price_30d FLOAT"))
            session.commit()
        except: pass

    # Migration: InvestmentTransaction table
    try:
        session.execute(sqlalchemy.text("SELECT * FROM investment_transactions LIMIT 1"))
    except Exception:
        try:
            Base.metadata.create_all(bind=engine)
        except: pass

    session.close()

def get_db_session():
    return SessionLocal()

# --- ISIN / Ticker Logic ---

ISIN_MAP = {
    "INE674K01013": "ABCAPITAL.NS",
    "INE885A01032": "ARE&M.NS",
    "INE238A01034": "AXISBANK.NS",
    "INE483A01010": "CENTRALBK.NS",
    "INE059A01026": "CIPLA.NS",
    "INE491A01021": "CUB.NS",
    "INE757A01017": "COSMOFIRST.NS",
    "INE148O01028": "DELHIVERY.NS",
    "INE361B01024": "DIVISLAB.NS",
    "INE089A01031": "DRREDDY.NS",
    "INE302A01020": "EXIDEIND.NS",
    "INE171A01029": "FEDERALBNK.NS",
    "INE860A01027": "HCLTECH.NS",
    "INE040A01034": "HDFCBANK.NS",
    "INE158A01026": "HEROMOTOCO.NS",
    "INE765G01017": "ICICIGI.NS",
    "INE092T01019": "IDFCFIRSTB.NS",
    "INE095A01012": "INDUSINDBK.NS",
    "INE009A01021": "INFY.NS",
    "INE154A01025": "ITC.NS",
    "INE668F01031": "JYOTHYLAB.NS",
    "INE303R01014": "KALYANKJIL.NS",
    "INE614B01018": "KTKBANK.NS",
    "INE498L01015": "L&TFH.NS",
    "INE998I01010": "MHRIL.NS",
    "INE522D01027": "MANAPPURAM.NS",
    "INE893J01029": "MOLDTKPAC.NS",
    "INE414G01012": "MUTHOOTFIN.NS",
    "INE987B01026": "NATCOPHARM.NS",
    "INE347G01014": "PETRONET.NS",
    "INE683A01023": "SOUTHBANK.NS",
    "INE572J01011": "SPANDANA.NS",
    "INE00IN01015": "STOVEKRAFT.NS",
    "INE044A01036": "SUNPHARMA.NS",
    "INE668A01016": "TMB.NS",
    "INE092A01019": "TATACHEM.NS",
    "INE467B01029": "TCS.NS",
    "INE1TAE01010": "TATAMOTORS.NS", 
    "INE155A01022": "TATAMTRDVR.NS",
    "INE245A01021": "TATAPOWER.NS",
    "INE081A01020": "TATASTEEL.NS",
    "INE669C01036": "TECHM.NS",
    "INE085J01014": "THANGAMAYL.NS",
    "INE280A01028": "TITAN.NS",
    "INE690A01028": "TTKPRESTIG.NS",
    "INE075A01022": "WIPRO.NS",
    "INE0JO301016": "YATHARTH.NS",
    "INE768C01028": "ZYDUSWELL.NS",
    "INE010B01027": "ZYDUSLIFE.NS",
    "INE296A01032": "BAJFINANCE.NS",
    "INE288B01029": "DEEPAKNTR.NS",
    "INE200M01039": "VBL.NS",
    "INE871C01038": "AVANTIFEED.NS",
    "INE552Z01027": "ABDL.NS",
    "INE0FDU25010": "BIRET.NS",
    "INE041025011": "EMBASSY.NS",
    "INE0GGX23010": "PGINVIT.NS",
    "INE272A01031": "PVR.NS",
    "INE538L01033": "DOMS.NS"
}

def resolve_ticker_from_yahoo(query):
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            quotes = data.get('quotes', [])
            if not quotes: return None
            for q in quotes:
                symbol = q.get('symbol', '')
                if symbol.endswith('.NS') or symbol.endswith('.BO'):
                    return symbol
            return quotes[0].get('symbol')
    except Exception:
        return None
    return None

def get_exchange_rate(from_currency):
    """
    Fetches exchange rate to INR.
    """
    if from_currency == 'INR':
        return 1.0
    
    # Handle pence
    if from_currency == 'GBp':
        try:
            ticker = yf.Ticker("GBPINR=X")
            hist = ticker.history(period="1d")
            if not hist.empty:
                return hist['Close'].iloc[-1]
        except:
            pass
        return 1.0 # Fallback

    try:
        # Standard pairs: USDINR=X, EURINR=X
        symbol = f"{from_currency}INR=X"
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if not hist.empty:
            return hist['Close'].iloc[-1]
    except:
        pass
    
    return 1.0

def guess_ticker(name, isin):
    if isin and isin.strip() in ISIN_MAP: return ISIN_MAP[isin.strip()]
    if not name: return None
    clean_name = name.upper()
    if "#" in clean_name: clean_name = clean_name.split("#")[0]
    if "-" in clean_name: clean_name = clean_name.split("-")[0]
    for suffix in [" LIMITED", " LTD", " PVT", " PRIVATE", " EQUITY SHARES", " S.A.", " INC", " CORP", " CORPORATION", " INDIA", " NEW EQUITY SHARES", " EQUITY SHARES"]:
        clean_name = clean_name.replace(suffix, "")
    clean_name = clean_name.strip()
    if clean_name:
        first_word = clean_name.split(' ')[0]
        if len(first_word) > 2: return f"{first_word}.NS"
    return None

# --- Data Loading ---

def clear_all_data():
    session = SessionLocal()
    session.query(Asset).delete()
    session.commit()
    session.close()
    st.cache_data.clear()

def parse_and_load_json(json_content, owner_name, auto_fill_tickers=True):
    try:
        # 1. Backup Database
        bkp = backup_database()
        if bkp:
            print(f"Backup created: {bkp}")
        
        if isinstance(json_content, dict):
            data = json_content
        else:
            data = json.loads(json_content)
        
        session = SessionLocal()
        
        # 2. Fetch Existing State (Before Deletion) for History Calculation
        # Map by ISIN (primary) and Name (secondary fallback)
        # We must AGGREGATE existing holdings because the DB might currently contain duplicates.
        existing_assets_map = {}      # Key: ISIN, Value: {quantity, ticker}
        existing_assets_by_name = {}  # Key: Name, Value: {quantity, ticker}
        
        # Helper to normalize names for matching
        def normalize(n): return n.strip().lower() if n else ""
        
        current_db_assets = session.query(Asset).filter(Asset.owner == owner_name).all()
        for asset in current_db_assets:
            # Aggregate by ISIN
            if asset.isin:
                if asset.isin not in existing_assets_map:
                    existing_assets_map[asset.isin] = {'quantity': 0.0, 'ticker': asset.ticker}
                existing_assets_map[asset.isin]['quantity'] += asset.quantity
                # Keep the first non-null ticker found
                if not existing_assets_map[asset.isin]['ticker'] and asset.ticker:
                    existing_assets_map[asset.isin]['ticker'] = asset.ticker
            
            # Aggregate by Name (Fallback)
            norm_name = normalize(asset.name)
            if norm_name:
                if norm_name not in existing_assets_by_name:
                    existing_assets_by_name[norm_name] = {'quantity': 0.0, 'ticker': asset.ticker}
                existing_assets_by_name[norm_name]['quantity'] += asset.quantity
                if not existing_assets_by_name[norm_name]['ticker'] and asset.ticker:
                     existing_assets_by_name[norm_name]['ticker'] = asset.ticker

        # 3. Snapshot Strategy: Delete existing assets for this owner
        # This prevents duplicates and ensures the portfolio matches the imported file exactly.
        # We delete AFTER fetching state, so we can still diff.
        session.query(Asset).filter(Asset.owner == owner_name).delete()
        
        updated_count = 0
        added_count = 0
        transactions_logged = 0
        
        # --- PRE-PROCESS & AGGREGATE INPUT DATA ---
        # We will collect all inputs into a dictionary first to handle multiple folios/entries
        # Key: ISIN (preferred) or Normalized Name
        # Value: Object with summed fields
        aggregated_holdings = {} 

        def add_to_aggregate(name, type_, qty, val, isin=None, dp_name=None):
            if qty <= 0: return

            # Key Generation
            key = isin if isin else normalize(name)
            if not key: return # Skip invalid items

            if key not in aggregated_holdings:
                aggregated_holdings[key] = {
                    'name': name,
                    'type': type_,
                    'quantity': 0.0,
                    'value': 0.0,
                    'isin': isin,
                    'dp_name': dp_name, # Will keep the first one encountered
                    'ticker': None # Will resolve later
                }
            
            # Aggregate
            aggregated_holdings[key]['quantity'] += qty
            aggregated_holdings[key]['value'] += val
            # Update name/dp if better one found? For now, stick to first or last.
            # Maybe prefer name with more details? N/A for now.

        # --- Traverse JSON Structure ---
        if 'demat_accounts' in data:
            for account in data['demat_accounts']:
                dp = account.get('dp_name')
                h = account.get('holdings', {})
                for i in h.get('equities', []): add_to_aggregate(i.get('name'), 'Stock', float(i.get('units',0)), float(i.get('value',0)), i.get('isin'), dp_name=dp)
                for i in h.get('demat_mutual_funds', []): add_to_aggregate(i.get('name'), 'MF', float(i.get('units',0)), float(i.get('value',0)), i.get('isin'), dp_name=dp)
                for i in h.get('corporate_bonds', []): add_to_aggregate(i.get('name'), 'Bond', float(i.get('units',0)), float(i.get('value',0)), i.get('isin'), dp_name=dp)
                for i in h.get('government_securities', []): add_to_aggregate(i.get('name'), 'Govt Sec', float(i.get('units',0)), float(i.get('value',0)), i.get('isin'), dp_name=dp)

        if 'mutual_funds' in data:
            for mf in data['mutual_funds']:
                amc = mf.get('amc')
                for s in mf.get('schemes', []):
                    add_to_aggregate(s.get('name'), 'MF', float(s.get('units',0)), float(s.get('value',0)), s.get('isin'), dp_name=amc)
        
        # --- PROCESS AGGREGATED ITEMS ---
        # Special MF Ticker map
        ISIN_MAP_HARDCODED = {
            "INE674K01013": "ABCAPITAL.NS",
            "INE009A01021": "INFY.NS",
            "INE467B01029": "TCS.NS",
            "INE040A01034": "HDFCBANK.NS",
            "INF204KB17I5": "GOLDBEES.NS",
            "INF789F01XA0": "0P0000XVU2.BO",
        }

        for key, item in aggregated_holdings.items():
            name = item['name']
            type_ = item['type']
            qty = item['quantity']
            val = item['value']
            isin = item['isin']
            dp_name = item['dp_name']

            unit_price = val / qty if qty else 0

            # --- HISTORY TRACKING (Diff against Old State) ---
            prev_qty = 0.0
            prev_ticker = None
            
            # Lookup in aggregated existing state
            if isin and isin in existing_assets_map:
                prev_qty = existing_assets_map[isin]['quantity']
                prev_ticker = existing_assets_map[isin]['ticker']
            elif normalize(name) in existing_assets_by_name:
                prev_qty = existing_assets_by_name[normalize(name)]['quantity']
                prev_ticker = existing_assets_by_name[normalize(name)]['ticker']
            
            # Calculate Change
            qty_diff = qty - prev_qty
            
            # Log Transaction if significant increase (BUY)
            if qty_diff > 0.001:
                hist_ticker = prev_ticker
                if not hist_ticker and auto_fill_tickers and type_ == 'Stock':
                     hist_ticker = guess_ticker(name, isin)
                
                if type_ == 'MF' and isin in ISIN_MAP_HARDCODED:
                    hist_ticker = ISIN_MAP_HARDCODED[isin]

                invested_amt = qty_diff * unit_price
                
                trans = InvestmentTransaction(
                    date=datetime.date.today(),
                    asset_name=name,
                    ticker=hist_ticker,
                    transaction_type="BUY",
                    quantity_change=qty_diff,
                    price_per_unit=unit_price,
                    total_amount=invested_amt,
                    owner=owner_name
                )
                session.add(trans)
                transactions_logged += 1

            # --- CREATE NEW ASSET (Aggregated) ---
            ticker = None
            if prev_ticker:
                 ticker = prev_ticker # Preserve ticker from DB
            elif auto_fill_tickers and type_ == 'Stock':
                ticker = guess_ticker(name, isin)
            
            if type_ == 'MF' and isin in ISIN_MAP_HARDCODED:
                ticker = ISIN_MAP_HARDCODED[isin]
            
            new_asset = Asset(
                owner=owner_name,
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
            session.add(new_asset)
            added_count += 1

        session.commit()
        session.close()
        st.cache_data.clear()
        return True, f"Data processed! Assets (Aggregated): {added_count}, Transactions Logged: {transactions_logged}"
    except Exception as e:
        return False, str(e)

# --- Price Updates ---

def auto_populate_tickers_smart():
    session = SessionLocal()
    assets = session.query(Asset).filter((Asset.ticker == None) | (Asset.ticker == ''), Asset.isin.isnot(None)).all()
    count = 0
    progress_bar = st.progress(0)
    total = len(assets)
    for i, asset in enumerate(assets):
        found_ticker = resolve_ticker_from_yahoo(asset.isin)
        if not found_ticker: found_ticker = resolve_ticker_from_yahoo(asset.name)
        if found_ticker:
            asset.ticker = found_ticker
            count += 1
        if total > 0: progress_bar.progress((i + 1) / total)
    session.commit()
    session.close()
    st.cache_data.clear()
    return count

def update_prices_from_yfinance():
    session = SessionLocal()
    assets = session.query(Asset).filter(Asset.ticker.isnot(None)).all()
    updated_count = 0
    progress_bar = st.progress(0)
    total = len(assets)
    
    # Cache rates to speed up
    rates_cache = {
        'INR': 1.0,
        'USD': get_exchange_rate('USD'),
        'GBP': get_exchange_rate('GBP'),
        'EUR': get_exchange_rate('EUR')
    }
    
    for i, asset in enumerate(assets):
        if asset.ticker and asset.ticker.strip():
            try:
                ticker = yf.Ticker(asset.ticker)
                price = None
                prev_close = None
                currency = 'INR'

                # Try to get currency
                try:
                    currency = ticker.fast_info.get('currency', 'INR')
                except:
                    pass
                
                # Try getting fast history first
                hist = ticker.history(period="5d") # Fetch a few days to be safe
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    if len(hist) >= 2:
                        prev_close = hist['Close'].iloc[-2]
                
                # Fallback to info if history fails or is insufficient
                if not price:
                    info = ticker.info
                    price = info.get('currentPrice') or info.get('regularMarketPrice')
                    prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
                    if not currency and 'currency' in info:
                        currency = info['currency']

                if price and price > 0:
                    # --- CURRENCY CONVERSION ---
                    native_price = price
                    price_inr = price
                    
                    # Handle GBp (Pence) specially
                    if currency == 'GBp':
                        rate = rates_cache.get('GBP', 1.0)
                        price_inr = (native_price / 100) * rate
                    elif currency != 'INR':
                        rate = rates_cache.get(currency)
                        if not rate:
                            rate = get_exchange_rate(currency)
                            rates_cache[currency] = rate
                        price_inr = native_price * rate

                    asset.unit_price = price_inr
                    asset.original_unit_price = native_price
                    asset.original_currency = currency
                    asset.last_updated = datetime.datetime.now()
                    
                    # Calculate Daily Change %
                    if prev_close and prev_close > 0:
                        change = ((price - prev_close) / prev_close) * 100
                        asset.daily_change_pct = change
                        
                    updated_count += 1
                else:
                    asset.last_updated = None # Set blank if price not found
            except Exception:
                asset.last_updated = None # Set blank if error
        if total > 0: progress_bar.progress((i + 1) / total)
                
    session.commit()
    session.close()
    st.cache_data.clear()
    return updated_count

# --- History ---

def record_portfolio_value(total_value):
    session = SessionLocal()
    today = datetime.date.today()
    entry = session.query(PortfolioHistory).filter(PortfolioHistory.date == today).first()
    if entry:
        entry.total_value = total_value
    else:
        entry = PortfolioHistory(date=today, total_value=total_value)
        session.add(entry)
    session.commit()
    session.close()

@st.cache_data(ttl=3600) # Cache history for 1 hour as it's daily
def get_history_df():
    # optimized to use global engine
    return pd.read_sql(sessionmaker(bind=engine)().query(PortfolioHistory).statement, sessionmaker(bind=engine)().bind)

@st.cache_data(ttl=10) # Cache assets for 10s to allow quick interactions without DB hits
def get_assets_df():
    # optimized to use global engine
    return pd.read_sql(sessionmaker(bind=engine)().query(Asset).statement, sessionmaker(bind=engine)().bind)

@st.cache_data(ttl=10)
def get_transactions_df():
    # optimized to use global engine
    return pd.read_sql(sessionmaker(bind=engine)().query(InvestmentTransaction).statement, sessionmaker(bind=engine)().bind)

# --- UI ---

st.set_page_config(page_title="Net Worth Tracker", layout="wide")

# Run DB Migration (Fix Schema) on Startup
try:
    import fix_db
    fix_db.run_migration()
except Exception as e:
    print(f"Migration failed: {e}")

init_db()

# Initialize/Fetch API Key
if "groq_api_key" not in st.session_state:
    # Try to get from DB first
    session = SessionLocal()
    settings = session.query(AppSettings).filter(AppSettings.id == 1).first()
    db_key = settings.groq_api_key if settings else None
    session.close()
    
    # Fallback to env var or empty
    st.session_state.groq_api_key = db_key or os.getenv("GROQ_API_KEY", "")

# Sidebar
st.sidebar.title("Data Management")

# Trigger Background Update Button
if st.sidebar.button("🔄 Update Live Prices & AI Summary"):
    try:
        # Run background_updater.py with --once flag
        result = subprocess.Popen([sys.executable, "background_updater.py", "--once"])
        st.sidebar.success("Background update triggered! Check Gotify/Logs in a moment.")
    except Exception as e:
        st.sidebar.error(f"Failed to trigger update: {e}")

# Owner Selection with Validation
owner_options = ["Select Owner...", "Vivek", "Wife", "Father", "Mother"]
selected_owner_label = st.sidebar.selectbox("Select Portfolio Owner", owner_options, index=0)
owner = None if selected_owner_label == "Select Owner..." else selected_owner_label

st.sidebar.subheader("Import Data")
import_mode = st.sidebar.radio("Source", ["Paste JSON", "Upload JSON File", "Upload CAS PDF"])
json_content = None
parsed_data = None

if import_mode == "Paste JSON":
    json_text = st.sidebar.text_area("Paste data.json content:", height=150)
    if st.sidebar.button("Append Data", disabled=(owner is None)):
        if json_text: json_content = json_text
        
elif import_mode == "Upload JSON File":
    uploaded_file = st.sidebar.file_uploader("Upload data.json", type=["json", "txt"])
    if uploaded_file and st.sidebar.button("Append Data", disabled=(owner is None)):
        json_content = uploaded_file.read().decode("utf-8")

elif import_mode == "Upload CAS PDF":
    if casparser is None:
        st.sidebar.error("casparser library not installed. Rebuild Docker image.")
    else:
        uploaded_pdf = st.sidebar.file_uploader("Upload CAS PDF", type=["pdf"])
        pdf_password = st.sidebar.text_input("CAS Password", type="password")
        
        # Parse Button
        if uploaded_pdf and st.sidebar.button("1. Parse PDF", disabled=(not pdf_password)):
            with st.spinner("Parsing CAS PDF..."):
                try:
                    # Save temp file
                    with open("temp_cas.pdf", "wb") as f:
                        f.write(uploaded_pdf.getbuffer())
                    
                    # Parse
                    data = casparser.read_cas_pdf("temp_cas.pdf", pdf_password, force_pdfminer=True)
                    st.session_state['parsed_cas_data'] = data
                    st.sidebar.success("✅ Parsing Successful!")
                    
                    # Cleanup
                    if os.path.exists("temp_cas.pdf"):
                        os.remove("temp_cas.pdf")
                        
                except Exception as e:
                    st.sidebar.error(f"Failed to parse PDF: {e}")
                    if os.path.exists("temp_cas.pdf"):
                        os.remove("temp_cas.pdf")

        # Preview and Import
        if 'parsed_cas_data' in st.session_state and st.session_state['parsed_cas_data']:
            st.sidebar.markdown("---")
            st.sidebar.subheader("Preview Data")
            
            # Show a snippet or the full JSON
            with st.sidebar.expander("View Parsed JSON Content"):
                st.json(st.session_state['parsed_cas_data'])
                
            if st.sidebar.button("2. Confirm & Import", disabled=(owner is None)):
                parsed_data = st.session_state['parsed_cas_data']
                # Trigger import below
                
                # Clear session state after import
                del st.session_state['parsed_cas_data']

if json_content or parsed_data:
    if not owner:
        st.sidebar.error("Please select a Portfolio Owner first!")
    else:
        content_to_load = parsed_data if parsed_data else json_content
        success, msg = parse_and_load_json(content_to_load, owner, auto_fill_tickers=True)
        if success:
            st.sidebar.success(f"Added data for {owner}!")
            time.sleep(1)
            st.rerun()
        else:
            st.sidebar.error(f"Error: {msg}")

st.sidebar.markdown("---")
if st.sidebar.button("🗑️ Clear ENTIRE Database", type="primary"):
    clear_all_data()
    st.sidebar.warning("All data deleted.")
    time.sleep(1)
    st.rerun()

# Main
st.title("📈 Family Net Worth Tracker")

df = get_assets_df()

if not df.empty:
    # --- Global Calculations ---
    # Calculate per-unit daily price change & total value change
    if 'daily_change_pct' in df.columns:
        df['daily_price_change'] = df['unit_price'] - (df['unit_price'] / (1 + (df['daily_change_pct'] / 100)))
        df['daily_total_value_change'] = df['daily_price_change'] * df['quantity']
    else:
        df['daily_price_change'] = 0.0
        df['daily_total_value_change'] = 0.0

    df['Value (INR)'] = df['quantity'] * df['unit_price']
    total_net_worth = df['Value (INR)'].sum()
    record_portfolio_value(total_net_worth)
    
    c1, c2, c3 = st.columns([1, 1, 1])
    c1.metric("Total Net Worth", f"₹ {total_net_worth:,.2f}")
    
    with c2:
        st.write("Missing Tickers?")
        if st.button("🔎 Smart-Find using ISIN"):
            with st.spinner("Searching Yahoo Finance by ISIN..."):
                count = auto_populate_tickers_smart()
            st.success(f"Found tickers for {count} assets!")
            time.sleep(1)
            st.rerun()
            
    with c3:
        st.write("Update Values?")
        if st.button("🔄 Sync Live Prices"):
            with st.spinner("Fetching latest prices..."):
                count = update_prices_from_yfinance()
            st.success(f"Updated {count} assets!")
            time.sleep(1)
            st.rerun()

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["Highlights", "Portfolio", "Analysis", "Growth Graph", "Settings", "AI Insights"])
    
    with tab1:
        st.header("✨ Daily Highlights & Movers")
        
        if not df.empty:
            # Prepare Data for Overall Change
            # Calculate overall pct change where buy price is available
            # Formula: (current - buy) / buy
            # Create a copy to avoid SettingWithCopy warnings on main df
            highlights_df = df.copy()
            highlights_df['overall_change_pct'] = 0.0
            
            # Vectorized calculation where buy_price > 0
            valid_buy = (highlights_df['avg_buy_price'].notna()) & (highlights_df['avg_buy_price'] > 0)
            highlights_df.loc[valid_buy, 'overall_change_pct'] = (
                (highlights_df.loc[valid_buy, 'unit_price'] - highlights_df.loc[valid_buy, 'avg_buy_price']) 
                / highlights_df.loc[valid_buy, 'avg_buy_price']
            ) * 100
            
            # --- Daily Movers ---
            st.subheader("📅 Today's Top Movers (Family Total)")
            col_d1, col_d2 = st.columns(2)
            
            # 1. Prepare global data for aggregation (ignore owner filter for Highlights)
            # Use the full 'df' fetched from DB
            m_df = df.copy()
            m_df['ticker'] = m_df['ticker'].fillna('').str.strip().str.upper()
            
            # 2. Group by Ticker to sum quantities across ALL owners (Vivek, Wife, Father, etc.)
            # This ensures Cipla holdings in all accounts are combined.
            daily_grouped = m_df[m_df['ticker'] != ''].groupby('ticker', as_index=False).agg({
                'name': 'first',
                'quantity': 'sum',
                'unit_price': 'first',
                'daily_change_pct': 'first', # All rows for same ticker have same % change
                'daily_total_value_change': 'sum' # Sum calculated value change
            })
            
            # Rename for compatibility with downstream logic
            daily_grouped['daily_change_value'] = daily_grouped['daily_total_value_change']

            # Filter for assets with valid sync data
            daily_active = daily_grouped[daily_grouped['daily_change_pct'].notna()].copy()

            # --- SECTION 1: By Value ---
            st.subheader("💰 Top Movers by Total Value (₹)")
            col_v1, col_v2 = st.columns(2)

            with col_v1:
                st.markdown("**🚀 Top Gainers (Value)**")
                # Sort by daily_change_value descending
                top_val = daily_active.sort_values('daily_change_value', ascending=False).head(3)
                if not top_val.empty:
                    for _, row in top_val.iterrows():
                        pct = row['daily_change_pct']
                        val_change = row['daily_change_value']
                        st.metric(
                            label=f"{row['name']} ({row['quantity']:.0f} shares)", 
                            value=f"+₹ {val_change:,.2f}", 
                            delta=f"{pct:.2f}%",
                            delta_color="normal"
                        )
                else:
                    st.write("No daily data available. Hit 'Sync Live Prices'.")

            with col_v2:
                st.markdown("**🔻 Top Losers (Value)**")
                # Sort by daily_change_value ascending
                bottom_val = daily_active.sort_values('daily_change_value', ascending=True).head(3)
                if not bottom_val.empty:
                    for _, row in bottom_val.iterrows():
                         pct = row['daily_change_pct']
                         val_change = row['daily_change_value']
                         st.metric(
                            label=f"{row['name']} ({row['quantity']:.0f} shares)", 
                            value=f"₹ {val_change:,.2f}", 
                            delta=f"{pct:.2f}%",
                            delta_color="normal"
                         )
                else:
                    st.write("No daily data available.")

            st.divider()

            # --- SECTION 2: By Percentage ---
            st.subheader("📊 Top Movers by Percentage (%)")
            col_p1, col_p2 = st.columns(2)

            with col_p1:
                st.markdown("**🚀 Top Gainers (%)**")
                # Sort by daily_change_pct descending
                top_pct = daily_active.sort_values('daily_change_pct', ascending=False).head(3)
                if not top_pct.empty:
                    for _, row in top_pct.iterrows():
                        pct = row['daily_change_pct']
                        val_change = row['daily_change_value']
                        st.metric(
                            label=f"{row['name']} ({row['quantity']:.0f} shares)", 
                            value=f"{pct:.2f}%", 
                            delta=f"₹ {val_change:+,.2f}",
                            delta_color="normal"
                        )
                else:
                    st.write("No data.")

            with col_p2:
                st.markdown("**🔻 Top Losers (%)**")
                # Sort by daily_change_pct ascending
                bottom_pct = daily_active.sort_values('daily_change_pct', ascending=True).head(3)
                if not bottom_pct.empty:
                    for _, row in bottom_pct.iterrows():
                         pct = row['daily_change_pct']
                         val_change = row['daily_change_value']
                         st.metric(
                            label=f"{row['name']} ({row['quantity']:.0f} shares)", 
                            value=f"{pct:.2f}%", 
                            delta=f"₹ {val_change:+,.2f}",
                            delta_color="normal"
                         )
                else:
                    st.write("No data.")

            st.divider()

            # --- Overall Movers ---
            st.subheader("📈 Overall Performance (Since Buy)")
            
            # Check if we have any buy price data
            if valid_buy.any():
                col_o1, col_o2 = st.columns(2)
                
                with col_o1:
                    st.markdown("**🏆 Top 3 Gainers (Overall)**")
                    top_overall = highlights_df[valid_buy].sort_values('overall_change_pct', ascending=False).head(3)
                    for _, row in top_overall.iterrows():
                        st.metric(label=row['name'], value=f"₹ {row['unit_price']:.2f}", delta=f"{row['overall_change_pct']:.2f}%")
                
                with col_o2:
                    st.markdown("**📉 Top 3 Losers (Overall)**")
                    bottom_overall = highlights_df[valid_buy].sort_values('overall_change_pct', ascending=True).head(3)
                    for _, row in bottom_overall.iterrows():
                        st.metric(label=row['name'], value=f"₹ {row['unit_price']:.2f}", delta=f"{row['overall_change_pct']:.2f}%")
            else:
                st.info("ℹ️ To see Overall Gains/Losses, please enter 'Avg Buy Price' for your assets in the 'Portfolio' tab.")
        else:
             st.write("Add assets to see highlights.")
             
    with tab2:
        st.write("### All Assets")
        
        # Prepare display dataframe to avoid modifying the global 'df'
        display_df = df.copy()

        # Append TOTAL Row for display
        if not display_df.empty:
            total_val = display_df['Value (INR)'].sum()
            total_diff = display_df['daily_total_value_change'].sum()
            
            # Create a dictionary for the total row
            total_row = {col: None for col in display_df.columns}
            total_row['id'] = -1  # Dummy ID to identify and skip saving
            total_row['name'] = "💰 TOTAL"
            total_row['Value (INR)'] = total_val
            total_row['daily_total_value_change'] = total_diff
            
            # Use pd.concat to append
            display_df = pd.concat([display_df, pd.DataFrame([total_row])], ignore_index=True)

        all_cols = ['id', 'owner', 'dp_name', 'name', 'isin', 'ticker', 'last_updated', 'asset_type', 'quantity', 'unit_price', 'daily_price_change', 'daily_total_value_change', 'Value (INR)', 'original_currency', 'original_unit_price', 'daily_change_pct', 'avg_buy_price']
        
        # 1. Select visible columns
        selected_cols = st.multiselect("Select Columns to Show", all_cols, default=all_cols)
        
        # 2. Reorder them using drag-and-drop
        st.write("Drag to Reorder:")
        sorted_cols = sort_items(selected_cols, direction="horizontal")
        
        # Ensure 'id' is available for updates even if not selected for view
        cols_to_use = sorted_cols.copy()
        if 'id' not in cols_to_use:
            cols_to_use.append('id')

        # Base config
        col_config = {
            "id": st.column_config.NumberColumn(disabled=True, width="small"),
            "owner": st.column_config.TextColumn(label="Owner", disabled=True, width="medium"),
            "dp_name": st.column_config.TextColumn(label="DP/AMC", disabled=True, width="medium"),
            "name": st.column_config.TextColumn(disabled=True, width="large"),
            "isin": st.column_config.TextColumn(label="ISIN", disabled=True, width="medium"),
            "ticker": st.column_config.TextColumn(label="Ticker", width="medium"),
            "last_updated": st.column_config.DatetimeColumn(label="Last Updated", disabled=True, format="D MMM, HH:mm"),
            "asset_type": st.column_config.TextColumn(disabled=True, width="small"),
            "Value (INR)": st.column_config.NumberColumn(disabled=True),
            "original_currency": st.column_config.TextColumn(label="Orig. Curr", disabled=True, width="small"),
            "original_unit_price": st.column_config.NumberColumn(label="Orig. Price", disabled=True),
            "daily_change_pct": st.column_config.NumberColumn(label="Day %", disabled=True, format="%.2f %%"),
            "daily_price_change": st.column_config.NumberColumn(label="Day Price Diff", disabled=True, format="%.2f"),
            "daily_total_value_change": st.column_config.NumberColumn(label="Day Total Diff", disabled=True, format="%.2f"),
            "avg_buy_price": st.column_config.NumberColumn(label="Avg Buy Price", format="%.2f"),
        }
        
        # Hide 'id' if not selected by user
        if 'id' not in sorted_cols:
             col_config['id'] = st.column_config.NumberColumn(hidden=True)

        edited_df = st.data_editor(
            display_df[cols_to_use],
            key="asset_editor",
            column_config=col_config,
            num_rows="dynamic",
            use_container_width=True
        )

        if st.button("Save Changes to Database"):
            session = SessionLocal()
            
            # 1. Identify IDs to Delete
            # Get IDs from the original dataframe (excluding the dummy TOTAL row with id=-1)
            original_ids = set(display_df[display_df['id'] != -1]['id'].unique())
            
            # Get IDs from the edited dataframe
            # Note: edited_df might contain new rows without IDs (if enabled), but we focus on deletions here
            current_ids = set(edited_df[edited_df['id'] != -1]['id'].unique())
            
            # Calculate IDs that were removed
            ids_to_delete = original_ids - current_ids
            
            if ids_to_delete:
                try:
                    # Fetch names and owners before deleting to clean up transactions
                    to_del_info = session.query(Asset.name, Asset.owner).filter(Asset.id.in_(ids_to_delete)).all()
                    
                    # Perform deletion
                    session.query(Asset).filter(Asset.id.in_(ids_to_delete)).delete(synchronize_session=False)
                    
                    # Cleanup Transactions associated with these assets
                    for name, owner_name in to_del_info:
                        session.query(InvestmentTransaction).filter(
                            InvestmentTransaction.asset_name == name,
                            InvestmentTransaction.owner == owner_name
                        ).delete(synchronize_session=False)
                        
                    st.toast(f"Deleted {len(ids_to_delete)} assets and their transaction history.")
                except Exception as e:
                    st.error(f"Error deleting rows: {e}")

            # 2. Perform Updates
            for index, row in edited_df.iterrows():
                if 'id' not in row: continue
                # Skip the Total row or new rows without valid IDs if any
                if row['id'] == -1 or pd.isna(row['id']): continue
                
                asset = session.query(Asset).filter(Asset.id == row['id']).first()
                if asset:
                    # Update fields if changed
                    # We check if keys exist in row because data_editor might not return hidden columns if config is weird,
                    # but usually it returns what's passed.
                    if 'ticker' in row: asset.ticker = row['ticker']
                    if 'quantity' in row: asset.quantity = row['quantity']
                    if 'unit_price' in row: asset.unit_price = row['unit_price']
                    if 'avg_buy_price' in row: asset.avg_buy_price = row['avg_buy_price']
            
            session.commit()
            session.close()
            st.cache_data.clear()
            st.success("Saved!")
            st.rerun()

    with tab3:
        st.header("Detailed Portfolio Analysis")
        
        # --- Top Level Filters ---
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            all_owners = list(df['owner'].unique()) if not df.empty else []
            sel_owners = st.multiselect("Filter by Owner", all_owners, default=all_owners)
        with col_f2:
            all_types = list(df['asset_type'].unique()) if not df.empty else []
            sel_types = st.multiselect("Filter by Asset Type", all_types, default=all_types)
            
        # Apply Filters
        if not df.empty:
            filtered_df = df[df['owner'].isin(sel_owners) & df['asset_type'].isin(sel_types)].copy()
        else:
            filtered_df = pd.DataFrame()
        
        if filtered_df.empty:
            st.info("No data available for the selected filters.")
        else:
            # --- KPI Row ---
            total_inr = filtered_df['Value (INR)'].sum()
            
            # GBP specific calculations
            # Ensure we handle NaN in original_currency/price safely
            gbp_mask = filtered_df['original_currency'] == 'GBP'
            gbp_assets = filtered_df[gbp_mask]
            
            # Calculate Total GBP (sum of qty * original_price)
            # Fillna(0) just in case, though logically shouldn't be needed if mask is correct
            total_gbp = (gbp_assets['quantity'] * gbp_assets['original_unit_price'].fillna(0)).sum()
            total_gbp_inr_equiv = gbp_assets['Value (INR)'].sum()
            
            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric("Total Filtered Value (INR)", f"₹ {total_inr:,.0f}")
            kpi2.metric("GBP Assets Value (Original)", f"£ {total_gbp:,.2f}")
            kpi3.metric("GBP Assets Value (in INR)", f"₹ {total_gbp_inr_equiv:,.0f}")
            
            st.divider()
            
            # --- Interactive View Selector ---
            view_mode = st.radio("Group Data By:", ["Asset Class", "DP / AMC", "Individual Assets", "Currency"], horizontal=True)
            
            if view_mode == "Asset Class":
                grouped = filtered_df.groupby("asset_type")['Value (INR)'].sum().reset_index()
                fig = px.pie(grouped, values='Value (INR)', names='asset_type', title='Allocation by Asset Class', hole=0.4)
                st.plotly_chart(fig, use_container_width=True)
                
            elif view_mode == "DP / AMC":
                # Handle None/NaN DP names
                filtered_df['dp_name_display'] = filtered_df['dp_name'].fillna('Unknown')
                grouped = filtered_df.groupby("dp_name_display")['Value (INR)'].sum().reset_index().sort_values('Value (INR)', ascending=False)
                fig = px.bar(grouped, x='dp_name_display', y='Value (INR)', color='dp_name_display', title='Value by DP / AMC', labels={'dp_name_display': 'DP / AMC'})
                st.plotly_chart(fig, use_container_width=True)
                
            elif view_mode == "Individual Assets":
                top_n = st.slider("Show Top N Assets", 5, 50, 10)
                sorted_assets = filtered_df.sort_values('Value (INR)', ascending=False).head(top_n)
                fig = px.bar(sorted_assets, x='name', y='Value (INR)', color='asset_type', title=f'Top {top_n} Assets by Value')
                st.plotly_chart(fig, use_container_width=True)

            elif view_mode == "Currency":
                 filtered_df['display_currency'] = filtered_df['original_currency'].fillna('INR')
                 grouped = filtered_df.groupby("display_currency")['Value (INR)'].sum().reset_index()
                 fig = px.pie(grouped, values='Value (INR)', names='display_currency', title='Exposure by Currency (converted to INR)', hole=0.4)
                 st.plotly_chart(fig, use_container_width=True)

            st.markdown("### 🌳 Portfolio Map")
            # Fill NaNs for treemap path to avoid errors
            filtered_df['dp_name'] = filtered_df['dp_name'].fillna('Unknown')
            filtered_df['asset_type'] = filtered_df['asset_type'].fillna('Other')
            
            fig_tree = px.treemap(
                filtered_df, 
                path=[px.Constant("Total Portfolio"), 'owner', 'asset_type', 'dp_name', 'name'], 
                values='Value (INR)',
                color='asset_type',
                title='Hierarchical Portfolio View'
            )
            st.plotly_chart(fig_tree, use_container_width=True)
             
    with tab4:
        st.header("Portfolio Growth & Trends")
        history_df = get_history_df()
        
        if not history_df.empty:
            # Ensure sorted by date
            history_df = history_df.sort_values('date')
            
            # --- Calculations ---
            # Use GLOBAL total_net_worth for the most up-to-date 'current' value
            current_val = total_net_worth
            
            # 1. Daily Change (Based on Live Data, consistent with Portfolio Tab)
            if 'daily_total_value_change' in df.columns:
                daily_change = df['daily_total_value_change'].sum()
                
                # Calculate previous day's theoretical close to get %
                prev_day_close = current_val - daily_change
                if prev_day_close != 0:
                    daily_pct = (daily_change / prev_day_close) * 100
                else:
                    daily_pct = 0.0
            else:
                daily_change = 0.0
                daily_pct = 0.0
            
            # 2. Monthly Change (Market Value Change over 30d)
            # This uses 'price_30d' column populated by background_updater
            monthly_market_change = 0.0
            month_pct = 0.0
            
            if 'price_30d' in df.columns:
                # Calculate only for assets where price_30d is available
                valid_30d = df['price_30d'].notna() & (df['price_30d'] > 0)
                if valid_30d.any():
                    # Sum of (Current Price - Price 30 Days Ago) * Qty
                    monthly_market_change = ((df.loc[valid_30d, 'unit_price'] - df.loc[valid_30d, 'price_30d']) * df.loc[valid_30d, 'quantity']).sum()
                    
                    # Base value 30 days ago for % calc
                    # Theoretical Base = Current Value - Gain
                    # But if we only sum valid ones, we should use total_net_worth for context? 
                    # Standard: (Gain / (Total - Gain)) * 100
                    base_val_month = current_val - monthly_market_change
                    if base_val_month != 0:
                        month_pct = (monthly_market_change / base_val_month) * 100
                else:
                    # Fallback if no 30d history (e.g. just reset): use Daily Change as proxy
                    monthly_market_change = daily_change
                    month_pct = daily_pct
            else:
                monthly_market_change = daily_change
                month_pct = daily_pct
            
            # 3. Total Growth (Since Buy - Market P&L)
            # Calculate sum of (Current - AvgBuy) * Qty
            total_growth_market = 0.0
            total_pct = 0.0
            
            if 'avg_buy_price' in df.columns:
                valid_buy = df['avg_buy_price'].notna() & (df['avg_buy_price'] > 0)
                if valid_buy.any():
                    total_growth_market = ((df.loc[valid_buy, 'unit_price'] - df.loc[valid_buy, 'avg_buy_price']) * df.loc[valid_buy, 'quantity']).sum()
                    
                    # Base Cost = Current Value - Total Profit
                    cost_basis = current_val - total_growth_market
                    if cost_basis != 0:
                        total_pct = (total_growth_market / cost_basis) * 100
            
            # --- Display Metrics ---
            m1, m2, m3, m4 = st.columns(4)
            
            m1.metric("Daily Change", f"₹ {daily_change:,.2f}", f"{daily_pct:.2f}%")
            m2.metric("Monthly Change (30d)", f"₹ {monthly_market_change:,.2f}", f"{month_pct:.2f}%")
            m3.metric("Total Growth (P&L)", f"₹ {total_growth_market:,.2f}", f"{total_pct:.2f}%")
            m4.metric("XIRR / CAGR", "N/A", help="Requires detailed transaction history (deposits/withdrawals) to calculate accurately.")
            
            st.divider()

            fig_line = px.line(history_df, x='date', y='total_value', title='Total Family Net Worth Growth', markers=True)
            fig_line.update_traces(line_color='#00CC96')
            st.plotly_chart(fig_line, use_container_width=True)

            # --- Monthly Investments ---
            st.divider()
            st.subheader("💰 Monthly Investments")
            trans_df = get_transactions_df()
            
            if not trans_df.empty:
                # Add Owner Filter for this specific chart
                owner_list = ["All"] + list(trans_df['owner'].unique()) if 'owner' in trans_df.columns else ["All"]
                selected_inv_owner = st.selectbox("Filter Investments by Owner", owner_list, index=0)
                
                if selected_inv_owner != "All":
                    trans_df = trans_df[trans_df['owner'] == selected_inv_owner]

                if not trans_df.empty:
                    trans_df['date'] = pd.to_datetime(trans_df['date'])
                    trans_df['Month'] = trans_df['date'].dt.strftime('%Y-%m')
                    
                    # Monthly Aggregation
                    monthly_inv = trans_df[trans_df['transaction_type'] == 'BUY'].groupby('Month')['total_amount'].sum().reset_index()
                    
                    fig_inv = px.bar(monthly_inv, x='Month', y='total_amount', title=f'Monthly Investment Amount ({selected_inv_owner})', text_auto='.2s')
                    fig_inv.update_traces(marker_color='#FF5733')
                    st.plotly_chart(fig_inv, use_container_width=True)
                    
                    with st.expander("See Investment Details"):
                        st.dataframe(trans_df.sort_values('date', ascending=False))
                else:
                    st.info(f"No transactions found for {selected_inv_owner}.")
            else:
                st.info("No investment transactions recorded yet. Future updates to your portfolio will appear here.")

        else:
            st.info("Growth history will be built as you visit the app over time.")
            
    with tab5:
        st.header("⚙️ Settings")
        st.subheader("Email Report Configuration")
        
        session = SessionLocal()
        settings = session.query(AppSettings).filter(AppSettings.id == 1).first()
        
        with st.form("email_settings_form"):
            smtp_server = st.text_input("SMTP Server", value=settings.smtp_server or "smtp.gmail.com")
            smtp_port = st.number_input("SMTP Port", value=settings.smtp_port or 587)
            sender_email = st.text_input("Sender Email", value=settings.sender_email or "")
            sender_password = st.text_input("Sender Password (App Password)", value=settings.sender_password or "", type="password")
            receiver_email = st.text_input("Receiver Email", value=settings.receiver_email or "")
            
            if st.form_submit_button("Save Settings"):
                settings.smtp_server = smtp_server
                settings.smtp_port = int(smtp_port)
                settings.sender_email = sender_email
                settings.sender_password = sender_password
                settings.receiver_email = receiver_email
                session.commit()
                st.success("Settings saved successfully!")
                
        st.divider()
        st.subheader("Gotify Notification Configuration")
        
        with st.form("gotify_settings_form"):
            gotify_enabled = st.checkbox("Enable Gotify Notifications", value=settings.gotify_enabled if settings.gotify_enabled is not None else False)
            gotify_url = st.text_input("Gotify Server URL", value=settings.gotify_url or "https://your-gotify-instance.com", help="Base URL of your Gotify server, e.g., https://push.example.com")
            gotify_token = st.text_input("Gotify App Token", value=settings.gotify_token or "", type="password")
            
            if st.form_submit_button("Save Gotify Settings"):
                settings.gotify_enabled = gotify_enabled
                settings.gotify_url = gotify_url.rstrip('/') # Remove trailing slash if present
                settings.gotify_token = gotify_token
                session.commit()
                st.success("Gotify settings saved!")

        if st.button("🔔 Send Test Notification"):
            if not settings.gotify_url or not settings.gotify_token:
                st.error("Please configure and save Gotify URL and Token first.")
            else:
                try:
                    full_url = f"{settings.gotify_url}/message?token={settings.gotify_token}"
                    payload = {
                        "title": "FinanceApp Test",
                        "message": "This is a test notification from your Finance App.",
                        "priority": 5
                    }
                    resp = requests.post(full_url, json=payload, timeout=5)
                    if resp.status_code == 200:
                        st.success("Notification sent successfully!")
                    else:
                        st.error(f"Failed to send: {resp.status_code} - {resp.text}")
                except Exception as e:
                    st.error(f"Error sending notification: {e}")

        st.divider()
        st.subheader("Daily Report Schedule")
        
        with st.form("scheduler_settings_form"):
            c_sch1, c_sch2 = st.columns(2)
            with c_sch1:
                report_enabled = st.checkbox("Enable Daily Email Report", value=settings.report_enabled if settings.report_enabled is not None else False)
            with c_sch2:
                # Use text input for time (HH:MM) simplicity, or time_input
                # We need to parse string to time object for time_input if it exists
                default_time = datetime.time(18, 0)
                if settings.report_time:
                    try:
                        h, m = map(int, settings.report_time.split(':'))
                        default_time = datetime.time(h, m)
                    except:
                        pass
                
                report_time_obj = st.time_input("Run Report At (Server Time)", value=default_time)
            
            if st.form_submit_button("Update Schedule"):
                settings.report_enabled = report_enabled
                settings.report_time = report_time_obj.strftime("%H:%M")
                session.commit()
                st.success(f"Schedule updated! Report will run at {settings.report_time} daily.")

        st.divider()
        st.subheader("Test Configuration")
        if st.button("📧 Send Test Email Now"):
            # Trigger the daily email report script via subprocess to test
            try:
                # Run the script using the current python executable
                result = subprocess.run(
                    [sys.executable, "daily_email_report.py"], 
                    capture_output=True, 
                    text=True, 
                    cwd=os.getcwd()
                )
                if result.returncode == 0:
                    st.success("Test email command executed! Check the logs/output.")
                    st.text(result.stdout)
                else:
                    st.error("Error executing script.")
                    st.text(result.stderr)
            except Exception as e:
                st.error(f"Failed to run test: {e}")
        
        st.divider()
        st.subheader("AI Configuration")
        with st.form("ai_settings_form"):
            api_key_val = st.text_input("Groq API Key", value=settings.groq_api_key or "", type="password", help="Get free key from https://console.groq.com/keys")
            
            # Get available columns from the main dataframe if available
            avail_cols = list(df.columns) if 'df' in locals() and not df.empty else ["name", "ticker", "quantity", "unit_price", "Value (INR)", "daily_change_pct"]
            
            # Load saved columns
            current_saved = settings.ai_context_columns.split(",") if settings.ai_context_columns else ["name", "ticker", "quantity", "unit_price", "Value (INR)", "daily_change_pct"]
            # Filter to ensure they exist in current df
            default_sel = [c for c in current_saved if c in avail_cols]
            
            context_cols = st.multiselect(
                "Select Data Columns for AI Context", 
                options=avail_cols, 
                default=default_sel,
                help="Sending fewer columns reduces token usage and helps avoid rate limits."
            )
            
            if st.form_submit_button("Save AI Settings"):
                settings.groq_api_key = api_key_val
                settings.ai_context_columns = ",".join(context_cols)
                session.commit()
                st.session_state.groq_api_key = api_key_val
                st.success("AI Settings Saved!")
                
        st.divider()
        st.subheader("🛠️ Data Tools")
        if st.button("🔄 Reset Growth History Baseline", help="Deletes all historical tracking data and sets 'yesterday' as the new starting point based on current prices."):
            try:
                session = SessionLocal()
                # 1. Calculate Baseline
                # We need live total and live daily change
                current_total = df['Value (INR)'].sum()
                current_daily_diff = df['daily_total_value_change'].sum()
                baseline_val = current_total - current_daily_diff
                
                # 2. Clear History
                session.query(PortfolioHistory).delete()
                
                # 3. Insert Yesterday's Baseline
                yesterday = datetime.date.today() - datetime.timedelta(days=1)
                session.add(PortfolioHistory(date=yesterday, total_value=baseline_val))
                
                # 4. Insert Today's Actual
                today = datetime.date.today()
                session.add(PortfolioHistory(date=today, total_value=current_total))
                
                session.commit()
                session.close()
                st.success("Growth history reset! Your baseline now starts from yesterday's closing prices.")
                time.sleep(1)
                st.rerun()
                if result.returncode == 0:
                    st.success("Test email command executed! Check the logs/output.")
                    st.text(result.stdout)
                else:
                    st.error("Error executing script.")
                    st.text(result.stderr)
            except Exception as e:
                st.error(f"Error executing script: {e}")

        st.divider()
        st.subheader("Data Cleanup")
        with st.expander("Danger Zone"):
            st.warning("These actions are destructive. Please be careful.")
            
            clean_owner = st.selectbox("Select Owner to Clean History", ["Select...", "Vivek", "Wife", "Father", "Mother"])
            if clean_owner != "Select...":
                if st.button(f"🗑️ Clear Transaction History for {clean_owner}"):
                    # Backup before destructive action
                    bkp = backup_database()
                    if bkp:
                        st.info(f"Backup created: {bkp}")
                    
                    # Clear investment_transactions for this owner
                    try:
                        del_count = session.query(InvestmentTransaction).filter(InvestmentTransaction.owner == clean_owner).delete()
                        session.commit()
                        st.success(f"Deleted {del_count} transaction records for {clean_owner}.")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Error cleaning history: {e}")

                st.markdown("---")
                if st.button(f"🧨 Delete ENTIRE Portfolio & History for {clean_owner}", type="primary", help="Deletes ALL assets and transactions. Cannot be undone without restoring backup."):
                    # Backup before destructive action
                    bkp = backup_database()
                    if bkp:
                        st.info(f"Backup created: {bkp}")
                    
                    try:
                        # Delete Assets
                        asset_count = session.query(Asset).filter(Asset.owner == clean_owner).delete()
                        # Delete Transactions
                        trans_count = session.query(InvestmentTransaction).filter(InvestmentTransaction.owner == clean_owner).delete()
                        
                        session.commit()
                        st.success(f"Full Reset Complete: Deleted {asset_count} assets and {trans_count} transaction records for {clean_owner}.")
                        st.cache_data.clear()
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error resetting portfolio: {e}")
            
            st.divider()
            st.subheader("Database Restore")
            
            # Determine DB Directory and Filename
            db_dir = os.path.dirname(DB_FILE)
            db_filename = os.path.basename(DB_FILE)
            
            # List available backups in the DB directory
            try:
                # Filter for files that start with the DB filename (e.g. finance.db.2023...)
                backups = [f for f in os.listdir(db_dir) if f.startswith(f"{db_filename}.") and f.endswith('.bak')]
                backups.sort(reverse=True) # Newest first
            except Exception as e:
                st.error(f"Error reading backup directory: {e}")
                backups = []
            
            if backups:
                selected_backup = st.selectbox("Select Backup to Restore", ["Select..."] + backups)
                if selected_backup != "Select...":
                    if st.button(f"⚠️ Restore {selected_backup}"):
                        try:
                            # 1. Backup current state just in case
                            pre_restore_bkp = backup_database()
                            st.info(f"Safety backup created: {pre_restore_bkp}")
                            
                            # 2. Perform Restore
                            src = os.path.join(db_dir, selected_backup)
                            # Close session before overwriting DB file
                            session.close()
                            
                            # Give a small delay to ensure file handles are released if possible
                            time.sleep(1)
                            
                            shutil.copy2(src, DB_FILE)
                            st.success(f"Database restored from {selected_backup} successfully!")
                            st.warning("Please refresh the page to reload the data.")
                        except Exception as e:
                            st.error(f"Error restoring database: {e}")
            else:
                st.info("No backups found.")
        
        session.close()

    with tab6:
        st.header("🤖 AI Financial Assistant (Powered by Groq)")
        st.info("Ask questions about your portfolio and get insights using Llama 3 on Groq.")

        api_key = st.session_state.get("groq_api_key")
        
        if api_key:
            try:
                client = Groq(api_key=api_key)
                
                # Chat Interface
                if "messages" not in st.session_state:
                    st.session_state.messages = []

                # Display existing chat history
                for message in st.session_state.messages:
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])

                # Chat input
                if prompt := st.chat_input("Ask about your portfolio (e.g., 'What is my best performing asset?', 'Summarize my allocation')"):
                    st.chat_message("user").markdown(prompt)
                    st.session_state.messages.append({"role": "user", "content": prompt})

                    # Prepare Context
                    try:
                        # Create a context-rich dataframe with ALL fields
                        context_df = df.copy()
                        
                        # Optimization: Round floats to 2 decimal places to save tokens
                        for col in context_df.select_dtypes(include=['float', 'float64']).columns:
                            context_df[col] = context_df[col].round(2)
                        
                        # HARD LIMIT: Sort by Value and take top 50 to prevent Token Limit Exceeded
                        if 'Value (INR)' in context_df.columns:
                            context_df = context_df.sort_values('Value (INR)', ascending=False).head(50)
                        else:
                            context_df = context_df.head(50)

                        # Calculate total value (might not be in context_df anymore)
                        total_val = df['Value (INR)'].sum() if 'Value (INR)' in df.columns else 0
                        
                        # Convert to CSV string
                        csv_data = context_df.to_csv(index=False)
                        
                        system_instruction = f"""
                        You are a helpful financial assistant. You have access to the user's portfolio data in CSV format below.
                        
                        Portfolio Context:
                        - Owner: {owner}
                        - Total Portfolio Value: INR {total_val:,.2f}
                        - Note: Only the top 50 assets by value are listed below to save space and avoid rate limits.
                        
                        Data (CSV):
                        {csv_data}
                        
                        Instructions:
                        1. Answer the user's question based strictly on the provided data.
                        2. Be concise, professional, and friendly.
                        3. If calculations are needed (e.g., percentage of total), perform them based on the data.
                        4. If the user asks for financial advice, politely remind them you are an AI and this is not professional advice.
                        """
                        
                        with st.spinner("Thinking..."):
                            # Use Llama 3.3 70B on Groq (Current versatile model)
                            completion = client.chat.completions.create(
                                model="llama-3.3-70b-versatile",
                                messages=[
                                    {"role": "system", "content": system_instruction},
                                    {"role": "user", "content": prompt}
                                ],
                                temperature=0.5,
                                max_tokens=1024,
                                top_p=1,
                                stream=False,
                                stop=None,
                            )
                            answer = completion.choices[0].message.content
                        
                        with st.chat_message("assistant"):
                            st.markdown(answer)
                        st.session_state.messages.append({"role": "assistant", "content": answer})
                    
                    except Exception as e:
                        st.error(f"Error generating response: {e}")
            except Exception as e:
                 st.error(f"Error configuring Groq API: {e}")
        else:
            st.warning("⚠️ Groq API Key is not configured. Please go to the **Settings** tab to enter your API key.")

else:
    st.info("👋 Welcome! Select an Owner in the sidebar and append their data.")