# --- START OF FILE src/core/worker.py ---

import logging
import time
import os
import asyncio
import glob
from datetime import datetime
from pyrogram.enums import ParseMode
from pyrogram.client import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, MessageNotModified
from bson.objectid import ObjectId
import shutil
from typing import List, Dict

from src.db.mongo_manager import db_instance
from src.helpers.utils import format_status_message, sanitize_filename, escape_html, generate_summary_caption
from src.core import ffmpeg
from src.core import downloader
from src.core.resource_manager import resource_manager
from src.core.exceptions import (DiskSpaceError, FFmpegProcessingError, 
                                 InvalidMediaError, NetworkError, AuthenticationError)

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")

class ProgressTracker:
    def __init__(self, bot: Client, message: Message, task: Dict):
        self.bot = bot
        self.message = message
        self.task = task
        self.user_id = task['user_id']
        self.start_time = time.time()
        self.last_update_time = 0
        self.last_text = ""
        self.operation = "Iniciando..."
        self.filename = task.get('original_filename') or task.get('url', 'archivo')
        self.loop = asyncio.get_event_loop()

    def set_operation(self, operation: str, filename: str = None):
        self.operation = operation
        if filename: self.filename = filename
        self.start_time = time.time()

    async def update_progress(self, current: float, total: float, is_processing: bool = False):
        current_time = time.time()
        if current_time - self.last_update_time < 1.5 and current < total:
            return
        
        elapsed = current_time - self.start_time
        speed = current / elapsed if elapsed > 0 else 0
        eta = (total - current) / speed if speed > 0 and current > 0 else float('inf')
        percentage = (current / total) * 100 if total > 0 else 0

        text = format_status_message(operation=self.operation, filename=self.filename, percentage=percentage,
                                     processed_bytes=current, total_bytes=total, speed=speed, eta=eta,
                                     elapsed_time=elapsed, is_processing=is_processing)
        
        if text == self.last_text:
            return
            
        try:
            await self.bot.edit_message_text(chat_id=self.message.chat.id, message_id=self.message.id,
                                             text=text, parse_mode=ParseMode.HTML)
            self.last_text = text
            self.last_update_time = current_time
        except MessageNotModified:
            pass
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.warning(f"No se pudo editar mensaje de estado: {e}")

    async def pyrogram_callback(self, current: int, total: int):
        await self.update_progress(current, total, is_processing=False)

    def ytdlp_hook(self, d: Dict):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                asyncio.run_coroutine_threadsafe(self.update_progress(downloaded, total, is_processing=False), self.loop)

async def _run_shell_command(cmd: str, tracker: ProgressTracker):
    logger.info(f"Ejecutando comando de shell: {cmd}")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_log = stderr.decode('utf-8', 'ignore').strip()
        logger.error(f"Error en comando FFmpeg. Log:\n{error_log}")
        raise FFmpegProcessingError(log=error_log)
    
    logger.info(f"Comando '{cmd.split()[0]}...' ejecutado con éxito.")


async def _download_source_files(bot: Client, task: Dict, dl_dir: str, tracker: ProgressTracker) -> List[str]:
    if task.get('file_type') in ['join_operation', 'zip_operation']:
        return await _download_batch_files(bot, task, dl_dir, tracker)
    else:
        return await _download_single_file(bot, task, dl_dir, tracker)

async def _download_single_file(bot: Client, task: Dict, dl_dir: str, tracker: ProgressTracker) -> List[str]:
    tracker.set_operation("📥 Descargando")
    download_path = ""
    
    if url := task.get('url'):
        output_template = os.path.join(dl_dir, str(task['_id']))
        format_id = task.get('processing_config', {}).get('download_format_id')
        download_path = await asyncio.to_thread(
            downloader.download_from_url, url, output_template, format_id, tracker
        )
    elif file_id := task.get('file_id'):
        original_filename = task.get('original_filename', 'input_file')
        _, ext = os.path.splitext(original_filename)
        safe_local_filename = f"input_{task['_id']}{ext}"
        download_path = os.path.join(dl_dir, safe_local_filename)
        
        # [FINAL FIX - PROGRESS ARGS]
        # Reintroducimos los progress_args, pasando el tamaño total desde la DB
        # a la callback de progreso.
        total_size = task.get('file_metadata', {}).get('size', 0)
        
        await bot.download_media(
            message=file_id, 
            file_name=download_path, 
            progress=tracker.pyrogram_callback,
            progress_args=(total_size,)  # <--- ESTA ES LA CORRECCIÓN
        )
    
    if not download_path or not os.path.exists(download_path):
        raise NetworkError("La descarga del archivo principal falló.")
        
    return [download_path]

async def _download_batch_files(bot: Client, task: Dict, dl_dir: str, tracker: ProgressTracker) -> List[str]:
    source_task_ids = task.get('source_task_ids', [])
    downloaded_paths = []

    for i, source_id in enumerate(source_task_ids):
        source_task = await db_instance.get_task(str(source_id))
        if not source_task:
            logger.warning(f"Tarea fuente {source_id} para operación de lote no encontrada. Saltando.")
            continue
        
        filename = source_task.get('original_filename', f"source_{i}")
        tracker.set_operation(f"📥 Descargando Lote ({i+1}/{len(source_task_ids)})", filename)
        
        path = None
        if source_url := source_task.get('url'):
            output_template = os.path.join(dl_dir, str(source_id))
            format_id = source_task.get('processing_config', {}).get('download_format_id')
            path = await asyncio.to_thread(
                downloader.download_from_url, source_url, output_template, format_id, tracker
            )
        elif source_file_id := source_task.get('file_id'):
            _, ext = os.path.splitext(filename)
            safe_local_filename = f"source_{i}{ext}"
            path = os.path.join(dl_dir, safe_local_filename)
            total_size = source_task.get('file_metadata', {}).get('size', 0)
            await bot.download_media(
                message=source_file_id,
                file_name=path,
                progress=tracker.pyrogram_callback,
                progress_args=(total_size,)
            )
        
        if path and os.path.exists(path):
            downloaded_paths.append(path)
        else:
            raise NetworkError(f"La descarga del archivo fuente '{filename}' falló.")
            
    return downloaded_paths


async def process_task(bot: Client, task: Dict):
    task_id, user_id, tracker = str(task['_id']), task['user_id'], None
    status_message, files_to_clean = None, set()
    filename = task.get('original_filename') or task.get('url', f"Tarea_{task_id}")

    try:
        if ref := task.get('status_message_ref'):
            try: status_message = await bot.get_messages(ref['chat_id'], ref['message_id'])
            except Exception: pass
        if not status_message:
            status_message = await bot.send_message(user_id, f"Iniciando: <code>{escape_html(filename)}</code>", parse_mode=ParseMode.HTML)
        
        tracker = ProgressTracker(bot, status_message, task)
        dl_dir = os.path.join(DOWNLOAD_DIR, task_id); os.makedirs(dl_dir, exist_ok=True); files_to_clean.add(dl_dir)
        
        input_paths = await _download_source_files(bot, task, dl_dir, tracker)
        if not input_paths: raise NetworkError("No se pudo descargar ningún archivo fuente para la tarea.")

        config = task.get('processing_config', {})
        watermark_path, thumb_path = None, None
        
        if config.get('watermark', {}).get('type') == 'image' and (wm_id := config['watermark'].get('file_id')):
            watermark_path = os.path.join(dl_dir, f"watermark_{wm_id}"); files_to_clean.add(watermark_path)
            await bot.download_media(wm_id, file_name=watermark_path)

        if thumb_url := config.get('thumbnail_url'):
            thumb_path = os.path.join(dl_dir, f"thumb_{task_id}.jpg")
            if await asyncio.to_thread(downloader.download_thumbnail, thumb_url, thumb_path):
                files_to_clean.add(thumb_path)
            else:
                thumb_path = None # La descarga falló, no usarlo.

        main_input_path = input_paths[0]
        initial_size = os.path.getsize(main_input_path) if os.path.exists(main_input_path) else 0

        await resource_manager.acquire_ffmpeg_slot()
        try:
            tracker.set_operation("⚙️ Procesando...")
            await tracker.update_progress(0, 0, is_processing=True)
            
            output_dir = os.path.join(OUTPUT_DIR, task_id); os.makedirs(output_dir, exist_ok=True); files_to_clean.add(output_dir)
            
            # El nombre final ahora se sanea aquí, justo antes de usarlo.
            final_filename_base = sanitize_filename(config.get('final_filename', os.path.splitext(filename)[0]))
            output_path_base = os.path.join(output_dir, final_filename_base)
            
            commands, _ = ffmpeg.build_command_for_task(task, main_input_path, output_path_base, watermark_path)
            
            for cmd in commands:
                if cmd: await _run_shell_command(cmd, tracker)
        finally:
            resource_manager.release_ffmpeg_slot()

        output_files = [f for f in glob.glob(os.path.join(output_dir, "*")) if not f.endswith((".txt", ".part", ".ytdl"))]
        if not output_files: raise FFmpegProcessingError("El procesamiento finalizó pero no se encontró ningún archivo de salida válido.")
        
        for i, final_path in enumerate(output_files):
            final_size = os.path.getsize(final_path)
            final_filename_up = os.path.basename(final_path)
            caption = generate_summary_caption(task, initial_size, final_size, final_filename_up) if i == 0 else ""
            tracker.set_operation(f"⬆️ Subiendo ({i+1}/{len(output_files)})", final_filename_up)
            
            file_type = task.get('file_type')
            if config.get('extract_audio'): file_type = 'audio'
            
            if file_type == 'video':
                await bot.send_video(user_id, video=final_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(final_size,))
            elif file_type == 'audio':
                await bot.send_audio(user_id, audio=final_path, thumb=thumb_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(final_size,))
            else:
                await bot.send_document(user_id, document=final_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(final_size,))

        await db_instance.update_task_field(task_id, "status", "completed")
        await tracker.message.delete()

    except Exception as e:
        logger.critical(f"Fallo irrecuperable en la tarea {task_id}: {e}", exc_info=True)
        error_details = escape_html(str(e))
        if len(error_details) > 3500: error_details = error_details[:3500] + "\n... (mensaje truncado)"
        error_message = f"❌ <b>Error en Tarea</b>\n<code>{escape_html(filename)}</code>\n\n<b>Motivo:</b>\n<pre>{error_details}</pre>"
        await db_instance.update_task_fields(task_id, {"status": "failed", "last_error": str(e)})
        
        try:
            if tracker and tracker.message: await tracker.message.edit_text(error_message, parse_mode=ParseMode.HTML)
            else: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
        except Exception as notification_error:
            logger.error(f"No se pudo notificar al usuario del error. Fallback. Error: {notification_error}")
            try: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
            except Exception as final_error: logger.critical(f"FALLO FINAL de notificación al usuario {user_id}. Error: {final_error}")
    finally:
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath): shutil.rmtree(fpath, ignore_errors=True)
                elif os.path.exists(fpath): os.remove(fpath)
            except Exception as e: logger.error(f"No se pudo limpiar {fpath}: {e}")

async def worker_loop(bot_instance: Client):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    while True:
        try:
            task_doc = await db_instance.tasks.find_one_and_update(
                {"status": "queued"},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}},
                sort=[('created_at', 1)]
            )
            if task_doc:
                task_id = task_doc['_id']
                logger.info(f"Tomando tarea {task_id} para procesar.")
                asyncio.create_task(process_task(bot_instance, task_doc))
            else:
                await asyncio.sleep(2)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(10)