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
    from src.core.worker import ProgressContext

logger = logging.getLogger(__name__)

class YtdlpLogger:
    def debug(self, msg):
        pass
    def info(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        if "Sign in to confirm" in msg or "confirm you’re not a bot" in msg:
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

def ytdlp_progress_hook(d, context: 'ProgressContext'):
    if not context or d['status'] != 'downloading':
        return

    from src.helpers.utils import format_status_message
    
    total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
    if total_bytes <= 0: return

    downloaded_bytes = d.get('downloaded_bytes', 0)
    speed = d.get('speed', 0)
    eta = d.get('eta', 0)
    percentage = (downloaded_bytes / total_bytes) * 100

    text = format_status_message(
        operation="📥 Descargando...",
        filename=context.task.get('original_filename', 'archivo'),
        percentage=percentage,
        processed_bytes=downloaded_bytes,
        total_bytes=total_bytes,
        speed=speed,
        eta=eta,
        engine="yt-dlp",
        user_id=context.user_id,
        user_mention=context.message.from_user.mention
    )
    
    asyncio.run_coroutine_threadsafe(context.edit_message(text), context.bot.loop)

def download_from_url(url: str, output_path: str, format_id: str, context: 'ProgressContext' = None) -> str or None:
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({
        'format': format_id,
        'outtmpl': {'default': f'{output_path}.%(ext)s'},
        'noplaylist': True, 'merge_output_format': 'mkv',
        'http_chunk_size': 10485760, 'retries': 5, 'fragment_retries': 5,
        'nopart': True, 'logger': YtdlpLogger(),
    })
    if 'forcejson' in ydl_opts: del ydl_opts['forcejson']

    if context:
        ydl_opts['progress_hooks'] = [lambda d: ytdlp_progress_hook(d, context)]

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
        if not f.endswith((".part", ".ytdl")):
            logger.info(f"Descarga de yt-dlp completada. Archivo final: {f}")
            return f

    logger.error(f"yt-dlp finalizó pero no se encontró un archivo final válido en {output_path}.*")
    return None

def get_best_audio_format_id(formats: list) -> str:
    if not formats: return 'bestaudio/best'
    audio_only = [f for f in formats if f.get('vcodec') in ['none', None] and f.get('acodec') not in ['none', None]]
    if audio_only:
        best = sorted(audio_only, key=lambda x: x.get('abr', 0), reverse=True)[0]
        return best.get('format_id')
    return 'bestaudio/best'

def get_best_video_format_id(formats: list) -> str:
    return 'bestvideo+bestaudio/best'

def _parse_entry(entry: dict) -> dict:
    formats = []
    if entry.get('formats'):
        for f in entry['formats']:
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
    ydl_opts.update({'skip_download': True, 'logger': YtdlpLogger(), 'playlist_items': '1'})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            
            if 'entries' in info: # Es una playlist
                first_entry = info['entries'][0]
                playlist_result = _parse_entry(first_entry)
                playlist_result['is_playlist'] = True
                playlist_result['playlist_count'] = info.get('playlist_count')
                playlist_result['title'] = info.get('title') # Usar el título de la playlist
                playlist_result['entries'] = [_parse_entry(e) for e in info.get('entries', [])]
                return playlist_result

            return _parse_entry(info)
            
    except AuthenticationError:
        raise
    except Exception as e:
        logger.error(f"Excepción genérica en get_url_info para {url}: {e}", exc_info=True)
        return None

def search_music(query: str, limit: int = 10) -> list:
    results = []
    if spotify_api:
        try:
            spotify_results = spotify_api.search(q=query, type='track', limit=limit)
            for item in spotify_results['tracks']['items']:
                results.append({
                    'source': 'spotify', 'title': item['name'],
                    'artist': ", ".join(artist['name'] for artist in item['artists']),
                    'album': item['album']['name'], 'duration': item['duration_ms'] / 1000,
                    'search_term': f"{item['name']} {item['artists'][0]['name']} Audio",
                    'thumbnail': item['album']['images'][0]['url'] if item['album']['images'] else None
                })
        except Exception as e:
            logger.warning(f"Búsqueda en Spotify falló: {e}")

    if not results:
        logger.info(f"No hay resultados en Spotify, usando YouTube.")
        try:
            info = get_url_info(f"ytsearch{limit}:{query} official audio")
            if info and 'entries' in info:
                for entry in info['entries']:
                    title, artist = entry.get('title', 'N/A'), entry.get('artist', entry.get('uploader', 'N/A'))
                    if ' - ' in title:
                        parts = title.split(' - ', 1)
                        if len(parts) == 2 and len(parts[0]) < len(parts[1]):
                            artist, title = parts[0], parts[1]
                    results.append({'source': 'youtube', 'title': title.strip(), 'artist': artist.strip(),
                                    'duration': entry.get('duration'), 'url': entry.get('url')})
        except Exception as e:
            logger.error(f"La búsqueda en YouTube falló: {e}")
            
    return results