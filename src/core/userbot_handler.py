import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict

from pyrogram import Client
from src.db.mongo_manager import db_instance

logger = logging.getLogger(__name__)

class UserbotHandler:
    def __init__(self):
        self.api_id = os.getenv("API_ID")
        self.api_hash = os.getenv("API_HASH")
        self.session_string = os.getenv("USERBOT_SESSION_STRING")
        self._client = None
        self._last_used = {}  # Control de rate limit por usuario

    async def ensure_client(self) -> Optional[Client]:
        """Asegura que el cliente del userbot esté iniciado."""
        if not all([self.api_id, self.api_hash, self.session_string]):
            logger.error("Faltan credenciales para el userbot")
            return None

        if not self._client:
            try:
                self._client = Client(
                    "userbot",
                    api_id=int(self.api_id),
                    api_hash=self.api_hash,
                    session_string=self.session_string,
                    in_memory=True
                )
                await self._client.start()
                logger.info("Cliente userbot iniciado exitosamente")
            except Exception as e:
                logger.error(f"Error al iniciar el cliente userbot: {e}")
                return None

        return self._client

    async def check_rate_limit(self, user_id: int) -> bool:
        """Controla el rate limit por usuario (1 uso cada 5 minutos)."""
        now = datetime.now()
        if user_id in self._last_used:
            if now - self._last_used[user_id] < timedelta(minutes=5):
                return False
        self._last_used[user_id] = now
        return True

    async def validate_and_get_channel(self, channel_identifier: str) -> Tuple[Optional[int], Optional[str]]:
        """Valida y obtiene información de un canal."""
        client = await self.ensure_client()
        if not client:
            return None, None

        try:
            chat = await client.get_chat(channel_identifier)
            return chat.id, chat.title
        except Exception as e:
            logger.error(f"Error al validar canal {channel_identifier}: {e}")
            return None, None

    async def download_media_from_channel(self, channel_id: int, message_id: int, destination: str) -> Optional[str]:
        """Descarga un medio de un canal restringido."""
        client = await self.ensure_client()
        if not client:
            return None

        try:
            message = await client.get_messages(channel_id, message_id)
            if not message or not message.media:
                logger.error(f"No se encontró medio en el mensaje {message_id}")
                return None

            file_path = await client.download_media(message, file_name=destination)
            return file_path
        except Exception as e:
            logger.error(f"Error al descargar medio del canal {channel_id}: {e}")
            return None

    async def get_message_info(self, channel_id: int, message_id: int) -> Optional[Dict]:
        """Obtiene información de un mensaje específico."""
        client = await self.ensure_client()
        if not client:
            return None

        try:
            message = await client.get_messages(channel_id, message_id)
            if not message:
                return None

            info = {
                "type": None,
                "file_id": None,
                "file_name": None,
                "mime_type": None,
                "file_size": 0,
                "duration": None,
                "width": None,
                "height": None
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
                    "height": message.video.height
                })
            elif message.document:
                info.update({
                    "type": "document",
                    "file_id": message.document.file_id,
                    "file_name": message.document.file_name,
                    "mime_type": message.document.mime_type,
                    "file_size": message.document.file_size
                })
            elif message.audio:
                info.update({
                    "type": "audio",
                    "file_id": message.audio.file_id,
                    "file_name": message.audio.file_name,
                    "mime_type": message.audio.mime_type,
                    "file_size": message.audio.file_size,
                    "duration": message.audio.duration
                })

            return info
        except Exception as e:
            logger.error(f"Error al obtener info del mensaje {message_id}: {e}")
            return None

# Instancia global
userbot_handler = UserbotHandler()