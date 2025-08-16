# --- START OF FILE src/core/worker.py ---

import logging
import time
import os
import asyncio
import re
import shutil
from datetime import datetime
from zipfile import ZipFile, ZIP_DEFLATED
from pyrogram.enums import ParseMode
from bson.objectid import ObjectId
from typing import Dict, List

from src.db.mongo_manager import db_instance
from src.helpers.utils import (format_status_message, sanitize_filename,
                               escape_html, _edit_status_message,
                               generate_summary_caption)
from src.core import ffmpeg
from src.core import downloader
from src.core.ffmpeg import get_media_info

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")


# --- CLASE DE PROGRESO (Sin cambios) ---
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
        *command, stderr=asyncio.subprocess.PIPE
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


# --- FUNCIONES DE PROCESAMIENTO ESPECIALIZADAS (CON CORRECCIÓN) ---

async def _process_media_task(bot, task: dict, dl_dir: str):
    """Procesa una tarea de un solo archivo (video o audio)."""
    user_id = task['user_id']
    config = task.get('processing_config', {})
    original_filename = task.get('original_filename', 'archivo.mkv') # Default con extensión
    ctx = progress_tracker[user_id]
    
    actual_download_path = None
    if file_id := task.get('file_id'):
        actual_download_path = os.path.join(dl_dir, original_filename)
        db_total_size = task.get('file_metadata', {}).get('size', 0)
        await bot.download_media(
            message=file_id, file_name=actual_download_path,
            progress=_progress_callback_pyrogram,
            progress_args=(user_id, "Downloading...", "#TelegramDownload", db_total_size)
        )
    elif url := task.get('url'):
        base_path = os.path.join(dl_dir, sanitize_filename(task.get('final_filename', 'url_download')))
        await _edit_status_message(user_id, "Descargando desde URL...", progress_tracker)
        actual_download_path = await asyncio.to_thread(
            downloader.download_from_url, url, base_path, config.get('download_format_id')
        )
    else:
        raise ValueError("La tarea no contiene ni 'file_id' ni 'url' para descargar.")
        
    if not actual_download_path or not os.path.exists(actual_download_path):
        raise FileNotFoundError("La descarga del archivo principal falló o no se encontró el archivo.")

    initial_size = os.path.getsize(actual_download_path)
    
    watermark_path = None
    if wm_conf := config.get('watermark', {}):
        if wm_conf.get('type') == 'image' and (wm_id := wm_conf.get('file_id')):
            await _edit_status_message(user_id, "Descargando marca de agua...", progress_tracker)
            watermark_path = await bot.download_media(wm_id, file_name=os.path.join(dl_dir, "watermark_img"))
    
    # [FIX] Determinar la extensión de salida ANTES de construir la ruta
    if config.get('gif_options'):
        output_extension = ".gif"
    elif config.get('extract_audio'):
        # La función de ffmpeg determinará la mejor extensión, pero necesitamos una para el contenedor
        output_extension = ".m4a" 
    elif config.get('transcode'):
        output_extension = ".mp4"
    else:
        _, original_ext = os.path.splitext(original_filename)
        output_extension = original_ext if original_ext in ['.mp4', '.mkv', '.mov', '.webm'] else ".mkv"

    final_filename_base = sanitize_filename(config.get('final_filename', os.path.splitext(original_filename)[0]))
    # [FIX] Construir la ruta final sin .tmp, FFmpeg lo sobrescribirá gracias a '-y'
    output_path = os.path.join(OUTPUT_DIR, f"{final_filename_base}{output_extension}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    command_groups, definitive_output_path = ffmpeg.build_ffmpeg_command(
        task, actual_download_path, output_path, watermark_path=watermark_path
    )
    
    if command_groups:
        await _run_command_with_progress(user_id, command_groups[0], actual_download_path)
    
    final_size = os.path.getsize(definitive_output_path)
    caption = generate_summary_caption(task, initial_size, final_size, os.path.basename(definitive_output_path))
    
    ctx.reset_timer()
    file_type = task.get('file_type', 'video')

    if definitive_output_path.endswith('.gif'):
        sender_func = bot.send_animation
        kwargs = {'animation': definitive_output_path}
    elif file_type == 'video' and not config.get('extract_audio'):
        sender_func = bot.send_video
        kwargs = {'video': definitive_output_path}
    elif file_type == 'audio' or config.get('extract_audio'):
        sender_func = bot.send_audio
        kwargs = {'audio': definitive_output_path}
    else:
        sender_func = bot.send_document
        kwargs = {'document': definitive_output_path}
    
    await sender_func(
        user_id, caption=caption, parse_mode=ParseMode.HTML,
        progress=_progress_callback_pyrogram,
        progress_args=(user_id, "Uploading...", "#TelegramUpload", final_size),
        **kwargs
    )
    return definitive_output_path

# --- EL RESTO DEL ARCHIVO (join, zip, process_task, worker_loop) PERMANECE IGUAL ---

async def _process_join_task(bot, task: dict, dl_dir: str):
    """Procesa una tarea de unión de videos."""
    user_id = task['user_id']
    source_task_ids = task.get('source_task_ids', [])
    if not source_task_ids:
        raise ValueError("Tarea de unión sin source_task_ids.")

    await _edit_status_message(user_id, f"Iniciando unión de {len(source_task_ids)} videos...", progress_tracker)
    
    file_list_path = os.path.join(dl_dir, "file_list.txt")

    with open(file_list_path, 'w', encoding='utf-8') as f:
        for i, tid in enumerate(source_task_ids):
            source_task = await db_instance.get_task(str(tid))
            if not source_task or not source_task.get('file_id'):
                continue
            
            filename = source_task.get('original_filename', f'video_{i}.mp4')
            dl_path = os.path.join(dl_dir, f"{i}_{filename}")

            await _edit_status_message(user_id, f"Descargando video {i+1}/{len(source_task_ids)}...", progress_tracker)
            await bot.download_media(source_task['file_id'], file_name=dl_path)
            
            f.write(f"file '{dl_path.replace('\'', '\\\'')}'\n")

    output_filename = f"{task.get('final_filename', 'union_video')}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", file_list_path, "-c", "copy", output_path]
    
    await _edit_status_message(user_id, "Uniendo videos...", progress_tracker)
    process = await asyncio.create_subprocess_exec(*command, stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    
    if process.returncode != 0:
        raise Exception(f"FFmpeg (concat) falló: {stderr.decode()}")
        
    final_size = os.path.getsize(output_path)
    await bot.send_video(
        user_id, video=output_path, caption=f"✅ Unión de {len(source_task_ids)} videos completada.",
        progress=_progress_callback_pyrogram,
        progress_args=(user_id, "Subiendo...", "#TelegramUpload", final_size)
    )
    return output_path

async def _process_zip_task(bot, task: dict, dl_dir: str):
    """Procesa una tarea de compresión de archivos."""
    user_id = task['user_id']
    source_task_ids = task.get('source_task_ids', [])
    if not source_task_ids:
        raise ValueError("Tarea de compresión sin source_task_ids.")

    output_filename = f"{task.get('final_filename', 'comprimido')}.zip"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    with ZipFile(output_path, 'w', ZIP_DEFLATED) as zf:
        for i, tid in enumerate(source_task_ids):
            source_task = await db_instance.get_task(str(tid))
            if not source_task or not source_task.get('file_id'):
                continue

            filename = source_task.get('original_filename', f'archivo_{i}')
            dl_path = os.path.join(dl_dir, filename)

            await _edit_status_message(user_id, f"Descargando para comprimir: {i+1}/{len(source_task_ids)}...", progress_tracker)
            await bot.download_media(source_task['file_id'], file_name=dl_path)
            
            await _edit_status_message(user_id, f"Añadiendo al ZIP: {filename}", progress_tracker)
            zf.write(dl_path, arcname=filename)

    final_size = os.path.getsize(output_path)
    await bot.send_document(
        user_id, document=output_path, caption=f"✅ Compresión de {len(source_task_ids)} archivos completada.",
        progress=_progress_callback_pyrogram,
        progress_args=(user_id, "Subiendo...", "#TelegramUpload", final_size)
    )
    return output_path

async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    status_message, files_to_clean = None, set()
    original_filename = "Tarea sin nombre"

    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("Tarea no encontrada.")
        
        file_type = task.get('file_type', 'video')
        original_filename = task.get('original_filename') or task.get('url', 'Tarea sin nombre')
        
        status_message = await bot.send_message(user_id, "✅ Tarea recibida. Preparando...", parse_mode=ParseMode.HTML)
        
        global progress_tracker
        progress_tracker[user_id] = ProgressContext(bot, status_message, task, asyncio.get_running_loop())
        
        task_dir = os.path.join(DOWNLOAD_DIR, task_id)
        os.makedirs(task_dir, exist_ok=True)
        files_to_clean.add(task_dir)
        
        definitive_output_path = None

        if file_type in ['video', 'audio', 'document']:
            definitive_output_path = await _process_media_task(bot, task, task_dir)
        elif file_type == 'join_operation':
            definitive_output_path = await _process_join_task(bot, task, task_dir)
        elif file_type == 'zip_operation':
            definitive_output_path = await _process_zip_task(bot, task, task_dir)
        else:
            raise NotImplementedError(f"El tipo de tarea '{file_type}' no está implementado.")
        
        if definitive_output_path:
            files_to_clean.add(definitive_output_path)

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