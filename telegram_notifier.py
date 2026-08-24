from services import TelegramNotifier

def send_telegram_notification(*args, **kwargs):
    """Kompatibilitäts-Funktion für TelegramNotifier.send()."""
    return TelegramNotifier.send(*args, **kwargs)

