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

    async def _preload_dialogs(self):
        """Tarea en segundo plano para poblar la caché de diálogos."""
        if not self.is_active():
            return
        logger.info("[USERBOT] Tarea en segundo plano: Iniciando precarga de diálogos...")
        try:
            count = 0
            async for _ in self.client.get_dialogs():
                count += 1
            logger.info(f"[USERBOT] Tarea en segundo plano: Caché poblada con {count} diálogos.")
        except Exception as e:
            logger.error(f"[USERBOT] Tarea en segundo plano: Falló la precarga de diálogos. Error: {e}")

    async def start(self):
        """Inicia el cliente Pyrogram y lanza la precarga de diálogos en segundo plano."""
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

            # --- ARQUITECTURA MEJORADA: Lanzar la precarga como una tarea en segundo plano ---
            # Esto permite que la función start() termine inmediatamente, desbloqueando el bot.
            asyncio.create_task(self._preload_dialogs())

        except (AuthKeyUnregistered, UserDeactivated, AuthKeyDuplicated) as e:
            logger.critical(f"[USERBOT] ¡Error de autenticación! La SESSION_STRING es inválida. Regenerarla. Error: {e}")
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