import logging
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os

logger = logging.getLogger(__name__)

# --- Configuración de APIs ---
# Spotify
# CORRECCIÓN: Nombres de variables cambiados a SPOTIFY_... para coincidir con el .env
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

spotify_api = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        auth_manager = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
        spotify_api = spotipy.Spotify(auth_manager=auth_manager)
        logger.info("Cliente de Spotify API inicializado con éxito.")
    except Exception as e:
        logger.warning(f"No se pudo inicializar Spotify API: {e}. La búsqueda de música estará limitada.")

# --- Funciones de yt-dlp ---

def get_url_info(url: str) -> dict or None:
    """
    Usa yt-dlp para obtener información detallada de una URL sin descargarla.
    Devuelve un diccionario con los datos clave o None si falla.
    """
    if "terabox.com" in url or "teraboxapp.com" in url:
        logger.warning("URL de Terabox detectada. Se requiere un extractor específico no implementado.")
        return None

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': 'in_playlist',
        'forcejson': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            entry = info
            if 'entries' in info and info['entries']:
                entry = info['entries'][0]

            formats = []
            if entry.get('formats'):
                for f in entry['formats']:
                    if (f.get('vcodec') != 'none' and f.get('acodec') != 'none') or \
                       (f.get('vcodec') == 'none' and f.get('acodec') != 'none'):
                        formats.append({
                            'format_id': f.get('format_id'),
                            'ext': f.get('ext'),
                            'resolution': f.get('resolution') or f"{f.get('height', 0)}p",
                            'fps': f.get('fps'),
                            'filesize': f.get('filesize') or f.get('filesize_approx'),
                            'abr': f.get('abr'),
                            'vbr': f.get('vbr'),
                            'acodec': f.get('acodec'),
                            'vcodec': f.get('vcodec'),
                        })

            return {
                'url': entry.get('webpage_url', url),
                'title': entry.get('title', 'Título Desconocido'),
                'uploader': entry.get('uploader', 'Uploader Desconocido'),
                'duration': entry.get('duration'),
                'thumbnail': entry.get('thumbnail'),
                'is_video': entry.get('vcodec', 'none') != 'none',
                'formats': formats,
            }
    except Exception as e:
        logger.error(f"yt-dlp no pudo obtener info de {url}: {e}")
        return None

def download_from_url(url: str, output_path: str, format_id: str = 'best', progress_hook=None) -> bool:
    """
    Descarga contenido desde una URL usando yt-dlp con un formato específico.
    """
    ydl_opts = {
        'format': format_id,
        'outtmpl': {'default': output_path},
        'progress_hooks': [progress_hook] if progress_hook else [],
        'noplaylist': True,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'http_chunk_size': 10485760, # 10MB chunk size
        'retries': 5,
        'fragment_retries': 5,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"yt-dlp falló al descargar {url} con formato {format_id}: {e}")
        return False

# --- Búsqueda de Música ---

def search_music(query: str, limit: int = 5) -> list:
    """
    Busca música usando la API de Spotify y devuelve una lista de resultados.
    Si Spotify no está disponible, hace un fallback a YouTube.
    """
    results = []
    if spotify_api:
        try:
            spotify_results = spotify_api.search(q=query, type='track', limit=limit)
            for item in spotify_results['tracks']['items']:
                results.append({
                    'source': 'spotify',
                    'title': item['name'],
                    'artist': ", ".join(artist['name'] for artist in item['artists']),
                    'album': item['album']['name'],
                    'duration': item['duration_ms'] / 1000,
                    'search_term': f"{item['name']} {item['artists'][0]['name']} Audio"
                })
            if results: return results
        except Exception as e:
            logger.warning(f"Búsqueda en Spotify falló, haciendo fallback a YouTube: {e}")

    ydl_opts = {
        'quiet': True,
        'default_search': f'ytsearch{limit}',
        'skip_download': True,
        'extract_flat': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(query, download=False)
            for entry in search_result.get('entries', []):
                title = entry.get('title', 'Título Desconocido')
                artist = entry.get('uploader', 'Artista Desconocido')
                if ' - ' in title:
                    parts = title.split(' - ', 1)
                    if len(parts) == 2:
                        artist, title = parts[0], parts[1]
                
                results.append({
                    'source': 'youtube',
                    'title': title.strip(),
                    'artist': artist.strip(),
                    'album': 'YouTube',
                    'duration': entry.get('duration'),
                    'url': entry.get('url'),
                })
        return results
    except Exception as e:
        logger.error(f"La búsqueda de música en YouTube falló: {e}")
        return []