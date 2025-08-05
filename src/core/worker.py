import logging
import time
import os
from bson.objectid import ObjectId
from telegram.ext import CallbackContext
from telegram.error import BadRequest

from src.db.mongo_manager import db_instance
from src.helpers.utils import create_progress_bar, format_bytes, format_time
from . import ffmpeg

logger = logging.getLogger(__name__)

# Rutas a las carpetas de trabajo
DOWNLOAD_DIR = "downloads/"
OUTPUT_DIR = "outputs/"

# --- Variables de Progreso (Simples, para un solo worker) ---
last_update_time = 0
start_time = 0

# --- Callback de Progreso para Telegram ---
async def progress_callback(current, total, context: CallbackContext, message_to_edit, operation: str):
    """Callback para actualizar el mensaje de estado durante la descarga/subida."""
    global last_update_time, start_time
    
    current_time = time.time()
    # Actualizar solo cada 2 segundos para no sobrecargar a Telegram
    if current_time - last_update_time < 2 and current != total:
        return
        
    last_update_time = current_time
    percentage = (current / total) * 100
    elapsed_time = current_time - start_time
    
    # Estimación de velocidad y tiempo restante
    speed = current / elapsed_time if elapsed_time > 0 else 0
    eta = ((total - current) / speed) if speed > 0 else 0
    
    progress_bar = create_progress_bar(percentage)
    
    text = (
        f"<b>{operation}...</b>\n\n"
        f"<code>{progress_bar} {percentage:.2f}%</code>\n\n"
        f"<b>Procesado:</b> {format_bytes(current)} de {format_bytes(total)}\n"
        f"<b>Velocidad:</b> {format_bytes(speed)}/s\n"
        f"<b>Tiempo:</b> Restante {format_time(eta)} | Total {format_time(elapsed_time)}"
    )
    
    try:
        await context.bot.edit_message_text(text, chat_id=message_to_edit.chat_id, message_id=message_to_edit.message_id, parse_mode='HTML')
    except BadRequest as e:
        # Ignorar errores de "Message is not modified"
        if "Message is not modified" not in str(e):
            logger.warning(f"No se pudo editar el mensaje de progreso: {e}")

# --- Lógica del Worker ---
async def process_task(task, context: CallbackContext):
    """Procesa una única tarea de principio a fin."""
    global start_time
    task_id = task['_id']
    user_id = task['user_id']
    
    # Enviar un mensaje de estado inicial que editaremos
    status_message = await context.bot.send_message(user_id, f"Iniciando proceso para: <code>{task['original_filename']}</code>", parse_mode='HTML')

    download_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{task['original_filename']}")
    output_path = None

    try:
        # 1. Descarga
        start_time = time.time()
        file_to_download = await context.bot.get_file(task['file_id'])
        await file_to_download.download_to_drive(
            download_path,
            read_timeout=60, # Timeout más largo para archivos grandes
            write_timeout=60,
            connect_timeout=60,
            pool_timeout=60,
            callback=lambda current, total: progress_callback(current, total, context, status_message, "⬇️ Descargando")
        )
        logger.info(f"[WORKER] Archivo para tarea {task_id} descargado en: {download_path}")

        # 2. Procesamiento (Usando el placeholder de ffmpeg.py)
        await context.bot.edit_message_text(f"⚙️ Procesando <code>{task['original_filename']}</code>...", chat_id=user_id, message_id=status_message.message_id, parse_mode='HTML')
        
        # Aquí la lógica real de FFmpeg. Por ahora, solo renombramos.
        extension = os.path.splitext(task['original_filename'])[1]
        final_filename_with_ext = f"{task['final_filename']}{extension}"
        output_path = os.path.join(OUTPUT_DIR, final_filename_with_ext)
        os.rename(download_path, output_path) # Simulación de procesamiento
        
        logger.info(f"[WORKER] Archivo para tarea {task_id} procesado en: {output_path}")

        # 3. Subida
        start_time = time.time()
        with open(output_path, 'rb') as f:
            if task['file_type'] == 'video':
                await context.bot.send_video(
                    chat_id=user_id,
                    video=f,
                    filename=final_filename_with_ext,
                    caption=f"✅ Proceso completado, Jefe.\nOriginal: {format_bytes(task['file_size'])}",
                    read_timeout=60, write_timeout=60, connect_timeout=60, pool_timeout=60,
                    callback=lambda current, total: progress_callback(current, total, context, status_message, "⬆️ Subiendo")
                )
            # Añadir elif para 'audio' y 'document'
            else:
                 await context.bot.send_document(
                    chat_id=user_id, document=f, filename=final_filename_with_ext,
                    caption=f"✅ Proceso completado, Jefe.\nOriginal: {format_bytes(task['file_size'])}",
                    read_timeout=60, write_timeout=60, connect_timeout=60, pool_timeout=60,
                    callback=lambda current, total: progress_callback(current, total, context, status_message, "⬆️ Subiendo")
                )

        db_instance.update_task_status(task_id, "done")
        await context.bot.delete_message(user_id, status_message.message_id) # Limpiar mensaje de estado

    except Exception as e:
        logger.critical(f"[WORKER] Error crítico al procesar la tarea {task_id}: {e}", exc_info=True)
        db_instance.update_task_status(task_id, "error")
        await context.bot.send_message(user_id, f"❌ Lo siento, Jefe. Ocurrió un error grave al procesar <code>{task['original_filename']}</code>.", parse_mode='HTML')

    finally:
        # 4. Limpieza
        if os.path.exists(download_path):
            os.remove(download_path)
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
        logger.info(f"[WORKER] Limpieza de archivos para tarea {task_id} completada.")


def start_worker_loop(context: CallbackContext):
    """Bucle principal del worker."""
    logger.info("[WORKER] El bucle del worker ha comenzado.")
    while True:
        try:
            task = db_instance.tasks.find_one_and_update(
                {"status": "queued"},
                {"$set": {"status": "downloading"}},
                sort=[("created_at", 1)]
            )
            if task:
                # Usamos asyncio.run para ejecutar la función asíncrona desde un hilo síncrono
                import asyncio
                asyncio.run(process_task(task, context))
            else:
                time.sleep(5)
        except Exception as e:
            logger.critical(f"[WORKER] El bucle del worker ha fallado: {e}", exc_info=True)
            time.sleep(30)