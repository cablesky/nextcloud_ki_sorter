import html
import logging
import requests
from config import Config

logger = logging.getLogger("telegram_notifier")

def send_telegram_notification(
    original_filename: str,
    target_folder: str,
    suggested_filename: str,
    reasoning: str,
    ai_provider: str
) -> bool:
    """Sendet eine Telegram-Benachrichtigung über eine erfolgreich einsortierte Datei."""
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
