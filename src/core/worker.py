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
from typing import Dict, List, Optional

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

class ProgressContext:
    def __init__(self, bot, message, task, loop):
        self.bot, self.message, self.task, self.loop = bot, message, task, loop
        self.start_time, self.last_update_time, self.last_update_text = time.time(), 0, ""
    def reset_timer(self):
        self.start_time, self.last_update_time = time.time(), 0

progress_tracker: Dict[int, ProgressContext] = {}

def _progress_callback_pyrogram(
    current: int,
    total: int,
    user_id: int,
    title: str,
    status: str,
    db_total_size: int,
    file_info: Optional[str] = None
):
    ctx = progress_tracker.get(user_id)
    if not ctx: return
    final_total = total if total > 0 else db_total_size
    if current > final_total: current = final_total
    now = time.time()
    if now - ctx.last_update_time < 1.5 and current < final_total: return
    ctx.last_update_time = now
    elapsed = now - ctx.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (final_total - current) / speed if current > 0 and speed > 0 else float('inf')
    percentage = (current / final_total) * 100 if final_total > 0 else 0
    text = format_status_message(
        operation_title=title,
        percentage=percentage,
        processed_bytes=current,
        total_bytes=final_total,
        speed=speed,
        eta=eta,
        elapsed=elapsed,
        status_tag=status,
        engine="Pyrogram",
        user_id=user_id,
        file_info=file_info
    )
    coro = _edit_status_message(user_id, text, progress_tracker)
    asyncio.run_coroutine_threadsafe(coro, ctx.loop)

async def _run_command_with_progress(user_id: int, command: List[str], input_path: str):
    media_info = get_media_info(input_path)
    try: duration = float(media_info.get("format", {}).get("duration", "0"))
    except (TypeError, ValueError): duration = 0
    time_pattern, ctx = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})"), progress_tracker.get(user_id)
    if not ctx: return
    ctx.reset_timer()
    file_info = os.path.basename(input_path)
    process = await asyncio.create_subprocess_exec(*command, stderr=asyncio.subprocess.PIPE)
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
                percentage, elapsed = (processed_time / duration) * 100, now - ctx.start_time
                speed = processed_time / elapsed if elapsed > 0 else 0
                eta = (duration - processed_time) / speed if speed > 0 else float('inf')
                text = format_status_message(
                    operation_title="→ Processing ...",
                    percentage=percentage,
                    processed_bytes=processed_time,
                    total_bytes=duration,
                    speed=speed,
                    eta=eta,
                    elapsed=elapsed,
                    status_tag="#Processing - #FFmpeg",
                    engine="FFmpeg",
                    user_id=user_id,
                    file_info=file_info
                )
                await _edit_status_message(user_id, text, progress_tracker)
    await process.wait()
    if process.returncode != 0:
        raise Exception(f"FFmpeg falló con código {process.returncode}. Log:\n{''.join(all_stderr_lines[-10:])}") # Solo los últimos 10 logs

async def _process_media_task(bot, task: dict, dl_dir: str):
    user_id, config = task['user_id'], task.get('processing_config', {})
    original_filename = task.get('original_filename', 'archivo.mkv')

    actual_download_path = None
    if file_id := task.get('file_id'):
        actual_download_path = os.path.join(dl_dir, original_filename)
        db_total_size = task.get('file_metadata', {}).get('size', 0)
        await bot.download_media(
            file_id,
            file_name=actual_download_path,
            progress=_progress_callback_pyrogram,
            progress_args=(
                user_id,
                "↓ Downloading ...",
                "#Download - #Telegram",
                db_total_size,
                os.path.basename(actual_download_path)
            )
        )
    elif url := task.get('url'):
        base_path = os.path.join(dl_dir, sanitize_filename(task.get('final_filename', 'url_download')))
        await _edit_status_message(user_id, "Descargando desde URL...", progress_tracker)
        actual_download_path = await asyncio.to_thread(downloader.download_from_url, url, base_path, config.get('download_format_id'))
    else: raise ValueError("La tarea no contiene 'file_id' ni 'url'.")

    if not actual_download_path or not os.path.exists(actual_download_path):
        raise FileNotFoundError("La descarga del archivo principal falló.")

    initial_size = os.path.getsize(actual_download_path)

    watermark_path, watermark_text, replace_audio_path, audio_thumb_path, subs_path = None, None, None, None, None
    if wm_conf := config.get('watermark', {}):
        if wm_conf.get('type') == 'image' and (wm_id := wm_conf.get('file_id')):
            await _edit_status_message(user_id, "Descargando marca de agua...", progress_tracker)
            watermark_path = await bot.download_media(wm_id, file_name=os.path.join(dl_dir, "watermark_img"))
        elif wm_conf.get('type') == 'text':
            watermark_text = wm_conf.get('text')

    if audio_file_id := config.get('replace_audio_file_id'):
        await _edit_status_message(user_id, "Descargando nuevo audio...", progress_tracker)
        replace_audio_path = await bot.download_media(audio_file_id, file_name=os.path.join(dl_dir, "new_audio"))
    if thumb_file_id := config.get('audio_thumbnail_file_id'):
        await _edit_status_message(user_id, "Descargando carátula...", progress_tracker)
        audio_thumb_path = await bot.download_media(thumb_file_id, file_name=os.path.join(dl_dir, "audio_thumb"))
    if subs_file_id := config.get('subs_file_id'):
        await _edit_status_message(user_id, "Descargando subtítulos...", progress_tracker)
        subs_path = await bot.download_media(subs_file_id, file_name=os.path.join(dl_dir, "subtitles.srt"))

    if config.get('gif_options'): output_extension = ".gif"
    elif config.get('extract_audio'): output_extension = ".m4a"
    elif config.get('transcode'): output_extension = ".mp4"
    else: _, original_ext = os.path.splitext(original_filename); output_extension = original_ext if original_ext in ['.mp4', '.mkv', '.mov', '.webm', '.mp3', '.m4a', '.flac'] else ".mkv"

    final_filename_base = sanitize_filename(config.get('final_filename', os.path.splitext(original_filename)[0]))
    output_path = os.path.join(OUTPUT_DIR, f"{final_filename_base}{output_extension}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    command_groups, definitive_output_path = ffmpeg.build_ffmpeg_command(
        task=task, input_path=actual_download_path, output_path=output_path, watermark_path=watermark_path,
        replace_audio_path=replace_audio_path, audio_thumb_path=audio_thumb_path, subs_path=subs_path
    )

    if watermark_text:
        logger.info(f"Aplicando marca de agua de texto: {watermark_text}")
        # Aquí se puede añadir lógica para manejar marcas de agua de texto en FFmpeg

    if command_groups: await _run_command_with_progress(user_id, command_groups[0], actual_download_path)

    if not os.path.exists(definitive_output_path):
        raise FileNotFoundError(f"FFmpeg finalizó pero el archivo de salida '{definitive_output_path}' no fue creado.")

    final_size = os.path.getsize(definitive_output_path)
    caption = generate_summary_caption(task, initial_size, final_size, os.path.basename(definitive_output_path))
    ctx = progress_tracker.get(user_id)
    if ctx: ctx.reset_timer()

    file_type = task.get('file_type', 'video')

    if definitive_output_path.endswith('.gif'): sender_func, kwargs = bot.send_animation, {'animation': definitive_output_path}
    elif file_type == 'video' and not config.get('extract_audio'): sender_func, kwargs = bot.send_video, {'video': definitive_output_path}
    elif file_type == 'audio' or config.get('extract_audio'): sender_func, kwargs = bot.send_audio, {'audio': definitive_output_path}
    else: sender_func, kwargs = bot.send_document, {'document': definitive_output_path}

    await sender_func(
        user_id,
        caption=caption,
        parse_mode=ParseMode.HTML,
        progress=_progress_callback_pyrogram,
        progress_args=(
            user_id,
            "↑ Uploading ...",
            "#Upload - #Telegram",
            final_size,
            os.path.basename(definitive_output_path)
        ),
        **kwargs
    )
    return definitive_output_path

async def _process_join_task(bot, task: dict, dl_dir: str):
    user_id, source_task_ids = task['user_id'], task.get('source_task_ids', [])
    if not source_task_ids: raise ValueError("Tarea de unión sin source_task_ids.")
    await _edit_status_message(user_id, f"Iniciando unión de {len(source_task_ids)} videos...", progress_tracker)
    file_list_path = os.path.join(dl_dir, "file_list.txt")
    with open(file_list_path, 'w', encoding='utf-8') as f:
        for i, tid in enumerate(source_task_ids):
            source_task = await db_instance.get_task(str(tid))
            if not source_task or not source_task.get('file_id'): continue
            filename, dl_path = sanitize_filename(source_task.get('original_filename', f'v_{i}.mp4')), os.path.join(dl_dir, f"{i}_{filename}")
            await _edit_status_message(user_id, f"Descargando video {i+1}/{len(source_task_ids)}...", progress_tracker)
            await bot.download_media(source_task['file_id'], file_name=dl_path)
            f.write(f"file '{dl_path.replace('\'', '\\\'')}'\n")
    output_path = os.path.join(OUTPUT_DIR, f"{sanitize_filename(task.get('final_filename', 'union_video'))}.mp4")
    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", file_list_path, "-c", "copy", output_path]
    await _edit_status_message(user_id, "Uniendo videos...", progress_tracker)
    process = await asyncio.create_subprocess_exec(*command, stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    if process.returncode != 0: raise Exception(f"FFmpeg (concat) falló: {stderr.decode()}")
    final_size = os.path.getsize(output_path)
    await bot.send_video(
        user_id,
        video=output_path,
        caption=f"✅ Unión de {len(source_task_ids)} videos completada.",
        progress=_progress_callback_pyrogram,
        progress_args=(
            user_id,
            "↑ Uploading ...",
            "#Upload - #Telegram",
            final_size,
            os.path.basename(output_path)
        )
    )
    return output_path

async def _process_zip_task(bot, task: dict, dl_dir: str):
    user_id, source_task_ids = task['user_id'], task.get('source_task_ids', [])
    if not source_task_ids: raise ValueError("Tarea de compresión sin source_task_ids.")
    output_path = os.path.join(OUTPUT_DIR, f"{sanitize_filename(task.get('final_filename', 'comprimido'))}.zip")
    with ZipFile(output_path, 'w', ZIP_DEFLATED) as zf:
        for i, tid in enumerate(source_task_ids):
            source_task = await db_instance.get_task(str(tid))
            if not source_task or not source_task.get('file_id'): continue
            filename, dl_path = sanitize_filename(source_task.get('original_filename', f'f_{i}')), os.path.join(dl_dir, filename)
            await _edit_status_message(user_id, f"Descargando para ZIP: {i+1}/{len(source_task_ids)}...", progress_tracker)
            await bot.download_media(source_task['file_id'], file_name=dl_path)
            await _edit_status_message(user_id, f"Añadiendo al ZIP: {filename}", progress_tracker)
            zf.write(dl_path, arcname=filename)
    final_size = os.path.getsize(output_path)
    await bot.send_document(
        user_id,
        document=output_path,
        caption=f"✅ Compresión de {len(source_task_ids)} archivos completada.",
        progress=_progress_callback_pyrogram,
        progress_args=(
            user_id,
            "↑ Uploading ...",
            "#Upload - #Telegram",
            final_size,
            os.path.basename(output_path)
        )
    )
    return output_path

async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    status_message, files_to_clean = None, set()
    original_filename = "Tarea sin nombre"
    
    # Verificar si la tarea fue cancelada antes de procesar
    current_task = await db_instance.get_task(task_id)
    if not current_task or current_task.get('status') == 'cancelled':
        logger.info(f"Tarea {task_id} fue cancelada, saltando procesamiento")
        return
    
    # Manejar descargas de canales restringidos
    if task.get('is_restricted', False):
        try:
            return await process_restricted_content(bot, task)
        except Exception as e:
            logger.error(f"Error procesando contenido restringido: {e}")
            await bot.send_message(
                user_id,
                f"❌ <b>Error al procesar contenido restringido</b>\n"
                f"<code>{escape_html(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
            return
    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("Tarea no encontrada.")
        
        # Verificar cancelación nuevamente después de obtener la tarea
        if task.get('status') == 'cancelled':
            logger.info(f"Tarea {task_id} fue cancelada durante el procesamiento")
            return
            
        file_type = task.get('file_type', 'video')
        original_filename = task.get('original_filename') or task.get('url', 'Tarea sin nombre')
        status_message = await bot.send_message(user_id, "✅ Tarea recibida. Preparando...", parse_mode=ParseMode.HTML)
        global progress_tracker; progress_tracker[user_id] = ProgressContext(bot, status_message, task, asyncio.get_running_loop())
        task_dir = os.path.join(DOWNLOAD_DIR, task_id); os.makedirs(task_dir, exist_ok=True); files_to_clean.add(task_dir)

        definitive_output_path = None
        if file_type in ['video', 'audio', 'document']: definitive_output_path = await _process_media_task(bot, task, task_dir)
        elif file_type == 'join_operation': definitive_output_path = await _process_join_task(bot, task, task_dir)
        elif file_type == 'zip_operation': definitive_output_path = await _process_zip_task(bot, task, task_dir)
        else: raise NotImplementedError(f"Tipo de tarea '{file_type}' no implementado.")

        if definitive_output_path: files_to_clean.add(definitive_output_path)
        await db_instance.update_task(task_id, "status", "done")
        # [FIX] Manejo seguro de la eliminación del mensaje de estado.
        if status_message:
            try: await status_message.delete()
            except Exception: pass

    except Exception as e:
        logger.critical(f"Error procesando tarea {task_id}: {e}", exc_info=True)
        error_message = f"❌ <b>Error Fatal en Tarea</b>\n<code>{escape_html(original_filename)}</code>\n\n<b>Motivo:</b>\n<pre>{escape_html(str(e))}</pre>"
        await db_instance.update_task(task_id, "status", "error")
        await db_instance.update_task(task_id, "last_error", str(e))

        # [FIX] Manejo seguro de la edición/envío del mensaje de error.
        if status_message:
            try: await status_message.edit_text(error_message, parse_mode=ParseMode.HTML)
            except Exception:
                logger.warning(f"No se pudo editar mensaje de error para {user_id}. Enviando uno nuevo.")
                await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
        else:
            await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)

    finally:
        if user_id in progress_tracker: del progress_tracker[user_id]
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath): shutil.rmtree(fpath, ignore_errors=True)
                elif os.path.exists(fpath): os.remove(fpath)
            except Exception as e: logger.error(f"No se pudo limpiar {fpath}: {e}")

async def process_restricted_content(bot, task: dict) -> None:
    """Procesa contenido de canales restringidos"""
    user_id = task['user_id']
    message_link = task.get('message_link')
    status_message = None
    
    try:
        # Validar el enlace
        if not message_link:
            raise ValueError("No se proporcionó enlace al mensaje")
            
        # Enviar mensaje de estado inicial
        status_message = await bot.send_message(
            user_id,
            "🔄 <b>Procesando contenido restringido...</b>",
            parse_mode=ParseMode.HTML
        )
        
        # Intentar obtener el mensaje
        try:
            message = await bot.get_messages(
                chat_id=task.get('chat_id'),
                message_ids=task.get('message_id')
            )
        except Exception as e:
            raise Exception(f"No se pudo acceder al mensaje: {str(e)}")
            
        if not message:
            raise ValueError("No se encontró el mensaje")
            
        if not message.media:
            raise ValueError("El mensaje no contiene archivos multimedia")
            
        # Actualizar mensaje de estado
        await status_message.edit_text(
            "⬇️ <b>Descargando archivo del canal restringido...</b>",
            parse_mode=ParseMode.HTML
        )
        
        # Preparar directorios
        task_dir = os.path.join(DOWNLOAD_DIR, str(task['_id']))
        os.makedirs(task_dir, exist_ok=True)
        
        # Descargar el archivo
        file_basename = getattr(message.media, 'file_name', 'restricted_file')
        file_path = await bot.download_media(
            message,
            file_name=os.path.join(task_dir, file_basename),
            progress=_progress_callback_pyrogram,
            progress_args=(
                user_id,
                "↓ Downloading ...",
                "#Download - #Restricted",
                message.media.file_size if hasattr(message.media, 'file_size') else 0,
                file_basename
            )
        )
        
        if not file_path:
            raise Exception("Error al descargar el archivo")
            
        # Actualizar estado
        await status_message.edit_text(
            "⬆️ <b>Subiendo archivo procesado...</b>",
            parse_mode=ParseMode.HTML
        )
        
        # Determinar tipo de archivo y enviar
        if message.video:
            await bot.send_video(
                user_id,
                video=file_path,
                caption=f"✅ <b>Archivo descargado exitosamente</b>\n🔗 De: {message_link}",
                parse_mode=ParseMode.HTML,
                progress=_progress_callback_pyrogram,
                progress_args=(
                    user_id,
                    "↑ Uploading ...",
                    "#Upload - #Restricted",
                    os.path.getsize(file_path),
                    os.path.basename(file_path)
                )
            )
        elif message.document:
            await bot.send_document(
                user_id,
                document=file_path,
                caption=f"✅ <b>Archivo descargado exitosamente</b>\n🔗 De: {message_link}",
                parse_mode=ParseMode.HTML,
                progress=_progress_callback_pyrogram,
                progress_args=(
                    user_id,
                    "↑ Uploading ...",
                    "#Upload - #Restricted",
                    os.path.getsize(file_path),
                    os.path.basename(file_path)
                )
            )
        elif message.audio:
            await bot.send_audio(
                user_id,
                audio=file_path,
                caption=f"✅ <b>Archivo descargado exitosamente</b>\n🔗 De: {message_link}",
                parse_mode=ParseMode.HTML,
                progress=_progress_callback_pyrogram,
                progress_args=(
                    user_id,
                    "↑ Uploading ...",
                    "#Upload - #Restricted",
                    os.path.getsize(file_path),
                    os.path.basename(file_path)
                )
            )
            
        # Limpiar
        try:
            if os.path.exists(task_dir):
                shutil.rmtree(task_dir)
        except Exception as e:
            logger.error(f"Error limpiando archivos temporales: {e}")
            
        # Eliminar mensaje de estado
        try:
            await status_message.delete()
        except Exception:
            pass
            
    except Exception as e:
        error_msg = f"❌ <b>Error al procesar contenido restringido</b>\n<code>{escape_html(str(e))}</code>"
        if status_message:
            try:
                await status_message.edit_text(error_msg, parse_mode=ParseMode.HTML)
            except Exception:
                await bot.send_message(user_id, error_msg, parse_mode=ParseMode.HTML)
        else:
            await bot.send_message(user_id, error_msg, parse_mode=ParseMode.HTML)
        
        # Actualizar estado de la tarea
        await db_instance.update_task(str(task['_id']), "status", "error")
        await db_instance.update_task(str(task['_id']), "last_error", str(e))

class TaskQueue:
    def __init__(self, max_concurrent_tasks=3, min_task_interval=5):
        self.active_tasks = {}  # user_id -> Task
        self.max_concurrent_tasks = max_concurrent_tasks
        self.min_task_interval = min_task_interval
        self.last_task_time = {}  # user_id -> timestamp
        self.task_semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def can_start_task(self, user_id):
        """Check if a new task can be started for the user."""
        now = time.time()
        if user_id in self.last_task_time:
            if now - self.last_task_time[user_id] < self.min_task_interval:
                return False
        return True

    def register_task(self, user_id, task):
        """Register a new task for a user."""
        self.active_tasks[user_id] = task
        self.last_task_time[user_id] = time.time()

    def remove_task(self, user_id):
        """Remove a completed task."""
        self.active_tasks.pop(user_id, None)

    async def process_with_rate_limit(self, bot_instance, task):
        """Process a task with rate limiting."""
        async with self.task_semaphore:
            try:
                return await process_task(bot_instance, task)
            finally:
                self.remove_task(task['user_id'])

async def worker_loop(bot_instance):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True); os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    task_queue = TaskQueue(max_concurrent_tasks=3, min_task_interval=5)
    
    while True:
        try:
            # Clean up completed tasks
            for user_id in list(task_queue.active_tasks.keys()):
                task = task_queue.active_tasks[user_id]
                if task.done():
                    try:
                        await task
                    except Exception as e:
                        logger.error(f"Task for user {user_id} failed: {e}")
                    task_queue.remove_task(user_id)
            
            # Get available users not currently processing tasks
            available_users = [
                uid for uid in (await db_instance.tasks.distinct("user_id", {"status": "queued"}))
                if uid not in task_queue.active_tasks and task_queue.can_start_task(uid)
            ]
            
            if not available_users:
                await asyncio.sleep(2)
                continue
            
            # Process one task per available user
            for user_id in available_users:
                if len(task_queue.active_tasks) >= task_queue.max_concurrent_tasks:
                    break
                    
                task = await db_instance.tasks.find_one_and_update(
                    {
                        "status": "queued",
                        "user_id": user_id
                    },
                    {
                        "$set": {
                            "status": "processing",
                            "processed_at": datetime.utcnow(),
                            "queue_position": await db_instance.tasks.count_documents({"status": "processing"}) + 1
                        }
                    },
                    sort=[('priority', -1), ('created_at', 1)]
                )
                
                if task:
                    try:
                        queue_msg = (
                            f"⌛ <b>Tarea en cola</b>\n"
                            f"Posición: {task['queue_position']}\n"
                            f"ID: <code>{task['_id']}</code>"
                        )
                        await bot_instance.send_message(user_id, queue_msg, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        logger.error(f"Error sending queue message: {e}")
                    
                    process_task_obj = asyncio.create_task(task_queue.process_with_rate_limit(bot_instance, task))
                    task_queue.register_task(user_id, process_task_obj)
            
            await asyncio.sleep(2)
            
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(10)