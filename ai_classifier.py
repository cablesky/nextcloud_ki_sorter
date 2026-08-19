import json
import logging
from typing import Dict, List
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from config import Config

logger = logging.getLogger("ai_classifier")

class ClassificationResult(BaseModel):
    target_folder: str = Field(
        description="Der am besten passende Zielordner aus der vorgegebenen Ordnerstruktur (z. B. /Dokumente/Versicherungen/Auto). Falls keiner passt, wähle den nächsthöheren passenden Hauptordner."
    )
    suggested_filename: str = Field(
        description="Der neu formatierte Dateiname mit .pdf Endung. Er MUSS mit dem Datum im Format YYYY-MM (Jahr und Monat, OHNE Tag!) beginnen (z. B. YYYY-MM_Absender_Dokumententyp.pdf)."
    )
    reasoning: str = Field(
        description="Kurze Erläuterung (1-2 Sätze), warum dieser Ordner und Dateiname gewählt wurden."
    )

class GeminiClassifier:
    """Modul zur Dokumenten-Klassifizierung und Namensgenerierung mittels Google Gemini API."""

    def __init__(self):
        if not Config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY ist nicht in der Konfiguration vorhanden.")
        self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
        self.model = Config.GEMINI_MODEL

    def classify_document(
        self, 
        document_text: str, 
        current_filename: str, 
        folder_structure: Dict[str, List[str]]
    ) -> ClassificationResult:
        """
        Analysiert den Dokumententext, gleicht ihn mit der Nextcloud-Ordnerstruktur und 
        Beispieldateien ab und liefert Ordner & neuen Dateinamen zurück.
        """
        # Ordnerstruktur für den Prompt aufbereiten
        folder_overview_lines = []
        for folder_path, sample_files in folder_structure.items():
            samples_str = ", ".join(sample_files) if sample_files else "keine Dateien vorhanden"
            folder_overview_lines.append(f"- Ordner: {folder_path}\n  Beispiel-Dateinamen: [{samples_str}]")
        
        folder_overview = "\n".join(folder_overview_lines)

        prompt = f"""
Du bist ein präziser Assistent zur Dokumentenarchivierung in Nextcloud.
Deine Aufgabe ist es, ein hochgeladenes Dokument anhand seines Inhalts in die bestehende Nextcloud-Ordnerstruktur unter '{Config.TARGET_ROOT_FOLDER}' einzusortieren und einen passenden Dateinamen zu generieren.

### Aktueller Dateiname:
{current_filename}

### Verfügbare Nextcloud-Ordnerstruktur und Beispieldateien:
{folder_overview}

### Dokumenten-Inhalt (OCR Text):
---
{document_text[:6000]}
---

### Regeln für die Einsortierung und Benennung:
1. Wähle den BESTEN bestehenden Ordner aus der obigen Liste.
2. Das Datum am Anfang des Dateinamens MUSS IMMER im Format YYYY-MM (nur Jahr und Monat, OHNE Tag!) formatiert werden (z. B. '2024-05').
3. Generiere den neuen Dateinamen nach dem Schema: 'YYYY-MM_Absender_Betreff.pdf' (z. B. '2024-05_HUK-Coburg_Beitragsrechnung.pdf').
4. Das Standardmuster lautet IMMER: 'YYYY-MM_Absender_Dokumententyp.pdf'.
5. Gib die Antwort im vorgegebenen JSON-Schema zurück.
"""

        logger.info(f"Sende Anfrage an Google Gemini ({self.model})...")
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ClassificationResult,
                    temperature=0.1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
                )
            )
            
            result_json = response.text
            result = ClassificationResult.model_validate_json(result_json)
            logger.info(f"Gemini Ergebnis: Ordner='{result.target_folder}', Name='{result.suggested_filename}'")
            logger.info(f"KI-Begründung: {result.reasoning}")
            return result

        except Exception as e:
            logger.error(f"Fehler bei der Anfrage an Gemini: {e}")
            # Fallback
            return ClassificationResult(
                target_folder=Config.TARGET_ROOT_FOLDER,
                suggested_filename=current_filename,
                reasoning=f"Fehler bei KI-Klassifizierung: {str(e)}"
            )
