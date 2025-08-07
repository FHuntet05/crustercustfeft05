# --- START OF FILE src/core/downloader.py ---

import logging
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import requests
import glob
import shutil

from src.helpers.utils import _progress_hook_yt_dlp

logger = logging.getLogger(__name__)

# --- Clases de Excepción y Logger Personalizado ---

class AuthenticationError(Exception):
    """Excepción para errores de autenticación de yt-dlp (bloqueo de bot)."""
    pass

class ProgressError(Exception):
    """Excepción para fallos específicos del progreso en yt-dlp."""
    pass

class YtdlpLogger:
    """Logger personalizado para detectar errores específicos y levantar excepciones."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg):
        if "Sign in to confirm" in msg or "confirm you’re not a bot" in msg:
            raise AuthenticationError(msg)
        if "not supported between instances of 'NoneType' and 'int'" in msg:
            raise ProgressError(msg)
        else:
            logger.error(f"yt-dlp internal error: {msg}")

# --- Configuración de APIs ---
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
    """
    Construye las opciones comunes para yt-dlp.
    La comprobación de cookies es ahora dinámica en cada llamada.
    """
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
        logger.warning("ADVERTENCIA en tiempo de ejecución: No se encontró 'youtube_cookies.txt'. La fiabilidad de las descargas será muy baja.")

    return opts

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
        'outtmpl': {'default': f'{output_path}.%(ext)s'},
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
        logger.warning("Fallo de progreso detectado. Reintentando con estrategia inteligente...")
        is_audio_task = 'ba' in format_id or 'bestaudio' in format_id
        retry_format = 'ba' if is_audio_task else 'best'
        logger.info(f"Estrategia de reintento seleccionada: {retry_format}")
        ydl_opts.update({ 'format': retry_format, 'progress_hooks': [], 'logger': None })
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as retry_e:
            logger.error(f"El reintento de descarga también falló: {retry_e}", exc_info=True)
            return None
    
    except AuthenticationError:
        raise
    
    except Exception as e:
        logger.error(f"Error inesperado durante la descarga de yt-dlp: {e}", exc_info=True)
        return None

    found_files = glob.glob(f"{output_path}.*")
    for f in found_files:
        if not f.endswith(".part"):
            logger.info(f"Descarga de yt-dlp completada. Archivo final: {f}")
            return f

    logger.error(f"yt-dlp finalizó pero no se encontró un archivo final válido en {output_path}.*")
    return None

def get_best_audio_format(formats: list) -> str or None:
    """
    Selecciona el mejor formato de audio disponible usando una estrategia defensiva en cascada.
    """
    # --- LÓGICA DE SELECCIÓN DE AUDIO REESCRITA (VERSIÓN DEFINITIVA) ---
    if not formats:
        logger.warning("No se proporcionaron formatos. Usando el selector final 'bestaudio/best'.")
        return 'bestaudio/best'

    available_formats = [f for f in formats if f.get('format_id')]

    # Estrategia 1: Mejor formato de SOLO AUDIO por bitrate ('abr').
    audio_only_formats = [f for f in available_formats if f.get('vcodec') in ['none', None] and f.get('abr')]
    if audio_only_formats:
        best_format = sorted(audio_only_formats, key=lambda x: x.get('abr', 0), reverse=True)[0]
        logger.info(f"Estrategia 1 (Éxito): Mejor formato de SOLO AUDIO seleccionado: ID {best_format['format_id']} con ABR {best_format['abr']}k")
        return best_format['format_id']
    logger.info("Estrategia 1 (Fallo): No se encontraron formatos de solo audio con 'abr'.")

    # Estrategia 2: Mejor formato MIXTO por bitrate de audio ('abr').
    mixed_formats_with_abr = [f for f in available_formats if f.get('abr')]
    if mixed_formats_with_abr:
        best_format = sorted(mixed_formats_with_abr, key=lambda x: x.get('abr', 0), reverse=True)[0]
        logger.info(f"Estrategia 2 (Éxito): Mejor formato MIXTO (por audio) seleccionado: ID {best_format['format_id']} con ABR {best_format['abr']}k")
        return best_format['format_id']
    logger.info("Estrategia 2 (Fallo): No se encontraron formatos mixtos con 'abr'.")

    # Estrategia 3: Buscar IDs de audio conocidos y fiables en orden de calidad.
    known_audio_ids = ['141', '251', '140', '250', '139', '249'] # m4a 256k, opus ~160k, m4a 128k, etc.
    available_ids = {f['format_id'] for f in available_formats}
    for audio_id in known_audio_ids:
        if audio_id in available_ids:
            logger.info(f"Estrategia 3 (Éxito): Encontrado ID de audio conocido y fiable: {audio_id}")
            return audio_id
    logger.info("Estrategia 3 (Fallo): No se encontraron IDs de audio conocidos.")

    # Estrategia 4: Fallback final al selector compuesto más robusto.
    logger.warning("Estrategia 4 (Fallback Final): Usando el selector genérico 'bestaudio/best'.")
    return 'bestaudio/best'

def get_lyrics(url: str) -> str or None:
    """Intenta descargar la letra (subtítulos) de una URL."""
    temp_lyrics_path = f"temp_lyrics_{os.urandom(4).hex()}"
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({
        'writesubtitles': True,
        'subtitleslangs': ['es', 'en', 'es-419'],
        'skip_download': True,
        'outtmpl': {'default': temp_lyrics_path},
        'logger': YtdlpLogger(),
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
    except AuthenticationError:
        raise
    except Exception as e:
        logger.warning(f"No se pudo obtener la letra para {url}: {e}")
    finally:
        for fname in os.listdir('.'):
            if fname.startswith(temp_lyrics_path):
                try: os.remove(fname)
                except OSError: pass
    return None

def get_url_info(url: str) -> dict or None:
    """Usa yt-dlp para obtener información detallada de una URL sin descargarla."""
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({
        'skip_download': True,
        'logger': YtdlpLogger(),
    })

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
    except AuthenticationError:
        logger.error(f"Error de autenticación al obtener info de {url}. Propagando excepción al worker.")
        raise
    except Exception as e:
        logger.error(f"Excepción genérica en get_url_info para {url}: {e}", exc_info=True)
        return None

def search_music(query: str, limit: int = 20) -> list:
    """Busca música usando la API de Spotify y/o YouTube."""
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
        except AuthenticationError:
            logger.critical("La búsqueda en YouTube falló por un problema de autenticación. Devolviendo lista vacía.")
            return []
        except Exception as e:
            logger.error(f"La búsqueda en YouTube falló por un error genérico: {e}")
            
    return results

def download_file(url: str, output_path: str) -> bool:
    """Descarga un archivo genérico (como una imagen) desde una URL."""
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
# --- END OF FILE src/core/downloader.py ---