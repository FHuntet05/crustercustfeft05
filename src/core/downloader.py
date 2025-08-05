import logging
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os

logger = logging.getLogger(__name__)

# --- Configuración de APIs (Opcional, pero recomendado) ---
# Spotify
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")

spotify_api = None
if SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET:
    try:
        auth_manager = SpotifyClientCredentials(client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET)
        spotify_api = spotipy.Spotify(auth_manager=auth_manager)
        logger.info("Cliente de Spotify API inicializado con éxito.")
    except Exception as e:
        logger.warning(f"No se pudo inicializar Spotify API: {e}. La búsqueda de música estará limitada.")

# --- Funciones de yt-dlp ---

def get_url_info(url: str):
    """
    Usa yt-dlp para obtener información detallada de una URL sin descargarla.
    Devuelve un diccionario con los datos clave o None si falla.
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': 'in_playlist', # Extrae info de playlist más rápido
        'forcejson': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Si es una playlist, tomamos el primer video como referencia
            if 'entries' in info and info['entries']:
                entry = info['entries'][0]
                playlist_title = info.get('title')
            else:
                entry = info
                playlist_title = None

            # Extraer todos los formatos disponibles para la selección de calidad
            formats = []
            if entry.get('formats'):
                for f in entry['formats']:
                    # Solo nos interesan los formatos que tienen video y audio, o solo audio
                    if (f.get('vcodec') != 'none' and f.get('acodec') != 'none') or (f.get('vcodec') == 'none' and f.get('acodec') != 'none'):
                        formats.append({
                            'format_id': f.get('format_id'),
                            'ext': f.get('ext'),
                            'resolution': f.get('resolution') or f"{f.get('height', 0)}p",
                            'fps': f.get('fps'),
                            'filesize': f.get('filesize') or f.get('filesize_approx'),
                            'abr': f.get('abr'), # Audio Bitrate
                            'vbr': f.get('vbr'), # Video Bitrate
                            'acodec': f.get('acodec'),
                            'vcodec': f.get('vcodec'),
                        })

            return {
                'url': entry.get('webpage_url', url),
                'title': entry.get('title', 'Título Desconocido'),
                'uploader': entry.get('uploader', 'Uploader Desconocido'),
                'duration': entry.get('duration'),
                'thumbnail': entry.get('thumbnail'),
                'is_video': entry.get('vcodec') != 'none',
                'formats': formats,
                'is_playlist': playlist_title is not None,
                'playlist_title': playlist_title,
                'playlist_count': len(info['entries']) if playlist_title else 1
            }
    except Exception as e:
        logger.error(f"yt-dlp no pudo obtener info de {url}: {e}")
        return None

def download_from_url(url: str, output_path: str, format_id: str = 'best', progress_hook=None):
    """
    Descarga contenido desde una URL usando yt-dlp con un formato específico.
    """
    ydl_opts = {
        'format': format_id,
        'outtmpl': output_path,
        'progress_hooks': [progress_hook] if progress_hook else [],
        'noplaylist': True, # Descarga solo un video, no la playlist entera
        'merge_output_format': 'mp4', # Si se descargan video y audio por separado, unirlos en mp4
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"yt-dlp falló al descargar {url} con formato {format_id}: {e}")
        return False

# --- Búsqueda de Música ---

def search_music(query: str, limit: int = 5):
    """
    Busca música usando la API de Spotify y devuelve una lista de resultados.
    Si Spotify no está disponible, hace un fallback a YouTube (menos preciso para metadatos).
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
                    'url': item['external_urls']['spotify'],
                    'search_term': f"{item['name']} {item['artists'][0]['name']} Audio" # Término para buscar en YouTube
                })
            return results
        except Exception as e:
            logger.warning(f"Búsqueda en Spotify falló, haciendo fallback a YouTube: {e}")

    # Fallback a YouTube si Spotify falla o no está configurado
    ydl_opts = {
        'quiet': True,
        'default_search': 'ytsearch5', # Buscar 5 videos
        'skip_download': True,
        'extract_flat': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(query, download=False)
            for entry in search_result.get('entries', []):
                results.append({
                    'source': 'youtube',
                    'title': entry.get('title', 'Título Desconocido'),
                    'artist': entry.get('uploader', 'Artista Desconocido'),
                    'album': 'YouTube',
                    'duration': entry.get('duration'),
                    'url': entry.get('url'),
                    'search_term': None
                })
        return results
    except Exception as e:
        logger.error(f"La búsqueda de música en YouTube falló: {e}")
        return []