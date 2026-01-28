from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from typing import List, Optional
import datetime
import os
import yfinance as yf
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import sys
import requests

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.getenv('DB_FILE_PATH', os.path.join(BASE_DIR, 'finance.db'))
DATABASE_URL = f"sqlite:///{DB_FILE}"

# --- Database Setup (Mirroring app.py) ---
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

class TransactionHistory(Base):
    __tablename__ = 'investment_transactions'
    id = Column(Integer, primary_key=True)
    asset_name = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    ticker = Column(String, nullable=True)
    quantity_change = Column(Float, nullable=False)
    price_per_unit = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    owner = Column(String, nullable=False)

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
Base.metadata.create_all(bind=engine)

# --- Pydantic Models (For API Responses) ---
class PriceUpdateRequest(BaseModel):
    ticker: Optional[str] = None
    isin: Optional[str] = None

class PriceUpdateResult(BaseModel):
    asset_id: int
    name: str
    owner: str
    quantity: float
    old_price_inr: float
    new_price_inr: float
    total_value_inr: float
    daily_change_pct: Optional[float]
    day_change_value_inr: float
    overall_gain_loss_inr: Optional[float]
    overall_gain_loss_pct: Optional[float]

class PriceUpdateResponse(BaseModel):
    ticker: str
    original_price: float
    currency: str
    updates: List[PriceUpdateResult]

class AssetSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner: str
    name: str
    dp_name: Optional[str]
    asset_type: str
    currency: str
    quantity: float
    unit_price: float
    isin: Optional[str]
    ticker: Optional[str]
    last_updated: Optional[datetime.datetime]
    current_value_inr: float = 0.0
    day_price_diff: float = 0.0
    day_total_diff: float = 0.0
    day_percent_change: float = 0.0
    avg_buy_price: Optional[float]
    price_30d: Optional[float]
    original_currency: Optional[str]
    original_unit_price: Optional[float]

class HistorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: datetime.date
    total_value: float

class TransactionHistorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_name: str
    date: datetime.date
    ticker: Optional[str]
    quantity_change: float
    price_per_unit: float
    total_amount: float
    owner: str

class PortfolioChangeHistorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: datetime.date
    daily_change_value: Optional[float]
    daily_change_percent: Optional[float]
    monthly_change_value: Optional[float]
    monthly_change_percent: Optional[float]

# --- Helpers ---
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
    if from_currency == 'INR': return 1.0
    if from_currency == 'GBp':
        try:
            return yf.Ticker("GBPINR=X").history(period="1d")['Close'].iloc[-1]
        except: return 1.0
    try:
        return yf.Ticker(f"{from_currency}INR=X").history(period="1d")['Close'].iloc[-1]
    except: return 1.0

# --- FastAPI App ---
app = FastAPI(title="Finance Portfolio API", version="1.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def process_asset_details(asset: Asset) -> AssetSchema:
    schema = AssetSchema.model_validate(asset)
    schema.current_value_inr = asset.quantity * asset.unit_price
    if asset.daily_change_pct is not None:
        schema.day_percent_change = asset.daily_change_pct
        prev_price = asset.unit_price / (1 + (asset.daily_change_pct / 100))
        schema.day_price_diff = asset.unit_price - prev_price
        schema.day_total_diff = schema.day_price_diff * asset.quantity
    return schema

@app.get("/")
def read_root():
    return {"message": "Welcome to the Finance Portfolio API. Go to /docs for Swagger UI."}

@app.get("/api/v1/assets", response_model=List[AssetSchema])
def get_all_assets():
    session = SessionLocal()
    assets = session.query(Asset).all()
    results = [process_asset_details(asset) for asset in assets]
    session.close()
    return results

@app.get("/api/v1/assets/{owner}", response_model=List[AssetSchema])
def get_assets_by_owner(owner: str):
    session = SessionLocal()
    assets = session.query(Asset).filter(Asset.owner == owner).all()
    if not assets:
        raise HTTPException(status_code=404, detail=f"No assets found for owner '{owner}'")
    results = [process_asset_details(asset) for asset in assets]
    session.close()
    return results

@app.post("/api/v1/assets/update-price", response_model=PriceUpdateResponse)
def update_individual_asset_price(request: PriceUpdateRequest):
    session = SessionLocal()
    
    # 1. Resolve Ticker
    target_ticker = request.ticker
    if not target_ticker and request.isin:
        target_ticker = resolve_ticker_from_yahoo(request.isin)
    
    if not target_ticker:
        session.close()
        raise HTTPException(status_code=400, detail="A ticker or valid ISIN must be provided.")

    # 2. Find matching assets in DB
    query = session.query(Asset)
    if request.ticker:
        query = query.filter(Asset.ticker == request.ticker)
    elif request.isin:
        query = query.filter(Asset.isin == request.isin)
    
    assets = query.all()
    if not assets:
        session.close()
        raise HTTPException(status_code=404, detail="No matching assets found in database.")

    # 3. Fetch Price from Yahoo Finance
    try:
        ticker_obj = yf.Ticker(target_ticker)
        hist = ticker_obj.history(period="5d")
        if hist.empty:
            raise Exception("No price history found.")
        
        current_price = hist['Close'].iloc[-1]
        prev_close = hist['Close'].iloc[-2] if len(hist) >= 2 else current_price
        currency = ticker_obj.fast_info.get('currency', 'INR')
    except Exception as e:
        session.close()
        raise HTTPException(status_code=500, detail=f"Yahoo Finance Error: {e}")

    # 4. Currency Conversion
    rate = get_exchange_rate(currency)
    if currency == 'GBp':
        price_inr = (current_price / 100) * rate
    elif currency != 'INR':
        price_inr = current_price * rate
    else:
        price_inr = current_price

    # 5. Update Database Records
    updates = []
    for asset in assets:
        old_price = asset.unit_price
        asset.unit_price = price_inr
        asset.original_unit_price = current_price
        asset.original_currency = currency
        asset.last_updated = datetime.datetime.now()
        
        daily_change_pct = None
        if prev_close > 0:
            daily_change_pct = ((current_price - prev_close) / prev_close) * 100
            asset.daily_change_pct = daily_change_pct
        
        # Calculations for Response
        total_value = asset.quantity * price_inr
        day_change_val = (price_inr - old_price) * asset.quantity
        
        overall_gain_inr = None
        overall_gain_pct = None
        if asset.avg_buy_price and asset.avg_buy_price > 0:
            overall_gain_inr = (price_inr - asset.avg_buy_price) * asset.quantity
            overall_gain_pct = ((price_inr - asset.avg_buy_price) / asset.avg_buy_price) * 100

        updates.append(PriceUpdateResult(
            asset_id=asset.id,
            name=asset.name,
            owner=asset.owner,
            quantity=asset.quantity,
            old_price_inr=old_price,
            new_price_inr=price_inr,
            total_value_inr=total_value,
            daily_change_pct=daily_change_pct,
            day_change_value_inr=day_change_val,
            overall_gain_loss_inr=overall_gain_inr,
            overall_gain_loss_pct=overall_gain_pct
        ))

    session.commit()
    session.close()

    return PriceUpdateResponse(
        ticker=target_ticker,
        original_price=current_price,
        currency=currency,
        updates=updates
    )

@app.get("/api/v1/history", response_model=List[HistorySchema])
def get_portfolio_history():
    session = SessionLocal()
    history = session.query(PortfolioHistory).order_by(PortfolioHistory.date).all()
    session.close()
    return history

@app.get("/api/v1/transactions", response_model=List[TransactionHistorySchema])
def get_transaction_history():
    session = SessionLocal()
    transactions = session.query(TransactionHistory).order_by(TransactionHistory.date.desc()).all()
    session.close()
    return transactions

@app.get("/api/v1/changes", response_model=Optional[PortfolioChangeHistorySchema])
def get_latest_change_summary():
    """
    Returns the most recent daily and monthly change summary from the history table.
    """
    session = SessionLocal()
    latest_change = session.query(PortfolioChangeHistory).order_by(PortfolioChangeHistory.date.desc()).first()
    session.close()
    if not latest_change:
        raise HTTPException(status_code=404, detail="No change history found. Run the background updater first.")
    return latest_change

@app.get("/api/v1/changes/history", response_model=List[PortfolioChangeHistorySchema])
def get_all_change_history():
    """
    Returns the full history of daily and monthly portfolio changes.
    """
    session = SessionLocal()
    history = session.query(PortfolioChangeHistory).order_by(PortfolioChangeHistory.date.desc()).all()
    session.close()
    return history

@app.post("/api/v1/trigger-background-job", status_code=202)
def trigger_background_job():
    try:
        subprocess.Popen([sys.executable, "background_updater.py", "--once"])
        return {"message": "Background update job triggered successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to trigger background job: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
