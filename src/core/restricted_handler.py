import logging
import os
import asyncio
from typing import Optional, Dict, Tuple, List
from datetime import datetime

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from .channel_joiner import channel_joiner
from ..helpers.utils import format_bytes, format_time

logger = logging.getLogger(__name__)

class RestrictedContentHandler:
    def __init__(self):
        self._download_tasks = {}  # {user_id: {message_id: task}}
        self.download_dir = "downloads"
        os.makedirs(self.download_dir, exist_ok=True)

    async def process_message_link(
        self, client: Client, message_link: str, progress_callback=None
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Procesa un enlace a un mensaje de Telegram.
        Returns: (success, message, data)
        """
        try:
            # Extraer información del enlace
            parts = message_link.split('/')
            if len(parts) < 4:
                return False, "❌ Enlace inválido", None

            chat_id = parts[-2]
            try:
                message_id = int(parts[-1])
            except ValueError:
                return False, "❌ ID de mensaje inválido", None

            # Intentar unirse al canal si es necesario
            success, join_msg, chat = await channel_joiner.join_channel(client, chat_id)
            if not success:
                return False, join_msg, None

            # Obtener el mensaje
            try:
                message = await client.get_messages(chat.id, message_id)
                if not message:
                    return False, "❌ No se encontró el mensaje", None
                if not message.media:
                    return False, "❌ El mensaje no contiene archivos multimedia", None
                
                # Extraer información del medio
                media_info = await self._extract_media_info(message)
                return True, "✅ Mensaje encontrado", {
                    "message": message,
                    "chat": chat,
                    "media_info": media_info
                }

            except FloodWait as e:
                return False, f"⚠️ Rate limit de Telegram. Intenta en {e.value} segundos", None
            except Exception as e:
                logger.error(f"Error al obtener mensaje {message_id} de {chat_id}: {e}")
                return False, f"❌ Error al obtener el mensaje: {str(e)}", None

        except Exception as e:
            logger.error(f"Error procesando enlace {message_link}: {e}")
            return False, f"❌ Error inesperado: {str(e)}", None

    async def download_media(
        self, client: Client, message: Message, user_id: int,
        progress_callback=None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Descarga un archivo multimedia de un mensaje.
        Returns: (success, message, file_path)
        """
        try:
            # Cancelar descargas previas del mismo usuario
            await self._cancel_user_downloads(user_id)

            # Preparar directorio de descarga
            file_name = self._get_safe_filename(message)
            download_path = os.path.join(self.download_dir, str(user_id), file_name)
            os.makedirs(os.path.dirname(download_path), exist_ok=True)

            # Iniciar descarga
            start_time = datetime.now()
            file_path = await client.download_media(
                message,
                file_name=download_path,
                progress=progress_callback
            )

            if not file_path or not os.path.exists(file_path):
                return False, "❌ Error al descargar el archivo", None

            duration = (datetime.now() - start_time).total_seconds()
            size = os.path.getsize(file_path)
            speed = size / duration if duration > 0 else 0

            success_msg = (
                f"✅ Descarga completada\n"
                f"📁 Archivo: {os.path.basename(file_path)}\n"
                f"💾 Tamaño: {format_bytes(size)}\n"
                f"⚡️ Velocidad: {format_bytes(speed)}/s\n"
                f"⏱ Duración: {format_time(duration)}"
            )

            return True, success_msg, file_path

        except FloodWait as e:
            return False, f"⚠️ Rate limit de Telegram. Intenta en {e.value} segundos", None
        except Exception as e:
            logger.error(f"Error descargando medio para usuario {user_id}: {e}")
            return False, f"❌ Error durante la descarga: {str(e)}", None

    async def _extract_media_info(self, message: Message) -> Dict:
        """Extrae información detallada del medio."""
        info = {
            "file_id": None,
            "file_name": None,
            "mime_type": None,
            "file_size": 0,
            "duration": None,
            "width": None,
            "height": None,
            "thumb": None
        }

        if message.video:
            info.update({
                "type": "video",
                "file_id": message.video.file_id,
                "file_name": message.video.file_name,
                "mime_type": message.video.mime_type,
                "file_size": message.video.file_size,
                "duration": message.video.duration,
                "width": message.video.width,
                "height": message.video.height,
                "thumb": message.video.thumbs[0].file_id if message.video.thumbs else None
            })
        elif message.document:
            info.update({
                "type": "document",
                "file_id": message.document.file_id,
                "file_name": message.document.file_name,
                "mime_type": message.document.mime_type,
                "file_size": message.document.file_size,
                "thumb": message.document.thumbs[0].file_id if message.document.thumbs else None
            })
        elif message.audio:
            info.update({
                "type": "audio",
                "file_id": message.audio.file_id,
                "file_name": message.audio.file_name,
                "mime_type": message.audio.mime_type,
                "file_size": message.audio.file_size,
                "duration": message.audio.duration,
                "thumb": message.audio.thumbs[0].file_id if message.audio.thumbs else None
            })
        
        return info

    def _get_safe_filename(self, message: Message) -> str:
        """Genera un nombre de archivo seguro basado en el mensaje."""
        if message.video and message.video.file_name:
            return message.video.file_name
        elif message.document and message.document.file_name:
            return message.document.file_name
        elif message.audio and message.audio.file_name:
            return message.audio.file_name
        
        # Fallback a nombre genérico con timestamp
        ext = ".mp4" if message.video else ".bin"
        return f"downloaded_{int(datetime.now().timestamp())}{ext}"

    async def _cancel_user_downloads(self, user_id: int):
        """Cancela descargas previas del usuario."""
        if user_id in self._download_tasks:
            for task in self._download_tasks[user_id].values():
                if not task.done():
                    task.cancel()
            del self._download_tasks[user_id]

# Instancia global
restricted_handler = RestrictedContentHandler()