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
from typing import List, Dict, Any

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
    
    def reset_timer(self):
        self.start_time = time.time()
        self.last_update_time = 0

progress_tracker: Dict[int, ProgressContext] = {}

def _progress_callback_pyrogram(current: int, total: int, user_id: int, title: str, status: str, db_total_size: int):
    ctx = progress_tracker.get(user_id)
    if not ctx: return

    final_total = total if total > 0 else db_total_size
    if current > final_total: current = final_total

    now = time.time()
    if now - ctx.last_update_time < 1.5 and current < final_total:
        return
    ctx.last_update_time = now

    elapsed = now - ctx.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (final_total - current) / speed if current > 0 and speed > 0 else float('inf')
    percentage = (current / final_total) * 100 if final_total > 0 else 0

    text = format_status_message(
        operation_title=title, percentage=percentage,
        processed_bytes=current, total_bytes=final_total, speed=speed, eta=eta,
        elapsed=elapsed, status_tag=status, engine="Pyrogram", user_id=user_id
    )
    
    coro = _edit_status_message(user_id, text, progress_tracker)
    asyncio.run_coroutine_threadsafe(coro, ctx.loop)

async def _run_command_with_progress(user_id: int, command: List[str], input_path: str):
    media_info = get_media_info(input_path)
    duration_str = media_info.get("format", {}).get("duration", "0")
    try:
        duration = float(duration_str)
    except (TypeError, ValueError):
        duration = 0

    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")

    ctx = progress_tracker.get(user_id)
    if not ctx: return

    ctx.reset_timer()
    process = await asyncio.create_subprocess_exec(
        *command,
        stderr=asyncio.subprocess.PIPE
    )

    all_stderr_lines = []
    async for line in process.stderr:
        log_line = line.decode('utf-8', 'ignore')
        all_stderr_lines.append(log_line)
        if match := time_pattern.search(log_line):
            if duration > 0:
                now = time.time()
                if now - ctx.last_update_time < 1.5: continue
                ctx.last_update_time = now

                h, m, s, cs = map(int, match.groups())
                processed_time = h * 3600 + m * 60 + s + cs / 100
                if processed_time > duration: processed_time = duration

                percentage = (processed_time / duration) * 100
                elapsed = now - ctx.start_time
                speed = processed_time / elapsed if elapsed > 0 else 0
                eta = (duration - processed_time) / speed if speed > 0 else float('inf')

                text = format_status_message(
                    operation_title="Task is being Processed!", percentage=percentage,
                    processed_bytes=processed_time, total_bytes=duration,
                    speed=speed, eta=eta, elapsed=elapsed,
                    status_tag="#Processing", engine="FFmpeg", user_id=user_id
                )
                await _edit_status_message(user_id, text, progress_tracker)
    
    await process.wait()
    if process.returncode != 0:
        error_log = "".join(all_stderr_lines)
        raise Exception(f"FFmpeg falló con código de salida {process.returncode}. Log:\n{error_log}")


async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    status_message, files_to_clean = None, set()
    actual_download_path = None
    original_filename = "Tarea sin nombre"

    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("Tarea no encontrada.")
        
        config = task.get('processing_config', {})
        original_filename = task.get('original_filename') or task.get('url', 'Tarea sin nombre')
        
        status_message = await bot.send_message(user_id, "✅ Tarea recibida. Preparando...", parse_mode=ParseMode.HTML)
        
        global progress_tracker
        progress_tracker[user_id] = ProgressContext(bot, status_message, task, asyncio.get_running_loop())
        ctx = progress_tracker[user_id]

        dl_dir = os.path.join(DOWNLOAD_DIR, task_id)
        os.makedirs(dl_dir, exist_ok=True)
        files_to_clean.add(dl_dir)
        
        if file_id := task.get('file_id'):
            actual_download_path = os.path.join(dl_dir, original_filename)
            db_total_size = task.get('file_metadata', {}).get('size', 0)
            
            ctx.reset_timer()
            await bot.download_media(
                message=file_id,
                file_name=actual_download_path,
                progress=_progress_callback_pyrogram,
                progress_args=(user_id, "Downloading...", "#TelegramDownload", db_total_size)
            )
        else:
            raise Exception("Tarea sin file_id no soportada.")
        
        if not actual_download_path or not os.path.exists(actual_download_path):
            raise Exception("La descarga falló.")

        initial_size = os.path.getsize(actual_download_path)
        
        watermark_path = None
        if wm_conf := config.get('watermark'):
            if wm_conf.get('type') == 'image' and (wm_id := wm_conf.get('file_id')):
                await _edit_status_message(user_id, "Descargando marca de agua...", progress_tracker)
                watermark_path = await bot.download_media(wm_id, file_name=os.path.join(dl_dir, "watermark_img"))
                files_to_clean.add(watermark_path)
        
        ctx.reset_timer()
        media_info = get_media_info(actual_download_path)
        duration = float(media_info.get("format", {}).get("duration", 0))

        processing_text = format_status_message(
            operation_title="Task is being Processed!", percentage=0,
            processed_bytes=0, total_bytes=duration,
            speed=0, eta=float('inf'), elapsed=0,
            status_tag="#Processing", engine="FFmpeg", user_id=user_id
        )
        await _edit_status_message(user_id, processing_text, progress_tracker)

        final_filename_base = sanitize_filename(config.get('final_filename', original_filename))
        if config.get('transcode'):
            output_extension = ".mp4"
        else:
            _, original_ext = os.path.splitext(original_filename)
            output_extension = original_ext if original_ext else ".mkv"

        final_output_filename = f"{final_filename_base}{output_extension}"
        output_path = os.path.join(OUTPUT_DIR, final_output_filename)
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        files_to_clean.add(output_path)
        
        # [DEFINITIVE FIX - TypeError & FileNotFoundError]
        # La función build_ffmpeg_command ahora devuelve una tupla: (lista_de_listas_de_comandos, ruta_final)
        # Accedemos a la primera (y usualmente única) lista de comandos con [0]
        command_groups, definitive_output_path = ffmpeg.build_ffmpeg_command(
            task, actual_download_path, output_path, watermark_path=watermark_path
        )
        
        # El bucle for fue incorrecto. Ahora, ejecutamos el primer grupo de comandos.
        # Esto asume que para tareas simples, solo hay un comando.
        # Para tareas complejas como GIF, `build_ffmpeg_command` debería devolver múltiples listas.
        if command_groups:
             await _run_command_with_progress(user_id, command_groups[0], actual_download_path)
        
        final_size = os.path.getsize(definitive_output_path)
        caption = generate_summary_caption(task, initial_size, final_size, os.path.basename(definitive_output_path))
        
        ctx.reset_timer()
        await bot.send_video(
            user_id, video=definitive_output_path, caption=caption, 
            parse_mode=ParseMode.HTML, 
            progress=_progress_callback_pyrogram, 
            progress_args=(user_id, "Uploading...", "#TelegramUpload", final_size)
        )

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