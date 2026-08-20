import os
import re
import abc
import json
import logging
from datetime import datetime
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
        description="Der am besten passende Zielordner aus der vorgegebenen Ordnerstruktur."
    )
    suggested_filename: str = Field(
        description="Der neu formatierte Dateiname im Format YYYY-MM_Absender_Typ.pdf."
    )
    reasoning: str = Field(
        default="Dokumenten-Klassifizierung",
        description="Kurze Begründung für die Einsortierung."
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
            # Max 2 Beispieldateien pro Ordner für minimale Token-Nutzung
            samples = sample_files[:2] if sample_files else []
            samples_str = ", ".join(samples) if samples else "keine"
            folder_overview_lines.append(f"- {folder_path} (Beispiele: {samples_str})")
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
Sortiere dieses Dokument in die Nextcloud-Ordnerstruktur unter '{Config.TARGET_ROOT_FOLDER}' ein.

Aktueller Dateiname: {current_filename}

Verfügbare Zielordner:
{folder_overview}

Dokumententext (OCR):
---
{document_text[:6000]}
---

Regeln:
1. Wähle den besten Zielordner aus der Liste. Bevorzuge spezifische Unterordner (z.B. /Dokumente/Gutscheine).
2. Generiere einen Dateinamen im Format 'YYYY-MM_Absender_Typ.pdf' (z.B. '2024-05_Lidl_Gutschein.pdf'). Verwende kurze Firmennamen ohne GmbH/AG.
3. Gib das Ergebnis im vorgegebenen JSON-Schema zurück.
"""

        logger.info(f"Sende Anfrage an Google Gemini ({self.model})...")
        
        import time
        max_retries = 3
        for attempt in range(1, max_retries + 1):
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
                err_str = str(e)
                if ("503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str) and attempt < max_retries:
                    logger.warning(f"Gemini Server kurzzeitig überlastet (Versuch {attempt}/{max_retries}). Warte 120s (2 Min.) und versuche erneut...")
                    time.sleep(120)
                    continue

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
Sortiere dieses Dokument in die Nextcloud-Ordnerstruktur ein.

Aktueller Dateiname: {current_filename}

Verfügbare Zielordner:
{folder_overview}

Dokumententext (OCR):
---
{document_text[:3500]}
---

Regeln:
1. Wähle den besten Zielordner aus der Liste oben. Bevorzuge spezifische Unterordner (z.B. /Dokumente/Gutscheine) vor dem Hauptordner.
2. Erstelle einen Dateinamen im Format 'YYYY-MM_Absender_Typ.pdf' (z.B. '2024-05_Lidl_Gutschein.pdf'). Verwende NUR kurze Firmennamen (z.B. 'Lidl' statt 'Lidl Digital GmbH').
3. Gib das Ergebnis als JSON zurück:
{{
  "target_folder": "/Dokumente/Gutscheine",
  "suggested_filename": "2024-05_Lidl_Gutschein.pdf",
  "reasoning": "Gutschein von Lidl"
}}
"""

        json_schema = ClassificationResult.model_json_schema()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system", 
                    "content": "Du bist ein präziser Assistent zur Dokumentenklassifizierung. Antworte AUSSCHLIESSLICH mit dem geforderten JSON-Objekt."
                },
                {"role": "user", "content": prompt}
            ],
            "format": json_schema,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 150
            }
        }

        logger.info(f"Ollama Dokumententext-Länge: {len(document_text)} Zeichen. Ausschnitt: {repr(document_text[:150])}")
        logger.info(f"Sende Anfrage an lokale KI (Ollama model={self.model} url={self.base_url} timeout={Config.OLLAMA_TIMEOUT}s)...")
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=Config.OLLAMA_TIMEOUT)
            if res.status_code == 400 and "format" in res.text.lower():
                logger.warning("Ollama akzeptierte kein JSON-Schema in 'format'. Wechsle auf 'format': 'json'.")
                payload["format"] = "json"
                res = requests.post(url, json=payload, headers=headers, timeout=Config.OLLAMA_TIMEOUT)

            if res.status_code != 200:
                raise Exception(f"Ollama API antwortete mit Status {res.status_code}: {res.text}")
            
            res_data = res.json()
            content = res_data.get("message", {}).get("content", "").strip()
            logger.info(f"Ollama Rohantwort ({len(content)} Zeichen): {content}")

            if content.startswith("```"):
                content = content.strip("`").removeprefix("json").strip()
            
            if not content or content == "{}":
                raise ValueError(f"Ollama hat ein leeres JSON-Objekt '{{}}' zurückgegeben (Modell: {self.model}).")

            # 1. Versuch: Strikte Pydantic Validierung
            try:
                result = ClassificationResult.model_validate_json(content).sanitize(current_filename)
            except Exception as parse_err:
                logger.warning(f"Pydantic Validierung fehlgeschlagen ({parse_err}). Versuche flexibles JSON-Parsing...")
                data = json.loads(content)
                tf = data.get("target_folder") or data.get("targetFolder") or data.get("folder") or Config.TARGET_ROOT_FOLDER
                sf = data.get("suggested_filename") or data.get("suggestedFilename") or data.get("filename") or current_filename
                re_val = data.get("reasoning") or data.get("reason") or "Klassifiziert durch lokale KI (Ollama)"
                result = ClassificationResult(
                    target_folder=tf,
                    suggested_filename=sf,
                    reasoning=re_val
                ).sanitize(current_filename)

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
