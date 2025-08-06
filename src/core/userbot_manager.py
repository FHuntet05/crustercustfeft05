import os
import logging
import asyncio
from pyrogram import Client
from pyrogram.errors import AuthKeyUnregistered, UserDeactivated, AuthKeyDuplicated

logger = logging.getLogger(__name__)

class UserbotManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UserbotManager, cls).__new__(cls)
            cls._instance.api_id = os.getenv("API_ID")
            cls._instance.api_hash = os.getenv("API_HASH")
            cls._instance.session_string = os.getenv("USERBOT_SESSION_STRING")
            cls._instance.client = None
        return cls._instance

    async def start(self):
        if not all([self.api_id, self.api_hash, self.session_string]):
            logger.warning("[USERBOT] Faltan credenciales. Userbot no se iniciará.")
            return
        logger.info("[USERBOT] Iniciando cliente Pyrogram...")
        self.client = Client("userbot_session", api_id=int(self.api_id), api_hash=self.api_hash, session_string=self.session_string, in_memory=True)
        try:
            await self.client.start()
            me = await self.client.get_me()
            logger.info(f"[USERBOT] Cliente conectado como: {me.username or me.first_name}")
        except Exception as e:
            logger.critical(f"[USERBOT] Error de autenticación: {e}")
            self.client = None
    
    async def stop(self):
        if self.is_active():
            await self.client.stop()
            logger.info("[USERBOT] Cliente Pyrogram detenido.")

    def is_active(self) -> bool:
        return self.client and self.client.is_connected

    async def download_file(self, chat_id: int, message_id: int, download_path: str, progress_callback=None):
        if not self.is_active():
            raise ConnectionError("Userbot no está activo.")
        
        message_to_delete = None
        try:
            logger.info(f"[USERBOT] Obteniendo mensaje de trabajo {message_id} desde chat {chat_id}")
            message_to_download = await self.client.get_messages(chat_id, message_id)
            message_to_delete = message_to_download
            
            if not message_to_download or not message_to_download.media:
                raise Exception("El mensaje de trabajo reenviado no tiene medios.")

            logger.info(f"[USERBOT] Descargando desde el mensaje de trabajo {message_to_download.id}")
            await self.client.download_media(
                message=message_to_download,
                file_name=download_path,
                progress=progress_callback
            )
            logger.info(f"[USERBOT] Descarga completada.")
        except Exception as e:
            logger.error(f"[USERBOT] Falló la descarga desde el chat de trabajo. Error: {e}")
            raise
        finally:
            if message_to_delete:
                try:
                    await self.client.delete_messages(chat_id=chat_id, message_ids=message_to_delete.id)
                    logger.info("[USERBOT] Mensaje de trabajo eliminado.")
                except Exception as e:
                    logger.warning(f"[USERBOT] No se pudo eliminar el mensaje de trabajo. Error: {e}")

# Instancia única para ser importada globalmente
userbot_instance = UserbotManager()