import os
import html
import tempfile
import logging
import threading
import requests
from typing import Optional
from pydantic import BaseModel

from config import Config
from nc_client import NextcloudClient
from ocr_processor import OCRProcessor
from ai_classifier import get_ai_classifier

logger = logging.getLogger("services")

class ProcessResponse(BaseModel):
    status: str
    original_path: str
    target_path: Optional[str] = None
    reasoning: Optional[str] = None

class TelegramNotifier:
    """Service zum Versenden von Benachrichtigungen über Telegram."""

    @staticmethod
    def send(
        original_filename: str,
        target_folder: str,
        suggested_filename: str,
        reasoning: str,
        ai_provider: str,
        created_new_folder: bool = False,
        relocated_count: int = 0
    ) -> bool:
        token = Config.TELEGRAM_BOT_TOKEN
        chat_id = Config.TELEGRAM_CHAT_ID

        if not token or not chat_id:
            logger.debug("Telegram-Benachrichtigung übersprungen (TELEGRAM_BOT_TOKEN oder TELEGRAM_CHAT_ID nicht gesetzt).")
            return False

        orig_esc = html.escape(original_filename)
        folder_esc = html.escape(target_folder)
        sug_esc = html.escape(suggested_filename)
        prov_esc = html.escape(ai_provider)
        reason_esc = html.escape(reasoning) if reasoning else ""

        text = (
            "<b>📄 Neues Dokument einsortiert!</b>\n\n"
            f"<b>📁 Zielordner:</b> <code>{folder_esc}</code>\n"
            f"<b>📝 Neuer Dateiname:</b> <code>{sug_esc}</code>\n"
            f"<b>📑 Original:</b> <code>{orig_esc}</code>\n"
            f"<b>🤖 KI-Anbieter:</b> <code>{prov_esc}</code>\n"
        )
        if created_new_folder:
            text += "✨ <i>Neuer Ordner wurde in Nextcloud erstellt.</i>\n"
        if relocated_count > 0:
            text += f"📦 <i>{relocated_count} bereits vorhandene(s) Dokument(e) dort hinein umsortiert.</i>\n"
        if reason_esc:
            text += f"\n💡 <b>Begründung:</b> {reason_esc}"

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            logger.info(f"Sende Telegram-Benachrichtigung an Chat ID {chat_id}...")
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Telegram-Benachrichtigung erfolgreich gesendet.")
                return True
            else:
                logger.warning(f"Fehler beim Senden der Telegram-Nachricht ({response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"Ausnahme beim Senden der Telegram-Benachrichtigung: {e}")
            return False

class DocumentSorterService:
    """Zentraler Orchestrierungs-Service für die Dokumentensortierung."""

    _processing_lock = threading.Lock()

    @classmethod
    def process_document(cls, nc_file_path: str, user: Optional[str] = None) -> ProcessResponse:
        """Führt die komplette Verarbeitungs-Pipeline für ein Dokument aus."""
        logger.info(f"== Starte Verarbeitung für: {nc_file_path} (User: {user or Config.NEXTCLOUD_USER}) ==")

        with cls._processing_lock:
            nc_client = NextcloudClient(user=user)
            try:
                classifier = get_ai_classifier()
            except Exception as e:
                logger.error(f"Fehler beim Erstellen des KI-Classifiers ({Config.AI_PROVIDER}): {e}")
                return ProcessResponse(status="error", original_path=nc_file_path, reasoning=f"KI-Konfigurationsfehler: {e}")

            clean_nc_path = nc_client.normalize_inbox_path(nc_file_path, user)
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

                # Falls OCR eine neue Datei erzeugt hat, zu Nextcloud hochladen
                if ocr_pdf != local_input and os.path.exists(ocr_pdf):
                    try:
                        nc_client.upload_file(ocr_pdf, clean_nc_path)
                    except Exception as e:
                        logger.error(f"Fehler beim Upload des OCR-PDFs zu Nextcloud: {e}")

                # 3. Nextcloud Zielordner-Baum scannen (mit Caching)
                logger.info(f"Scanne Ordnerstruktur unter '{Config.TARGET_ROOT_FOLDER}'...")
                folder_structure = nc_client.get_folder_structure_and_samples(Config.TARGET_ROOT_FOLDER)

                # 4. KI-Analyse
                classification = classifier.classify_document(
                    document_text=text,
                    current_filename=current_filename,
                    folder_structure=folder_structure
                )
                logger.info(f"KI-Begründung: {classification.reasoning}")

                # Ordner-Neuerstellung & Schwellenwertprüfung
                created_new_folder = False
                relocated_files_count = 0
                actual_target_folder = classification.target_folder

                if classification.is_new_folder and Config.ALLOW_NEW_FOLDERS:
                    total_docs = 1 + len(classification.files_to_relocate)
                    if total_docs >= Config.MIN_DOCUMENTS_FOR_NEW_FOLDER:
                        logger.info(f"Neuer Ordner '{classification.target_folder}' erfüllt Mindestanzahl ({total_docs} >= {Config.MIN_DOCUMENTS_FOR_NEW_FOLDER}). Erstelle Ordner...")
                        nc_client.ensure_directory_exists(classification.target_folder)
                        created_new_folder = True
                        actual_target_folder = classification.target_folder

                        if Config.AUTO_REORGANIZE_EXISTING and classification.files_to_relocate:
                            relocated = nc_client.relocate_files(classification.files_to_relocate, actual_target_folder)
                            relocated_files_count = len(relocated)
                            logger.info(f"{relocated_files_count} bestehende Dokumente wurden in den neuen Ordner '{actual_target_folder}' umsortiert.")
                    else:
                        logger.info(f"Neuer Ordner '{classification.target_folder}' verworfen, da nur {total_docs}/{Config.MIN_DOCUMENTS_FOR_NEW_FOLDER} Dokumente vorhanden. Nutze Ausweichordner '{classification.fallback_folder}'.")
                        actual_target_folder = classification.fallback_folder or Config.TARGET_ROOT_FOLDER
                        classification.reasoning += f" (Neuer Ordner '{classification.target_folder}' verworfen, da nur {total_docs}/{Config.MIN_DOCUMENTS_FOR_NEW_FOLDER} Dokumente vorhanden. Verwende '{actual_target_folder}')"
                else:
                    actual_target_folder = classification.target_folder

                # Target Pfad zusammensetzen
                target_dir = actual_target_folder.strip("/")
                target_file_path = f"{target_dir}/{classification.suggested_filename}"

                # 5. Verschieben & Umbenennen in Nextcloud per WebDAV
                try:
                    _, final_target_file_path = nc_client.move_file(clean_nc_path, target_file_path)
                    logger.info(f"SUCCESS: Datei einsortiert unter -> {final_target_file_path}")

                    final_filename = os.path.basename(final_target_file_path)

                    # Telegram-Benachrichtigung senden
                    TelegramNotifier.send(
                        original_filename=current_filename,
                        target_folder=actual_target_folder,
                        suggested_filename=final_filename,
                        reasoning=classification.reasoning,
                        ai_provider=Config.AI_PROVIDER,
                        created_new_folder=created_new_folder,
                        relocated_count=relocated_files_count
                    )

                    return ProcessResponse(
                        status="success",
                        original_path=clean_nc_path,
                        target_path=final_target_file_path,
                        reasoning=classification.reasoning
                    )
                except Exception as e:
                    logger.error(f"Fehler beim Verschieben per WebDAV: {e}")
                    return ProcessResponse(
                        status="error",
                        original_path=clean_nc_path,
                        reasoning=f"Verschiebe-Fehler: {e}"
                    )
