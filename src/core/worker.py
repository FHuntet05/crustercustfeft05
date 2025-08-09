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
from typing import List

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
    def __init__(self, bot: Client, message: Message, task: dict):
        self.bot = bot
        self.message = message
        self.task = task
        self.user_id = task['user_id']
        self.start_time = time.time()
        self.last_update_time = 0
        self.last_text = ""
        self.operation = "Iniciando..."
        self.filename = task.get('original_filename') or task.get('url', 'archivo')

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

    def ytdlp_hook(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                # Usa run_coroutine_threadsafe porque este hook se llama desde otro hilo.
                asyncio.run_coroutine_threadsafe(self.update_progress(downloaded, total, is_processing=False), self.bot.loop)

async def _run_shell_command(cmd: str, tracker: ProgressTracker):
    """
    Ejecuta un comando de shell (FFmpeg, zip, etc.) y captura la salida.
    """
    tracker.set_operation("⚙️ Procesando...")
    # Enviamos una actualización de estado inicial para la etapa de procesamiento
    await tracker.update_progress(0, 0, is_processing=True)

    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_log = stderr.decode('utf-8', 'ignore').strip()
        raise FFmpegProcessingError(log=error_log)
    
    logger.info(f"Comando ejecutado con éxito: {cmd.split()[0]}")

async def _download_source_files(bot: Client, task: dict, dl_dir: str, tracker: ProgressTracker) -> List[str]:
    """
    Descarga los archivos fuente necesarios para una tarea.
    Puede ser un solo archivo (normal) o varios (join/zip).
    """
    file_type = task.get('file_type')
    downloaded_paths = []

    if file_type in ['join_operation', 'zip_operation']:
        source_task_ids = task.get('source_task_ids', [])
        logger.info(f"Tarea de '{file_type}' detectada. Descargando {len(source_task_ids)} archivos fuente.")
        for i, source_id in enumerate(source_task_ids):
            source_task = await db_instance.get_task(str(source_id))
            if not source_task:
                logger.warning(f"No se encontró la tarea fuente con ID {source_id}. Omitiendo.")
                continue
            
            filename = source_task.get('original_filename', f"source_{i}")
            tracker.set_operation(f"📥 Descargando fuente {i+1}/{len(source_task_ids)}", filename)
            
            if source_url := source_task.get('url'):
                # Descarga de URL para una de las fuentes
                path = await asyncio.to_thread(
                    downloader.download_from_url,
                    source_url,
                    os.path.join(dl_dir, str(source_id)),
                    source_task.get('processing_config', {}).get('download_format_id'),
                    tracker
                )
            elif source_file_id := source_task.get('file_id'):
                # Descarga de Telegram para una de las fuentes
                path = os.path.join(dl_dir, filename)
                total_size = source_task.get('file_metadata', {}).get('size', 0)
                await bot.download_media(message=source_file_id, file_name=path, progress=tracker.pyrogram_callback, progress_args=(total_size,))
            else:
                path = None

            if path and os.path.exists(path):
                downloaded_paths.append(path)
            else:
                raise NetworkError(f"La descarga del archivo fuente '{filename}' falló.")
        return downloaded_paths

    else: # Tarea estándar con un solo archivo
        tracker.set_operation("📥 Descargando")
        actual_download_path = ""
        if url := task.get('url'):
            format_id = task.get('processing_config', {}).get('download_format_id')
            actual_download_path = await asyncio.to_thread(downloader.download_from_url, url, os.path.join(dl_dir, str(task['_id'])), format_id, tracker)
        elif file_id := task.get('file_id'):
            actual_download_path = os.path.join(dl_dir, task.get('original_filename', 'archivo'))
            # [CRITICAL FIX] Pasar el tamaño total al callback de progreso.
            total_size = task.get('file_metadata', {}).get('size', 0)
            await bot.download_media(message=file_id, file_name=actual_download_path, progress=tracker.pyrogram_callback, progress_args=(total_size,))
        
        if not actual_download_path or not os.path.exists(actual_download_path):
            raise NetworkError("La descarga del archivo principal falló.")
        return [actual_download_path]

async def process_task(bot: Client, task: dict):
    task_id, user_id, tracker = str(task['_id']), task['user_id'], None
    status_message, files_to_clean = None, set()
    filename = task.get('original_filename') or task.get('url', f"Tarea_{task_id}")

    try:
        # 1. Preparación y adopción de mensaje
        if ref := task.get('status_message_ref'):
            try: status_message = await bot.get_messages(ref['chat_id'], ref['message_id'])
            except Exception: pass
        if not status_message:
            status_message = await bot.send_message(user_id, f"Iniciando: <code>{escape_html(filename)}</code>", parse_mode=ParseMode.HTML)
        
        tracker = ProgressTracker(bot, status_message, task)
        dl_dir = os.path.join(DOWNLOAD_DIR, task_id); os.makedirs(dl_dir, exist_ok=True); files_to_clean.add(dl_dir)
        
        # 2. Descarga de archivos fuente (ahora centralizada)
        input_paths = await _download_source_files(bot, task, dl_dir, tracker)
        if not input_paths:
            raise NetworkError("No se pudo descargar ningún archivo fuente para la tarea.")

        # 3. Descarga de archivos auxiliares
        config = task.get('processing_config', {})
        watermark_path = None
        if config.get('watermark', {}).get('type') == 'image' and (wm_id := config['watermark'].get('file_id')):
            watermark_path = os.path.join(dl_dir, f"watermark_{wm_id}"); files_to_clean.add(watermark_path)
            await bot.download_media(wm_id, file_name=watermark_path)

        # 4. Procesamiento
        initial_size = os.path.getsize(input_paths[0]) if input_paths else 0
        await resource_manager.acquire_ffmpeg_slot()
        try:
            output_dir = os.path.join(OUTPUT_DIR, task_id); os.makedirs(output_dir, exist_ok=True); files_to_clean.add(output_dir)
            final_filename_base = sanitize_filename(config.get('final_filename', os.path.splitext(filename)[0]))
            output_path_base = os.path.join(output_dir, final_filename_base)
            
            commands, definitive_output_path = ffmpeg.build_command_for_task(task, input_paths, output_path_base, watermark_path)
            
            for list_file in glob.glob(os.path.join(output_dir, "*_concat_list.txt")):
                files_to_clean.add(list_file)
            
            # [CRITICAL FIX] Ejecutar todos los comandos en secuencia
            for cmd in commands:
                if cmd: await _run_shell_command(cmd, tracker)
        finally:
            resource_manager.release_ffmpeg_slot()

        # 5. Subida de resultados
        output_files = glob.glob(os.path.join(output_dir, "*"))
        if not output_files:
            raise FFmpegProcessingError("El procesamiento finalizó pero no se encontró ningún archivo de salida.")
        
        for final_path in output_files:
             if os.path.isdir(final_path) or final_path.endswith('.txt'): continue

             final_size = os.path.getsize(final_path)
             final_filename_up = os.path.basename(final_path)
             caption = generate_summary_caption(task, initial_size, final_size, final_filename_up)
             tracker.set_operation("⬆️ Subiendo", final_filename_up)
             
             file_type = task.get('file_type')
             if file_type == 'video' and not config.get('extract_audio'):
                 await bot.send_video(user_id, video=final_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(final_size,))
             elif file_type == 'audio' or config.get('extract_audio'):
                 await bot.send_audio(user_id, audio=final_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(final_size,))
             else:
                 await bot.send_document(user_id, document=final_path, caption=caption, parse_mode=ParseMode.HTML, progress=tracker.pyrogram_callback, progress_args=(final_size,))

        await db_instance.update_task_field(task_id, "status", "completed")
        await tracker.message.delete()

    except Exception as e:
        logger.critical(f"Fallo irrecuperable en la tarea {task_id}: {e}", exc_info=True)
        # [CRITICAL FIX] Truncar el mensaje de error para evitar fallos de Telegram.
        error_details = escape_html(str(e))
        if len(error_details) > 3500:
            error_details = error_details[:3500] + "\n... (mensaje truncado)"

        error_message = f"❌ <b>Error en Tarea</b>\n<code>{escape_html(filename)}</code>\n\n<b>Motivo:</b>\n<pre>{error_details}</pre>"
        await db_instance.update_task_fields(task_id, {"status": "failed", "last_error": str(e)})
        
        try:
            if tracker: await tracker.message.edit_text(error_message, parse_mode=ParseMode.HTML)
            else: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
        except Exception as notification_error:
            logger.error(f"No se pudo editar el mensaje de estado. Fallback a nuevo mensaje. Error: {notification_error}")
            try: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
            except Exception as final_error: logger.critical(f"FALLO FINAL de notificación al usuario {user_id}. Error: {final_error}")
    finally:
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath): shutil.rmtree(fpath, ignore_errors=True)
                elif os.path.exists(fpath): os.remove(fpath)
            except Exception as e:
                logger.error(f"No se pudo limpiar {fpath}: {e}")

async def worker_loop(bot_instance: Client):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    while True:
        try:
            # Encuentra una tarea en cola y la marca como 'processing' atómicamente.
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