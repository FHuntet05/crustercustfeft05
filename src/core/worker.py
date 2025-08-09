# --- START OF FILE src/core/worker.py ---

import logging
import time
import os
import asyncio
import re
import glob
from datetime import datetime
from pyrogram.enums import ParseMode
from bson.objectid import ObjectId
import shutil

from src.db.mongo_manager import db_instance
from src.helpers.utils import (format_status_message, sanitize_filename, 
                               escape_html, _edit_status_message,
                               generate_summary_caption)
from src.core import ffmpeg
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
        self.last_update_time = 0
        self.last_update_text = ""
        self.loop = loop

progress_tracker = {}

def _progress_callback_pyrogram(current, total, user_id, operation_title, status_tag, file_info):
    ctx = progress_tracker.get(user_id)
    if not ctx: return

    current_time = time.time()
    if current_time - ctx.last_update_time < 1.5 and current < total:
        return
    ctx.last_update_time = current_time

    if not total or total <= 0: return
    
    elapsed = current_time - ctx.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    percentage = (current / total) * 100
    
    text = format_status_message(
        operation_title=operation_title, percentage=percentage,
        processed_bytes=current, total_bytes=total, speed=speed, eta=eta,
        elapsed=elapsed, status_tag=status_tag, engine="Pyrogram",
        user_id=user_id, file_info=file_info
    )
    
    coro = _edit_status_message(user_id, text, progress_tracker)
    asyncio.run_coroutine_threadsafe(coro, ctx.loop)

async def _run_ffmpeg_with_progress(user_id: int, cmd: str, input_path: str):
    duration_info = get_media_info(input_path)
    total_duration_sec = float(duration_info.get('format', {}).get('duration', 0))
    
    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    
    ctx = progress_tracker.get(user_id)
    if not ctx:
        await process.kill()
        return

    stderr_buffer = ""
    all_stderr_lines = []

    while True:
        chunk = await process.stderr.read(1024)
        if not chunk: break
        
        stderr_buffer += chunk.decode('utf-8', 'ignore')
        lines = stderr_buffer.split('\r')
        stderr_buffer = lines.pop(-1)
        
        for line in lines:
            if not line: continue
            all_stderr_lines.append(line.strip())
            if match := time_pattern.search(line):
                if total_duration_sec > 0:
                    h, m, s, ms = map(int, match.groups())
                    processed_sec = h * 3600 + m * 60 + s + ms / 100
                    percentage = (processed_sec / total_duration_sec) * 100
                    elapsed = time.time() - ctx.start_time
                    speed_factor = processed_sec / elapsed if elapsed > 0 else 0
                    eta = (total_duration_sec - processed_sec) / speed_factor if speed_factor > 0 else 0
                    
                    text = format_status_message(
                        operation_title="Task is being Processed!", percentage=percentage,
                        processed_bytes=processed_sec, total_bytes=total_duration_sec,
                        speed=speed_factor, eta=eta, elapsed=elapsed,
                        status_tag="#Processing", engine="FFmpeg", user_id=user_id
                    )
                    await _edit_status_message(user_id, text, progress_tracker)

    await process.wait()
    if process.returncode != 0:
        error_log = "\n".join(all_stderr_lines)
        raise Exception(f"El proceso de FFmpeg falló. Log:\n{error_log}")

async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    status_message, files_to_clean = None, set()
    actual_download_path = None

    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("Tarea no encontrada.")
        
        config = task.get('processing_config', {})
        original_filename = task.get('original_filename') or task.get('url', 'Tarea sin nombre')
        
        status_message = await bot.send_message(user_id, f"Iniciando tarea para <code>{escape_html(original_filename)}</code>...", parse_mode=ParseMode.HTML)
        
        global progress_tracker
        progress_tracker[user_id] = ProgressContext(bot, status_message, task, asyncio.get_running_loop())

        dl_dir = os.path.join(DOWNLOAD_DIR, task_id)
        os.makedirs(dl_dir, exist_ok=True)
        files_to_clean.add(dl_dir)
        
        # [CONCURRENCY FIX]
        # Creamos una tarea separada para la descarga para que no bloquee el bucle de eventos.
        download_task = asyncio.create_task(
            bot.download_media(
                message=task['file_id'],
                file_name=os.path.join(dl_dir, original_filename),
                progress=_progress_callback_pyrogram,
                progress_args=(user_id, "Downloading...", "#TelegramDownload", "1/1")
            )
        )
        
        # Mientras la descarga ocurre, podemos hacer otras cosas o simplemente esperar sin bloquear.
        # En este caso, simplemente esperamos a que la tarea de descarga termine.
        actual_download_path = await download_task
        if not actual_download_path or not os.path.exists(actual_download_path):
             raise Exception("La descarga del archivo falló o fue cancelada.")

        initial_size = os.path.getsize(actual_download_path)
        
        watermark_path = None
        if wm_conf := config.get('watermark'):
            if wm_conf.get('type') == 'image' and (wm_id := wm_conf.get('file_id')):
                await _edit_status_message(user_id, "Descargando marca de agua...", progress_tracker)
                watermark_path = await bot.download_media(wm_id, file_name=os.path.join(dl_dir, "watermark_img"))
                files_to_clean.add(watermark_path)

        await _edit_status_message(user_id, "Preparando para procesar...", progress_tracker)
        
        final_filename = sanitize_filename(config.get('final_filename', original_filename))
        output_path = os.path.join(OUTPUT_DIR, final_filename)
        files_to_clean.add(output_path)
        
        commands, definitive_output_path = ffmpeg.build_ffmpeg_command(
            task, actual_download_path, output_path, watermark_path=watermark_path
        )
        
        for cmd in commands:
            await _run_ffmpeg_with_progress(user_id, cmd, actual_download_path)
        
        # Lógica de subida ...
        final_size = os.path.getsize(definitive_output_path)
        caption = generate_summary_caption(task, initial_size, final_size, os.path.basename(definitive_output_path))
        prog_args_upload = (user_id, "Uploading...", "#TelegramUpload", "1/1")
        await bot.send_video(user_id, video=definitive_output_path, caption=caption, parse_mode=ParseMode.HTML, progress=_progress_callback_pyrogram, progress_args=prog_args_upload)

        await db_instance.update_task(task_id, "status", "done")
        await status_message.delete()

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        error_message = f"❌ <b>Error Fatal en Tarea</b>\n<code>{escape_html(original_filename)}</code>\n\n<b>Motivo:</b>\n<pre>{escape_html(str(e))}</pre>"
        
        await db_instance.update_task(task_id, "status", "error")
        await db_instance.update_task(task_id, "last_error", str(e))
        
        if status_message:
            try: await status_message.edit_text(error_message, parse_mode=ParseMode.HTML)
            except Exception: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
        else:
            await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)

    finally:
        if user_id in progress_tracker: del progress_tracker[user_id]
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath): shutil.rmtree(fpath, ignore_errors=True)
                elif os.path.exists(fpath): os.remove(fpath)
            except Exception as e: logger.error(f"No se pudo limpiar {fpath}: {e}")

async def worker_loop(bot_instance):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    while True:
        try:
            processing_users = list(progress_tracker.keys())
            task = await db_instance.tasks.find_one_and_update(
                {"status": "queued", "user_id": {"$nin": processing_users}},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}},
                sort=[('created_at', 1)]
            )
            if task:
                asyncio.create_task(process_task(bot_instance, task))
            else:
                await asyncio.sleep(2)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(10)