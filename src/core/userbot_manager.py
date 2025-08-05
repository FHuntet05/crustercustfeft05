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
        """Inicia el cliente Pyrogram y precarga los diálogos."""
        if not all([self.api_id, self.api_hash, self.session_string]):
            logger.warning("[USERBOT] Faltan API_ID, API_HASH o SESSION_STRING. El Userbot no se iniciará.")
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

            # --- NUEVO: Precargar diálogos para evitar errores de 'Peer id invalid' ---
            logger.info("[USERBOT] Precargando diálogos para poblar caché...")
            async for _ in self.client.get_dialogs():
                await asyncio.sleep(0.1) # Pequeña pausa para no sobrecargar
            logger.info("[USERBOT] Caché de diálogos poblada.")

        except (AuthKeyUnregistered, UserDeactivated, AuthKeyDuplicated) as e:
            logger.critical(f"[USERBOT] ¡Error CRÍTICO de autenticación! La SESSION_STRING es inválida. Regenerarla. Error: {e}")
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

    async def download_file(self, file_id: str, download_path: str, progress_callback=None):
        """Descarga un archivo usando el cliente Pyrogram."""
        if not self.is_active():
            raise ConnectionError("El Userbot no está activo o conectado.")
        
        await self.client.download_media(
            message=file_id,
            file_name=download_path,
            progress=progress_callback
        )

# Instancia única para ser importada globalmente
userbot_instance = UserbotManager()