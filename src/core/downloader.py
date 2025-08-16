# --- START OF FILE src/core/downloader.py ---

import logging
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import requests
import glob
from typing import List, Dict, Optional

from src.core.exceptions import AuthenticationError, NetworkError

logger = logging.getLogger(__name__)

# --- Configuración de APIs Externas ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
spotify_api = None

if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        auth_manager = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
        spotify_api = spotipy.Spotify(auth_manager=auth_manager)
        logger.info("Cliente de Spotify API inicializado con éxito.")
    except Exception as e:
        logger.warning(f"No se pudo inicializar Spotify API. La búsqueda de música se verá limitada. Error: {e}")
else:
    logger.warning("No se proporcionaron credenciales de Spotify. La búsqueda de música se realizará directamente en YouTube.")

# --- Clase de Logger Personalizado para capturar errores de yt-dlp ---
class YtdlpLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        logger.info(f"[yt-dlp] {msg}")

    def warning(self, msg):
        logger.warning(f"[yt-dlp] {msg}")

    def error(self, msg):
        if "confirm your age" in msg or "sign in to view this video" in msg or "authentication" in msg or "not a bot" in msg:
            raise AuthenticationError("YouTube", "YouTube requiere una verificación que no se pudo superar sin cookies.")
        logger.error(f"Error interno de yt-dlp: {msg}")

# --- Funciones de yt-dlp ---

def get_common_ydl_opts() -> Dict:
    """Devuelve un diccionario base con las opciones comunes y optimizadas para yt-dlp."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'logger': YtdlpLogger(),
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.5',
        },
        # [SOLUCIÓN] Añadir argumento para intentar saltar la comprobación de autenticación de YouTube.
        'extractor_args': {
            'youtubetab': {'skip': ['authcheck']}
        }
    }
    cookies_file_path = "youtube_cookies.txt"
    if os.path.exists(cookies_file_path):
        opts['cookiefile'] = cookies_file_path
    else:
        logger.warning("ADVERTENCIA: No se encontró 'youtube_cookies.txt'. La descarga de videos restringidos podría fallar.")
    
    return opts

def download_from_url(url: str, output_path_base: str, format_id: Optional[str] = None) -> Optional[str]:
    """Descarga un archivo desde una URL usando yt-dlp, con reintentos y manejo de errores."""
    ydl_opts = get_common_ydl_opts()
    
    ydl_opts.update({
        'format': format_id or 'bestvideo+bestaudio/best',
        'outtmpl': f'{output_path_base}.%(ext)s',
        'noplaylist': True,
        'merge_output_format': 'mkv',
        'fragment_retries': 10,
        'retries': 10,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except AuthenticationError:
        raise
    except yt_dlp.utils.DownloadError as e:
        raise NetworkError(f"yt-dlp no pudo descargar el contenido. El enlace puede estar roto o ser privado. Detalles: {e.msg}")
    except Exception as e:
        logger.error(f"Error inesperado durante la descarga con yt-dlp: {e}", exc_info=True)
        return None

    found_files = glob.glob(f"{output_path_base}.*")
    for f in found_files:
        if not f.endswith((".part", ".ytdl")):
            logger.info(f"Descarga de yt-dlp completada. Archivo final: {f}")
            return f

    logger.error(f"yt-dlp finalizó pero no se encontró un archivo de salida válido en {output_path_base}.*")
    return None

def get_url_info(url: str) -> Optional[Dict]:
    """Obtiene metadatos de una URL sin descargar el contenido."""
    ydl_opts = get_common_ydl_opts()
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False, process=True)
            if not info:
                return None
            return _parse_ydl_entry(info)
    except AuthenticationError:
        raise
    except Exception as e:
        logger.error(f"Excepción genérica en get_url_info para {url}: {e}", exc_info=True)
        return None

def _parse_ydl_entry(entry: Dict) -> Dict:
    """Parsea la salida de yt-dlp a un formato estandarizado y limpio."""
    if 'entries' in entry and entry['entries']:
        return get_url_info(entry['entries'][0]['url'])

    formats = []
    if entry.get('formats'):
        for f in entry['formats']:
            if f.get('vcodec', 'none') != 'none' or f.get('acodec', 'none') != 'none':
                formats.append({
                    'format_id': f.get('format_id'), 'ext': f.get('ext'),
                    'filesize': f.get('filesize') or f.get('filesize_approx'),
                    'vcodec': f.get('vcodec'), 'acodec': f.get('acodec'),
                    'height': f.get('height'), 'fps': f.get('fps'), 'abr': f.get('abr'),
                })
    
    return {
        'id': entry.get('id'), 'webpage_url': entry.get('webpage_url'), 'title': entry.get('title', 'Título Desconocido'),
        'uploader': entry.get('uploader', 'Uploader Desconocido'), 'duration': entry.get('duration'),
        'thumbnail': entry.get('thumbnail'), 'is_video': entry.get('vcodec') != 'none', 'formats': formats,
    }

def get_best_audio_format_id(formats: List[Dict]) -> str:
    """Selecciona el mejor formato de solo audio, priorizando opus y luego por bitrate."""
    if not formats: return 'bestaudio/best'
    
    audio_only = [f for f in formats if f.get('vcodec') in ['none', None] and f.get('acodec') not in ['none', None]]
    if not audio_only:
        return 'bestaudio/best'
    
    best_format = sorted(audio_only, key=lambda x: (x.get('acodec') == 'opus', x.get('abr', 0)), reverse=True)[0]
    return best_format['format_id']

# --- Funciones de Búsqueda de Música (sin cambios) ---
def search_music(query: str, limit: int = 10) -> List[Dict]:
    if spotify_api:
        try:
            spotify_results = spotify_api.search(q=query, type='track', limit=limit)
            if spotify_results and spotify_results['tracks']['items']:
                return _parse_spotify_results(spotify_results)
        except Exception as e:
            logger.error(f"La búsqueda en Spotify falló: {e}. Usando fallback a YouTube.")
    
    return _search_youtube_for_music(query, limit)

def _parse_spotify_results(spotify_results: Dict) -> List[Dict]:
    results = []
    for item in spotify_results['tracks']['items']:
        if not item or not item.get('name') or not item['artists']:
            continue
        
        search_term = f"{item['artists'][0]['name']} - {item['name']} Official Audio"
        
        results.append({
            'source': 'spotify', 'title': item['name'],
            'artist': ", ".join(artist['name'] for artist in item['artists']),
            'album': item['album']['name'], 'duration': item['duration_ms'] / 1000,
            'search_term': search_term
        })
    return results

def _search_youtube_for_music(query: str, limit: int) -> List[Dict]:
    logger.info(f"Realizando búsqueda de música de respaldo en YouTube para: '{query}'")
    ydl_opts = get_common_ydl_opts()
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
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
        logger.error(f"La búsqueda de respaldo en YouTube falló: {e}", exc_info=True)
        return []