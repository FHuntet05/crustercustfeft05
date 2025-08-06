import os
import logging
import asyncio
from pyrogram import Client
from pyrogram.errors import MessageIdInvalid, ChannelInvalid, PeerIdInvalid, UserIsBlocked, RPCError

from src.db.mongo_manager import db_instance
from src.helpers.utils import sanitize_filename

logger = logging.getLogger(__name__)

class UserbotManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UserbotManager, cls).__new__(cls)
            cls._instance.api_id = os.getenv("API_ID")
            cls._instance.api_hash = os.getenv("API_HASH")
            cls._instance.session_string = os.getenv("USERBOT_SESSION_STRING")
            cls._instance.forward_chat_id = os.getenv("FORWARD_CHAT_ID")
            cls._instance.client = None
        return cls._instance

    async def start(self):
        if not all([self.api_id, self.api_hash, self.session_string]):
            logger.warning("[USERBOT] Faltan credenciales. Userbot no se iniciará.")
            return
        if not self.forward_chat_id:
            logger.warning("[USERBOT] FORWARD_CHAT_ID no está definido. El Userbot no podrá descargar archivos reenviados.")
        
        logger.info("[USERBOT] Iniciando cliente Pyrogram...")
        self.client = Client("userbot_session", api_id=int(self.api_id), api_hash=self.api_hash, session_string=self.session_string, in_memory=True)
        
        try:
            await self.client.start()
            me = await self.client.get_me()
            logger.info(f"[USERBOT] Cliente conectado como: {me.username or me.first_name}")

            if self.forward_chat_id:
                try:
                    # --- LÍNEA CRÍTICA BLINDADA ---
                    # Se usa .strip() para eliminar cualquier espacio o carácter invisible
                    chat_id_int = int(self.forward_chat_id.strip())
                    logger.info(f"[USERBOT] Verificando acceso al canal de trabajo: {chat_id_int}")
                    await self.client.get_chat(chat_id_int)
                    logger.info(f"[USERBOT] Verificación del canal de trabajo {chat_id_int} exitosa.")
                except ValueError:
                    logger.critical(f"[USERBOT] FALLO CRÍTICO DE CONFIGURACIÓN: El FORWARD_CHAT_ID '{self.forward_chat_id}' no es un número válido.")
                    raise ConnectionError("FORWARD_CHAT_ID en el archivo .env no es un ID numérico válido. Asegúrese de que no tenga espacios ni caracteres invisibles.")

        except (ValueError, PeerIdInvalid) as e:
            logger.critical(f"[USERBOT] FALLO CRÍTICO DE CONFIGURACIÓN: El Userbot no puede encontrar o acceder al canal con ID {self.forward_chat_id}.")
            logger.critical("[USERBOT] CAUSA MÁS PROBABLE: La 'USERBOT_SESSION_STRING' es incorrecta, está desactualizada o no pertenece a la cuenta que creó el canal.")
            logger.critical("[USERBOT] ACCIÓN REQUERIDA: Regenere la session string con 'generate_session.py' usando la cuenta correcta y actualice el .env.")
            raise ConnectionError(f"El Userbot no pudo acceder al FORWARD_CHAT_ID. Verifique la session string y los permisos del canal.")
        except RPCError as e:
            logger.critical(f"[USERBOT] Error de autenticación o conexión con Pyrogram: {e}")
            self.client = None
        except Exception as e:
            logger.critical(f"[USERBOT] Error inesperado al iniciar el Userbot: {e}")
            raise

    async def stop(self):
        if self.is_active():
            await self.client.stop()
            logger.info("[USERBOT] Cliente Pyrogram detenido.")

    def is_active(self) -> bool:
        return self.client and self.client.is_connected

    async def download_file(self, chat_id: int, message_id: int, task_id: str, download_path: str, progress_callback=None):
        if not self.is_active():
            raise ConnectionError("Userbot no está activo o no pudo conectarse.")
        
        message_to_delete = None
        try:
            logger.info(f"[USERBOT] Obteniendo mensaje de trabajo {message_id} desde chat {chat_id}")
            message_to_download = await self.client.get_messages(chat_id, message_id)
            
            if not message_to_download or not message_to_download.media:
                raise FileNotFoundError("El mensaje de trabajo reenviado no existe, fue eliminado o no contiene medios.")

            message_to_delete = message_to_download

            logger.info(f"[USERBOT] Descargando desde el mensaje de trabajo {message_to_download.id}")
            downloaded_file_path = await self.client.download_media(
                message=message_to_download,
                file_name=download_path,
                progress=progress_callback
            )
            logger.info(f"[USERBOT] Descarga completada. Archivo guardado en: {downloaded_file_path}")

        except (MessageIdInvalid, ChannelInvalid, PeerIdInvalid):
            logger.error(f"[USERBOT] ID de mensaje o chat inválido para la descarga. Tarea: {task_id}")
            raise FileNotFoundError(f"No se pudo encontrar el mensaje a descargar. Es posible que haya sido eliminado del chat del Userbot.")
        except UserIsBlocked:
            logger.error(f"[USERBOT] El bot ha sido bloqueado por el usuario de destino de la descarga. Tarea: {task_id}")
            raise ConnectionError("El Userbot fue bloqueado y no puede enviar/recibir mensajes.")
        except Exception as e:
            logger.error(f"[USERBOT] Falló la descarga desde el chat de trabajo. Error: {e}")
            raise
        finally:
            if message_to_delete:
                try:
                    await self.client.delete_messages(chat_id=chat_id, message_ids=message_to_delete.id)
                    logger.info("[USERBOT] Mensaje de trabajo eliminado del chat del Userbot.")
                except Exception as e:
                    logger.warning(f"[USERBOT] No se pudo eliminar el mensaje de trabajo. Puede requerir limpieza manual. Error: {e}")

# Instancia única para ser importada globalmente
userbot_instance = UserbotManager()