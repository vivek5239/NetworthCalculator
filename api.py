from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from typing import List, Optional
import datetime
import os

# --- Configuration ---
# Point to the same database used by the Streamlit app
# Use Environment Variable for Docker flexibility, else default relative path
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

class PortfolioHistory(Base):
    __tablename__ = 'portfolio_history'
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    total_value = Column(Float, nullable=False)

engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- Pydantic Models (For API Responses) ---
class AssetSchema(BaseModel):
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

    class Config:
        from_attributes = True

class HistorySchema(BaseModel):
    date: datetime.date
    total_value: float

    class Config:
        from_attributes = True

# --- FastAPI App ---
app = FastAPI(title="Finance Portfolio API")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
def read_root():
    return {"message": "Welcome to the Finance Portfolio API. Go to /docs for Swagger UI."}

@app.get("/assets", response_model=List[AssetSchema])
def get_all_assets():
    """
    Retrieve all assets from the database.
    """
    session = SessionLocal()
    assets = session.query(Asset).all()
    
    # Enrich with calculated value
    results = []
    for asset in assets:
        schema = AssetSchema.model_validate(asset)
        schema.current_value_inr = asset.quantity * asset.unit_price
        results.append(schema)
        
    session.close()
    return results

@app.get("/assets/{owner}", response_model=List[AssetSchema])
def get_assets_by_owner(owner: str):
    """
    Retrieve assets filtered by owner (e.g., 'Vivek', 'Wife').
    """
    session = SessionLocal()
    assets = session.query(Asset).filter(Asset.owner == owner).all()
    
    results = []
    for asset in assets:
        schema = AssetSchema.model_validate(asset)
        schema.current_value_inr = asset.quantity * asset.unit_price
        results.append(schema)
        
    session.close()
    return results

@app.get("/history", response_model=List[HistorySchema])
def get_portfolio_history():
    """
    Retrieve historical total net worth data.
    """
    session = SessionLocal()
    history = session.query(PortfolioHistory).order_by(PortfolioHistory.date).all()
    session.close()
    return history

if __name__ == "__main__":
    import uvicorn
    # Run on port 8000 by default
    uvicorn.run(app, host="0.0.0.0", port=8000)
