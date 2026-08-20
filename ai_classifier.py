import os
import re
import abc
import json
import logging
from typing import Dict, List
from pydantic import BaseModel, Field
from config import Config

logger = logging.getLogger("ai_classifier")

def sanitize_filename(filename: str, fallback: str = "dokument.pdf") -> str:
    """Bereinigt den Dateinamen von ungültigen Zeichen und erzwingt .pdf Endung."""
    if not filename:
        return fallback
    # Nur Dateinamen-Teil ohne Ordnerpfade zulassen
    base_name = os.path.basename(filename.replace("\\", "/"))
    # Ungültige Zeichen für Dateisysteme & WebDAV entfernen
    cleaned = re.sub(r'[\\/:*?"<>|]', '_', base_name).strip()
    cleaned = re.sub(r'\s+', '_', cleaned)
    cleaned = re.sub(r'_+', '_', cleaned)
    
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned}.pdf"
    return cleaned

def sanitize_target_folder(folder: str, default_root: str = Config.TARGET_ROOT_FOLDER) -> str:
    """Stellt sicher, dass der Zielordner mit / beginnt und sauber formatiert ist."""
    if not folder or not folder.strip():
        return default_root
    cleaned = folder.strip()
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    return cleaned.rstrip("/")

class ClassificationResult(BaseModel):
    target_folder: str = Field(
        description="Der am besten passende Zielordner aus der vorgegebenen Ordnerstruktur (z. B. /Dokumente/Versicherungen/Auto). Falls keiner passt, wähle den nächsthöheren passenden Hauptordner."
    )
    suggested_filename: str = Field(
        description="Der neu formatierte Dateiname mit .pdf Endung. Er MUSS mit dem Datum im Format YYYY-MM (Jahr und Monat, OHNE Tag!) beginnen (z. B. YYYY-MM_Absender_Dokumententyp.pdf)."
    )
    reasoning: str = Field(
        default="Lokale KI Einsortierung",
        description="Kurze Erläuterung (1-2 Sätze), warum dieser Ordner und Dateiname gewählt wurden."
    )

    def sanitize(self, original_filename: str) -> "ClassificationResult":
        self.suggested_filename = sanitize_filename(self.suggested_filename, fallback=original_filename)
        self.target_folder = sanitize_target_folder(self.target_folder)
        return self

class BaseAIClassifier(abc.ABC):
    """Abstrakte Basisklasse für KI-Dokumenten-Klassifizierer."""

    @abc.abstractmethod
    def classify_document(
        self, 
        document_text: str, 
        current_filename: str, 
        folder_structure: Dict[str, List[str]]
    ) -> ClassificationResult:
        pass

    def _build_folder_overview(self, folder_structure: Dict[str, List[str]]) -> str:
        folder_overview_lines = []
        for folder_path, sample_files in folder_structure.items():
            samples_str = ", ".join(sample_files) if sample_files else "keine Dateien vorhanden"
            folder_overview_lines.append(f"- Ordner: {folder_path}\n  Beispiel-Dateinamen: [{samples_str}]")
        return "\n".join(folder_overview_lines)

class GeminiClassifier(BaseAIClassifier):
    """Modul zur Dokumenten-Klassifizierung mittels Google Gemini API."""

    def __init__(self):
        if not Config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY ist nicht in der Konfiguration vorhanden.")
        from google import genai
        self.genai = genai
        self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
        self.model = Config.GEMINI_MODEL

    def classify_document(
        self, 
        document_text: str, 
        current_filename: str, 
        folder_structure: Dict[str, List[str]]
    ) -> ClassificationResult:
        from google.genai import types
        folder_overview = self._build_folder_overview(folder_structure)

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
            result = ClassificationResult.model_validate_json(result_json).sanitize(current_filename)
            logger.info(f"Gemini Ergebnis: Ordner='{result.target_folder}', Name='{result.suggested_filename}'")
            logger.info(f"KI-Begründung: {result.reasoning}")
            return result

        except Exception as e:
            logger.error(f"Fehler bei der Anfrage an Gemini: {e}")
            return ClassificationResult(
                target_folder=Config.TARGET_ROOT_FOLDER,
                suggested_filename=current_filename,
                reasoning=f"Fehler bei Gemini-Klassifizierung: {str(e)}"
            ).sanitize(current_filename)

class OllamaClassifier(BaseAIClassifier):
    """Modul zur Dokumenten-Klassifizierung mittels lokaler Ollama-Instanz."""

    def __init__(self):
        if not Config.OLLAMA_BASE_URL:
            raise ValueError("OLLAMA_BASE_URL ist nicht in der Konfiguration vorhanden.")
        self.base_url = Config.OLLAMA_BASE_URL
        self.model = Config.OLLAMA_MODEL
        self.api_key = Config.OLLAMA_API_KEY

    def classify_document(
        self, 
        document_text: str, 
        current_filename: str, 
        folder_structure: Dict[str, List[str]]
    ) -> ClassificationResult:
        import requests
        folder_overview = self._build_folder_overview(folder_structure)

        prompt = f"""
Sortiere dieses Dokument in die Nextcloud-Struktur ein.
Dateiname: {current_filename}

Verfügbare Ordner:
{folder_overview}

Dokumententext:
{document_text[:3000]}

Regeln:
1. Wähle den besten Zielordner aus der Liste.
2. Dateiname MUSS im Format 'YYYY-MM_Absender_Typ.pdf' sein (nur Jahr-Monat!).
3. Gib AUSSCHLIESSLICH ein valides JSON-Objekt ohne Erklärungen oder Markdown-Formatierung aus!
JSON-Schema:
{{"target_folder": "string", "suggested_filename": "string", "reasoning": "string"}}
"""

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system", 
                    "content": "Du bist ein extrem schneller Assistent. Gib AUSSCHLIESSLICH das angeforderte JSON-Objekt zurück. KEINE Erklärungen oder Begründungen."
                },
                {"role": "user", "content": prompt}
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 120
            }
        }

        logger.info(f"Sende Anfrage an lokale KI (Ollama model={self.model} url={self.base_url})...")
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=60)
            if res.status_code != 200:
                raise Exception(f"Ollama API antwortete mit Status {res.status_code}: {res.text}")
            
            res_data = res.json()
            content = res_data.get("message", {}).get("content", "").strip()
            if content.startswith("```"):
                content = content.strip("`").removeprefix("json").strip()
            
            result = ClassificationResult.model_validate_json(content).sanitize(current_filename)
            if not result.reasoning:
                result.reasoning = "Klassifiziert durch lokale KI (Ollama)"
            logger.info(f"Ollama Ergebnis: Ordner='{result.target_folder}', Name='{result.suggested_filename}'")
            return result

        except Exception as e:
            logger.error(f"Fehler bei der Anfrage an Ollama: {e}")
            return ClassificationResult(
                target_folder=Config.TARGET_ROOT_FOLDER,
                suggested_filename=current_filename,
                reasoning=f"Fehler bei Ollama-Klassifizierung: {str(e)}"
            ).sanitize(current_filename)

def get_ai_classifier() -> BaseAIClassifier:
    """Factory-Funktion zur Auswahl des konfigurierten KI-Classifiers."""
    provider = Config.AI_PROVIDER.lower()
    if provider == "gemini":
        return GeminiClassifier()
    elif provider == "ollama":
        return OllamaClassifier()
    else:
        raise ValueError(f"Unbekannter AI_PROVIDER: '{Config.AI_PROVIDER}'. Erlaubt sind: gemini, ollama")
