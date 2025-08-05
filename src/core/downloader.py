import logging
import yt_dlp
import json

logger = logging.getLogger(__name__)

def get_url_info(url):
    """Usa yt-dlp para obtener información de una URL sin descargarla."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'format': 'best'
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # Devolvemos un diccionario simplificado
            return {
                'title': info.get('title', 'Título Desconocido'),
                'uploader': info.get('uploader', 'Uploader Desconocido'),
                'duration': info.get('duration'),
                'filesize': info.get('filesize') or info.get('filesize_approx'),
                'thumbnail': info.get('thumbnail'),
                'is_video': info.get('is_live') is False and info.get('protocol') not in ['m3u8_native']
            }
    except Exception as e:
        logger.error(f"yt-dlp no pudo obtener info de {url}: {e}")
        return None

def download_from_url(url, output_path, progress_hook):
    """Descarga un video/audio desde una URL usando yt-dlp."""
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'progress_hooks': [progress_hook],
        'noplaylist': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"yt-dlp falló al descargar {url}: {e}")
        return False

# Placeholder para la búsqueda en Spotify
def search_music(query):
    logger.info(f"Buscando música para: {query} (placeholder)")
    return [{"artist": "Artista A", "title": "Canción 1"}, {"artist": "Artista B", "title": "Canción 2"}]