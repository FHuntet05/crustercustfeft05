import logging
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import requests
import glob
import shutil
from typing import List, Dict, Optional, TYPE_CHECKING
import asyncio

from src.core.exceptions import AuthenticationError, NetworkError

# Evita la importación circular, solo para type hints
if TYPE_CHECKING:
    from src.core.worker import ProgressTracker

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

# --- Clase de Logger Personalizado para yt-dlp ---

class YtdlpLogger:
    """Logger para capturar errores específicos de yt-dlp sin inundar la consola."""
    def debug(self, msg):
        # Ignorar mensajes de depuración de yt-dlp
        if "yt-dlp" in msg: return
        logger.debug(msg)

    def info(self, msg):
        logger.info(msg)

    def warning(self, msg):
        logger.warning(msg)

    def error(self, msg):
        # Este es el error clave que indica que las cookies son necesarias o han expirado.
        if "confirm your age" in msg or "sign in to view this video" in msg or "authentication" in msg:
            raise AuthenticationError("YouTube", "El video requiere autenticación (cookies) o está restringido por edad.")
        logger.error(f"Error interno de yt-dlp: {msg}")

# --- Funciones de yt-dlp ---

def get_common_ydl_opts(progress_hook: Optional[callable] = None) -> Dict:
    """Devuelve un diccionario base con las opciones comunes para yt-dlp."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'logger': YtdlpLogger(),
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.5',
        },
    }
    
    # Añadir hook de progreso si se proporciona
    if progress_hook:
        opts['progress_hooks'] = [progress_hook]

    # Añadir ruta de cookies si existe el archivo
    cookies_file_path = "youtube_cookies.txt"
    if os.path.exists(cookies_file_path):
        opts['cookiefile'] = cookies_file_path
    else:
        logger.warning("ADVERTENCIA: No se encontró 'youtube_cookies.txt'. La descarga de videos restringidos podría fallar.")
    
    return opts

def download_from_url(url: str, output_path: str, format_id: Optional[str] = None, tracker: Optional['ProgressTracker'] = None) -> Optional[str]:
    """Descarga un archivo desde una URL usando yt-dlp."""
    hook = tracker.ytdlp_hook if tracker else None
    ydl_opts = get_common_ydl_opts(progress_hook=hook)
    
    ydl_opts.update({
        'format': format_id or 'bestvideo+bestaudio/best',
        'outtmpl': f'{output_path}.%(ext)s',
        'noplaylist': True,
        'merge_output_format': 'mkv',
        'fragment_retries': 10,
        'retries': 10,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except AuthenticationError:
        raise  # Propagar la excepción de autenticación capturada por el logger
    except yt_dlp.utils.DownloadError as e:
        # Capturar otros errores de descarga de yt-dlp
        logger.error(f"Error de descarga de yt-dlp: {e}", exc_info=True)
        raise NetworkError(f"yt-dlp no pudo descargar el contenido. El enlace puede estar roto o ser privado. Detalles: {e.msg}")
    except Exception as e:
        logger.error(f"Error inesperado durante la descarga con yt-dlp: {e}", exc_info=True)
        return None

    # Buscar el archivo descargado, ignorando archivos temporales
    found_files = glob.glob(f"{output_path}.*")
    for f in found_files:
        if not f.endswith((".part", ".ytdl")):
            logger.info(f"Descarga de yt-dlp completada. Archivo final: {f}")
            return f

    logger.error(f"yt-dlp finalizó pero no se encontró un archivo de salida válido en {output_path}.*")
    return None

def get_url_info(url: str) -> Optional[Dict]:
    """Obtiene metadatos de una URL sin descargar el contenido."""
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({'extract_flat': 'in_playlist', 'forcejson': True})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
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
    if 'entries' in entry: # Es una playlist
        if not entry['entries']: return None # Playlist vacía
        first_entry = entry['entries'][0]
        # Recursivamente obtener info completa del primer video para mostrar formatos.
        return get_url_info(first_entry['url'])

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
        # Si no hay formatos de solo audio, yt-dlp elegirá el mejor stream de audio de un archivo de video.
        return 'bestaudio/best'
    
    # Priorizar opus, luego ordenar por bitrate (abr) descendente.
    best_format = sorted(audio_only, key=lambda x: (x.get('acodec') == 'opus', x.get('abr', 0)), reverse=True)[0]
    return best_format['format_id']

# --- Funciones de Búsqueda ---

def search_music(query: str, limit: int = 10) -> List[Dict]:
    """
    Busca música en Spotify para metadatos de alta calidad.
    Si falla, realiza una búsqueda de respaldo en YouTube.
    """
    if spotify_api:
        try:
            spotify_results = spotify_api.search(q=query, type='track', limit=limit)
            if spotify_results and spotify_results['tracks']['items']:
                return _parse_spotify_results(spotify_results)
        except Exception as e:
            logger.error(f"La búsqueda en Spotify falló: {e}. Usando fallback a YouTube.")
    
    # Fallback a YouTube si Spotify falla o no está configurado
    return _search_youtube_for_music(query, limit)

def _parse_spotify_results(spotify_results: Dict) -> List[Dict]:
    """Parsea los resultados de la API de Spotify a nuestro formato estándar."""
    results = []
    for item in spotify_results['tracks']['items']:
        if not item or not item.get('name') or not item['artists']:
            continue
        
        # Construir un término de búsqueda preciso para yt-dlp
        search_term = f"{item['artists'][0]['name']} - {item['name']} Official Audio"
        
        results.append({
            'source': 'spotify', 'title': item['name'],
            'artist': ", ".join(artist['name'] for artist in item['artists']),
            'album': item['album']['name'], 'duration': item['duration_ms'] / 1000,
            'search_term': search_term,
            'thumbnail': item['album']['images'][0]['url'] if item['album']['images'] else None
        })
    return results

def _search_youtube_for_music(query: str, limit: int) -> List[Dict]:
    """Busca música directamente en YouTube como método de respaldo."""
    logger.info(f"Realizando búsqueda de música de respaldo en YouTube para: '{query}'")
    ydl_opts = get_common_ydl_opts()
    ydl_opts['extract_flat'] = True
    ydl_opts['forcejson'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            if not search_result or not search_result.get('entries'):
                return []
            
            results = []
            for entry in search_result['entries']:
                # Intenta extraer artista y título del título del video
                title = entry.get('title', 'Canción Desconocida')
                artist = entry.get('uploader', 'Artista Desconocido')
                if ' - ' in title:
                    parts = title.split(' - ', 1)
                    artist, title = parts[0], parts[1]

                results.append({
                    'source': 'youtube', 'title': title.strip(), 'artist': artist.strip(),
                    'album': 'YouTube', 'duration': entry.get('duration'),
                    'search_term': entry.get('title'), # Usar el título original para la descarga
                    'thumbnail': entry.get('thumbnail')
                })
            return results
    except Exception as e:
        logger.error(f"La búsqueda de respaldo en YouTube falló: {e}", exc_info=True)
        return []

# --- Funciones de Descarga de Archivos Auxiliares ---

def download_thumbnail(url: str, save_path: str) -> Optional[str]:
    """
    Descarga una imagen (miniatura) desde una URL y la guarda localmente.
    """
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Carátula descargada y guardada en: {save_path}")
        return save_path
    except requests.RequestException as e:
        logger.error(f"No se pudo descargar la carátula desde {url}: {e}")
        return None