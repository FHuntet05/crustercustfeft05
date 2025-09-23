# --- START OF FILE src/core/task_processor.py ---

import logging
import os
import shutil
import traceback
from typing import Dict, Any, Optional

from src.config import Config
from src.db.mongo_manager import db_instance
from src.core.downloader import download_from_url
from src.core.ffmpeg import FfmpegProcessor
from src.core.exceptions import FfmpegError, NetworkError, AuthenticationError
from src.logic.path_resolver import PathResolver
from src.logic.filename_generator import generate_final_filename
from src.telegram.uploader import Uploader
from src.helpers.utils import get_media_type, get_file_size, get_media_info

logger = logging.getLogger(__name__)

class TaskProcessor:
    def __init__(self, task: Dict[str, Any], uploader: Uploader):
        self.task = task
        self.task_id = str(task['_id'])
        self.user_id = task['user_id']
        self.uploader = uploader
        self.resource_manager = uploader.resource_manager
        self.path_resolver = PathResolver()
        self.temp_dir = self.resource_manager.create_temp_dir(self.task_id)

    async def process_task(self):
        operation_name = "inicialización"
        try:
            logger.info(f"[TASK:{self.task_id}] Iniciando procesamiento.")
            
            # --- Fase 1: Descarga ---
            operation_name = "descarga"
            source_path = await self._handle_download()
            if not source_path: return

            # --- Fase 2: Procesamiento con FFmpeg ---
            operation_name = "procesamiento"
            final_filename, processed_path = await self._handle_processing(source_path)
            if not processed_path: return

            # --- Fase 3: Copia a destino final (si aplica) ---
            operation_name = "copiar"
            final_destination_path = await self._handle_copy_to_final_destination(final_filename, processed_path)
            # Nota: Si la copia falla, no detenemos el proceso de subida.

            # --- Fase 4: Subida ---
            operation_name = "subida"
            await self._handle_upload(final_filename, processed_path)
            
            await self.update_task_status("completed", "✅ Tarea Completada")
            logger.info(f"[TASK:{self.task_id}] Procesamiento completado con éxito.")

        except (FfmpegError, NetworkError, AuthenticationError) as e:
            logger.error(f"[TASK:{self.task_id}] Error conocido en '{operation_name}': {e}")
            await self.update_task_status("error", "❌ Error en Tarea", operation_name, str(e))
        except Exception as e:
            error_details = f"{type(e).__name__}: {e}"
            logger.critical(f"[TASK:{self.task_id}] Error crítico no controlado en '{operation_name}': {error_details}", exc_info=True)
            await self.update_task_status("error", "❌ Error Fatal en Tarea", operation_name, error_details, traceback.format_exc())
        finally:
            self.resource_manager.cleanup_temp_dir(self.task_id)
            logger.info(f"[TASK:{self.task_id}] Limpieza de recursos completada.")

    async def _handle_download(self) -> Optional[str]:
        await self.update_task_status("processing", "⬇️ Descargando...")
        
        file_id_or_url = self.task['file_id_or_url']
        output_base = os.path.join(self.temp_dir, self.task_id)

        if "http" in file_id_or_url:
            source_path = download_from_url(file_id_or_url, output_base)
        else:
            source_path = await self.uploader.download_telegram_file(file_id_or_url, output_base)
        
        if not source_path or not os.path.exists(source_path):
            await self.update_task_status("error", "Error en Tarea", "descarga", "El archivo no pudo ser descargado o no se encontró tras la descarga.")
            return None
            
        logger.info(f"[TASK:{self.task_id}] Archivo descargado en: {source_path}")
        return source_path

    async def _handle_processing(self, source_path: str) -> tuple[Optional[str], Optional[str]]:
        await self.update_task_status("processing", "⚙️ Procesando...")
        
        config = self.task.get('processing_config', {})
        media_info = get_media_info(source_path)
        final_filename = generate_final_filename(self.task, media_info, config)

        ffmpeg_processor = FfmpegProcessor(source_path, self.temp_dir, config, media_info)
        processed_path = ffmpeg_processor.run()
        
        if not processed_path:
            logger.info(f"[TASK:{self.task_id}] No se requirió procesamiento FFmpeg, usando archivo original.")
            return final_filename, source_path
        
        logger.info(f"[TASK:{self.task_id}] Archivo procesado por FFmpeg en: {processed_path}")
        return final_filename, processed_path

    async def _handle_copy_to_final_destination(self, final_filename: str, source_path: str) -> Optional[str]:
        if not Config.ENABLE_COPY_TO_DESTINATION:
            return None

        await self.update_task_status("processing", "🗂️ Copiando a destino...")
        destination_path = self.path_resolver.get_path_for_file(final_filename)

        # --- INICIO DE LA SOLUCIÓN ---
        # Si el PathResolver falla (devuelve None), no nos rendimos. Usamos una ruta por defecto.
        if not destination_path:
            logging.warning(f"[TASK:{self.task_id}] PathResolver no pudo determinar una ruta para '{final_filename}'. Usando ruta por defecto.")
            destination_path = Config.DEFAULT_DESTINATION_PATH
            
            # Verificación de seguridad: si ni la ruta por defecto está configurada, ahora sí fallamos y notificamos.
            if not destination_path:
                error_msg = "PathResolver falló y no hay una ruta de destino por defecto configurada (DEFAULT_DESTINATION_PATH en .env)."
                await self.update_task_status("error", "Error Fatal en Tarea", "copiar", error_msg)
                return None
        # --- FIN DE LA SOLUCIÓN ---

        try:
            # Asegurarse de que la carpeta de destino exista
            os.makedirs(destination_path, exist_ok=True)
            
            final_destination = os.path.join(destination_path, final_filename)
            
            logger.info(f"[TASK:{self.task_id}] Copiando de '{source_path}' a '{final_destination}'")
            shutil.copy(source_path, final_destination)
            logger.info(f"[TASK:{self.task_id}] Copia a destino final completada.")
            return final_destination

        except Exception as e:
            error_details = f"{type(e).__name__}: {e}"
            logger.error(f"[TASK:{self.task_id}] Falló la copia a destino final: {error_details}", exc_info=True)
            # Notificamos el error pero no detenemos el proceso, la subida a Telegram es más importante.
            await self.uploader.send_warning_message(f"⚠️ **Alerta en Tarea:**\nNo se pudo copiar el archivo a su destino final.\n\n**Motivo:** `{error_details}`")
            return None

    async def _handle_upload(self, final_filename: str, file_path: str):
        await self.update_task_status("processing", "⬆️ Subiendo...")
        
        media_type = get_media_type(file_path)
        file_size = get_file_size(file_path)
        
        if file_size > 2000 * 1024 * 1024:
            await self.uploader.send_warning_message("⚠️ **Alerta en Tarea:**\nEl archivo final supera los 2GB y no puede ser subido a Telegram.")
            return

        await self.uploader.upload_file(
            file_path=file_path,
            file_name=final_filename,
            media_type=media_type,
            task=self.task
        )

    async def update_task_status(self, status: str, text: str, operation: Optional[str] = None, details: Optional[str] = None, tb: Optional[str] = None):
        await db_instance.update_task_status(self.task_id, status, operation, details, tb)
        await self.uploader.edit_task_message(text, details)

# --- END OF FILE src/core/task_processor.py ---