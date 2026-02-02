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
import sys
import argparse

# --- Configuration ---
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
    avg_buy_price = Column(Float, nullable=True) # Added to match app.py

class AppSettings(Base):
    __tablename__ = 'app_settings'
    id = Column(Integer, primary_key=True)
    groq_api_key = Column(String, nullable=True)
    gotify_url = Column(String, nullable=True)
    gotify_token = Column(String, nullable=True)
    gotify_enabled = Column(Boolean, default=False)
    ai_context_columns = Column(String, default="name,ticker,quantity,unit_price,Value (INR),daily_change_pct")
    notification_threshold = Column(Float, default=5.0)

class PortfolioChangeHistory(Base):
    __tablename__ = 'portfolio_change_history'
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, unique=True)
    daily_change_value = Column(Float, nullable=True)
    daily_change_percent = Column(Float, nullable=True)
    monthly_change_value = Column(Float, nullable=True)
    monthly_change_percent = Column(Float, nullable=True)

engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- Helpers ---
# Global cache to prevent duplicate notifications
# Format: { 'TICKER': last_notified_percentage }
last_notified_prices = {} 
last_total_change = None

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
    ONLY if the summary has changed significantly or total portfolio moved > 0.5%
    or individual stock crossed the threshold.
    """
    global last_notified_prices, last_total_change
    
    settings = get_settings(session)
    if not settings or not settings.groq_api_key or not settings.gotify_enabled:
        return

    # 1. Calculate Total Portfolio Change
    total_val = 0.0
    total_change_val = 0.0
    
    for a in assets:
        val = a.quantity * a.unit_price
        total_val += val
        if a.daily_change_pct is not None:
            prev_val = val / (1 + (a.daily_change_pct / 100))
            total_change_val += (val - prev_val)
            
    total_change_pct = (total_change_val / (total_val - total_change_val)) * 100 if (total_val - total_change_val) > 0 else 0.0
    
    # 2. Check Thresholds
    threshold = settings.notification_threshold or 5.0
    
    print(f"Memory Check: Currently tracking {len(last_notified_prices)} movers. Total Change Ref: {last_total_change}")
    if last_notified_prices:
        print(f"Last Notified: { {t: round(p, 2) for t, p in last_notified_prices.items()} }")

    # Identify current significant movers
    current_significant_movers = [a for a in assets if a.daily_change_pct is not None and abs(a.daily_change_pct) >= threshold]
    
    # Decide if we should notify
    should_notify = False
    assets_to_notify_about = []
    
    # Check 1: Individual Stock Logic
    # Clean up memory: Remove stocks that have calmed down (below threshold)
    current_tickers = set(a.ticker for a in current_significant_movers)
    tickers_to_remove = [t for t in last_notified_prices if t not in current_tickers]
    for t in tickers_to_remove:
        del last_notified_prices[t]
        
    for asset in current_significant_movers:
        ticker = asset.ticker
        current_pct = asset.daily_change_pct
        last_pct = last_notified_prices.get(ticker)
        
        if last_pct is None:
            # Case A: New significant mover
            print(f"Trigger: New mover {asset.name} ({current_pct:.2f}%)")
            should_notify = True
            assets_to_notify_about.append(asset)
            last_notified_prices[ticker] = current_pct
        else:
            # Case B: Already notified, check for re-notify threshold (fixed at 5% deviance)
            # You requested: "if there is a change to that + or - 5 to the previous one"
            if abs(current_pct - last_pct) >= 5.0:
                print(f"Trigger: Re-notify {asset.name} (Prev: {last_pct:.2f}%, Curr: {current_pct:.2f}%)")
                should_notify = True
                assets_to_notify_about.append(asset)
                last_notified_prices[ticker] = current_pct
            else:
                pass # Ignored: Fluctuation is within 5% of last alert

    # Check 2: Global Portfolio Logic
    # Notify if total portfolio moves > 1% AND changes by at least 0.5% from last alert
    current_total_state = round(total_change_pct, 2)
    if abs(total_change_pct) >= 1.0:
        if last_total_change is None or abs(current_total_state - last_total_change) >= 0.5:
            should_notify = True
            print(f"Trigger: Portfolio moved {total_change_pct:.2f}% (Prev: {last_total_change})")
            last_total_change = current_total_state

    if not should_notify:
        print("Skipping notification: No new significant movements.")
        return

    # 3. Generate AI Summary
    sorted_assets = sorted([a for a in assets if a.daily_change_pct is not None], key=lambda x: x.daily_change_pct, reverse=True)
    top_gainers = sorted_assets[:5]
    top_losers = sorted_assets[-5:]
    
    context_lines = [f"Total Portfolio Change: {total_change_pct:+.2f}%"]
    context_lines.append("Asset | Price | Change %")
    for a in top_gainers:
        context_lines.append(f"{a.name} ({a.ticker}) | {a.unit_price:.2f} | +{a.daily_change_pct:.2f}%")
    for a in top_losers:
        if a not in top_gainers:
            context_lines.append(f"{a.name} ({a.ticker}) | {a.unit_price:.2f} | {a.daily_change_pct:.2f}%")
            
    data_str = "\n".join(context_lines)
    prompt = f"Analyze these stock price movements:\n\n{data_str}\n\nTask:\n1. Identify significant fluctuations.\n2. Write a concise, 2-3 sentence summary for a push notification.\n3. If flat, say 'Market is quiet.'\n4. Give only the summary."
    
    try:
        client = Groq(api_key=settings.groq_api_key)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=200
        )
        summary = completion.choices[0].message.content.strip()
        
        # Add links to notification
        full_message = f"{summary}\n\n"
        full_message += "🔗 [Grafana Dashboard](https://grafana.rvitservices.uk/d/c1e8e5e5-3e4b-4f67-9d7c-6d8b4e7e9b9c/finance-portfolio-overview?orgId=1&from=now-30d&to=now&timezone=browser&refresh=auto)\n"
        full_message += "🏠 [Web UI](http://172.23.177.144:8502/)"
            
        send_gotify_alert("📉 Market Update", full_message, settings)
        print(f"Sent AI Notification: {summary}")
    except Exception as e:
        print(f"AI Analysis Failed: {e}")

def get_exchange_rate(from_currency):
    if from_currency == 'INR': return 1.0
    if from_currency == 'GBp':
        try:
            return yf.Ticker("GBPINR=X").history(period="1d")['Close'].iloc[-1]
        except: return 1.0
    try:
        return yf.Ticker(f"{from_currency}INR=X").history(period="1d")['Close'].iloc[-1]
    except: return 1.0

# --- Update Logic ---
def update_prices():
    print(f"[{datetime.datetime.now()}] Starting Price Update...")
    session = SessionLocal()
    
    try:
        assets = session.query(Asset).filter(Asset.ticker.isnot(None)).all()
        updated_count = 0
        updated_assets_for_ai = []
        
        rates_cache = { 'INR': 1.0, 'USD': get_exchange_rate('USD'), 'GBP': get_exchange_rate('GBP'), 'EUR': get_exchange_rate('EUR') }

        for asset in assets:
            if not asset.ticker or not asset.ticker.strip(): continue
            try:
                ticker = yf.Ticker(asset.ticker)
                
                # --- 1. Get Current Price (Robust) ---
                today_price = None
                prev_close_price = None
                currency = 'INR' # Default

                # Try getting fast history first (like app.py)
                try:
                    hist_short = ticker.history(period="5d")
                    if not hist_short.empty:
                        today_price = hist_short['Close'].iloc[-1]
                        if len(hist_short) >= 2:
                            prev_close_price = hist_short['Close'].iloc[-2]
                except Exception as e:
                    print(f"Short history failed for {asset.ticker}: {e}")

                # Fallback to info if history fails
                if today_price is None:
                    try:
                        info = ticker.info
                        today_price = info.get('currentPrice') or info.get('regularMarketPrice')
                        prev_close_price = info.get('previousClose') or info.get('regularMarketPreviousClose')
                        currency = info.get('currency', 'INR')
                    except Exception as e:
                        print(f"Info fallback failed for {asset.ticker}: {e}")

                if today_price is None:
                    print(f"Skipping {asset.ticker}: No price found.")
                    continue

                # --- 2. Update Asset Price ---
                # Attempt to get currency from history metadata if not set
                if currency == 'INR':
                    try:
                        currency = ticker.fast_info.get('currency', 'INR')
                    except: pass

                # Currency Conversion
                native_price = today_price
                price_inr = native_price
                
                if currency == 'GBp':
                    price_inr = (native_price / 100) * rates_cache.get('GBP', 1.0)
                elif currency != 'INR':
                    price_inr = native_price * rates_cache.get(currency, 1.0)

                asset.unit_price = price_inr
                asset.original_unit_price = native_price
                asset.original_currency = currency
                asset.last_updated = datetime.datetime.now()
                
                if prev_close_price and prev_close_price > 0:
                    asset.daily_change_pct = ((today_price - prev_close_price) / prev_close_price) * 100

                # --- 3. Get 30d History (Best Effort) ---
                try:
                    hist_long = ticker.history(period="40d")
                    if not hist_long.empty:
                        target_date = datetime.date.today() - datetime.timedelta(days=30)
                        # Find closest date
                        closest_date = min(hist_long.index, key=lambda d: abs(d.date() - target_date))
                        # Only use if reasonably close (within 5 days)
                        if abs((closest_date.date() - target_date).days) < 5:
                            asset.price_30d = hist_long.loc[closest_date]['Close']
                except Exception as e:
                    # Do not fail the update if 30d history fails
                    print(f"30d history check failed for {asset.ticker}: {e}")
                
                updated_assets_for_ai.append(asset)
                updated_count += 1
            except Exception as e:
                print(f"Error updating {asset.ticker}: {e}")

        # --- Calculate & Store Portfolio Changes ---
        total_value = 0
        total_daily_change_value = 0
        total_monthly_change_value = 0
        total_value_30d_ago = 0
        
        all_updated_assets = session.query(Asset).all() # Re-fetch all assets to get a complete picture
        for asset in all_updated_assets:
            current_val = asset.quantity * asset.unit_price
            total_value += current_val

            if asset.daily_change_pct is not None:
                prev_price = asset.unit_price / (1 + (asset.daily_change_pct / 100))
                total_daily_change_value += (asset.unit_price - prev_price) * asset.quantity
            
            if asset.price_30d is not None and asset.price_30d > 0:
                total_monthly_change_value += (asset.unit_price - asset.price_30d) * asset.quantity
                total_value_30d_ago += asset.price_30d * asset.quantity

        yesterday_total_value = total_value - total_daily_change_value
        daily_pct = (total_daily_change_value / yesterday_total_value) * 100 if yesterday_total_value else 0
        monthly_pct = (total_monthly_change_value / total_value_30d_ago) * 100 if total_value_30d_ago else 0

        # Upsert into history table
        today = datetime.date.today()
        change_record = session.query(PortfolioChangeHistory).filter_by(date=today).first()
        if change_record:
            change_record.daily_change_value = total_daily_change_value
            change_record.daily_change_percent = daily_pct
            change_record.monthly_change_value = total_monthly_change_value
            change_record.monthly_change_percent = monthly_pct
        else:
            change_record = PortfolioChangeHistory(
                date=today,
                daily_change_value=total_daily_change_value,
                daily_change_percent=daily_pct,
                monthly_change_value=total_monthly_change_value,
                monthly_change_percent=monthly_pct
            )
            session.add(change_record)
        
        print(f"[{datetime.datetime.now()}] Logged portfolio changes for {today}.")

        session.commit()
        print(f"Updated {updated_count} assets.")
        
        if updated_count > 0:
            settings = get_settings(session)
            analyze_and_notify(session, updated_assets_for_ai)
            
    except Exception as e:
        print(f"Update Loop Error: {e}")
        session.rollback()
    finally:
        session.close()

def main_loop():
    print("Background Price Updater Started. Schedule: Every 60 minutes.")
    update_prices() # Run once on start
    while True:
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