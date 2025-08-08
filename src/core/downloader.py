import logging
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import requests
import glob
import shutil
from typing import TYPE_CHECKING
import asyncio

from src.core.exceptions import AuthenticationError

if TYPE_CHECKING:
    from src.core.worker import ProgressTracker

logger = logging.getLogger(__name__)

# Este logger personalizado se mantiene solo para capturar el error de autenticación específico.
class YtdlpAuthLogger:
    def debug(self, msg):
        # Ignorar mensajes de depuración
        pass
    def info(self, msg):
        # Ignorar mensajes de información
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        if "Sign in to confirm" in msg or "confirm you’re not a bot" in msg or "authentication" in msg:
            raise AuthenticationError("YouTube", "Las cookies de autenticación son inválidas o han expirado.")
        else:
            logger.error(f"yt-dlp internal error: {msg}")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
spotify_api = None

if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        auth_manager = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
        spotify_api = spotipy.Spotify(auth_manager=auth_manager)
        logger.info("Cliente de Spotify API inicializado.")
    except Exception as e:
        logger.warning(f"No se pudo inicializar Spotify API: {e}.")

def get_common_ydl_opts():
    opts = {
        'quiet': True, 'no_warnings': True, 'forcejson': True,
        'ignoreerrors': True, 'geo_bypass': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.5',
        },
    }
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        opts['ffmpeg_location'] = ffmpeg_path
    
    cookies_file_path = "youtube_cookies.txt"
    if os.path.exists(cookies_file_path):
        opts['cookiefile'] = cookies_file_path
    else:
        logger.warning("ADVERTENCIA: No se encontró 'youtube_cookies.txt'. La funcionalidad de YouTube puede ser limitada.")
    
    return opts

def download_from_url(url: str, output_path: str, format_id: str, tracker: 'ProgressTracker' = None) -> str or None:
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({
        'format': format_id,
        'outtmpl': {'default': f'{output_path}.%(ext)s'},
        'noplaylist': True, 'merge_output_format': 'mkv',
        'http_chunk_size': 10485760, 'retries': 5, 'fragment_retries': 5,
        'nopart': True, 'logger': YtdlpAuthLogger(),
    })
    # 'forcejson' no es necesario para la descarga real.
    if 'forcejson' in ydl_opts: del ydl_opts['forcejson']

    if tracker:
        ydl_opts['progress_hooks'] = [tracker.ytdlp_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except AuthenticationError:
        raise
    except Exception as e:
        logger.error(f"Error inesperado durante la descarga de yt-dlp: {e}", exc_info=True)
        return None

    found_files = glob.glob(f"{output_path}.*")
    for f in found_files:
        # Excluir archivos temporales que yt-dlp podría dejar atrás
        if not f.endswith((".part", ".ytdl")):
            logger.info(f"Descarga de yt-dlp completada. Archivo final: {f}")
            return f

    logger.error(f"yt-dlp finalizó pero no se encontró un archivo final válido en {output_path}.*")
    return None

def get_best_audio_format_id(formats: list) -> str:
    if not formats: return 'bestaudio/best'
    # Priorizar formatos de solo audio (vcodec=none)
    audio_only = [f for f in formats if f.get('vcodec') in ['none', None] and f.get('acodec') not in ['none', None]]
    if audio_only:
        # Ordenar por bitrate de audio (abr) descendente
        best = sorted(audio_only, key=lambda x: x.get('abr', 0), reverse=True)[0]
        return best.get('format_id')
    # Si no hay de solo audio, yt-dlp elegirá el mejor stream de audio de un archivo de video.
    return 'bestaudio/best'

def get_best_video_format_id(formats: list) -> str:
    # Dejar que yt-dlp elija la mejor combinación de video y audio.
    return 'bestvideo+bestaudio/best'

def _parse_entry(entry: dict) -> dict:
    formats = []
    if entry.get('formats'):
        for f in entry['formats']:
            # Solo incluir formatos que tengan video o audio.
            if f.get('vcodec', 'none') != 'none' or f.get('acodec', 'none') != 'none':
                formats.append({
                    'format_id': f.get('format_id'), 'ext': f.get('ext'),
                    'filesize': f.get('filesize') or f.get('filesize_approx'),
                    'vcodec': f.get('vcodec'), 'acodec': f.get('acodec'),
                    'height': f.get('height'), 'fps': f.get('fps'),
                })
    
    is_video = any(f.get('vcodec', 'none') != 'none' for f in formats)

    return {
        'id': entry.get('id'), 'url': entry.get('webpage_url'), 'title': entry.get('title', 'Título Desconocido'),
        'uploader': entry.get('uploader', 'Uploader Desconocido'), 'duration': entry.get('duration'),
        'thumbnail': entry.get('thumbnail'), 'is_video': is_video, 'formats': formats,
    }

def get_url_info(url: str) -> dict or None:
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({'skip_download': True, 'logger': YtdlpAuthLogger(), 'playlist_items': '1'})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            
            if 'entries' in info: # Es una playlist
                # Tomar el primer video para la información general
                if not info.get('entries'): return None # Playlist vacía
                first_entry = info['entries'][0]
                playlist_result = _parse_entry(first_entry)
                # Sobrescribir con metadatos de la playlist
                playlist_result['is_playlist'] = True
                playlist_result['playlist_count'] = info.get('playlist_count')
                playlist_result['title'] = info.get('title') # Usar el título de la playlist
                # Guardar todas las entradas parseadas para posible uso futuro
                playlist_result['entries'] = [_parse_entry(e) for e in info.get('entries', [])]
                return playlist_result

            return _parse_entry(info)
            
    except AuthenticationError:
        raise
    except Exception as e:
        logger.error(f"Excepción genérica en get_url_info para {url}: {e}", exc_info=True)
        return None

def search_music(query: str, limit: int = 10) -> list:
    """
    Busca música exclusivamente en Spotify para obtener metadatos de alta calidad.
    """
    results = []
    if not spotify_api:
        logger.warning("La búsqueda de música está deshabilitada: no se configuraron las credenciales de Spotify.")
        return results

    try:
        spotify_results = spotify_api.search(q=query, type='track', limit=limit)
        for item in spotify_results['tracks']['items']:
            if not item or not item.get('name') or not item['artists']:
                continue

            # Construir un término de búsqueda preciso para YouTube
            search_term = f"{item['artists'][0]['name']} - {item['name']} Official Audio"
            
            results.append({
                'source': 'spotify',
                'title': item['name'],
                'artist': ", ".join(artist['name'] for artist in item['artists']),
                'album': item['album']['name'],
                'duration': item['duration_ms'] / 1000,
                'search_term': search_term,
                'thumbnail': item['album']['images'][0]['url'] if item['album']['images'] else None
            })
    except Exception as e:
        logger.error(f"La búsqueda en Spotify falló: {e}", exc_info=True)
        # No devolvemos nada si Spotify falla, para mantener la consistencia del flujo.
        return []
            
    return results

def download_thumbnail(url: str, save_path: str) -> str | None:
    """
    Descarga una imagen desde una URL y la guarda localmente.
    """
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # Asegurarse de que el directorio existe
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Carátula descargada y guardada en: {save_path}")
        return save_path
    except requests.RequestException as e:
        logger.error(f"No se pudo descargar la carátula desde {url}: {e}")
        return None