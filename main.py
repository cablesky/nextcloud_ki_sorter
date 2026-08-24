import sys
import argparse
import logging
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import uvicorn

from config import Config
from nc_client import NextcloudClient
from services import DocumentSorterService, ProcessResponse

# Logging einrichten
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

app = FastAPI(
    title="Nextcloud KI-Sorter",
    description="Automatisierte OCR- und KI-basierte Einsortierung von PDF-Dokumenten in Nextcloud (Gemini, Ollama)",
    version="1.2.0"
)

class ProcessRequest(BaseModel):
    file_path: str
    user: Optional[str] = None

@app.get("/")
def health_check():
    return {
        "status": "ok", 
        "service": "Nextcloud KI-Sorter",
        "ai_provider": Config.AI_PROVIDER
    }

@app.post("/process", response_model=ProcessResponse)
def process_webhook(request: ProcessRequest, background_tasks: BackgroundTasks):
    """Webhook Endpoint für Nextcloud Workflow External Scripts."""
    if not request.file_path.lower().endswith(".pdf"):
        return ProcessResponse(
            status="skipped", 
            original_path=request.file_path, 
            reasoning="Datei ist kein PDF"
        )

    clean_path = NextcloudClient.normalize_inbox_path(request.file_path, request.user)
    inbox = Config.INBOX_FOLDER.strip("/")

    if not clean_path.startswith(inbox):
        logger.info(f"Überspringe '{request.file_path}': Datei liegt nicht im Inbox-Ordner '{inbox}'")
        return ProcessResponse(
            status="skipped",
            original_path=request.file_path,
            reasoning=f"Datei liegt nicht im Inbox-Ordner '{inbox}'"
        )
        
    result = DocumentSorterService.process_document(clean_path, request.user)
    if result.status == "error":
        raise HTTPException(status_code=500, detail=result.reasoning)
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nextcloud KI-Sorter CLI / Server")
    parser.add_argument("--file", help="Relativer Nextcloud Dateipfad zum Verarbeiten (CLI-Modus)")
    parser.add_argument("--user", help="Nextcloud Benutzername (CLI-Modus)")
    parser.add_argument("--server", action="store_true", help="FastAPI Server starten")
    
    args = parser.parse_args()

    # Konfiguration beim Starten prüfen und validieren
    try:
        Config.validate()
        logger.info("Konfigurationsvalidierung erfolgreich.")
    except Exception as err:
        logger.error(f"Konfigurationsfehler beim Start: {err}")
        if not args.file:
            sys.exit(1)

    if args.file:
        res = DocumentSorterService.process_document(args.file, args.user)
        print(res.model_dump_json(indent=2))
    else:
        logger.info(f"Starte Webhook Server auf {Config.HOST}:{Config.PORT} mit AI_PROVIDER='{Config.AI_PROVIDER}'...")
        uvicorn.run(app, host=Config.HOST, port=Config.PORT)
