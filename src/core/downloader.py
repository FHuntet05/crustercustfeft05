# --- START OF FILE src/core/downloader.py ---

import logging
from typing import List, Dict
import re

from src.core.exceptions import AuthenticationError, NetworkError

logger = logging.getLogger(__name__)

# --- Funciones de Búsqueda de Música ---
def search_music(query: str, limit: int = 10) -> List[Dict]:
    """Realiza una búsqueda de música en una fuente externa."""
    logger.info(f"Realizando búsqueda de música para: '{query}'")
    # Aquí debería ir la lógica para buscar música en una fuente externa.
    return []

def validate_url(url: str) -> bool:
    """Valida si el enlace proporcionado es un enlace de Telegram válido."""
    telegram_url_patterns = [
        r"https://t\.me/\+",  # Enlaces de invitación a canales privados
        r"https://t\.me/[a-zA-Z0-9_]+",  # Enlaces de canales públicos o usuarios
        r"https://t\.me/[a-zA-Z0-9_]+/\d+"  # Enlaces de mensajes específicos
    ]

    for pattern in telegram_url_patterns:
        if re.match(pattern, url):
            return True

    return False