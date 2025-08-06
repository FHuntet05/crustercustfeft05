# src/core/downloader.py

import os
import yt_dlp
import spotipy
import requests
import asyncio
from spotipy.oauth2 import SpotifyClientCredentials
from lyricsgenius import Genius
from src.db.mongo_manager import db

# --- CONFIGURACIÓN ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
GENIUS_ACCESS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN")
DOWNLOAD_PATH = "downloads/"
COOKIES_FILE_PATH = "youtube_cookies.txt"

os.makedirs(DOWNLOAD_PATH, exist_ok=True)

class Downloader:
    """
    Clase que encapsula toda la lógica de búsqueda, obtención de información
    y descarga de medios usando yt-dlp, spotipy y lyricsgenius.
    """
    def __init__(self):
        self.spotify = None
        if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
            auth_manager = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
            self.spotify = spotipy.Spotify(auth_manager=auth_manager)
        
        self.genius = Genius(GENIUS_ACCESS_TOKEN, verbose=False, remove_section_headers=True, timeout=15) if GENIUS_ACCESS_TOKEN else None

    class DownloaderError(Exception):
        """Excepción personalizada para errores de esta clase."""
        pass

    def get_url_info(self, url: str) -> dict:
        """
        Obtiene información de una URL usando yt-dlp.
        Lanza DownloaderError si la información no se puede obtener.
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'cookiefile': COOKIES_FILE_PATH,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            
            if not info:
                raise self.DownloaderError(
                    "yt-dlp no devolvió información. Puede que las cookies hayan expirado o el contenido no esté disponible."
                )
            return info
            
        except yt_dlp.utils.DownloadError as e:
            print(f"ERROR [yt-dlp]: No se pudo obtener info de {url}: {e}")
            clean_error = str(e).split('ERROR: ')[-1].strip()
            raise self.DownloaderError(clean_error)
            
        except Exception as e:
            print(f"ERROR [Downloader]: Error inesperado en get_url_info para {url}: {e}")
            raise self.DownloaderError(f"Error inesperado al procesar la URL: {e}")

    async def download_media(self, task: dict) -> str:
        """
        Descarga el medio especificado en una tarea de la base de datos.
        Utiliza progress hooks para actualizar el estado en la DB.
        """
        task_id = task["_id"]

        async def progress_hook(d):
            if d['status'] == 'downloading':
                percentage = d.get('_percent_str', '0.0%').strip()
                speed = d.get('_speed_str', 'N/A').strip()
                eta = d.get('_eta_str', 'N/A').strip()
                status_message = f"Descargando... {percentage} ({speed} - ETA: {eta})"
                asyncio.create_task(db.update_task_status(task_id, "downloading", status_message))

        output_template = os.path.join(DOWNLOAD_PATH, f"{task_id}.%(ext)s")
        
        ydl_opts = {
            'outtmpl': output_template,
            'progress_hooks': [progress_hook],
            'cookiefile': COOKIES_FILE_PATH,
            'format': task['format_id'],
            'postprocessors': []
        }
        
        if task.get("is_audio_only", False):
            ydl_opts['postprocessors'].append({'key': 'FFmpegExtractAudio', 'preferredcodec': 'm4a'})
            ydl_opts['postprocessors'].append({'key': 'EmbedThumbnail'})
            ydl_opts['writethumbnail'] = True

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                meta = await asyncio.to_thread(ydl.extract_info, task['url'], download=True)
                downloaded_file = ydl.prepare_filename(meta)
                final_path = downloaded_file
                if not os.path.exists(final_path):
                    base, _ = os.path.splitext(downloaded_file)
                    if os.path.exists(base + ".m4a"):
                        final_path = base + ".m4a"
                    else:
                        raise self.DownloaderError("No se encontró el archivo descargado final.")
                return final_path
        except Exception as e:
            print(f"ERROR [Downloader]: Falló la descarga para la tarea {task_id}: {e}")
            raise self.DownloaderError(f"Fallo en la descarga: {e}")

    def search_music(self, query: str, limit: int = 10):
        """
        Busca música usando yt-dlp. Prioriza la búsqueda como URL si es una.
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'cookiefile': COOKIES_FILE_PATH,
            'default_search': 'ytsearch',
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # yt-dlp maneja URLs directamente, si no, busca.
                search_query = query if "http" in query else f"ytsearch{limit}:{query}"
                result = ydl.extract_info(search_query, download=False)
                
                # extract_info puede devolver un solo video o una lista (playlist/búsqueda)
                if 'entries' in result:
                    return result['entries'] # Es una búsqueda o playlist
                else:
                    return [result] # Es un solo video

        except Exception as e:
            print(f"ERROR [Downloader]: Falló la búsqueda de música para '{query}': {e}")
            raise self.DownloaderError(f"La búsqueda falló: {e}")

    def download_cover_and_lyrics(self, title: str, artist: str) -> (str, str):
        """
        Intenta descargar la carátula y la letra de una canción.
        """
        cover_path = None
        lyrics_text = None
        
        # Lógica de carátula con Spotify (si está configurado)
        if self.spotify:
            try:
                results = self.spotify.search(q=f'track:{title} artist:{artist}', type='track', limit=1)
                if results['tracks']['items']:
                    track = results['tracks']['items'][0]
                    if track['album']['images']:
                        cover_url = track['album']['images'][0]['url']
                        response = requests.get(cover_url, timeout=10)
                        if response.status_code == 200:
                            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit()]).rstrip()
                            cover_path = os.path.join(DOWNLOAD_PATH, f"cover_{safe_title}.jpg")
                            with open(cover_path, 'wb') as f:
                                f.write(response.content)
            except Exception as e:
                print(f"WARN [Spotify]: No se pudo descargar la carátula: {e}")

        # Lógica de letras con Genius (si está configurado)
        if self.genius:
            try:
                song = self.genius.search_song(title, artist)
                if song:
                    lyrics_text = song.lyrics
            except Exception as e:
                print(f"WARN [Genius]: No se pudieron descargar las letras: {e}")
                
        return cover_path, lyrics_text

downloader = Downloader()