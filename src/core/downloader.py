# src/core/downloader.py

import logging
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import requests
import glob

# Se importa la función de progreso desde utils para evitar dependencia circular
from src.helpers.utils import _progress_hook_yt_dlp

logger = logging.getLogger(__name__)

# --- Clase de Excepción y Logger Personalizado para el Reintento ---

class ProgressError(Exception):
    """Excepción personalizada para señalar un fallo específico del progreso en yt-dlp."""
    pass

class YtdlpLogger:
    """Logger personalizado para inyectar en yt-dlp y detectar errores específicos."""
    def debug(self, msg):
        pass
    def info(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        # Si detectamos el error de progreso, levantamos nuestra propia excepción
        if "not supported between instances of 'NoneType' and 'int'" in msg:
            raise ProgressError(msg)
        else:
            # Para otros errores, simplemente los logueamos
            logger.error(f"yt-dlp internal error: {msg}")

# --- Configuración de APIs y Cookies ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
YOUTUBE_COOKIES_FILE = "youtube_cookies.txt" if os.path.exists("youtube_cookies.txt") else None

if YOUTUBE_COOKIES_FILE:
    logger.info("Archivo de cookies de YouTube encontrado. Se utilizará para las peticiones.")
else:
    logger.warning("No se encontró 'youtube_cookies.txt'. Las descargas de YouTube podrían fallar o ser de baja calidad debido a bloqueos.")

spotify_api = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        auth_manager = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
        spotify_api = spotipy.Spotify(auth_manager=auth_manager)
        logger.info("Cliente de Spotify API inicializado con éxito.")
    except Exception as e:
        logger.warning(f"No se pudo inicializar Spotify API: {e}. La búsqueda de música estará limitada.")

def get_common_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'forcejson': True,
        'ignoreerrors': True,
        'geo_bypass': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.5',
        },
    }
    if YOUTUBE_COOKIES_FILE:
        opts['cookiefile'] = YOUTUBE_COOKIES_FILE
    return opts

def get_best_audio_format(formats: list) -> str:
    audio_formats = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none' and f.get('abr')]
    if not audio_formats:
        logger.warning("No se encontraron formatos de solo audio, se usará 'bestaudio/best'.")
        return 'bestaudio/best'
    
    best_format = sorted(audio_formats, key=lambda x: x.get('abr', 0), reverse=True)[0]
    logger.info(f"Mejor formato de audio seleccionado: ID {best_format.get('format_id')} con ABR {best_format.get('abr')}k")
    return best_format.get('format_id', 'bestaudio/best')

def get_lyrics(url: str) -> str or None:
    temp_lyrics_path = f"temp_lyrics_{os.urandom(4).hex()}"
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({
        'writesubtitles': True,
        'subtitleslangs': ['es', 'en', 'es-419'],
        'skip_download': True,
        'outtmpl': {'default': temp_lyrics_path}
    })
    if 'forcejson' in ydl_opts: del ydl_opts['forcejson']

    lyrics_filename = ""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
            generated_files = [f for f in os.listdir('.') if f.startswith(temp_lyrics_path)]
            
            for ext in ['.es.vtt', '.en.vtt', '.es-419.vtt', '.vtt']:
                for fname in generated_files:
                    if fname.endswith(ext):
                        lyrics_filename = fname
                        break
                if lyrics_filename: break
            
            if lyrics_filename:
                with open(lyrics_filename, 'r', encoding='utf-8') as f:
                    lines = [line.strip() for line in f if not line.strip().isdigit() and '-->' not in line and line.strip() and "WEBVTT" not in line]
                    return "\n".join(lines)[:4000]
    except Exception as e:
        logger.warning(f"No se pudo obtener la letra para {url}: {e}")
    finally:
        for fname in os.listdir('.'):
            if fname.startswith(temp_lyrics_path):
                try: os.remove(fname)
                except OSError: pass
    return None

def get_url_info(url: str) -> dict or None:
    ydl_opts = get_common_ydl_opts()
    ydl_opts['skip_download'] = True
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                logger.error(f"yt-dlp no devolvió información para {url}.")
                return None
            entry = info.get('entries', [info])[0]
            if not entry: return None
            formats = []
            if entry.get('formats'):
                for f in entry['formats']:
                    if f.get('vcodec', 'none') != 'none' or f.get('acodec', 'none') != 'none':
                        formats.append({
                            'format_id': f.get('format_id'), 'ext': f.get('ext'),
                            'resolution': f.get('resolution') or (f"{f.get('height', 0)}p" if f.get('height') else None),
                            'filesize': f.get('filesize') or f.get('filesize_approx'),
                            'abr': f.get('abr'), 'acodec': f.get('acodec'), 'vcodec': f.get('vcodec'),
                            'height': f.get('height')
                        })
            is_video = any(f.get('vcodec', 'none') != 'none' for f in formats)
            return {
                'url': entry.get('webpage_url', url), 'title': entry.get('title', 'Título Desconocido'),
                'uploader': entry.get('uploader', 'Uploader Desconocido'), 'duration': entry.get('duration'),
                'thumbnail': entry.get('thumbnail'), 'is_video': is_video, 'formats': formats
            }
    except Exception as e:
        logger.error(f"Excepción en get_url_info para {url}: {e}", exc_info=True)
        return None

def download_from_url(url: str, output_path: str, format_id: str, progress_tracker: dict = None, user_id: int = None) -> str or None:
    if not format_id:
        logger.error("Se intentó descargar desde URL sin un format_id válido.")
        return None
    
    progress_hook = None
    if user_id and progress_tracker:
        progress_hook = lambda d: _progress_hook_yt_dlp({**d, 'user_id': user_id}, progress_tracker)
        
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({
        'format': format_id,
        'outtmpl': {'default': output_path},
        'progress_hooks': [progress_hook] if progress_hook else [],
        'noplaylist': True, 'merge_output_format': 'mkv',
        'http_chunk_size': 10485760, 'retries': 5, 'fragment_retries': 5,
        'nopart': True, 'logger': YtdlpLogger(),
    })
    if 'forcejson' in ydl_opts: del ydl_opts['forcejson']

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except ProgressError:
        logger.warning("Fallo de progreso detectado. Reintentando con estrategia 'best'...")
        # --- CAMBIO CLAVE: Estrategia de reintento ---
        ydl_opts['format'] = 'bestvideo+bestaudio/best' # Dejar que ytdlp elija la mejor combinación
        ydl_opts['progress_hooks'] = []
        ydl_opts['logger'] = None # Usar logger por defecto en el reintento
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as retry_e:
            logger.error(f"El reintento de descarga también falló: {retry_e}")
            return None
    except Exception as e:
        logger.error(f"Error inesperado durante la descarga de yt-dlp: {e}")
        return None

    found_files = glob.glob(f"{output_path}.*")
    for f in found_files:
        if not f.endswith(".part"):
            logger.info(f"Descarga de yt-dlp completada. Archivo final: {f}")
            return f

    logger.error(f"yt-dlp finalizó pero no se encontró un archivo final válido en {output_path}.*")
    return None

def search_music(query: str, limit: int = 20) -> list:
    results = []
    if spotify_api:
        try:
            spotify_results = spotify_api.search(q=query, type='track', limit=limit)
            for item in spotify_results['tracks']['items']:
                results.append({
                    'source': 'spotify', 'title': item['name'],
                    'artist': ", ".join(artist['name'] for artist in item['artists']),
                    'album': item['album']['name'], 'duration': item['duration_ms'] / 1000,
                    'search_term': f"{item['name']} {item['artists'][0]['name']} Audio"
                })
        except Exception as e:
            logger.warning(f"Búsqueda en Spotify falló: {e}")

    if not results:
        logger.info(f"No hay resultados en Spotify, usando YouTube.")
        try:
            info = get_url_info(f"ytsearch{limit}:{query} official audio")
            if info and 'entries' in info:
                for entry in info['entries']:
                    title, artist = entry.get('title', 'N/A'), entry.get('uploader', 'N/A')
                    if ' - ' in title:
                        parts = title.split(' - ', 1)
                        if len(parts) == 2: artist, title = parts[0], parts[1]
                    results.append({
                        'source': 'youtube', 'title': title.strip(), 'artist': artist.strip(),
                        'album': 'YouTube', 'duration': entry.get('duration'), 'url': entry.get('webpage_url'),
                    })
        except Exception as e:
            logger.error(f"La búsqueda en YouTube falló: {e}")
            
    return results

def download_file(url: str, output_path: str) -> bool:
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(output_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"No se pudo descargar {url}: {e}")
        return False