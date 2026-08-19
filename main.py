import os
import sys
import tempfile
import argparse
import logging
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import uvicorn

from config import Config
from nc_client import NextcloudClient
from ocr_processor import OCRProcessor
from ai_classifier import GeminiClassifier

# Logging einrichten
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

app = FastAPI(
    title="Nextcloud KI-Sorter",
    description="Automatisierte OCR- und Gemini-basierte Einsortierung von PDF-Dokumenten in Nextcloud",
    version="1.0.0"
)

class ProcessRequest(BaseModel):
    file_path: str
    user: Optional[str] = None

class ProcessResponse(BaseModel):
    status: str
    original_path: str
    target_path: Optional[str] = None
    reasoning: Optional[str] = None

def process_file_pipeline(nc_file_path: str, user: Optional[str] = None) -> ProcessResponse:
    """Verarbeitungspipeline für eine hochgeladene PDF-Datei."""
    logger.info(f"== Starte Verarbeitung für: {nc_file_path} (User: {user or Config.NEXTCLOUD_USER}) ==")
    
    nc_client = NextcloudClient(user=user)
    classifier = GeminiClassifier()
    
    # Bereinige Nextcloud-Pfad
    clean_nc_path = nc_file_path.strip("/")
    inbox = Config.INBOX_FOLDER.strip("/")
    if "__groupfolders/" in clean_nc_path:
        if "/files/" in clean_nc_path:
            sub_path = clean_nc_path.split("/files/", 1)[-1]
            clean_nc_path = f"{inbox}/{sub_path}"
        else:
            filename = os.path.basename(clean_nc_path)
            clean_nc_path = f"{inbox}/{filename}"
    elif "files/" in clean_nc_path:
        clean_nc_path = clean_nc_path.split("files/", 1)[-1]
    if user and clean_nc_path.startswith(f"{user}/"):
        clean_nc_path = clean_nc_path[len(user)+1:]

    current_filename = os.path.basename(clean_nc_path)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_input = os.path.join(tmp_dir, current_filename)
        local_output = os.path.join(tmp_dir, f"ocr_{current_filename}")
        
        # 1. Datei aus Nextcloud herunterladen
        try:
            nc_client.download_file(clean_nc_path, local_input)
        except Exception as e:
            logger.error(f"Fehler beim Download: {e}")
            return ProcessResponse(status="error", original_path=clean_nc_path, reasoning=f"Download-Fehler: {e}")

        # 2. OCR durchführen / prüfen
        ocr_pdf, text = OCRProcessor.ensure_ocr(local_input, local_output)
        
        if not text.strip():
            logger.warning(f"Kein Text aus {clean_nc_path} extrahierbar.")

        # Falls OCR eine neue Datei erzeugt hat, wieder zu Nextcloud hochladen vor dem Verschieben
        if ocr_pdf != local_input:
            nc_client.upload_file(ocr_pdf, clean_nc_path)

        # 3. Nextcloud Zielordner-Baum & Beispieldateien scannen
        logger.info(f"Scanne Ordnerstruktur unter '{Config.TARGET_ROOT_FOLDER}'...")
        folder_structure = nc_client.get_folder_structure_and_samples(Config.TARGET_ROOT_FOLDER)

        # 4. KI-Analyse mit Gemini
        classification = classifier.classify_document(
            document_text=text,
            current_filename=current_filename,
            folder_structure=folder_structure
        )
        logger.info(f"KI-Begründung: {classification.reasoning}")

        # Target Pfad zusammensetzen
        target_dir = classification.target_folder.strip("/")
        target_file_path = f"{target_dir}/{classification.suggested_filename}"

        # 5. Verschieben & Umbenennen in Nextcloud per WebDAV
        try:
            nc_client.move_file(clean_nc_path, target_file_path)
            logger.info(f"SUCCESS: Datei einsortiert unter -> {target_file_path}")
            return ProcessResponse(
                status="success",
                original_path=clean_nc_path,
                target_path=target_file_path,
                reasoning=classification.reasoning
            )
        except Exception as e:
            logger.error(f"Fehler beim Verschieben per WebDAV: {e}")
            return ProcessResponse(
                status="error",
                original_path=clean_nc_path,
                reasoning=f"Verschiebe-Fehler: {e}"
            )

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Nextcloud KI-Sorter"}

@app.post("/process", response_model=ProcessResponse)
def process_webhook(request: ProcessRequest, background_tasks: BackgroundTasks):
    """Webhook Endpoint für Nextcloud Workflow External Scripts."""
    if not request.file_path.lower().endswith(".pdf"):
        return ProcessResponse(
            status="skipped", 
            original_path=request.file_path, 
            reasoning="Datei ist kein PDF"
        )

    # Prüfen, ob die Datei im konfigurierten INBOX_FOLDER liegt
    clean_path = request.file_path.strip("/")
    inbox = Config.INBOX_FOLDER.strip("/")
    if "__groupfolders/" in clean_path:
        if "/files/" in clean_path:
            sub_path = clean_path.split("/files/", 1)[-1]
            clean_path = f"{inbox}/{sub_path}"
        else:
            filename = os.path.basename(clean_path)
            clean_path = f"{inbox}/{filename}"
    elif "files/" in clean_path:
        clean_path = clean_path.split("files/", 1)[-1]
    if request.user and clean_path.startswith(f"{request.user}/"):
        clean_path = clean_path[len(request.user)+1:]

    if not clean_path.startswith(inbox):
        logger.info(f"Überspringe '{request.file_path}': Datei liegt nicht im Inbox-Ordner '{inbox}'")
        return ProcessResponse(
            status="skipped",
            original_path=request.file_path,
            reasoning=f"Datei liegt nicht im Inbox-Ordner '{inbox}'"
        )
        
    result = process_file_pipeline(clean_path, request.user)
    if result.status == "error":
        raise HTTPException(status_code=500, detail=result.reasoning)
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nextcloud KI-Sorter CLI / Server")
    parser.add_argument("--file", help="Relativer Nextcloud Dateipfad zum Verarbeiten (CLI-Modus)")
    parser.add_argument("--user", help="Nextcloud Benutzername (CLI-Modus)")
    parser.add_argument("--server", action="store_true", help="FastAPI Server starten")
    
    args = parser.parse_args()

    if args.file:
        res = process_file_pipeline(args.file, args.user)
        print(res.model_dump_json(indent=2))
    else:
        logger.info(f"Starte Webhook Server auf {Config.HOST}:{Config.PORT}...")
        uvicorn.run(app, host=Config.HOST, port=Config.PORT)
