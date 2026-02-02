import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sqlalchemy
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import pandas as pd
import datetime
import yfinance as yf
import os
import sys

# --- DATABASE SETUP ---
# Use absolute path to ensure cron/task scheduler finds the DB correctly
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
    original_currency = Column(String, nullable=True)
    original_unit_price = Column(Float, nullable=True)
    daily_change_pct = Column(Float, nullable=True)
    avg_buy_price = Column(Float, nullable=True)

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
    gotify_url = Column(String, nullable=True)
    gotify_token = Column(String, nullable=True)
    gotify_enabled = Column(sqlalchemy.Boolean, default=False)
    groq_api_key = Column(String, nullable=True)

engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- LOGIC ---

def get_settings():
    session = SessionLocal()
    settings = session.query(AppSettings).filter(AppSettings.id == 1).first()
    if not settings:
        print("Settings not found in DB.")
        session.close()
        return None
    
    config = {
        'SMTP_SERVER': settings.smtp_server,
        'SMTP_PORT': settings.smtp_port,
        'SENDER_EMAIL': settings.sender_email,
        'SENDER_PASSWORD': settings.sender_password,
        'RECEIVER_EMAIL': settings.receiver_email,
        'GOTIFY_URL': settings.gotify_url,
        'GOTIFY_TOKEN': settings.gotify_token,
        'GOTIFY_ENABLED': settings.gotify_enabled,
        'GROQ_API_KEY': settings.groq_api_key
    }
    session.close()
    return config

def get_ai_summary(data, config):
    """
    Generates a daily portfolio summary using Groq AI.
    """
    if not config.get('GROQ_API_KEY'):
        return None

    try:
        from groq import Groq
        client = Groq(api_key=config['GROQ_API_KEY'])
        
        # Prepare context using Top Value Movers as they are most impactful
        movers_context = ""
        
        # Add Top Gainers (Value)
        if not data['top_val_gainers'].empty:
            movers_context += "\nTop Gainers (Value Impact):\n"
            for _, row in data['top_val_gainers'].head(5).iterrows():
                movers_context += f"- {row['name']}: +₹{row['daily_change_value']:,.2f} ({row['daily_change_pct']:.2f}%)\n"
        
        # Add Top Losers (Value)
        if not data['top_val_losers'].empty:
            movers_context += "\nTop Losers (Value Impact):\n"
            for _, row in data['top_val_losers'].head(5).iterrows():
                movers_context += f"- {row['name']}: ₹{row['daily_change_value']:,.2f} ({row['daily_change_pct']:.2f}%)\n"

        prompt = f"""
        You are a financial portfolio analyst. Analyze today's portfolio performance based on the data below.

        **Portfolio Stats:**
        - Total Net Worth: ₹ {data['net_worth']:,.2f}
        - Daily Change: ₹ {data['change_val']:+,.2f} ({data['change_pct']:+.2f}%)
        - Monthly Change: {data['month_change_pct']:+.2f}%
        
        **Market Movers (Drivers of Change):**
        {movers_context}

        **Instructions:**
        1. Start with a clear statement: "The portfolio [increased/decreased] by [X]% today."
        2. Explain the **primary cause** of this change based on the movers (e.g., "This growth was primarily driven by a rally in [Stock A] and [Stock B]" or "The decline was largely due to a drop in [Stock C]").
        3. Keep it concise (max 3-4 sentences). Professional but easy to read.
        """
        
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=250
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error generating AI summary: {e}")
        return None

def send_gotify(data, config, ai_summary=None):
    if not config.get('GOTIFY_ENABLED') or not config.get('GOTIFY_URL') or not config.get('GOTIFY_TOKEN'):
        return

    import requests
    
    title = f"Daily Report: ₹ {data['net_worth']:,.0f} ({data['change_pct']:+.2f}%)"
    
    # Simple markdown message
    message = (
        f"**Net Worth:** ₹ {data['net_worth']:,.2f}\n"
        f"**Daily Change:** ₹ {data['change_val']:+,.2f} ({data['change_pct']:+.2f}%)\n"
        f"**Month Change:** {data['month_change_pct']:+.2f}%\n\n"
    )
    
    if ai_summary:
        message += f"**📝 AI Analysis:**\n{ai_summary}\n\n"

    # Use Value-based movers for notification as they are more relevant
    top_gainer = data['top_val_gainers'].iloc[0] if not data['top_val_gainers'].empty else None
    top_loser = data['top_val_losers'].iloc[0] if not data['top_val_losers'].empty else None

    if top_gainer is not None:
        message += f"**Top Gainer:** {top_gainer['name']} (+₹{top_gainer['daily_change_value']:,.0f})\n"
    
    if top_loser is not None:
        message += f"**Top Loser:** {top_loser['name']} (₹{top_loser['daily_change_value']:,.0f})\n\n"
    
    # Add links to notification
    message += "🔗 [Grafana Dashboard](https://grafana.rvitservices.uk/d/c1e8e5e5-3e4b-4f67-9d7c-6d8b4e7e9b9c/finance-portfolio-overview?orgId=1&from=now-30d&to=now&timezone=browser&refresh=auto)\n"
    message += "🏠 [Web UI](http://172.23.177.144:8502/)"
    
    url = f"{config['GOTIFY_URL']}/message?token={config['GOTIFY_TOKEN']}"
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
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("Gotify notification sent successfully!")
        else:
            print(f"Failed to send Gotify notification: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Error sending Gotify notification: {e}")

def resolve_ticker_from_yahoo(query):
    import requests # Ensure requests is available locally if not global
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

def update_prices_headless():
    """Updates prices without UI interaction."""
    session = SessionLocal()
    # Fetch assets with ticker OR isin
    assets = session.query(Asset).filter((Asset.ticker.isnot(None)) | (Asset.isin.isnot(None))).all()
    print(f"Updating prices for {len(assets)} assets...")
    
    updated_count = 0
    for asset in assets:
        # Auto-resolve Ticker if missing but ISIN exists
        if (not asset.ticker or not asset.ticker.strip()) and asset.isin:
            print(f"Attempting to resolve ticker for ISIN: {asset.isin} ({asset.name})")
            found_ticker = resolve_ticker_from_yahoo(asset.isin)
            if found_ticker:
                print(f"Found ticker: {found_ticker}")
                asset.ticker = found_ticker
                session.commit()
            else:
                continue # Skip if still no ticker

        if asset.ticker and asset.ticker.strip():
            try:
                ticker = yf.Ticker(asset.ticker)
                price = None
                prev_close = None
                
                hist = ticker.history(period="5d")
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    if len(hist) >= 2:
                        prev_close = hist['Close'].iloc[-2]
                
                if not price:
                    info = ticker.info
                    price = info.get('currentPrice') or info.get('regularMarketPrice')
                    prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')

                if price and price > 0:
                    asset.unit_price = price
                    asset.last_updated = datetime.datetime.now()
                    
                    if prev_close and prev_close > 0:
                        change = ((price - prev_close) / prev_close) * 100
                        asset.daily_change_pct = change
                    updated_count += 1
            except Exception as e:
                print(f"Failed to update {asset.ticker}: {e}")
                
    session.commit()
    session.close()
    print(f"Updated {updated_count} assets.")

def get_portfolio_data():
    engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})
    df = pd.read_sql(sessionmaker(bind=engine)().query(Asset).statement, sessionmaker(bind=engine)().bind)
    if not df.empty:
        df['Value (INR)'] = df['quantity'] * df['unit_price']
    return df

def get_history_data():
    engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})
    return pd.read_sql(sessionmaker(bind=engine)().query(PortfolioHistory).statement, sessionmaker(bind=engine)().bind)

def generate_report():
    update_prices_headless()
    
    df = get_portfolio_data()
    if df.empty:
        return None, None

    # --- Net Worth & Change ---
    total_net_worth = df['Value (INR)'].sum()
    
    # Calculate History Change
    hist_df = get_history_data()
    daily_change_val = 0
    daily_change_pct = 0
    
    if not hist_df.empty:
        hist_df = hist_df.sort_values('date')
        if len(hist_df) >= 1:
            prev_val = hist_df.iloc[-1]['total_value'] # Last recorded
            # If the last record is today, compare with yesterday (2nd last)
            today = datetime.date.today()
            if hist_df.iloc[-1]['date'] == today and len(hist_df) >= 2:
                prev_val = hist_df.iloc[-2]['total_value']
            
            daily_change_val = total_net_worth - prev_val
            daily_change_pct = (daily_change_val / prev_val) * 100 if prev_val > 0 else 0

    # Monthly Change
    month_change_val = 0
    month_change_pct = 0
    month_ago = datetime.date.today() - datetime.timedelta(days=30)
    
    if not hist_df.empty:
        # Find closest date <= 30 days ago
        past_records = hist_df[hist_df['date'] <= month_ago]
        if not past_records.empty:
            month_val = past_records.iloc[-1]['total_value']
            month_change_val = total_net_worth - month_val
            month_change_pct = (month_change_val / month_val) * 100 if month_val > 0 else 0
        else:
            # If less than 30 days, use inception
            start_val = hist_df.iloc[0]['total_value']
            month_change_val = total_net_worth - start_val
            month_change_pct = (month_change_val / start_val) * 100 if start_val > 0 else 0

    # Overall Change (Total Growth)
    total_change_val = 0
    total_change_pct = 0
    if not hist_df.empty:
        start_val = hist_df.iloc[0]['total_value']
        total_change_val = total_net_worth - start_val
        total_change_pct = (total_change_val / start_val) * 100 if start_val > 0 else 0

    # Save today's value if not already saved (or update it)
    session = SessionLocal()
    today = datetime.date.today()
    entry = session.query(PortfolioHistory).filter(PortfolioHistory.date == today).first()
    if entry:
        entry.total_value = total_net_worth
    else:
        entry = PortfolioHistory(date=today, total_value=total_net_worth)
        session.add(entry)
    session.commit()
    session.close()

    # --- Highlights (Aggregated Family View) ---
    highlights_df = df.copy()
    
    # 1. Calculate per-row value change
    highlights_df['prev_price_est'] = highlights_df['unit_price'] / (1 + (highlights_df['daily_change_pct'].fillna(0) / 100))
    highlights_df['daily_change_value'] = (highlights_df['unit_price'] - highlights_df['prev_price_est']) * highlights_df['quantity']
    
    # 2. Normalize Ticker
    highlights_df['ticker'] = highlights_df['ticker'].fillna('').str.strip().str.upper()
    
    # 3. Aggregate by Ticker
    valid_tickers = highlights_df[highlights_df['ticker'] != '']
    
    if not valid_tickers.empty:
        daily_grouped = valid_tickers.groupby('ticker', as_index=False).agg({
            'name': 'first',
            'quantity': 'sum',
            'unit_price': 'first',
            'daily_change_pct': 'first',
            'daily_change_value': 'sum'
        })
    else:
        daily_grouped = pd.DataFrame(columns=['name', 'quantity', 'unit_price', 'daily_change_pct', 'daily_change_value'])
        
    daily_active = daily_grouped[daily_grouped['daily_change_pct'].notna()]
    
    # --- Top 8 Lists ---
    
    # By Value
    top_val_gainers = daily_active.sort_values('daily_change_value', ascending=False).head(8)
    top_val_losers = daily_active.sort_values('daily_change_value', ascending=True).head(8)
    
    # By Percentage
    top_pct_gainers = daily_active.sort_values('daily_change_pct', ascending=False).head(8)
    top_pct_losers = daily_active.sort_values('daily_change_pct', ascending=True).head(8)
    
    return {
        'net_worth': total_net_worth,
        'change_val': daily_change_val,
        'change_pct': daily_change_pct,
        'month_change_val': month_change_val,
        'month_change_pct': month_change_pct,
        'total_change_val': total_change_val,
        'total_change_pct': total_change_pct,
        'top_val_gainers': top_val_gainers,
        'top_val_losers': top_val_losers,
        'top_pct_gainers': top_pct_gainers,
        'top_pct_losers': top_pct_losers
    }

def format_price(amount, currency):
    symbol = "₹"
    if currency == "GBP":
        symbol = "£"
    elif currency == "USD":
        symbol = "$"
    return f"{symbol}{amount:,.2f}"

def send_email(data, config, ai_summary=None):
    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"Daily Portfolio Report: ₹ {data['net_worth']:,.0f} ({data['change_pct']:+.2f}%)"
    msg['From'] = config['SENDER_EMAIL']
    msg['To'] = config['RECEIVER_EMAIL']

    # Include AI Summary in HTML if available
    ai_html = ""
    if ai_summary:
        ai_html = f"""
        <div style="background-color: #e8f4fd; padding: 15px; border-radius: 5px; margin-bottom: 20px; border-left: 5px solid #2196F3;">
            <h3 style="margin-top: 0; color: #0d47a1;">🤖 AI Analysis</h3>
            <p style="font-style: italic; color: #333;">{ai_summary}</p>
        </div>
        """

    # Helper to generate table rows
    def make_rows(df, is_gain=True):
        rows = ""
        for _, row in df.iterrows():
            color = "green" if is_gain else "red"
            sign = "+" if is_gain else ""
            rows += f"""
            <tr>
                <td>{row['name']} <span style='font-size:0.8em; color:#888;'>({row['quantity']:.0f})</span></td>
                <td>{format_price(row['unit_price'], 'INR')}</td>
                <td style='color:{color}'><b>{sign}{format_price(row['daily_change_value'], 'INR')}</b></td>
                <td style='color:{color}'>{row['daily_change_pct']:.2f}%</td>
            </tr>
            """
        return rows

    # HTML Body
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #333;">📊 Portfolio Summary</h2>
        
        {ai_html}

        <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px;">
            <p style="font-size: 1.1em; margin: 5px 0;">
                <strong>Total Net Worth:</strong> <span style="font-size: 1.2em;">₹ {data['net_worth']:,.2f}</span>
            </p>
            <p style="margin: 5px 0;">
                <strong>Daily Change:</strong> 
                <span style="color: {'green' if data['change_val'] >= 0 else 'red'};">
                    ₹ {data['change_val']:+,.2f} ({data['change_pct']:+.2f}%)
                </span>
            </p>
            <p style="margin: 5px 0;">
                <strong>Monthly Change:</strong> 
                <span style="color: {'green' if data['month_change_val'] >= 0 else 'red'};">
                    ₹ {data['month_change_val']:+,.2f} ({data['month_change_pct']:+.2f}%)
                </span>
            </p>
            <p style="margin: 5px 0;">
                <strong>Total Growth:</strong> 
                <span style="color: {'green' if data['total_change_val'] >= 0 else 'red'};">
                    ₹ {data['total_change_val']:+,.2f} ({data['total_change_pct']:+.2f}%)
                </span>
            </p>
        </div>
        
        <hr>
        
        <h3 style="background-color: #e0f2f1; padding: 5px;">💰 Top Movers by Value (₹)</h3>
        
        <h4>🚀 Top Gainers</h4>
        <table style="border-collapse: collapse; width: 100%;">
            <tr style="background-color: #f2f2f2; text-align: left;"><th>Asset</th><th>Price</th><th>Day Change (₹)</th><th>%</th></tr>
            {make_rows(data['top_val_gainers'], True)}
        </table>
        
        <h4>🔻 Top Losers</h4>
        <table style="border-collapse: collapse; width: 100%;">
            <tr style="background-color: #f2f2f2; text-align: left;"><th>Asset</th><th>Price</th><th>Day Change (₹)</th><th>%</th></tr>
            {make_rows(data['top_val_losers'], False)}
        </table>
        
        <hr>
        
        <h3 style="background-color: #fff3e0; padding: 5px;">📊 Top Movers by Percentage (%)</h3>
        
        <h4>🚀 Top Gainers</h4>
        <table style="border-collapse: collapse; width: 100%;">
            <tr style="background-color: #f2f2f2; text-align: left;"><th>Asset</th><th>Price</th><th>Day Change (₹)</th><th>%</th></tr>
            {make_rows(data['top_pct_gainers'], True)}
        </table>
        
        <h4>🔻 Top Losers</h4>
        <table style="border-collapse: collapse; width: 100%;">
            <tr style="background-color: #f2f2f2; text-align: left;"><th>Asset</th><th>Price</th><th>Day Change (₹)</th><th>%</th></tr>
            {make_rows(data['top_pct_losers'], False)}
        </table>
        
        <div style="margin-top: 30px; padding: 15px; background-color: #f1f8e9; border-radius: 5px; text-align: center;">
            <a href="https://grafana.rvitservices.uk/d/c1e8e5e5-3e4b-4f67-9d7c-6d8b4e7e9b9c/finance-portfolio-overview?orgId=1&from=now-30d&to=now&timezone=browser&refresh=auto" 
               style="display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 5px; margin: 5px;">
               📈 View Grafana Dashboard
            </a>
            <a href="http://172.23.177.144:8502/" 
               style="display: inline-block; padding: 10px 20px; background-color: #2196F3; color: white; text-decoration: none; border-radius: 5px; margin: 5px;">
               🏠 Open Web UI
            </a>
        </div>
        
        <p style="font-size: small; color: #888; margin-top: 20px;">Generated by FinanceApp Clone</p>
    </body>
    </html>
    """
    
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(config['SMTP_SERVER'], config['SMTP_PORT']) as server:
            server.starttls()
            server.login(config['SENDER_EMAIL'], config['SENDER_PASSWORD'])
            server.sendmail(config['SENDER_EMAIL'], config['RECEIVER_EMAIL'], msg.as_string())
        print("Email sent successfully!")
    except Exception as e:
        print(f"Error sending email: {e}")

if __name__ == "__main__":
    print("Generating Daily Report...")
    config = get_settings()
    
    if not config:
        print("❌ Settings not found.")
        sys.exit(1)
        
    # Check if at least one notification method is configured
    email_configured = config.get('SENDER_EMAIL') and config.get('SENDER_PASSWORD')
    gotify_configured = config.get('GOTIFY_ENABLED') and config.get('GOTIFY_URL') and config.get('GOTIFY_TOKEN')
    
    if not email_configured and not gotify_configured:
        print("❌ Configuration needed: Please configure Email OR Gotify in 'Settings'.")
        sys.exit(1)
        
    data = generate_report()
    if data:
        # Generate AI Summary
        ai_summary = get_ai_summary(data, config)
        
        if email_configured:
            send_email(data, config, ai_summary)
        if gotify_configured:
            send_gotify(data, config, ai_summary)
    else:
        print("No data found to generate report.")
