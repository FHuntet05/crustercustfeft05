import os
import logging
import asyncio
from pyrogram import Client
from pyrogram.errors import AuthKeyUnregistered, UserDeactivated, AuthKeyDuplicated
from src.db.mongo_manager import db_instance # Necesario para actualizar la tarea

logger = logging.getLogger(__name__)

class UserbotManager:
    """Gestiona la inicialización y el acceso al cliente Pyrogram (Userbot)."""

    def __init__(self):
        self.api_id = os.getenv("API_ID")
        self.api_hash = os.getenv("API_HASH")
        self.session_string = os.getenv("USERBOT_SESSION_STRING")
        self.saved_messages_id = 0  # Se autoconfigurará al iniciar
        self.client: Client | None = None

    async def _preload_dialogs_background_task(self):
        """Tarea en segundo plano para poblar la caché de diálogos de forma segura."""
        if not self.is_active():
            return
        
        await asyncio.sleep(10) 
        
        logger.info("[USERBOT] Tarea en segundo plano: Iniciando precarga de diálogos...")
        try:
            count = 0
            async for _ in self.client.get_dialogs():
                count += 1
            logger.info(f"[USERBOT] Tarea en segundo plano: Caché poblada con {count} diálogos.")
        except Exception as e:
            logger.error(f"[USERBOT] Tarea en segundo plano: Falló la precarga de diálogos. Error: {e}")

    async def start(self):
        """Inicia el cliente Pyrogram y se autoconfigura con el ID de Mensajes Guardados."""
        if not all([self.api_id, self.api_hash, self.session_string]):
            logger.warning("[USERBOT] Faltan credenciales (API_ID, API_HASH, o SESSION_STRING). El Userbot no se iniciará.")
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
            self.saved_messages_id = me.id
            logger.info(f"[USERBOT] Cliente conectado como: {me.username or me.first_name}")
            logger.info(f"[USERBOT] Proxy de 'Mensajes Guardados' autoconfigurado con ID: {self.saved_messages_id}")
            asyncio.create_task(self._preload_dialogs_background_task())

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

    async def download_file(self, bot_username: str, message_id: int, task_id: str, download_path: str, progress_callback=None):
        """
        Descarga un archivo usando proxy y actualiza la tarea con el nombre de archivo real.
        """
        if not self.is_active():
            raise ConnectionError("El Userbot no está activo o conectado.")
        
        proxy_message = None
        try:
            from_chat_id = bot_username
            msg_id = message_id

            logger.info(f"[USERBOT] Reenviando mensaje {msg_id} desde {from_chat_id} al proxy {self.saved_messages_id}")
            proxy_message = await self.client.forward_messages(
                chat_id=self.saved_messages_id,
                from_chat_id=from_chat_id,
                message_ids=msg_id
            )
            
            if not proxy_message:
                raise Exception("El reenvío al chat proxy no devolvió un mensaje.")

            # --- INGENIERÍA DE EXTRACCIÓN DE METADATOS ---
            real_filename = None
            if proxy_message.video and proxy_message.video.file_name:
                real_filename = proxy_message.video.file_name
            elif proxy_message.document and proxy_message.document.file_name:
                real_filename = proxy_message.document.file_name
            elif proxy_message.audio and proxy_message.audio.file_name:
                real_filename = proxy_message.audio.file_name

            if real_filename:
                logger.info(f"[USERBOT] Nombre de archivo real extraído: {real_filename}. Actualizando DB.")
                db_instance.update_task(task_id, "original_filename", real_filename)
                db_instance.update_task(task_id, "final_filename", os.path.splitext(real_filename)[0])
            # ----------------------------------------------

            logger.info(f"[USERBOT] Descargando desde el mensaje proxy {proxy_message.id}")
            await self.client.download_media(
                message=proxy_message,
                file_name=download_path,
                progress=progress_callback
            )
            logger.info(f"[USERBOT] Descarga completada exitosamente en {download_path}")

        except Exception as e:
            logger.error(f"[USERBOT] Falló la descarga con proxy. Error: {e}")
            raise
        finally:
            if proxy_message:
                try:
                    await self.client.delete_messages(
                        chat_id=self.saved_messages_id,
                        message_ids=proxy_message.id
                    )
                    logger.info("[USERBOT] Mensaje proxy eliminado.")
                except Exception as e:
                    logger.warning(f"[USERBOT] No se pudo eliminar el mensaje proxy. Error: {e}")

# Instancia única para ser importada globalmente
userbot_instance = UserbotManager()