import logging
import time
import os
import asyncio
from bson.objectid import ObjectId
from telegram.ext import CallbackContext
from telegram.error import BadRequest

from src.db.mongo_manager import db_instance
from src.helpers.utils import create_progress_bar, format_bytes, format_time
from . import ffmpeg

logger = logging.getLogger(__name__)

# Rutas a las carpetas de trabajo
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")

# --- Variables de Progreso (Simples, para un solo worker) ---
last_update_time = 0
start_time = 0

# --- Callbacks de Progreso ---
async def edit_status_message(context: CallbackContext, message, text):
    """Función segura para editar un mensaje, manejando errores comunes."""
    try:
        await context.bot.edit_message_text(text, chat_id=message.chat_id, message_id=message.message_id, parse_mode='HTML')
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"No se pudo editar el mensaje de estado: {e}")

async def download_progress_callback(current, total, context: CallbackContext, message, operation: str):
    """Callback para el progreso de descarga/subida de Telegram."""
    global last_update_time, start_time
    current_time = time.time()
    if current_time - last_update_time < 2 and current != total: return
    last_update_time = current_time
    percentage = (current / total) * 100
    elapsed_time = current_time - start_time
    speed = current / elapsed_time if elapsed_time > 0 else 0
    eta = ((total - current) / speed) if speed > 0 else 0
    progress_bar = create_progress_bar(percentage)
    text = (f"<b>{operation}</b>\n\n<code>{progress_bar} {percentage:.1f}%</code>\n\n"
            f"<b>Progreso:</b> {format_bytes(current)} / {format_bytes(total)}\n"
            f"<b>Velocidad:</b> {format_bytes(speed)}/s\n"
            f"<b>ETA:</b> {format_time(eta)}")
    await edit_status_message(context, message, text)

def ffmpeg_progress_callback(percentage, time_processed, duration, context: CallbackContext, message, operation: str):
    """Callback síncrono para el progreso de FFmpeg."""
    global last_update_time, start_time
    current_time = time.time()
    if current_time - last_update_time < 2 and percentage < 100: return
    last_update_time = current_time
    progress_bar = create_progress_bar(percentage)
    text = (f"<b>{operation}</b>\n\n<code>{progress_bar} {percentage:.1f}%</code>\n\n"
            f"<b>Tiempo:</b> {format_time(time_processed)} / {format_time(duration)}")
    # Como esta función es síncrona, necesitamos correr la corutina de edición
    asyncio.run(edit_status_message(context, message, text))
# --- Lógica del Worker ---
async def process_task(task, app: Application):
    task_id, user_id = task['_id'], task['user_id']
    status_message = await app.bot.send_message(user_id, f"Iniciando proceso para: <code>{task.get('original_filename') or task.get('url')}</code>", parse_mode='HTML')
    
    download_path = os.path.join(DOWNLOAD_DIR, str(task_id))
    output_path = None
    
    try:
        # 1. Descarga (desde Telegram o URL)
        if task.get('url'):
            # Lógica de descarga desde URL
            await status_message.edit_text("Analizando URL...", parse_mode='HTML')
            info = downloader.get_url_info(task['url'])
            if not info: raise Exception("No se pudo obtener información de la URL.")
            
            # Placeholder para el hook de progreso
            def ytdl_progress_hook(d):
                if d['status'] == 'downloading':
                    # Aquí iría el código para actualizar el mensaje de Telegram
                    pass
            
            success = downloader.download_from_url(task['url'], download_path, ytdl_progress_hook)
            if not success: raise Exception("La descarga desde la URL falló.")
        else:
            # Lógica de descarga desde Telegram
            file_to_download = await app.bot.get_file(task['file_id'])
            await file_to_download.download_to_drive(download_path)

        # 2. Procesamiento
        await status_message.edit_text("⚙️ Procesando...", parse_mode='HTML')
        duration = ffmpeg.get_media_info(download_path).get('format', {}).get('duration', '0')
        final_filename = f"{task['final_filename']}{os.path.splitext(task['original_filename'])[1] if task['original_filename'] else '.mp4'}"
        output_path = os.path.join(OUTPUT_DIR, final_filename)
        
        command = ffmpeg.build_ffmpeg_command(task, download_path, output_path)
        ffmpeg_success = ffmpeg.run_ffmpeg_process(command) # Sin progreso por ahora para simplificar
        if not ffmpeg_success: raise Exception("El proceso FFmpeg falló.")

        # 3. Subida
        await status_message.edit_text("⬆️ Subiendo...", parse_mode='HTML')
        with open(output_path, 'rb') as f:
            if task['file_type'] == 'video':
                await app.bot.send_video(user_id, video=f, filename=final_filename, caption="✅ Proceso completado, Jefe.")
            else: # Añadir audio, etc.
                await app.bot.send_document(user_id, document=f, filename=final_filename, caption="✅ Proceso completado, Jefe.")
        
        db_instance.update_task(task_id, "status", "done")
        await status_message.delete()

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        db_instance.update_task(task_id, "status", "error")
        await status_message.edit_text(f"❌ Lo siento, Jefe. Ocurrió un error grave al procesar la tarea.", parse_mode='HTML')
    finally:
        # 4. Limpieza
        if os.path.exists(download_path): os.remove(download_path)
        if output_path and os.path.exists(output_path): os.remove(output_path)

def worker_thread_runner(application: Application):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("[WORKER] Bucle del worker iniciado.")
    while True:
        try:
            task = db_instance.tasks.find_one_and_update({"status": "queued"}, {"$set": {"status": "processing"}})
            if task:
                loop.run_until_complete(process_task(task, application))
            else:
                time.sleep(5)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle falló: {e}", exc_info=True)
            time.sleep(30)