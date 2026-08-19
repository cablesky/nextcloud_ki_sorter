FROM python:3.11-slim

# Installiere Tesseract OCR (inkl. deutschem Sprachpaket), ocrmypdf und System-Tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ocrmypdf \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    ghostscript \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installiere Python-Abhängigkeiten
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere Quellcode
COPY . .

# Setze Environment-Variablen
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

# Starte FastAPI Webhook Server
CMD ["python", "main.py"]
