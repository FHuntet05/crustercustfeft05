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
    """Valida si un enlace tiene un formato correcto."""
    url_pattern = re.compile(r'^(https?://)?(www\.)?t\.me/.+$')
    if not url_pattern.match(url):
        logger.warning(f"URL inválida: {url}")
        return False
    return True