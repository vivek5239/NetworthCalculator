FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (required for some python packages like yfinance/pandas/numpy build)
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
# Copy requirements first to leverage cache
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose ports
# 8501: Streamlit default (though we use 8502 usually)
# 8000: API
EXPOSE 8502
EXPOSE 8000

# Create a start script to run both or just the main app
# Ideally, we run them via docker-compose commands, but here is a default entry
CMD ["streamlit", "run", "app.py", "--server.port", "8502", "--server.address", "0.0.0.0"]
