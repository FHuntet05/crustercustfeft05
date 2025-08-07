# src/core/downloader.py

import yt_dlp
import logging
import asyncio
import os
import re
import aiohttp
import aiofiles

# Configuración del logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Opciones base para yt-dlp
YDL_OPTS_BASE = {
    'logger': logger,
    'cookiefile': 'youtube_cookies.txt' if os.path.exists('youtube_cookies.txt') else None,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'quiet': True,
    'noplaylist': True,
    'extract_flat': 'in_playlist', # Para manejar playlists de forma eficiente
}

async def search_music(query: str):
    """Busca música en YouTube y devuelve una lista de resultados."""
    loop = asyncio.get_event_loop()
    opts = {
        **YDL_OPTS_BASE,
        'default_search': 'ytsearch10',  # Busca 10 resultados en YouTube
        'format': 'bestaudio/best',
    }
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            search_result = await loop.run_in_executor(
                None, 
                lambda: ydl.extract_info(query, download=False)
            )
            
            results = []
            if 'entries' in search_result:
                for entry in search_result['entries']:
                    if entry:
                        results.append({
                            'id': entry.get('id'),
                            'title': entry.get('title', 'Título desconocido'),
                            'artist': entry.get('uploader', 'Artista desconocido'),
                            'duration': format_time(entry.get('duration', 0)),
                        })
            return results
        except Exception as e:
            logger.error(f"Error al buscar música con yt-dlp: {e}")
            return []

async def get_media_info(url):
    """Obtiene información detallada de un medio sin descargarlo."""
    loop = asyncio.get_event_loop()
    # Para obtener info detallada, no usamos 'extract_flat'
    opts = {key: value for key, value in YDL_OPTS_BASE.items() if key != 'extract_flat'}
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
            return info
        except Exception as e:
            logger.error(f"yt-dlp no pudo obtener información de {url}: {e}")
            return None

async def download_media(url, format_id, output_path, progress_hook):
    """Descarga un medio con el formato especificado."""
    loop = asyncio.get_event_loop()
    
    directory = os.path.dirname(output_path)
    if not os.path.exists(directory):
        os.makedirs(directory)

    # yt-dlp añade la extensión, así que se la quitamos a la plantilla
    output_template = os.path.splitext(output_path)[0]

    opts = {
        **YDL_OPTS_BASE,
        'format': format_id,
        'outtmpl': f'{output_template}.%(ext)s',
        'progress_hooks': [progress_hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = await loop.run_in_executor(
                None, 
                lambda: ydl.extract_info(url, download=True)
            )
            # El nombre de archivo final está en la info devuelta
            return info.get('requested_downloads')[0]['filepath']
        except Exception as e:
            logger.error(f"yt-dlp falló al descargar {url}: {e}")
            raise

async def download_thumbnail(thumbnail_url: str, output_path: str):
    """Descarga una imagen desde una URL de forma asíncrona."""
    logger.info(f"Descargando carátula desde {thumbnail_url}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(thumbnail_url) as response:
                if response.status == 200:
                    async with aiofiles.open(output_path, 'wb') as f:
                        await f.write(await response.read())
                    logger.info(f"Carátula guardada en {output_path}")
                    return output_path
                else:
                    logger.error(f"No se pudo descargar la carátula. Status: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Excepción al descargar la carátula: {e}")
        return None

async def get_lyrics(url: str, temp_dir: str = "downloads"):
    """
    Intenta descargar las letras (subtítulos) de un video de YouTube.
    Devuelve el texto de la letra o None si no se encuentra.
    """
    logger.info(f"Intentando obtener letras para {url}")
    loop = asyncio.get_event_loop()
    
    try:
        # Extraemos el ID para un nombre de archivo único
        ydl_temp = yt_dlp.YoutubeDL({'quiet': True, 'nocheckcertificate': True})
        video_id = ydl_temp.extract_info(url, download=False).get('id', 'temp_lyrics')
    except Exception as e:
        logger.error(f"No se pudo extraer el ID del video para las letras: {e}")
        video_id = "temp_lyrics" # Fallback

    lyrics_template = os.path.join(temp_dir, f'{video_id}_lyrics')
    
    opts = {
        'logger': logger,
        'nocheckcertificate': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['es', 'en'],
        'skip_download': True,
        'outtmpl': lyrics_template,
        'quiet': True,
    }

    sub_file_path = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            await loop.run_in_executor(None, lambda: ydl.download([url]))
        
        # Buscar el archivo de subtítulos (.vtt) descargado
        for lang in ['es', 'en']:
            expected_path = f"{lyrics_template}.{lang}.vtt"
            if os.path.exists(expected_path):
                sub_file_path = expected_path
                break
        
        if not sub_file_path:
            logger.warning(f"No se encontraron subtítulos VTT para {url}")
            return None

        # Leer y limpiar el archivo VTT
        async with aiofiles.open(sub_file_path, mode='r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        
        # Limpieza del VTT para obtener solo el texto
        lines = content.splitlines()
        lyrics_text = []
        # Expresión regular para limpiar timestamps y tags
        clean_line_regex = re.compile(r'<[^>]+>|&nbsp;')
        for line in lines:
            if line.strip() and "-->" not in line and "WEBVTT" not in line and "Kind: captions" not in line and "Language: " not in line:
                cleaned_line = clean_line_regex.sub('', line).strip()
                if cleaned_line and (not lyrics_text or cleaned_line != lyrics_text[-1]):
                    lyrics_text.append(cleaned_line)
        
        return "\n".join(lyrics_text) if lyrics_text else None

    except Exception as e:
        logger.error(f"Error al obtener letras para {url}: {e}")
        return None
    finally:
        # Limpiar archivo temporal de subtítulos
        if sub_file_path and os.path.exists(sub_file_path):
            os.remove(sub_file_path)

def format_time(seconds):
    """Formatea segundos en HH:MM:SS o MM:SS."""
    if seconds is None:
        return "N/A"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"