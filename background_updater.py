import time
import datetime
import pytz
import yfinance as yf
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, Date, Text
from sqlalchemy.orm import declarative_base, sessionmaker
import logging
import os
import requests
from groq import Groq

# --- Configuration ---
# Use absolute path to ensure reliability
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.getenv('DB_FILE_PATH', os.path.join(BASE_DIR, 'finance.db'))
DATABASE_URL = f"sqlite:///{DB_FILE}"
UK_TIMEZONE = pytz.timezone('Europe/London')

# --- Database Setup ---
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
    daily_change_pct = Column(Float, nullable=True)
    original_unit_price = Column(Float, nullable=True)
    original_currency = Column(String, nullable=True)
    price_30d = Column(Float, nullable=True)

class AppSettings(Base):
    __tablename__ = 'app_settings'
    id = Column(Integer, primary_key=True)
    # Adding only necessary columns for this script
    groq_api_key = Column(String, nullable=True)
    gotify_url = Column(String, nullable=True)
    gotify_token = Column(String, nullable=True)
    gotify_enabled = Column(Boolean, default=False)
    ai_context_columns = Column(String, default="name,ticker,quantity,unit_price,Value (INR),daily_change_pct")

engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- Helpers ---

# Global cache to prevent duplicate notifications
last_sent_summary = None

def get_settings(session):
    return session.query(AppSettings).filter(AppSettings.id == 1).first()

def send_gotify_alert(title, message, settings):
    if not settings.gotify_enabled or not settings.gotify_url or not settings.gotify_token:
        return

    url = f"{settings.gotify_url}/message?token={settings.gotify_token}"
    payload = {
        "title": title,
        "message": message,
        "priority": 5,
        "extras": {
            "client::display": {
                "contentType": "text/markdown"
            }
        }
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Gotify: {e}")

def analyze_and_notify(session, assets):
    """
    Uses Groq to analyze market moves and sends a Gotify notification
    ONLY if the summary has changed significantly or total portfolio moved > 0.5%.
    """
    global last_sent_summary
    
    settings = get_settings(session)
    if not settings or not settings.groq_api_key or not settings.gotify_enabled:
        return

    # Calculate Total Portfolio Value and Weighted Change
    total_val = 0.0
    total_change_val = 0.0
    
    for a in assets:
        val = a.quantity * a.unit_price
        total_val += val
        if a.daily_change_pct is not None:
            # Change in value = Value * (Pct / 100) / (1 + Pct/100) approx, 
            # or more simply: PrevVal = Val / (1 + pct/100). Change = Val - PrevVal.
            prev_val = val / (1 + (a.daily_change_pct / 100))
            total_change_val += (val - prev_val)
            
    total_change_pct = (total_change_val / (total_val - total_change_val)) * 100 if (total_val - total_change_val) > 0 else 0.0
    
    print(f"Total Portfolio Change: {total_change_pct:.2f}%")

    # Threshold for "Big Difference"
    THRESHOLD = 0.5 # 0.5% move
    
    if abs(total_change_pct) < THRESHOLD:
        print(f"Skipping notification: Portfolio move {total_change_pct:.2f}% is below threshold {THRESHOLD}%")
        # However, if individual stocks moved A LOT, we might still want to notify.
        # Let's keep the old logic for significant movers too.
        significant_movers = [a for a in assets if a.daily_change_pct is not None and abs(a.daily_change_pct) > 2.0]
        if not significant_movers:
             return

    # ... Proceed with AI Summary ...
    
    sorted_assets = sorted([a for a in assets if a.daily_change_pct is not None], key=lambda x: x.daily_change_pct, reverse=True)
    top_gainers = sorted_assets[:5]
    top_losers = sorted_assets[-5:]
    
    # Context Construction
    context_lines = [f"Total Portfolio Change: {total_change_pct:+.2f}%"]
    context_lines.append("Asset | Price | Change %")
    for a in top_gainers:
        context_lines.append(f"{a.name} ({a.ticker}) | {a.unit_price:.2f} | +{a.daily_change_pct:.2f}%")
    for a in top_losers: # handle overlap if list is short
        if a not in top_gainers:
            context_lines.append(f"{a.name} ({a.ticker}) | {a.unit_price:.2f} | {a.daily_change_pct:.2f}%")
            
    # Add a few high value assets for context regardless of move
    # (Simplified logic: just use movers for now to fit user request for "fluctuations")
    
    data_str = "\n".join(context_lines)
    
    prompt = f"""
    Analyze these stock price movements (Top Gainers/Losers for the portfolio):
    
    {data_str}
    
    Task:
    1. Identify any unusual or significant fluctuations.
    2. Write a concise, 2-3 sentence summary suitable for a push notification.
    3. If everything is relatively flat (e.g. < 1%), just say "Market is quiet."
    4. Do not start with "Here is the summary". Just give the summary.
    """
    
    try:
        client = Groq(api_key=settings.groq_api_key)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=200
        )
        summary = completion.choices[0].message.content.strip()
        
        # --- SPAM PREVENTION CHECK ---
        if summary == last_sent_summary:
            print("Skipping notification: Summary is identical to last sent.")
            return
            
        # Send Notification
        send_gotify_alert("📉 Market Update (30m)", summary, settings)
        print(f"Sent AI Notification: {summary}")
        
        # Update cache
        last_sent_summary = summary
        
    except Exception as e:
        print(f"AI Analysis Failed: {e}")

def find_ticker_with_ai(session, asset, settings):
    """
    Asks Groq to find the correct Yahoo Finance ticker.
    Returns new ticker string or None.
    """
    if not settings or not settings.groq_api_key:
        return None
        
    print(f"🤖 AI Fetching Ticker for: {asset.name} (ISIN: {asset.isin})")
    
    prompt = f"""
    The stock asset "{asset.name}" (ISIN: {asset.isin}) failed to load with ticker "{asset.ticker}".
    Please provide the CORRECT Yahoo Finance ticker symbol for this asset.
    - It is likely an Indian stock (.NS or .BO suffix) or Mutual Fund.
    - If it is a Tata Motors DVR, use 'TATAMTRDVR.NS' or 'TATAMOTORS-DVR.NS'.
    - Return ONLY the ticker symbol (e.g., RELIANCE.NS). Do not write sentences.
    """
    
    try:
        client = Groq(api_key=settings.groq_api_key)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=20
        )
        new_ticker = completion.choices[0].message.content.strip()
        
        # Basic validation: must look like a ticker (uppercase, no spaces usually)
        # Yahoo tickers can be "ABC.NS"
        if " " not in new_ticker and len(new_ticker) > 2:
            # Check if different
            if new_ticker != asset.ticker:
                print(f"✨ AI Suggested Correction: {asset.ticker} -> {new_ticker}")
                return new_ticker
    except Exception as e:
        print(f"AI Ticker Search Failed: {e}")
        
    return None

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
        # 100 GBp = 1 GBP.
        # Get GBPINR rate and divide by 100 later, or just return rate for 1 GBp
        # Yahoo symbol for GBP is GBPINR=X
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
        print(f"Could not fetch rate for {from_currency}")
    
    return 1.0

import sys
import argparse

# ... existing imports ...

# --- Update Logic ---
def update_prices():
    print(f"[{datetime.datetime.now()}] Starting Price Update...")
    session = SessionLocal()
    settings = get_settings(session)
    
    try:
        # Fetch assets with ticker OR isin
        assets = session.query(Asset).filter((Asset.ticker.isnot(None)) | (Asset.isin.isnot(None))).all()
        updated_count = 0
        updated_assets = []
        ai_correction_count = 0
        MAX_AI_CORRECTIONS = 3 
        
        # Calculate Total Portfolio Value BEFORE updates (using stale prices)
        # Actually, better to compare Daily Change % of the Total Portfolio after update.
        
        # ... (rest of the fetching logic) ...
        
        # AFTER loop:
        # Calculate weighted average change or total value change?
        # The individual assets have daily_change_pct updated.
        
        session.commit()
        print(f"Updated {updated_count} assets.")
        
        # Trigger AI Analysis
        if updated_count > 0:
            analyze_and_notify(session, updated_assets)
            
    except Exception as e:
        print(f"Update Loop Error: {e}")
    finally:
        session.close()

def main_loop():
    print("Background Price Updater Started.")
    print("Schedule: Every 60 minutes.")
    
    # Run once immediately on start
    update_prices()
    
    while True:
        # Sleep for 60 minutes
        time.sleep(3600)
        update_prices()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()
    
    if args.once:
        update_prices()
    else:
        main_loop()