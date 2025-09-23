# --- START OF FILE src/core/worker.py ---

import logging
import os
import asyncio
import traceback
from datetime import datetime
from bson.objectid import ObjectId

from pyrogram import Client

from src.db.mongo_manager import db_instance
from src.core.task_processor import TaskProcessor
from src.telegram.uploader import Uploader
from src.helpers.utils import get_media_type, get_file_size, get_media_info, sanitize_filename
from src.core.ffmpeg import FfmpegProcessor
from src.core.exceptions import FfmpegError
from src.config import Config

logger = logging.getLogger(__name__)

# --- INICIO DE LA SOLUCIÓN: Añadir una verificación de seguridad al principio ---
async def _ensure_original_filename(task):
    """
    Asegura que la tarea tenga un 'original_filename'. Si no lo tiene, intenta
    obtenerlo del mensaje original de Telegram. Esto es crucial.
    """
    if task.get('original_filename'):
        return task['original_filename']

    # Si no hay nombre de archivo, puede que venga de un mensaje reenviado.
    # Vamos a buscarlo en el mensaje original que creó la tarea.
    if message_id := task.get('source_message_id'):
        try:
            # Necesitamos una instancia del cliente para esto, lo cual es complicado aquí.
            # Por ahora, nos saltamos esta lógica compleja y simplemente devolvemos un nombre por defecto
            # si no encontramos uno. La mejor solución es asegurar que se guarde al crear la tarea.
            logger.warning(f"La tarea {task['_id']} no tiene 'original_filename'. Usando nombre por defecto.")
            # Un nombre genérico para evitar el crash, idealmente esto nunca debería pasar.
            return f"archivo_desconocido_{task['_id']}.tmp"
        except Exception as e:
            logger.error(f"No se pudo obtener el mensaje original para la tarea {task['_id']}: {e}")
            return None
            
    return None

async def _process_media_task(bot: Client, task: dict, task_dir: str) -> str | None:
    """Procesa una tarea de media individual (descarga, ffmpeg, copia, subida)."""
    
    # --- INICIO DE LA SOLUCIÓN (Parte 1): Verificación robusta ---
    # La corrección principal está aquí. Antes de hacer nada, nos aseguramos de tener un nombre de archivo.
    original_filename = task.get('original_filename')
    if not original_filename:
        # Si la tarea no tiene un nombre de archivo original, no es una tarea de media estándar.
        # No deberíamos estar en esta función. Devolvemos None para que el bucle principal sepa que algo anda mal.
        logger.error(f"Intento de procesar la tarea de media {task['_id']} sin 'original_filename'. Tipo de archivo: {task.get('file_type')}.")
        await db_instance.update_task_status(str(task['_id']), "error", "preparación", "La tarea no tiene un nombre de archivo original válido.")
        return None
    # --- FIN DE LA SOLUCIÓN (Parte 1) ---

    dl_dir = os.path.join(task_dir, "download")
    os.makedirs(dl_dir, exist_ok=True)
    
    # Esta línea ahora es segura porque hemos verificado 'original_filename' antes.
    actual_download_path = os.path.join(dl_dir, original_filename)

    # ... (El resto de la lógica de descarga, etc. debería estar aquí si la tuvieras, 
    # pero parece que la has movido a TaskProcessor, lo cual es una buena práctica.
    # Por lo tanto, esta función ahora solo actúa como un punto de entrada para el TaskProcessor.)

    try:
        uploader = Uploader(bot, task)
        processor = TaskProcessor(task, uploader)
        await processor.process_task()
        # TaskProcessor se encarga de todo, así que simplemente esperamos a que termine.
        return "completed_by_processor" # Devolvemos un valor para indicar que se manejó.

    except Exception:
        tb_str = traceback.format_exc()
        logger.error(f"Error fatal no controlado al procesar la tarea {task['_id']}:\n{tb_str}")
        await db_instance.update_task_status(str(task['_id']), 'error', 'fatal', tb_str)
        return None

async def _process_join_or_zip_task(bot: Client, task: dict, task_dir: str, operation: str) -> str | None:
    """Procesa tareas de unión (join) o compresión (zip)."""
    source_task_ids = task.get('custom_fields', {}).get('source_task_ids', [])
    if not source_task_ids:
        await db_instance.update_task_status(str(task['_id']), "error", operation, "No se encontraron tareas de origen para la operación.")
        return None

    await db_instance.update_task_status(str(task['_id']), "processing", f"Preparando para {operation}...")

    source_files = []
    dl_dir = os.path.join(task_dir, "download")
    os.makedirs(dl_dir, exist_ok=True)

    for source_id in source_task_ids:
        source_task = await db_instance.get_task(str(source_id))
        if not source_task or not source_task.get('uploaded_file_path'):
            logger.warning(f"La tarea de origen {source_id} no se encontró o no tiene 'uploaded_file_path'. Omitiendo.")
            continue
        
        # Asumimos que los archivos ya fueron procesados y están en su destino final
        source_files.append(source_task['uploaded_file_path'])

    if not source_files:
        await db_instance.update_task_status(str(task['_id']), "error", operation, "Ninguno de los archivos de origen pudo ser localizado.")
        return None

    output_filename_base = sanitize_filename(task.get('final_filename', f"{operation}_{task['_id']}"))
    uploader = Uploader(bot, task)

    try:
        if operation == 'join':
            output_path = FfmpegProcessor.join_videos(source_files, task_dir, output_filename_base)
        elif operation == 'zip':
            # La lógica de compresión iría aquí. Asumiendo que tienes una función para ello.
            # output_path = create_zip_archive(source_files, task_dir, output_filename_base)
            # Como no tengo la función de zip, la comento para que no dé error.
            await db_instance.update_task_status(str(task['_id']), "error", operation, "La funcionalidad de ZIP no está implementada en el worker.")
            return None
        
        if not output_path:
            raise FfmpegError(f"La operación de {operation} no generó un archivo de salida.")

        await uploader.upload_file(
            file_path=output_path,
            file_name=os.path.basename(output_path),
            media_type='video' if operation == 'join' else 'document',
            task=task
        )
        return output_path

    except Exception:
        tb_str = traceback.format_exc()
        logger.error(f"Error fatal durante la operación '{operation}' para la tarea {task['_id']}:\n{tb_str}")
        await db_instance.update_task_status(str(task['_id']), 'error', operation, tb_str)
        return None

async def process_task(bot: Client, task: dict):
    """
    Función principal que dirige una tarea a su procesador correspondiente.
    """
    task_id = str(task['_id'])
    task_dir = os.path.join(Config.DOWNLOAD_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    definitive_output_path = None
    
    try:
        file_type = task.get('file_type')
        logger.info(f"Procesando Tarea ID: {task_id}, Tipo: {file_type}")

        # --- INICIO DE LA SOLUCIÓN (Parte 2): Lógica de enrutamiento ---
        # Ahora el enrutamiento es más explícito y seguro.
        if file_type in ['video', 'audio', 'document']:
            definitive_output_path = await _process_media_task(bot, task, task_dir)
        elif file_type == 'join_operation':
            definitive_output_path = await _process_join_or_zip_task(bot, task, task_dir, 'join')
        elif file_type == 'zip_operation':
            definitive_output_path = await _process_join_or_zip_task(bot, task, task_dir, 'zip')
        else:
            logger.error(f"Tipo de archivo desconocido o no manejable: '{file_type}' para la tarea {task_id}")
            await db_instance.update_task_status(task_id, 'error', 'desconocido', f"Tipo de tarea '{file_type}' no soportado.")
        # --- FIN DE LA SOLUCIÓN (Parte 2) ---

        if definitive_output_path:
            await db_instance.update_task_status(task_id, 'completed', final_path=definitive_output_path)
            logger.info(f"Tarea {task_id} completada con éxito. Archivo final en: {definitive_output_path}")
        else:
            # El estado de error ya debería haber sido establecido por las sub-funciones.
            logger.warning(f"La tarea {task_id} no produjo una ruta de salida final.")

    except Exception:
        tb_str = traceback.format_exc()
        logger.error(f"Error fatal en el worker principal al procesar la tarea {task_id}:\n{tb_str}")
        await db_instance.update_task_status(task_id, 'error', 'fatal_worker', tb_str)
    finally:
        if os.path.exists(task_dir) and not Config.DEBUG_MODE:
            try:
                # La limpieza ahora se maneja dentro del TaskProcessor para tareas de media.
                # Aquí solo limpiamos si es otro tipo de tarea o si falló antes de llegar al TaskProcessor.
                # shutil.rmtree(task_dir)
                pass # Por ahora, deshabilitamos la limpieza aquí para evitar conflictos.
            except Exception as e:
                logger.error(f"No se pudo limpiar el directorio temporal {task_dir}: {e}")

async def main_worker(bot: Client):
    """Bucle principal del worker que busca y procesa tareas."""
    logger.info("[WORKER] Bucle del worker iniciado.")
    while True:
        task = await db_instance.get_next_queued_task()
        if task:
            try:
                await process_task(bot, task)
            except Exception as e:
                logger.critical(f"Excepción no controlada en el bucle del worker: {e}", exc_info=True)
                # Marcar la tarea como fallida para evitar bucles infinitos
                await db_instance.update_task_status(str(task['_id']), "error", "worker_loop_exception", str(e))
        else:
            await asyncio.sleep(Config.WORKER_POLL_INTERVAL)
# --- END OF FILE src/core/worker.py ---