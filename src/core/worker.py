# --- START OF FILE src/core/worker.py ---

import logging
import time
import os
import asyncio
import re
from datetime import datetime
from pyrogram.enums import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.utils import (format_status_message, sanitize_filename, 
                               escape_html, _edit_status_message, ADMIN_USER_ID)
from src.core import ffmpeg, downloader
from src.core.downloader import AuthenticationError
from src.core.ffmpeg import get_media_info

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")

class ProgressContext:
    def __init__(self, bot, message, task, loop):
        self.bot = bot
        self.message = message
        self.task = task
        self.start_time = time.time()
        self.last_edit_time = 0
        self.last_update_text = ""
        self.loop = loop

progress_tracker = {}

async def _progress_callback_pyrogram(current, total, user_id, operation):
    if user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    elapsed = time.time() - ctx.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    percentage = (current / total) * 100 if total > 0 else 0
    
    user_mention = "Usuario"
    if hasattr(ctx.message, 'from_user') and ctx.message.from_user:
        user_mention = ctx.message.from_user.mention

    text = format_status_message(
        operation=operation, 
        filename=ctx.task.get('original_filename', 'archivo'),
        percentage=percentage, 
        processed_bytes=current, 
        total_bytes=total,
        speed=speed, 
        eta=eta, 
        engine="Pyrogram", 
        user_id=user_id,
        user_mention=user_mention
    )
    await _edit_status_message(user_id, text, progress_tracker)

async def _run_ffmpeg_with_progress(user_id: int, cmd: str, input_path: str):
    # Si la tarea no tiene progreso, ejecutar el comando sin monitorear.
    if user_id not in progress_tracker:
        process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            error_message = stderr.decode('utf-8', 'ignore')
            logger.error(f"FFmpeg falló (sin progreso). Código: {process.returncode}\nError: {error_message}")
            raise Exception(f"El proceso de FFmpeg falló: {error_message[-500:]}")
        return

    duration_info = get_media_info(input_path)
    total_duration_sec = float(duration_info.get('format', {}).get('duration', 0))
    if total_duration_sec == 0: logger.warning("No se pudo obtener la duración.")
    
    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    
    ctx = progress_tracker.get(user_id)
    start_time = time.time()

    all_stderr_lines = []
    while True:
        line = await process.stderr.readline()
        if not line: break
        line_str = line.decode('utf-8', 'ignore').strip()
        all_stderr_lines.append(line_str)
        if match := time_pattern.search(line_str):
            if total_duration_sec > 0:
                h, m, s, ms = map(int, match.groups())
                processed_sec = h * 3600 + m * 60 + s + ms / 100
                percentage = (processed_sec / total_duration_sec) * 100
                elapsed = time.time() - start_time
                speed_factor = processed_sec / elapsed if elapsed > 0 else 0
                eta = (total_duration_sec - processed_sec) / speed_factor if speed_factor > 0 else 0
                
                user_mention = "Usuario"
                if hasattr(ctx.message, 'from_user') and ctx.message.from_user:
                    user_mention = ctx.message.from_user.mention
                
                text = format_status_message(
                    operation="⚙️ Codificando...", filename=ctx.task.get('original_filename', 'archivo'),
                    percentage=percentage, processed_bytes=processed_sec, total_bytes=total_duration_sec,
                    speed=speed_factor, eta=eta, engine="FFmpeg", user_id=user_id,
                    user_mention=user_mention
                )
                await _edit_status_message(user_id, text, progress_tracker)
            
    await process.wait()
    if process.returncode != 0:
        error_message = "\n".join(all_stderr_lines)
        logger.error(f"FFmpeg falló. Código: {process.returncode}\nError: {error_message}")
        raise Exception(f"El proceso de FFmpeg falló: {error_message[-500:]}")

async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    config = task.get('processing_config', {})
    
    # --- ARQUITECTURA "HYPER-SPEED": Ejecución silenciosa ---
    status_message = None
    if not config.get('suppress_progress'):
        status_message = await bot.send_message(user_id, f"Iniciando: <code>{escape_html(task.get('original_filename') or task.get('url', 'Tarea'))}</code>", parse_mode=ParseMode.HTML)
        global progress_tracker
        progress_tracker[user_id] = ProgressContext(bot, status_message, task, asyncio.get_running_loop())
    
    files_to_clean = set()
    output_path = ""
    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("Tarea no encontrada.")
        if status_message: progress_tracker[user_id].task = task

        base_filename = os.path.join(DOWNLOAD_DIR, task_id)
        actual_download_path = ""

        if url := task.get('url'):
            format_id = config.get('download_format_id')
            if not format_id: raise Exception("La tarea no tiene 'download_format_id'.")
            
            progress_args = {'progress_tracker': progress_tracker, 'user_id': user_id} if not config.get('suppress_progress') else {}
            actual_download_path = await asyncio.to_thread(
                downloader.download_from_url, url, base_filename, format_id, **progress_args
            )

            if not actual_download_path:
                raise Exception("La descarga desde la URL falló o no se generó ningún archivo.")

        elif file_id := task.get('file_id'):
            actual_download_path = base_filename
            progress_args = {'progress': _progress_callback_pyrogram, 'progress_args': (user_id, "📥 Descargando...")} if not config.get('suppress_progress') else {}
            await bot.download_media(message=file_id, file_name=actual_download_path, **progress_args)
        else:
            raise Exception("La tarea no tiene URL ni file_id.")
        
        files_to_clean.add(actual_download_path)
        logger.info(f"Descarga completada en: {actual_download_path}")
        
        file_type = task.get('file_type', 'document')
        if status_message: await _edit_status_message(user_id, f"⚙️ Archivo ({file_type}) listo para procesar.", progress_tracker)
        
        final_filename_base = sanitize_filename(config.get('final_filename', task.get('original_filename', task_id)))
        final_ext = f".{config.get('audio_format', 'mp3')}" if file_type == 'audio' else ".mp4"
        if 'gif_options' in config: final_ext = ".gif"
        
        final_filename = f"{final_filename_base}{final_ext}"
        output_path = os.path.join(OUTPUT_DIR, final_filename)
        files_to_clean.add(output_path)
        
        commands = ffmpeg.build_ffmpeg_command(task, actual_download_path, output_path, None) # Thumb se maneja en el envío
        processed_file = actual_download_path
        if commands and commands[0]:
            await _run_ffmpeg_with_progress(user_id, commands[0], actual_download_path)
            processed_file = output_path
        elif output_path != actual_download_path:
             os.rename(actual_download_path, output_path)
             processed_file = output_path
        
        progress_args_upload = {'progress': _progress_callback_pyrogram, 'progress_args': (user_id, "⬆️ Subiendo...")} if not config.get('suppress_progress') else {}
        
        if file_type == 'audio':
            await bot.send_audio(user_id, audio=processed_file, caption=f"✅ <code>{escape_html(final_filename)}</code>", parse_mode=ParseMode.HTML, **progress_args_upload)
        else:
            await bot.send_video(user_id, video=processed_file, caption=f"✅ <code>{escape_html(final_filename)}</code>", parse_mode=ParseMode.HTML, **progress_args_upload)

        await db_instance.update_task(task_id, "status", "done")
        if status_message: await status_message.delete()

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        await db_instance.update_task(task_id, "status", "error")
        await db_instance.update_task(task_id, "last_error", str(e))
        if status_message:
            await _edit_status_message(user_id, f"❌ <b>Error Grave</b>\n\n<code>{escape_html(str(e))}</code>", progress_tracker)
        else:
            await bot.send_message(user_id, f"❌ Error al procesar <code>{escape_html(task.get('original_filename'))}</code>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)
    finally:
        if user_id in progress_tracker: del progress_tracker[user_id]
        if status_message_id_to_delete := config.get("status_message_id"):
            try: await bot.delete_messages(user_id, status_message_id_to_delete)
            except Exception as e: logger.warning(f"No se pudo borrar el mensaje de estado {status_message_id_to_delete}: {e}")
        
        for fpath in files_to_clean:
            if os.path.exists(fpath): 
                try: os.remove(fpath)
                except Exception as e: logger.error(f"No se pudo limpiar {fpath}: {e}")

async def worker_loop(bot_instance):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    while True:
        try:
            active_users = list(progress_tracker.keys())
            task = await db_instance.tasks.find_one_and_update(
                {"status": "queued", "user_id": {"$nin": active_users}},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}},
                sort=[('created_at', 1)]
            )
            if task:
                logger.info(f"Iniciando procesamiento de la tarea {task['_id']} para el usuario {task['user_id']}")
                asyncio.create_task(process_task(bot_instance, task))
            else:
                await asyncio.sleep(1)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(10)
# --- END OF FILE src/core/worker.py ---