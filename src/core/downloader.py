# src/core/downloader.py

import logging
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os

logger = logging.getLogger(__name__)

# --- Configuración de APIs y Cookies ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
YOUTUBE_COOKIES_FILE = "youtube_cookies.txt" if os.path.exists("youtube_cookies.txt") else None

if YOUTUBE_COOKIES_FILE:
    logger.info("Archivo de cookies de YouTube encontrado. Se utilizará para las peticiones.")
else:
    logger.warning("No se encontró 'youtube_cookies.txt'. Las descargas de YouTube podrían fallar o ser de baja calidad.")

spotify_api = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        auth_manager = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
        spotify_api = spotipy.Spotify(auth_manager=auth_manager)
        logger.info("Cliente de Spotify API inicializado con éxito.")
    except Exception as e:
        logger.warning(f"No se pudo inicializar Spotify API: {e}. La búsqueda de música estará limitada.")

def get_common_ydl_opts():
    """Opciones comunes para yt-dlp, incluyendo cookies si están disponibles."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'forcejson': True,
    }
    if YOUTUBE_COOKIES_FILE:
        opts['cookiefile'] = YOUTUBE_COOKIES_FILE
    return opts

def get_url_info(url: str) -> dict or None:
    ydl_opts = get_common_ydl_opts()
    ydl_opts['skip_download'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            entry = info
            if 'entries' in info and info.get('entries'):
                entry = info['entries'][0]

            formats = []
            if entry.get('formats'):
                for f in entry['formats']:
                    if f.get('vcodec', 'none') != 'none' or f.get('acodec', 'none') != 'none':
                        formats.append({
                            'format_id': f.get('format_id'), 'ext': f.get('ext'),
                            'resolution': f.get('resolution') or (f"{f.get('height', 0)}p" if f.get('height') else None),
                            'fps': f.get('fps'), 'filesize': f.get('filesize') or f.get('filesize_approx'),
                            'abr': f.get('abr'), 'vbr': f.get('vbr'), 'acodec': f.get('acodec'), 'vcodec': f.get('vcodec'),
                        })
            return {
                'url': entry.get('webpage_url', url), 'title': entry.get('title', 'Título Desconocido'),
                'uploader': entry.get('uploader', 'Uploader Desconocido'), 'duration': entry.get('duration'),
                'thumbnail': entry.get('thumbnail'), 'is_video': any(f.get('vcodec', 'none') != 'none' for f in formats),
                'formats': formats,
            }
    except Exception as e:
        logger.error(f"yt-dlp no pudo obtener info de {url}: {e}")
        return None

def download_from_url(url: str, output_path: str, format_id: str = 'best', progress_hook=None) -> bool:
    ydl_opts = get_common_ydl_opts()
    ydl_opts.update({
        'format': format_id, 'outtmpl': {'default': output_path},
        'progress_hooks': [progress_hook] if progress_hook else [],
        'noplaylist': True, 'merge_output_format': 'mp4',
        'http_chunk_size': 10485760, 'retries': 5, 'fragment_retries': 5,
    })
    del ydl_opts['forcejson'] # No es necesaria para la descarga

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"yt-dlp falló al descargar {url} con formato {format_id}: {e}")
        return False

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

    # Si Spotify no dio resultados o no está configurado, usamos YouTube como fallback
    if not results:
        logger.info(f"No se encontraron resultados en Spotify para '{query}'. Usando YouTube como fallback.")
        try:
            info = get_url_info(f"ytsearch{limit}:{query} audio")
            if info and 'entries' in info:
                for entry in info['entries']:
                    title = entry.get('title', 'Título Desconocido')
                    artist = entry.get('uploader', 'Artista Desconocido')
                    if ' - ' in title:
                        parts = title.split(' - ', 1)
                        if len(parts) == 2: artist, title = parts[0], parts[1]
                    
                    results.append({
                        'source': 'youtube', 'title': title.strip(), 'artist': artist.strip(),
                        'album': 'YouTube', 'duration': entry.get('duration'), 'url': entry.get('webpage_url'),
                    })
        except Exception as e:
            logger.error(f"La búsqueda de música en YouTube falló: {e}")
            
    return results