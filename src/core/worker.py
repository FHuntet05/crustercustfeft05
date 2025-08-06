import logging
import time
import os
import asyncio
import re
from datetime import datetime
from telegram.ext import Application
from telegram.error import BadRequest, NetworkError

from src.db.mongo_manager import db_instance
# --- LÍNEA CRÍTICA AÑADIDA ---
# Se importa la función que faltaba para el manejo de errores.
from src.helpers.utils import format_status_message, sanitize_filename, escape_html
from src.core import ffmpeg, downloader
from src.core.userbot_manager import userbot_instance
from src.core.ffmpeg import get_media_info

logger = logging.getLogger(__name__)
DOWNLOAD_DIR, OUTPUT_DIR = os.path.join(os.getcwd(), "downloads"), os.path.join(os.getcwd(), "outputs")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
BOT_API_DOWNLOAD_LIMIT, BOT_API_UPLOAD_LIMIT = 20 * 1024 * 1024, 50 * 1024 * 1024

class ProgressContext:
    def __init__(self, bot, message, task):
        self.bot = bot
        self.message = message
        self.task = task
        self.start_time = time.time()
        self.last_edit_time = 0
        self.last_update_text = ""
        self.loop = asyncio.get_running_loop()

progress_tracker = {}

async def _edit_status_message(user_id: int, text: str):
    if user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    if text == ctx.last_update_text: return
    ctx.last_update_text = text
    
    current_time = time.time()
    if current_time - ctx.last_edit_time > 1.5:
        try:
            await ctx.bot.edit_message_text(text, chat_id=ctx.message.chat_id, message_id=ctx.message.message_id, parse_mode='HTML')
            ctx.last_edit_time = current_time
        except (BadRequest, NetworkError) as e:
            if "Message is not modified" not in str(e): logger.warning(f"No se pudo editar msg: {e}")

def sync_progress_callback(current, total, user_id, operation, engine="Userbot"):
    if user_id in progress_tracker:
        ctx = progress_tracker[user_id]
        asyncio.run_coroutine_threadsafe(
            async_progress_callback(current, total, user_id, operation, engine),
            ctx.loop
        )

async def async_progress_callback(current, total, user_id, operation, engine="Userbot"):
    if user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    percentage = (current / total) * 100 if total > 0 else 0
    elapsed = time.time() - ctx.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0

    text = format_status_message(
        operation=operation,
        filename=ctx.task.get('original_filename', 'archivo'),
        percentage=percentage,
        processed_bytes=current,
        total_bytes=total,
        speed=speed,
        eta=eta,
        engine=engine,
        user_id=user_id,
        user_mention=ctx.message.chat.mention_html()
    )
    await _edit_status_message(user_id, text)

async def _run_ffmpeg_with_progress(user_id: int, cmd: str, input_path: str):
    duration_info = get_media_info(input_path)
    total_duration_sec = float(duration_info.get('format', {}).get('duration', 0))
    if total_duration_sec == 0:
        logger.warning("No se pudo obtener la duración del video, el progreso de FFmpeg no funcionará.")

    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    
    while True:
        line = await process.stderr.readline()
        if not line: break
        
        line_str = line.decode('utf-8', 'ignore').strip()
        match = time_pattern.search(line_str)
        if match and total_duration_sec > 0:
            h, m, s, ms = map(int, match.groups())
            processed_sec = h * 3600 + m * 60 + s + ms / 100
            await async_progress_callback(processed_sec, total_duration_sec, user_id, "⚙️ Codificando...", "FFmpeg")
    
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error_message = stderr.decode('utf-8', 'ignore')
        logger.error(f"FFmpeg falló. Código: {process.returncode}\nError: {error_message}")
        raise Exception(f"El proceso de FFmpeg falló: {error_message[-500:]}")

async def _upload_file(user_id, output_path, file_type, caption, reply_markup):
    filename = os.path.basename(output_path)
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise Exception("El archivo de salida no existe o está vacío. El proceso FFmpeg probablemente falló.")

    progress = lambda c, t: sync_progress_callback(c, t, user_id, "⬆️ Subiendo...")
    
    if userbot_instance.is_active():
        if file_type == 'video':
            await userbot_instance.client.send_video(user_id, video=output_path, caption=caption, progress=progress)
        elif file_type == 'audio':
            await userbot_instance.client.send_audio(user_id, audio=output_path, caption=caption, progress=progress)
        else:
            await userbot_instance.client.send_document(user_id, document=output_path, caption=caption, progress=progress)
    else:
        if os.path.getsize(output_path) > BOT_API_UPLOAD_LIMIT:
            raise Exception(f"Archivo de salida ({os.path.getsize(output_path)}) excede el límite de 50MB y el Userbot no está activo.")
        
        ctx = progress_tracker.get(user_id)
        if not ctx: raise Exception("Contexto de progreso no encontrado para la subida con bot API.")
        
        with open(output_path, 'rb') as f:
            if file_type == 'video': await ctx.bot.send_video(user_id, video=f, filename=filename, caption=caption, reply_markup=reply_markup, write_timeout=600)
            elif file_type == 'audio': await ctx.bot.send_audio(user_id, audio=f, filename=filename, caption=caption, reply_markup=reply_markup, write_timeout=600)
            else: await ctx.bot.send_document(user_id, document=f, filename=filename, caption=caption, reply_markup=reply_markup, write_timeout=600)

async def _download_file_helper(task: dict, download_path: str):
    user_id = task['user_id']
    if os.path.exists(download_path):
        logger.info(f"El archivo {download_path} ya existe, omitiendo descarga.")
        return

    dl_progress = lambda c, t: sync_progress_callback(c, t, user_id, "📥 Descargando...")
    
    if userbot_instance.is_active() and task.get('forwarded_chat_id') and task.get('forwarded_message_id'):
        logger.info(f"Iniciando descarga con Userbot para la tarea {task['_id']}")
        await userbot_instance.download_file(
            chat_id=task['forwarded_chat_id'],
            message_id=task['forwarded_message_id'],
            task_id=str(task['_id']),
            download_path=download_path,
            progress_callback=dl_progress
        )
    elif task.get('file_id') and task.get('file_size', 0) <= BOT_API_DOWNLOAD_LIMIT:
        logger.info(f"Iniciando descarga con Bot API para la tarea {task['_id']}")
        ctx = progress_tracker.get(user_id)
        if not ctx: raise Exception("Contexto de progreso no encontrado para la descarga con bot API.")
        file_from_api = await ctx.bot.get_file(task['file_id'])
        await file_from_api.download_to_drive(download_path)
    else:
        error_msg = ("La descarga requiere Userbot (archivo grande o sin file_id), "
                     "pero la tarea no tiene referencia de mensaje reenviado o el Userbot está inactivo.")
        raise Exception(error_msg)

async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    status_message = await bot.send_message(user_id, f"Iniciando: <code>{task.get('original_filename') or task.get('url', 'Tarea')}</code>", parse_mode='HTML')
    
    global progress_tracker
    progress_tracker[user_id] = ProgressContext(bot, status_message, task)
    
    files_to_clean = set()
    try:
        task = db_instance.get_task(task_id)
        progress_tracker[user_id].task = task

        download_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{sanitize_filename(task.get('original_filename', 'file'))}")
        files_to_clean.add(download_path)

        if url := task.get('url'):
            format_id = task.get('processing_config', {}).get('download_format_id', 'best')
            if not downloader.download_from_url(url, download_path, format_id, lambda d: None):
                raise Exception("La descarga desde la URL falló.")
        elif task.get('forwarded_message_id') or task.get('file_id'):
            await _download_file_helper(task, download_path)
        else:
            raise Exception("La tarea no tiene URL ni referencia de archivo para descargar.")
        
        await _edit_status_message(user_id, "⚙️ Preparando para procesar...")
        config = task.get('processing_config', {})
        base_name, _ = os.path.splitext(task.get('original_filename', 'archivo'))
        final_filename_base = sanitize_filename(config.get('final_filename', base_name))
        ext_map = {'audio': f".{config.get('audio_format', 'mp3')}", 'video': ".mp4", 'document': os.path.splitext(task.get('original_filename', ''))[1]}
        ext = ".gif" if 'gif_options' in config else ext_map.get(task.get('file_type'), '.dat')
        final_filename = f"{final_filename_base}{ext}"
        output_path = os.path.join(OUTPUT_DIR, final_filename)
        files_to_clean.add(output_path)
        
        commands = ffmpeg.build_ffmpeg_command(task, download_path, output_path)
        for i, cmd in enumerate(commands):
            if not cmd: continue
            await _run_ffmpeg_with_progress(user_id, cmd, download_path)
        
        await _upload_file(user_id, output_path, task.get('file_type'), config.get('final_caption', f"✅ Proceso completado."), None)
        
        db_instance.update_task(task_id, "status", "done")
        await status_message.delete()

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        db_instance.update_task(task_id, "status", "error")
        db_instance.update_task(task_id, "last_error", str(e))
        # --- BLOQUE DE MANEJO DE ERROR CORREGIDO ---
        # Ahora escape_html está disponible y el mensaje de error se enviará correctamente.
        await _edit_status_message(user_id, f"❌ <b>Error Grave</b>\n\nHa ocurrido un fallo durante el procesamiento.\n\n<b>Motivo:</b>\n<code>{escape_html(str(e))}</code>")
    finally:
        if user_id in progress_tracker:
            del progress_tracker[user_id]
        for fpath in files_to_clean:
            if os.path.exists(fpath): 
                try: os.remove(fpath)
                except Exception as e: logger.error(f"No se pudo limpiar el archivo {fpath}: {e}")

async def worker_loop(application: Application):
    bot = application.bot
    logger.info("[WORKER] Bucle del worker iniciado en el hilo principal.")
    while True:
        try:
            task = db_instance.tasks.find_one_and_update(
                {"status": "queued"},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}}
            )
            if task:
                user_id = task['user_id']
                if user_id in progress_tracker:
                    logger.warning(f"El usuario {user_id} ya tiene una tarea en proceso. Re-encolando la tarea {task['_id']}.")
                    db_instance.update_task(str(task['_id']), "status", "queued")
                    await asyncio.sleep(10)
                    continue
                
                # Crear la tarea y añadir un "done callback" para registrar excepciones
                task_obj = asyncio.create_task(process_task(bot, task))
                task_obj.add_done_callback(lambda t: logger.error(f"La tarea {t.get_name()} falló con una excepción no recuperada: {t.exception()}", exc_info=t.exception()) if t.exception() else None)

            else:
                await asyncio.sleep(5)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(30)