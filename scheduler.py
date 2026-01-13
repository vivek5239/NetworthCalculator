import time
import schedule
import subprocess
import datetime
import os
import sqlalchemy
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Date
from sqlalchemy.orm import declarative_base, sessionmaker

# --- DATABASE SETUP ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.getenv('DB_FILE_PATH', os.path.join(BASE_DIR, 'finance.db'))
DATABASE_URL = f"sqlite:///{DB_FILE}"

Base = declarative_base()

class AppSettings(Base):
    __tablename__ = 'app_settings'
    id = Column(Integer, primary_key=True)
    report_enabled = Column(Boolean, default=False)
    report_time = Column(String, default="18:00")
    last_run_date = Column(Date, nullable=True)

engine = create_engine(DATABASE_URL, connect_args={'timeout': 30})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def run_report():
    print(f"[{datetime.datetime.now()}] Checking if report needs to run...")
    session = SessionLocal()
    settings = session.query(AppSettings).filter(AppSettings.id == 1).first()
    
    if not settings:
        print("Settings not found.")
        session.close()
        return

    if not settings.report_enabled:
        print("Report is disabled.")
        session.close()
        return

    # Check if already run today
    today = datetime.date.today()
    if settings.last_run_date == today:
        print("Report already run today.")
        session.close()
        return

    # Check time
    now_str = datetime.datetime.now().strftime("%H:%M")
    target_time = settings.report_time or "18:00"
    
    # Simple check: if now >= target_time
    # We rely on the loop running frequently. To avoid double running, we check last_run_date.
    # However, if we just check >=, it might run immediately if we restart the container late in the day.
    # A standard cron would run AT the time.
    # Let's try to match the hour/minute closely or just use the logic: "If time passed and not run today".
    
    now_time = datetime.datetime.now().time()
    try:
        h, m = map(int, target_time.split(':'))
        target_time_obj = datetime.time(h, m)
    except:
        target_time_obj = datetime.time(18, 0)
        
    if now_time >= target_time_obj:
        print("Time condition met. Running report...")
        try:
            # Run the report script
            result = subprocess.run(["python", "daily_email_report.py"], cwd=BASE_DIR)
            if result.returncode == 0:
                print("Report finished successfully.")
                settings.last_run_date = today
                session.commit()
            else:
                print("Report script failed.")
        except Exception as e:
            print(f"Error running report subprocess: {e}")
    else:
        print(f"Not yet time. Target: {target_time}, Now: {now_str}")

    session.close()

if __name__ == "__main__":
    print("Scheduler started...")
    while True:
        # Check every minute
        run_report()
        time.sleep(60)
