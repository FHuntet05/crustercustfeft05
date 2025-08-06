import os
import logging
import asyncio
from pyrogram import Client
from pyrogram.errors import AuthKeyUnregistered, UserDeactivated, AuthKeyDuplicated

logger = logging.getLogger(__name__)

class UserbotManager:
    """Gestiona la inicialización y el acceso al cliente Pyrogram (Userbot)."""

    def __init__(self):
        self.api_id = os.getenv("API_ID")
        self.api_hash = os.getenv("API_HASH")
        self.session_string = os.getenv("USERBOT_SESSION_STRING")
        self.client: Client | None = None

    async def start(self):
        """Inicia el cliente Pyrogram de forma rápida, sin precarga de diálogos."""
        if not all([self.api_id, self.api_hash, self.session_string]):
            logger.warning("[USERBOT] Faltan credenciales. El Userbot no se iniciará.")
            return

        logger.info("[USERBOT] Iniciando cliente Pyrogram...")
        self.client = Client(
            name="userbot_session",
            api_id=int(self.api_id),
            api_hash=self.api_hash,
            session_string=self.session_string,
            in_memory=True
        )
        try:
            await self.client.start()
            me = await self.client.get_me()
            logger.info(f"[USERBOT] Cliente Pyrogram conectado como: {me.username or me.first_name}")
        except (AuthKeyUnregistered, UserDeactivated, AuthKeyDuplicated) as e:
            logger.critical(f"[USERBOT] ¡Error de autenticación! La SESSION_STRING es inválida. Error: {e}")
            self.client = None
        except Exception as e:
            logger.critical(f"[USERBOT] No se pudo iniciar el cliente Pyrogram. Error: {e}")
            self.client = None
    
    async def stop(self):
        """Detiene el cliente Pyrogram si está activo."""
        if self.is_active():
            await self.client.stop()
            logger.info("[USERBOT] Cliente Pyrogram detenido.")

    def is_active(self) -> bool:
        """Comprueba si el cliente está inicializado y conectado."""
        return self.client and self.client.is_connected

    async def download_file(self, chat_id: int, message_id: int, download_path: str, progress_callback=None):
        """Descarga un archivo usando el contexto del mensaje (chat_id, message_id)."""
        if not self.is_active():
            raise ConnectionError("El Userbot no está activo o conectado.")
        
        await self.client.download_media(
            chat_id=chat_id,
            message_id=message_id,
            file_name=download_path,
            progress=progress_callback
        )

# Instancia única para ser importada globalmente
userbot_instance = UserbotManager()