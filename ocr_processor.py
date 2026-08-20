import os
import logging
import subprocess
from typing import Tuple, Optional
from pypdf import PdfReader

logger = logging.getLogger("ocr_processor")

class OCRProcessor:
    """Modul zur Prüfung und Durchführung von OCR (ocrmypdf) auf PDF-Dateien."""

    @staticmethod
    def extract_text(pdf_path: str) -> str:
        """Extrahiert lesbaren Text aus einer PDF-Datei."""
        try:
            reader = PdfReader(pdf_path)
            extracted_pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    extracted_pages.append(text)
            return "\n".join(extracted_pages).strip()
        except Exception as e:
            logger.error(f"Fehler beim Lesen des Textes aus {pdf_path}: {e}")
            return ""

    @classmethod
    def ensure_ocr(cls, input_pdf: str, output_pdf: Optional[str] = None) -> Tuple[str, str]:
        """
        Prüft, ob das PDF bereits durchsuchbar ist. 
        Wenn nicht, wird ocrmypdf ausgeführt.
        Gibt (pfad_zu_verarbeiteter_pdf, extrahierter_text) zurück.
        """
        if not output_pdf:
            output_pdf = input_pdf

        text = cls.extract_text(input_pdf)
        
        # Wenn genügend Text vorhanden ist (> 50 Zeichen), überspringe OCR
        if len(text) >= 50:
            logger.info(f"PDF {input_pdf} enthält bereits durchsuchbaren Text ({len(text)} Zeichen). OCR übersprungen.")
            return input_pdf, text

        logger.info(f"Wenig/kein Text in {input_pdf} gefunden ({len(text)} Zeichen). Starte ocrmypdf...")
        
        try:
            cmd = [
                "ocrmypdf",
                "--skip-text",           # Nur Seiten verarbeiten, die noch keinen Text enthalten
                "-l", "deu+eng",         # Sprachen: Deutsch und Englisch
                "--rotate-pages",        # Automatische Seitendrehung
                "--deskew",              # Schräglagenkorrektur
                input_pdf,
                output_pdf
            ]
            logger.info(f"Führe Befehl aus: {' '.join(cmd)}")
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            if result.returncode not in (0, 6): # 6 bedeutet: Datei ist bereits durchsuchbar
                logger.warning(f"ocrmypdf Warnung/Fehler (Code {result.returncode}): {result.stderr}")
            
            # Prüfen, ob Ausgabedatei tatsächlich erzeugt wurde
            if not os.path.exists(output_pdf):
                logger.warning(f"Ausgabedatei {output_pdf} wurde von ocrmypdf nicht erzeugt. Verwende Eingabedatei {input_pdf}.")
                return input_pdf, text

            new_text = cls.extract_text(output_pdf)
            logger.info(f"OCR abgeschlossen. Extrahierte Textlänge: {len(new_text)} Zeichen.")
            return output_pdf, new_text
            
        except Exception as e:
            logger.error(f"Fehler bei ocrmypdf Ausführung: {e}")
            # Fallback: Ursprüngliche Datei & Text zurückgeben
            return input_pdf, text
