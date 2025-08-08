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
    """
    Una clase robusta y aislada para rastrear y mostrar el progreso de una única tarea.
    Resuelve los problemas de concurrencia al encapsular el estado.
    """
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
        if filename:
            self.filename = filename
        self.start_time = time.time() # Reiniciar el cronómetro para la nueva operación

    async def update_progress(self, current: float, total: float, is_processing: bool = False):
        current_time = time.time()
        if current_time - self.last_update_time < 1.5:  # Throttling
            return
        
        elapsed = current_time - self.start_time
        
        if total > 0:
            speed = current / elapsed if elapsed > 0 else 0
            eta = (total - current) / speed if speed > 0 else float('inf')
            percentage = (current / total) * 100
        else:
            # Manejar caso de tamaño total desconocido
            speed = current / elapsed if elapsed > 0 else 0
            eta = float('inf')
            percentage = 0  # No se puede calcular el porcentaje

        text = format_status_message(
            operation=self.operation,
            filename=self.filename,
            percentage=percentage,
            processed_bytes=current,
            total_bytes=total,
            speed=speed,
            eta=eta,
            elapsed_time=elapsed,
            is_processing=is_processing
        )
        
        if text == self.last_text:
            return

        try:
            await self.bot.edit_message_text(
                chat_id=self.message.chat.id,
                message_id=self.message.id,
                text=text,
                parse_mode=ParseMode.HTML
            )
            self.last_text = text
            self.last_update_time = current_time
        except MessageNotModified:
            pass
        except FloodWait as e:
            logger.warning(f"FloodWait de {e.value} segundos. Esperando.")
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.error(f"Error al editar mensaje de estado para la tarea {self.task['_id']}: {e}")

    # Callback para Pyrogram (descargas/subidas)
    async def pyrogram_callback(self, current: int, total: int):
        await self.update_progress(current, total, is_processing=False)

    # Hook para yt-dlp (descargas de URL)
    def ytdlp_hook(self, d):
        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded_bytes = d.get('downloaded_bytes', 0)
            
            # El loop de eventos puede no estar corriendo en el hilo de yt-dlp,
            # así que programamos la corutina de forma segura.
            asyncio.run_coroutine_threadsafe(
                self.update_progress(downloaded_bytes, total_bytes, is_processing=False),
                self.bot.loop
            )

async def _run_ffmpeg_process(cmd: str):
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error_log = stderr.decode('utf-8', 'ignore')
        raise FFmpegProcessingError(log=error_log[-1000:])

async def _run_ffmpeg_with_progress(tracker: ProgressTracker, cmd: str, input_path: str):
    duration_info = get_media_info(input_path)
    total_duration_sec = float(duration_info.get('format', {}).get('duration', 0))
    if total_duration_sec <= 0:
        logger.warning("No se pudo obtener la duración. Ejecutando FFmpeg sin barra de progreso.")
        tracker.set_operation("⚙️ Procesando...")
        await tracker.update_progress(0, 0, is_processing=True)
        return await _run_ffmpeg_process(cmd)

    tracker.set_operation("⚙️ Procesando...")
    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    
    stderr_buffer, all_stderr_lines = "", []

    while True:
        chunk = await process.stderr.read(1024)
        if not chunk: break
        
        decoded_chunk = chunk.decode('utf-8', 'ignore')
        stderr_buffer += decoded_chunk

        if '\r' in stderr_buffer:
            *lines, stderr_buffer = stderr_buffer.split('\r')
            for line in lines:
                all_stderr_lines.append(line.strip())
                if match := time_pattern.search(line):
                    h, m, s, ms = map(int, match.groups())
                    processed_sec = h * 3600 + m * 60 + s + ms / 100
                    await tracker.update_progress(processed_sec, total_duration_sec, is_processing=True)
    
    await process.wait()
    if process.returncode != 0:
        all_stderr_lines.extend(stderr_buffer.strip().split('\n'))
        error_log = "\n".join(all_stderr_lines[-20:])
        raise FFmpegProcessingError(log=error_log)

async def process_media_task(bot: Client, task: dict):
    task_id, user_id, tracker = str(task['_id']), task['user_id'], None
    status_message, files_to_clean = None, set()
    filename = task.get('original_filename') or task.get('url', 'Tarea')

    try:
        task = await db_instance.get_task(task_id)
        if not task: raise InvalidMediaError("Tarea no encontrada después de recargar.")
        
        if thumbnail_path := task.get('processing_config', {}).get('thumbnail_path'):
            if os.path.exists(thumbnail_path):
                files_to_clean.add(thumbnail_path)
        
        if ref := task.get('status_message_ref'):
            try:
                status_message = await bot.get_messages(ref['chat_id'], ref['message_id'])
                logger.info(f"Mensaje de estado {status_message.id} adoptado para la tarea {task_id}.")
            except Exception as e:
                logger.warning(f"No se pudo adoptar mensaje {ref['message_id']} para la tarea {task_id}: {e}")
        
        if not status_message:
            status_message = await bot.send_message(user_id, f"Iniciando: <code>{escape_html(filename)}</code>", parse_mode=ParseMode.HTML)
            logger.info(f"Nuevo mensaje de estado {status_message.id} creado para la tarea {task_id}.")
        
        tracker = ProgressTracker(bot, status_message, task)
        await tracker.update_progress(0, 1)

        dl_dir = os.path.join(DOWNLOAD_DIR, task_id)
        os.makedirs(dl_dir, exist_ok=True)
        files_to_clean.add(dl_dir)
        
        resource_manager.check_disk_space(task.get('file_metadata', {}).get('size', 0))

        actual_download_path = ""
        if url := task.get('url'):
            tracker.set_operation("📥 Descargando")
            format_id = task.get('processing_config', {}).get('download_format_id')
            if not format_id: raise InvalidMediaError("Tarea de URL sin 'download_format_id'.")
            actual_download_path = await asyncio.to_thread(downloader.download_from_url, url, os.path.join(dl_dir, task_id), format_id, tracker)
            if not actual_download_path: raise NetworkError("La descarga desde la URL falló.")
        elif file_id := task.get('file_id'):
            tracker.set_operation("📥 Descargando")
            actual_download_path = os.path.join(dl_dir, filename)
            # Pyrogram no nos da el tamaño total aquí, así que lo obtenemos de la tarea
            total_size = task.get('file_metadata', {}).get('size', 0)
            await bot.download_media(message=file_id, file_name=actual_download_path, progress=tracker.pyrogram_callback, progress_args=(total_size,))
        
        initial_size = os.path.getsize(actual_download_path) if os.path.exists(actual_download_path) else 0

        await resource_manager.acquire_ffmpeg_slot()
        try:
            output_dir = os.path.join(OUTPUT_DIR, task_id); os.makedirs(output_dir, exist_ok=True); files_to_clean.add(output_dir)
            final_filename_base = sanitize_filename(task.get('processing_config', {}).get('final_filename', os.path.splitext(filename)[0]))
            output_path_base = os.path.join(output_dir, f"{final_filename_base}")
            
            await tracker.update_progress(0,1, is_processing=True)
            
            commands, definitive_output_path = ffmpeg.build_ffmpeg_command(task, actual_download_path, output_path_base)
            
            for i, cmd in enumerate(commands):
                if not cmd: continue
                if i == len(commands) - 1:
                    await _run_ffmpeg_with_progress(tracker, cmd, actual_download_path)
                else:
                    await _run_ffmpeg_process(cmd)
        finally:
            resource_manager.release_ffmpeg_slot()

        found_files = glob.glob(definitive_output_path) if "*" in definitive_output_path else ([definitive_output_path] if os.path.exists(definitive_output_path) else [])
        if not found_files: raise FFmpegProcessingError("FFmpeg finalizó pero no se encontró el archivo de salida.")

        for final_path in found_files:
            final_size, final_filename = os.path.getsize(final_path), os.path.basename(final_path)
            caption = generate_summary_caption(task, initial_size, final_size, final_filename)
            
            tracker.set_operation("⬆️ Subiendo", final_filename)
            
            upload_args = {'caption': caption, 'parse_mode': ParseMode.HTML, 'progress': tracker.pyrogram_callback, 'progress_args': (final_size,)}
            
            if final_path.endswith(('.mp4', '.mkv', '.webm')): await bot.send_video(user_id, video=final_path, **upload_args)
            elif final_path.endswith(('.mp3', '.flac', 'm4a', '.opus')): await bot.send_audio(user_id, audio=final_path, **upload_args)
            else: await bot.send_document(user_id, document=final_path, **upload_args)

        await db_instance.update_task_field(task_id, "status", "completed")
        if tracker: await tracker.message.delete()

    except Exception as e:
        error_message = f"❌ <b>Error en Tarea</b>\n<code>{escape_html(filename)}</code>\n\n<b>Motivo:</b>\n<pre>{escape_html(str(e))}</pre>"
        status_to_set = "failed"
        if isinstance(e, DiskSpaceError): status_to_set = "paused_no_space"
        
        await db_instance.update_task_fields(task_id, {"status": status_to_set, "last_error": str(e)})
        if tracker: await tracker.message.edit_text(error_message, parse_mode=ParseMode.HTML)
        else: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)

    finally:
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath): shutil.rmtree(fpath, ignore_errors=True)
                elif os.path.exists(fpath): os.remove(fpath)
            except Exception as e:
                logger.error(f"No se pudo limpiar {fpath}: {e}")

async def process_metadata_task(bot: Client, task: dict):
    task_id, user_id, tracker = str(task['_id']), task['user_id'], None
    dl_dir = os.path.join(DOWNLOAD_DIR, f"meta_{task_id}")
    try:
        status_message = await bot.send_message(user_id, f"🔎 Analizando metadatos de <code>{escape_html(task['original_filename'])}</code>...", parse_mode=ParseMode.HTML)
        tracker = ProgressTracker(bot, status_message, task)
        
        resource_manager.check_disk_space(task.get('file_metadata', {}).get('size', 0))
        os.makedirs(dl_dir, exist_ok=True)
        
        tracker.set_operation("📥 Descargando")
        file_path = os.path.join(dl_dir, task['original_filename'])
        total_size = task.get('file_metadata', {}).get('size', 0)
        await bot.download_media(message=task['file_id'], file_name=file_path, progress=tracker.pyrogram_callback, progress_args=(total_size,))

        media_info = get_media_info(file_path)
        if not media_info: raise InvalidMediaError("No se pudieron leer los metadatos del archivo descargado.")
        
        stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'video'),
                      next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), {}))
        
        metadata_update = {
            "duration": float(stream.get('duration', 0)) or float(media_info.get('format', {}).get('duration', 0)),
            "resolution": f"{stream.get('width')}x{stream.get('height')}" if stream.get('width') else None,
            "streams": [{"codec_type": s.get("codec_type"), "codec_name": s.get("codec_name")} for s in media_info.get('streams', [])]
        }
        await db_instance.update_task_fields(task_id, {"status": "pending_processing", "file_metadata": metadata_update})
        await tracker.message.edit_text(f"✅ Análisis de <code>{escape_html(task['original_filename'])}</code> completo. La tarea está lista en el panel.", parse_mode=ParseMode.HTML)
        await asyncio.sleep(5)
        await tracker.message.delete()

    except Exception as e:
        error_message = f"❌ <b>Error de Análisis</b>\n<code>{escape_html(task['original_filename'])}</code>\n\n<b>Motivo:</b>\n<pre>{escape_html(str(e))}</pre>"
        await db_instance.update_task_fields(task_id, {"status": "failed", "last_error": str(e)})
        if tracker: await tracker.message.edit_text(error_message, parse_mode=ParseMode.HTML)
        else: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
    finally:
        if os.path.exists(dl_dir):
            shutil.rmtree(dl_dir, ignore_errors=True)

async def worker_loop(bot_instance: Client):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    active_tasks = set()

    while True:
        try:
            query = {"status": {"$in": ["queued", "pending_metadata"]}, "_id": {"$nin": list(active_tasks)}}
            task = await db_instance.tasks.find_one_and_update(
                query, {"$set": {"status": "processing", "processed_at": datetime.utcnow()}},
                sort=[('created_at', 1)]
            )
            if task:
                task_id, status = task['_id'], task['status']
                active_tasks.add(task_id)
                logger.info(f"Tomando tarea {task_id} con estado '{status}' para el usuario {task['user_id']}")
                
                async def task_wrapper(task_doc):
                    try:
                        if task_doc.get('status') == 'pending_metadata' or (not task_doc.get('processed_at') and task_doc.get('status') != 'completed'):
                            await process_metadata_task(bot_instance, task_doc)
                        else:
                            await process_media_task(bot_instance, task_doc)
                    finally:
                        active_tasks.discard(task_doc['_id'])

                asyncio.create_task(task_wrapper(task))
            else:
                await asyncio.sleep(2)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(10)