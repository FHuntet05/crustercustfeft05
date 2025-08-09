import logging
import time
import os
import asyncio
import re
import glob
from datetime import datetime
from pyrogram.enums import ParseMode
from pyrogram.client import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, MessageNotModified
from bson.objectid import ObjectId
import shutil

from src.db.mongo_manager import db_instance
from src.helpers.utils import format_status_message, sanitize_filename, escape_html, generate_summary_caption
from src.core import ffmpeg, downloader
from src.core.resource_manager import resource_manager
from src.core.exceptions import (DiskSpaceError, FFmpegProcessingError, 
                                 InvalidMediaError, NetworkError, AuthenticationError)
from src.core.ffmpeg import get_media_info

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")
TEMP_DIR = os.path.join(os.getcwd(), "temp_lyrics")

class ProgressTracker:
    def __init__(self, bot: Client, message: Message, task: dict):
        self.bot = bot
        self.message = message
        self.task = task
        self.user_id = task['user_id']
        self.start_time = time.time()
        self.last_update_time = 0
        self.last_text = ""
        self.operation = "Iniciando..."
        self.filename = task.get('original_filename', 'archivo')

    def set_operation(self, operation: str, filename: str = None):
        self.operation = operation
        if filename: self.filename = filename
        self.start_time = time.time()

    async def update_progress(self, current: float, total: float, is_processing: bool = False):
        current_time = time.time()
        if current_time - self.last_update_time < 1.5: return
        
        elapsed = current_time - self.start_time
        speed = current / elapsed if elapsed > 0 else 0
        eta = (total - current) / speed if speed > 0 else float('inf')
        percentage = (current / total) * 100 if total > 0 else 0

        text = format_status_message(operation=self.operation, filename=self.filename, percentage=percentage,
                                     processed_bytes=current, total_bytes=total, speed=speed, eta=eta,
                                     elapsed_time=elapsed, is_processing=is_processing)
        
        if text == self.last_text: return
        try:
            await self.bot.edit_message_text(chat_id=self.message.chat.id, message_id=self.message.id,
                                             text=text, parse_mode=ParseMode.HTML)
            self.last_text = text; self.last_update_time = current_time
        except MessageNotModified: pass
        except FloodWait as e: await asyncio.sleep(e.value + 1)
        except Exception as e: logger.error(f"Error al editar mensaje de estado: {e}")

    async def pyrogram_callback(self, current: int, total: int, *args):
        await self.update_progress(current, total, is_processing=False)

    def ytdlp_hook(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            asyncio.run_coroutine_threadsafe(self.update_progress(downloaded, total, is_processing=False), self.bot.loop)

async def _run_ffmpeg_process(cmd: str):
    process = await asyncio.create_subprocess_shell(cmd, stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    if process.returncode != 0: raise FFmpegProcessingError(log=stderr.decode('utf-8', 'ignore')[-1000:])

async def _run_ffmpeg_with_progress(tracker: ProgressTracker, cmd: str, input_path: str):
    duration_info = get_media_info(input_path)
    total_sec = float(duration_info.get('format', {}).get('duration', 0))
    if total_sec <= 0:
        tracker.set_operation("⚙️ Procesando..."); await tracker.update_progress(0, 0, is_processing=True)
        return await _run_ffmpeg_process(cmd)
    tracker.set_operation("⚙️ Procesando...")
    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stderr=asyncio.subprocess.PIPE)
    async for line_bytes in process.stderr:
        line = line_bytes.decode('utf-8', 'ignore').strip()
        if match := time_pattern.search(line):
            h, m, s, ms = map(int, match.groups())
            processed_sec = h * 3600 + m * 60 + s + ms / 100
            await tracker.update_progress(processed_sec, total_sec, is_processing=True)
    await process.wait()
    if process.returncode != 0:
        raise FFmpegProcessingError("Error durante el procesamiento FFmpeg.")

async def process_task(bot: Client, task: dict):
    task_id, user_id, tracker = str(task['_id']), task['user_id'], None
    status_message, files_to_clean = None, set()
    filename = task.get('original_filename') or task.get('url', 'Tarea sin nombre')

    try:
        if ref := task.get('status_message_ref'):
            try: status_message = await bot.get_messages(ref['chat_id'], ref['message_id'])
            except Exception: logger.warning(f"No se pudo adoptar mensaje de estado para tarea {task_id}")
        if not status_message:
            status_message = await bot.send_message(user_id, f"Iniciando: <code>{escape_html(filename)}</code>", parse_mode=ParseMode.HTML)
        
        tracker = ProgressTracker(bot, status_message, task)
        dl_dir = os.path.join(DOWNLOAD_DIR, task_id); os.makedirs(dl_dir, exist_ok=True); files_to_clean.add(dl_dir)
        
        tracker.set_operation("📥 Descargando")
        actual_download_path = ""
        if url := task.get('url'):
            format_id = task.get('processing_config', {}).get('download_format_id')
            actual_download_path = await asyncio.to_thread(downloader.download_from_url, url, os.path.join(dl_dir, task_id), format_id, tracker)
        elif file_id := task.get('file_id'):
            actual_download_path = os.path.join(dl_dir, filename)
            total_size = task.get('file_metadata', {}).get('size', 0)
            await bot.download_media(message=file_id, file_name=actual_download_path, progress=tracker.pyrogram_callback, progress_args=(total_size,))
        if not actual_download_path or not os.path.exists(actual_download_path): raise NetworkError("La descarga del archivo principal falló.")
        
        config = task.get('processing_config', {}); watermark_path, subs_path, new_audio_path = None, None, None
        if thumb_id := config.get('thumbnail_file_id'):
            path = os.path.join(dl_dir, f"thumb_{thumb_id}"); await bot.download_media(thumb_id, file_name=path); config['thumbnail_path'] = path; files_to_clean.add(path)
        if config.get('watermark', {}).get('type') == 'image' and (wm_id := config['watermark'].get('file_id')):
            watermark_path = os.path.join(dl_dir, f"watermark_{wm_id}"); await bot.download_media(wm_id, file_name=watermark_path); files_to_clean.add(watermark_path)
        initial_size = os.path.getsize(actual_download_path)
        await resource_manager.acquire_ffmpeg_slot()
        try:
            output_dir = os.path.join(OUTPUT_DIR, task_id); os.makedirs(output_dir, exist_ok=True); files_to_clean.add(output_dir)
            final_filename_base = sanitize_filename(config.get('final_filename', os.path.splitext(filename)[0]))
            output_path_base = os.path.join(output_dir, final_filename_base)
            commands, definitive_output_path = ffmpeg.build_ffmpeg_command(task, actual_download_path, output_path_base, watermark_path, subs_path, new_audio_path)
            for i, cmd in enumerate(commands):
                if not cmd: continue
                if i == len(commands) - 1: await _run_ffmpeg_with_progress(tracker, cmd, actual_download_path)
                else: await _run_ffmpeg_process(cmd)
        finally: resource_manager.release_ffmpeg_slot()

        found_files = glob.glob(definitive_output_path) if "*" in definitive_output_path else ([definitive_output_path] if os.path.exists(definitive_output_path) else [])
        if not found_files: raise FFmpegProcessingError("FFmpeg finalizó pero no se encontró el archivo de salida.")
        
        for final_path in found_files:
            final_size, final_filename_up = os.path.getsize(final_path), os.path.basename(final_path)
            caption = generate_summary_caption(task, initial_size, final_size, final_filename_up)
            tracker.set_operation("⬆️ Subiendo", final_filename_up)
            total_size_up = os.path.getsize(final_path)
            if final_path.endswith(('.mp4', '.mkv')): await bot.send_video(user_id, video=final_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(total_size_up,))
            elif final_path.endswith(('.mp3', '.flac', 'm4a', 'opus')): await bot.send_audio(user_id, audio=final_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(total_size_up,))
            else: await bot.send_document(user_id, document=final_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(total_size_up,))
        
        await db_instance.update_task_field(task_id, "status", "completed")
        await tracker.message.delete()
    except Exception as e:
        logger.critical(f"Fallo irrecuperable en la tarea {task_id}: {e}", exc_info=True)
        error_message = f"❌ <b>Error en Tarea</b>\n<code>{escape_html(filename)}</code>\n\n<b>Motivo:</b>\n<pre>{escape_html(str(e))}</pre>"
        status_to_set = "paused_no_space" if isinstance(e, DiskSpaceError) else "failed"
        await db_instance.update_task_fields(task_id, {"status": status_to_set, "last_error": str(e)})
        try:
            if tracker: await tracker.message.edit_text(error_message, parse_mode=ParseMode.HTML)
        except Exception as notification_error:
            logger.error(f"No se pudo notificar al usuario editando el mensaje. Fallback a nuevo mensaje. Error: {notification_error}")
            try: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
            except Exception as final_error: logger.critical(f"FALLO FINAL: No se pudo ni siquiera enviar un nuevo mensaje de error al usuario {user_id}. Error: {final_error}")
    finally:
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath): shutil.rmtree(fpath, ignore_errors=True)
                elif os.path.exists(fpath): os.remove(fpath)
            except Exception as e: logger.error(f"No se pudo limpiar {fpath}: {e}")

async def worker_loop(bot_instance: Client):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True); os.makedirs(OUTPUT_DIR, exist_ok=True); os.makedirs(TEMP_DIR, exist_ok=True)
    active_tasks = set()
    while True:
        try:
            task_doc = await db_instance.tasks.find_one_and_update(
                {"status": "queued"},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}},
                sort=[('created_at', 1)]
            )
            if task_doc:
                task_id = task_doc['_id']
                active_tasks.add(task_id)
                logger.info(f"Tomando tarea {task_id} para procesar.")
                asyncio.create_task(process_task(bot_instance, task_doc)).add_done_callback(lambda t: active_tasks.discard(task_id))
            else:
                await asyncio.sleep(2)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(10)