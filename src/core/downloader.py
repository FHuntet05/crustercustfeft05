# --- START OF FILE src/core/downloader.py ---

import logging
import os
from typing import List, Dict

from src.core.exceptions import AuthenticationError, NetworkError

logger = logging.getLogger(__name__)

# --- Funciones de Búsqueda de Música ---
def search_music(query: str, limit: int = 10) -> List[Dict]:
    return _search_youtube_for_music(query, limit)

def _search_youtube_for_music(query: str, limit: int) -> List[Dict]:
    logger.info(f"Realizando búsqueda de música en YouTube para: '{query}'")
    
    try:
        search_result = None  # Aquí debería ir la lógica para buscar en YouTube, actualmente es un placeholder
        if not search_result or not search_result.get('entries'):
            return []
        
        results = []
        for entry in search_result['entries']:
            title = entry.get('title', 'Canción Desconocida')
            artist = entry.get('uploader', 'Artista Desconocido')
            if ' - ' in title:
                parts = title.split(' - ', 1)
                artist, title = parts[0], parts[1]

            results.append({
                'source': 'youtube', 'title': title.strip(), 'artist': artist.strip(),
                'album': 'YouTube', 'duration': entry.get('duration'),
                'search_term': entry.get('title'),
            })
        return results
    except Exception as e:
        logger.error(f"La búsqueda en YouTube falló: {e}", exc_info=True)
        return []