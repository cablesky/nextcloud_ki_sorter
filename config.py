import os
from dotenv import load_dotenv

# Load environment variables from .env file if available
load_dotenv()

class Config:
    # AI Provider Selection: gemini | ollama
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "gemini").lower()

    # Google Gemini Settings
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Ollama (Lokale KI) Settings
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    OLLAMA_API_KEY: str = os.getenv("OLLAMA_API_KEY", "")

    # Nextcloud Settings
    NEXTCLOUD_URL: str = os.getenv("NEXTCLOUD_URL", "https://nextcloud.example.com").rstrip("/")
    NEXTCLOUD_USER: str = os.getenv("NEXTCLOUD_USER", "")
    NEXTCLOUD_PASSWORD: str = os.getenv("NEXTCLOUD_PASSWORD", "")
    
    # Target root folder for organized documents (e.g. /Dokumente)
    TARGET_ROOT_FOLDER: str = os.getenv("TARGET_ROOT_FOLDER", "/Dokumente")
    
    # Inbox folder where raw PDFs are uploaded
    INBOX_FOLDER: str = os.getenv("INBOX_FOLDER", "/Posteingang")

    # Server settings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Telegram Notification Settings
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    @classmethod
    def get_webdav_url(cls, user: str = None) -> str:
        username = user if user else cls.NEXTCLOUD_USER
        return f"{cls.NEXTCLOUD_URL}/remote.php/dav/files/{username}"

    @classmethod
    def validate(cls):
        errors = []
        provider = cls.AI_PROVIDER.lower()
        if provider == "gemini":
            if not cls.GEMINI_API_KEY:
                errors.append("GEMINI_API_KEY ist für AI_PROVIDER=gemini nicht gesetzt.")
        elif provider == "ollama":
            if not cls.OLLAMA_BASE_URL:
                errors.append("OLLAMA_BASE_URL ist für AI_PROVIDER=ollama nicht gesetzt.")
        else:
            errors.append(f"Unbekannter AI_PROVIDER '{cls.AI_PROVIDER}'. Erlaubte Werte: gemini, ollama.")

        if not cls.NEXTCLOUD_URL:
            errors.append("NEXTCLOUD_URL ist nicht gesetzt.")
        if not cls.NEXTCLOUD_USER:
            errors.append("NEXTCLOUD_USER ist nicht gesetzt.")
        if not cls.NEXTCLOUD_PASSWORD:
            errors.append("NEXTCLOUD_PASSWORD ist nicht gesetzt.")
        if errors:
            raise ValueError("Fehlende Konfigurationswerte: " + ", ".join(errors))
