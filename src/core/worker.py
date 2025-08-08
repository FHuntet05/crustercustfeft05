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
from src.helpers.utils import (format_status_message, sanitize_filename, 
                               escape_html, generate_summary_caption)
from src.core import ffmpeg, downloader
from src.core.resource_manager import resource_manager
from src.core.exceptions import (DiskSpaceError, FFmpegProcessingError, 
                                 InvalidMediaError, NetworkError, AuthenticationError)
from src.core.ffmpeg import get_media_info

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")


class ProgressContext:
    def __init__(self, bot: Client, message: Message, task: dict):
        self.bot = bot
        self.message = message
        self.task = task
        self.user_id = task['user_id']
        self.start_time = time.time()
        self.last_update_time = 0
        self.last_text = ""

    async def edit_message(self, text: str):
        current_time = time.time()
        if current_time - self.last_update_time < 1.5:
            return
        if text == self.last_text:
            return

        try:
            await self.bot.edit_message_text(
                chat_id=self.message.chat.id, message_id=self.message.id,
                text=text, parse_mode=ParseMode.HTML
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

def pyrogram_progress_callback(current, total, context: ProgressContext, operation: str, filename: str):
    if not context or not total or total <= 0: return
    
    elapsed = time.time() - context.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    percentage = (current / total) * 100
    
    text = format_status_message(
        operation=operation, filename=filename or context.task.get('original_filename', 'archivo'),
        percentage=percentage, processed_bytes=current, total_bytes=total, speed=speed, eta=eta,
        engine="Pyrogram", user_id=context.user_id, user_mention=context.message.from_user.mention
    )
    asyncio.run_coroutine_threadsafe(context.edit_message(text), context.bot.loop)

async def _run_ffmpeg_process(cmd: str):
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error_log = stderr.decode('utf-8', 'ignore')
        raise FFmpegProcessingError(log=error_log[-1000:])

async def _run_ffmpeg_with_progress(context: ProgressContext, cmd: str, input_path: str, initial_file_size: int):
    duration_info = get_media_info(input_path)
    total_duration_sec = float(duration_info.get('format', {}).get('duration', 0))
    if total_duration_sec <= 0:
        logger.warning("No se pudo obtener la duración. Ejecutando FFmpeg sin barra de progreso.")
        await context.edit_message("⚙️ Procesando... (duración no disponible)")
        return await _run_ffmpeg_process(cmd)

    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    
    start_time, stderr_buffer, all_stderr_lines = time.time(), "", []

    while True:
        chunk = await process.stderr.read(1024)
        if not chunk: break
        stderr_buffer += chunk.decode('utf-8', 'ignore')
        lines, stderr_buffer = stderr_buffer.split('\r'), lines.pop(-1)
        for line in lines:
            if not line: continue
            all_stderr_lines.append(line.strip())
            if match := time_pattern.search(line):
                h, m, s, ms = map(int, match.groups())
                processed_sec = h * 3600 + m * 60 + s + ms / 100
                percentage = (processed_sec / total_duration_sec) * 100 if total_duration_sec > 0 else 0
                elapsed = time.time() - start_time
                speed_factor = processed_sec / elapsed if elapsed > 0 else 0
                eta = (total_duration_sec - processed_sec) / speed_factor if speed_factor > 0 else 0
                
                text = format_status_message(
                    operation="⚙️ Procesando...", filename=context.task.get('original_filename', 'archivo'),
                    percentage=percentage, processed_bytes=processed_sec, total_bytes=total_duration_sec,
                    speed=speed_factor, eta=eta, engine="FFmpeg", user_id=context.user_id,
                    user_mention=context.message.from_user.mention, is_processing=True, file_size=initial_file_size
                )
                await context.edit_message(text)
    
    await process.wait()
    if process.returncode != 0:
        error_log = "\n".join(all_stderr_lines[-20:])
        raise FFmpegProcessingError(log=error_log)

async def process_media_task(bot: Client, task: dict):
    task_id, user_id, context = str(task['_id']), task['user_id'], None
    files_to_clean = set()

    try:
        task = await db_instance.get_task(task_id)
        if not task: raise InvalidMediaError("Tarea no encontrada después de recargar.")
        
        filename = task.get('original_filename') or task.get('url', 'Tarea')
        if ref := task.get('status_message_ref'):
            try:
                status_message = await bot.get_messages(ref['chat_id'], ref['message_id'])
                await status_message.edit_text(f"Iniciando: <code>{escape_html(filename)}</code>", parse_mode=ParseMode.HTML)
                context = ProgressContext(bot, status_message, task)
            except Exception:
                context = None
        
        dl_dir = os.path.join(DOWNLOAD_DIR, task_id)
        os.makedirs(dl_dir, exist_ok=True)
        files_to_clean.add(dl_dir)
        
        resource_manager.check_disk_space(task.get('file_metadata', {}).get('size', 0))

        actual_download_path = ""
        if url := task.get('url'):
            format_id = task.get('processing_config', {}).get('download_format_id')
            if not format_id: raise InvalidMediaError("Tarea de URL sin 'download_format_id'.")
            actual_download_path = await asyncio.to_thread(downloader.download_from_url, url, os.path.join(dl_dir, task_id), format_id, context)
            if not actual_download_path: raise NetworkError("La descarga desde la URL falló.")
        elif file_id := task.get('file_id'):
            actual_download_path = os.path.join(dl_dir, filename)
            await bot.download_media(message=file_id, file_name=actual_download_path, progress=pyrogram_progress_callback if context else None,
                                     progress_args=(context, "📥 Descargando...", filename) if context else ())
        
        initial_size = os.path.getsize(actual_download_path) if os.path.exists(actual_download_path) else 0

        await resource_manager.acquire_ffmpeg_slot()
        try:
            output_dir = os.path.join(OUTPUT_DIR, task_id); os.makedirs(output_dir, exist_ok=True); files_to_clean.add(output_dir)
            final_filename_base = sanitize_filename(task.get('processing_config', {}).get('final_filename', os.path.splitext(filename)[0]))
            output_path_base = os.path.join(output_dir, f"{final_filename_base}.mp4")
            
            if context: await context.edit_message("Preparando para el procesamiento FFmpeg...")
            commands, definitive_output_path = ffmpeg.build_ffmpeg_command(task, actual_download_path, output_path_base)
            
            for i, cmd in enumerate(commands):
                if not cmd: continue
                if i == len(commands) - 1 and context:
                    await _run_ffmpeg_with_progress(context, cmd, actual_download_path, initial_size)
                else:
                    await _run_ffmpeg_process(cmd)
        finally:
            resource_manager.release_ffmpeg_slot()

        found_files = glob.glob(definitive_output_path) if "*" in definitive_output_path else ([definitive_output_path] if os.path.exists(definitive_output_path) else [])
        if not found_files: raise FFmpegProcessingError("FFmpeg finalizó pero no se encontró el archivo de salida.")

        for final_path in found_files:
            final_size, final_filename = os.path.getsize(final_path), os.path.basename(final_path)
            caption = generate_summary_caption(task, initial_size, final_size, final_filename)
            if context: await context.edit_message(f"⬆️ Subiendo: <code>{escape_html(final_filename)}</code>")
            
            upload_args = {'caption': caption, 'parse_mode': ParseMode.HTML,
                           'progress': pyrogram_progress_callback if context else None,
                           'progress_args': (context, "⬆️ Subiendo...", final_filename) if context else ()}
            
            if final_path.endswith(('.mp4', '.mkv', '.webm')): await bot.send_video(user_id, video=final_path, **upload_args)
            elif final_path.endswith(('.mp3', '.flac', '.m4a', '.opus')): await bot.send_audio(user_id, audio=final_path, **upload_args)
            else: await bot.send_document(user_id, document=final_path, **upload_args)

        await db_instance.update_task_field(task_id, "status", "completed")
        if context: await context.message.delete()

    except Exception as e:
        error_message = f"❌ <b>Error en Tarea</b>\n<code>{escape_html(filename)}</code>\n\n<b>Motivo:</b>\n<pre>{escape_html(str(e))}</pre>"
        status_to_set = "failed"
        if isinstance(e, DiskSpaceError): status_to_set = "paused_no_space"
        
        await db_instance.update_task_fields(task_id, {"status": status_to_set, "last_error": str(e)})
        if context: await context.edit_message(error_message)
        else: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)

    finally:
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath): shutil.rmtree(fpath, ignore_errors=True)
                elif os.path.exists(fpath): os.remove(fpath)
            except Exception as e:
                logger.error(f"No se pudo limpiar {fpath}: {e}")

async def process_metadata_task(bot: Client, task: dict):
    task_id, user_id, context = str(task['_id']), task['user_id'], None
    dl_dir = os.path.join(DOWNLOAD_DIR, f"meta_{task_id}")
    try:
        status_message = await bot.send_message(user_id, f"🔎 Analizando metadatos de <code>{escape_html(task['original_filename'])}</code>...", parse_mode=ParseMode.HTML)
        context = ProgressContext(bot, status_message, task)
        
        resource_manager.check_disk_space(task.get('file_metadata', {}).get('size', 0))
        os.makedirs(dl_dir, exist_ok=True)
        
        file_path = os.path.join(dl_dir, task['original_filename'])
        await bot.download_media(message=task['file_id'], file_name=file_path, progress=pyrogram_progress_callback,
                                 progress_args=(context, "📥 Descargando para análisis...", task['original_filename']))

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
        await context.edit_message(f"✅ Análisis de <code>{escape_html(task['original_filename'])}</code> completo. La tarea está lista en el panel.")
        await asyncio.sleep(5)
        await context.message.delete()

    except Exception as e:
        error_message = f"❌ <b>Error de Análisis</b>\n<code>{escape_html(task['original_filename'])}</code>\n\n<b>Motivo:</b>\n<pre>{escape_html(str(e))}</pre>"
        await db_instance.update_task_fields(task_id, {"status": "failed", "last_error": str(e)})
        if context: await context.edit_message(error_message)
        else: await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
    finally:
        if os.path.exists(dl_dir):
            shutil.rmtree(dl_dir, ignore_errors=True)

async def worker_loop(bot_instance: Client):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
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
                        if task_doc['status'] == 'pending_metadata':
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