# src/core/worker.py

import logging
import time
import os
import asyncio
import re
import glob
from datetime import datetime
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaVideo
from bson.objectid import ObjectId
import shutil
import zipfile

from src.db.mongo_manager import db_instance
from src.helpers.utils import (format_status_message, sanitize_filename, 
                               escape_html, _edit_status_message, ADMIN_USER_ID,
                               generate_summary_caption)
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

async def _progress_callback_pyrogram(current, total, user_id, operation, filename=""):
    if user_id not in progress_tracker or not total or total <= 0: return
    ctx = progress_tracker[user_id]
    
    elapsed = time.time() - ctx.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    percentage = (current / total) * 100
    
    user_mention = "Usuario"
    if hasattr(ctx.message, 'from_user') and ctx.message.from_user:
        user_mention = ctx.message.from_user.mention

    text = format_status_message(
        operation=operation, 
        filename=filename or ctx.task.get('original_filename', 'archivo'),
        percentage=percentage, 
        processed_bytes=current, 
        total_bytes=total,
        speed=speed, 
        eta=eta, 
        engine="Pyrogram", 
        user_id=user_id,
        user_mention=user_mention
    )
    
    asyncio.run_coroutine_threadsafe(_edit_status_message(user_id, text, progress_tracker), ctx.loop)

async def _run_ffmpeg_process(cmd: str):
    """Ejecuta un comando FFmpeg sin monitoreo de progreso."""
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error_log = stderr.decode('utf-8', 'ignore')
        logger.error(f"FFmpeg (sin progreso) falló. Código: {process.returncode}\nError: {error_log}")
        raise Exception(f"El proceso de FFmpeg falló. Log:\n...{error_log[-450:]}")

async def _run_ffmpeg_with_progress(user_id: int, cmd: str, input_path: str):
    duration_info = get_media_info(input_path)
    total_duration_sec = float(duration_info.get('format', {}).get('duration', 0))
    if total_duration_sec == 0: logger.warning("No se pudo obtener la duración.")
    
    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    
    ctx = progress_tracker.get(user_id)
    start_time = time.time()

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
                if total_duration_sec > 0 and ctx:
                    h, m, s, ms = map(int, match.groups())
                    processed_sec = h * 3600 + m * 60 + s + ms / 100
                    percentage = (processed_sec / total_duration_sec) * 100
                    elapsed = time.time() - start_time
                    speed_factor = processed_sec / elapsed if elapsed > 0 else 0
                    eta = (total_duration_sec - processed_sec) / speed_factor if speed_factor > 0 else 0
                    
                    user_mention = "Usuario"
                    if hasattr(ctx.message, 'from_user') and ctx.message.from_user: user_mention = ctx.message.from_user.mention
                    
                    text = format_status_message(
                        operation="⚙️ Procesando...", filename=ctx.task.get('original_filename', 'archivo'),
                        percentage=percentage, processed_bytes=processed_sec, total_bytes=total_duration_sec,
                        speed=speed_factor, eta=eta, engine="FFmpeg", user_id=user_id,
                        user_mention=user_mention
                    )
                    await _edit_status_message(user_id, text, progress_tracker)

    if stderr_buffer.strip(): all_stderr_lines.append(stderr_buffer.strip())
            
    await process.wait()
    if process.returncode != 0:
        error_log = "\n".join(all_stderr_lines[-20:])
        logger.error(f"FFmpeg falló. Código: {process.returncode}\nError: {error_log}")
        raise Exception(f"El proceso de FFmpeg falló. Log:\n...{error_log[-450:]}")

async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    status_message = None
    files_to_clean = set()

    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("Tarea no encontrada después de recargar.")
        
        config = task.get('processing_config', {})
        
        initial_text = f"Iniciando: <code>{escape_html(task.get('original_filename') or task.get('url', 'Tarea'))}</code>"
        
        # --- NUEVO: Lógica para adoptar o crear un mensaje de estado ---
        if ref := task.get('status_message_ref'):
            try:
                # Intenta "adoptar" y editar el mensaje que el usuario ya está viendo.
                status_message = await bot.get_messages(ref['chat_id'], ref['message_id'])
                await status_message.edit_text(initial_text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"No se pudo adoptar el mensaje de estado {ref['message_id']} para la tarea {task_id}: {e}. Se creará uno nuevo.")
                status_message = None # Forzar la creación de un nuevo mensaje.

        if not status_message:
            # Comportamiento de fallback: si no hay referencia o falló la adopción, enviar un nuevo mensaje.
            # Esto es clave para las tareas silenciosas (búsqueda de música) que no deben tener UI.
            if task.get('file_type') != 'audio' or 'download_format_id' not in config:
                 try:
                     status_message = await bot.send_message(user_id, initial_text, parse_mode=ParseMode.HTML)
                 except Exception:
                    # Si no se puede ni enviar un mensaje nuevo, es un error fatal para esta tarea.
                    raise Exception("No se pudo enviar el mensaje de estado inicial.")

        global progress_tracker
        # Solo rastrear el progreso si hay un mensaje que actualizar.
        if status_message:
            progress_tracker[user_id] = ProgressContext(bot, status_message, task, asyncio.get_running_loop())
        
        # --- META-TAREAS (UNIÓN Y COMPRESIÓN) ---
        if task.get('file_type') == 'join_operation':
            source_task_ids = config.get('source_task_ids', [])
            if not source_task_ids: raise Exception("Tarea de unión sin videos fuente.")
            
            join_dir = os.path.join(DOWNLOAD_DIR, f"join_{task_id}")
            os.makedirs(join_dir, exist_ok=True)
            files_to_clean.add(join_dir)
            
            downloaded_files = []
            for i, source_id in enumerate(source_task_ids):
                source_task = await db_instance.tasks.find_one({"_id": ObjectId(source_id)})
                if not source_task: continue
                
                filename = source_task.get('original_filename', f'video_{i+1}.mp4')
                dl_path = os.path.join(join_dir, filename)
                
                prog_args = (user_id, f"📥 Descargando ({i+1}/{len(source_task_ids)})...", filename)
                # Solo mostrar progreso si hay UI
                progress_fn = _progress_callback_pyrogram if status_message else None
                await bot.download_media(message=source_task['file_id'], file_name=dl_path, progress=progress_fn, progress_args=prog_args)
                downloaded_files.append(dl_path)

            file_list_path = os.path.join(join_dir, "file_list.txt")
            with open(file_list_path, 'w', encoding='utf-8') as f:
                for file_path in downloaded_files: f.write(f"file '{os.path.abspath(file_path)}'\n")

            output_path = os.path.join(OUTPUT_DIR, f"Union_{task_id}.mp4")
            files_to_clean.add(output_path)

            if status_message: await _edit_status_message(user_id, "⚙️ Uniendo videos...", progress_tracker)
            join_cmd = ffmpeg.build_join_command(file_list_path, output_path)
            await _run_ffmpeg_process(join_cmd)

            prog_args_upload = (user_id, "⬆️ Subiendo...")
            progress_fn_upload = _progress_callback_pyrogram if status_message else None
            await bot.send_video(user_id, video=output_path, caption=f"✅ Video unido a partir de {len(downloaded_files)} fuentes.", progress=progress_fn_upload, progress_args=prog_args_upload)
            
            await db_instance.update_task(task_id, "status", "done")
            if status_message: await status_message.delete()
            return
        
        elif task.get('file_type') == 'zip_operation':
            source_task_ids = config.get('source_task_ids', [])
            if not source_task_ids: raise Exception("Tarea de compresión sin archivos fuente.")
            
            zip_dir = os.path.join(DOWNLOAD_DIR, f"zip_{task_id}")
            os.makedirs(zip_dir, exist_ok=True)
            files_to_clean.add(zip_dir)
            
            downloaded_files = []
            for i, source_id in enumerate(source_task_ids):
                source_task = await db_instance.tasks.find_one({"_id": ObjectId(source_id)})
                if not source_task: continue

                filename = source_task.get('original_filename', f'archivo_{i+1}')
                dl_path = os.path.join(zip_dir, filename)
                
                prog_args = (user_id, f"📥 Descargando ({i+1}/{len(source_task_ids)})...", filename)
                progress_fn = _progress_callback_pyrogram if status_message else None
                await bot.download_media(message=source_task['file_id'], file_name=dl_path, progress=progress_fn, progress_args=prog_args)
                downloaded_files.append(dl_path)

            output_zip_path = os.path.join(OUTPUT_DIR, task.get('original_filename'))
            files_to_clean.add(output_zip_path)
            
            if status_message: await _edit_status_message(user_id, "⚙️ Comprimiendo archivos...", progress_tracker)
            with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in downloaded_files:
                    zipf.write(file, arcname=os.path.basename(file))
            
            prog_args_upload = (user_id, "⬆️ Subiendo ZIP...")
            progress_fn_upload = _progress_callback_pyrogram if status_message else None
            await bot.send_document(user_id, document=output_zip_path, caption=f"✅ Archivo ZIP creado con {len(downloaded_files)} archivos.", progress=progress_fn_upload, progress_args=prog_args_upload)

            await db_instance.update_task(task_id, "status", "done")
            if status_message: await status_message.delete()
            return
            
        # --- Flujo de procesamiento estándar ---
        actual_download_path = ""
        if url := task.get('url'):
            format_id = config.get('download_format_id')
            if not format_id: raise Exception("La tarea de URL no tiene 'download_format_id'. Flujo interrumpido.")
            
            # Pasar progress_tracker solo si hay un mensaje de estado que actualizar
            tracker_to_use = progress_tracker if status_message else None
            actual_download_path = await asyncio.to_thread(downloader.download_from_url, url, os.path.join(DOWNLOAD_DIR, task_id), format_id, progress_tracker=tracker_to_use, user_id=user_id)
            if not actual_download_path: raise Exception("La descarga desde la URL falló.")
        elif file_id := task.get('file_id'):
            dl_dir = os.path.join(DOWNLOAD_DIR, task_id)
            os.makedirs(dl_dir, exist_ok=True)
            actual_download_path = os.path.join(dl_dir, task.get('original_filename', task_id))
            
            prog_args = (user_id, "📥 Descargando...")
            progress_fn = _progress_callback_pyrogram if status_message else None
            await bot.download_media(message=file_id, file_name=actual_download_path, progress=progress_fn, progress_args=prog_args)
        else:
            raise Exception("La tarea no tiene URL ni file_id.")
        
        files_to_clean.add(os.path.dirname(actual_download_path))
        initial_size = os.path.getsize(actual_download_path) if os.path.exists(actual_download_path) else 0
        logger.info(f"Descarga completada en: {actual_download_path}")
        
        base_name_from_config = config.get('final_filename', task.get('original_filename', task_id))
        final_filename_base = os.path.splitext(sanitize_filename(base_name_from_config))[0]
        
        output_path = os.path.join(OUTPUT_DIR, f"{final_filename_base}.mp4")
        if 'gif_options' in config: output_path = os.path.join(OUTPUT_DIR, final_filename_base)
        elif task.get('file_type') == 'audio': output_path = os.path.join(OUTPUT_DIR, f"{final_filename_base}.{config.get('audio_format', 'mp3')}")
        elif config.get('extract_audio'): output_path = os.path.join(OUTPUT_DIR, final_filename_base)

        files_to_clean.add(output_path)
        
        watermark_path, thumb_to_use, subs_to_use, new_audio_path = None, None, None, None

        if status_message: await _edit_status_message(user_id, f"⚙️ Archivo listo para procesar.", progress_tracker)
        commands = ffmpeg.build_ffmpeg_command(task, actual_download_path, output_path, thumb_to_use, watermark_path, subs_to_use, new_audio_path)
        
        for i, cmd in enumerate(commands):
            if i == len(commands) - 1:
                # El último comando (o único) puede usar la barra de progreso.
                if status_message:
                    await _run_ffmpeg_with_progress(user_id, cmd, actual_download_path)
                else:
                    await _run_ffmpeg_process(cmd)
            else:
                # Los comandos intermedios (ej. paleta de GIF) se ejecutan sin progreso.
                await _run_ffmpeg_process(cmd)

        # Determinar el archivo final correcto después de la ejecución de comandos.
        final_output_path = glob.glob(f"{os.path.splitext(output_path)[0]}.*")
        if not final_output_path: raise Exception(f"FFmpeg finalizó pero no se encontró ningún archivo de salida en {output_path}")
        final_output_path = final_output_path[0]
        final_size = os.path.getsize(final_output_path) if os.path.exists(final_output_path) else 0

        final_filename = os.path.basename(final_output_path)
        caption = generate_summary_caption(task, initial_size, final_size, final_filename)
        
        progress_fn_upload = _progress_callback_pyrogram if status_message else None
        prog_args_upload = (user_id, "⬆️ Subiendo...", final_filename)

        if final_output_path.endswith(('.mp4', '.mkv', '.webm')):
            await bot.send_video(user_id, video=final_output_path, caption=caption, parse_mode=ParseMode.HTML, progress=progress_fn_upload, progress_args=prog_args_upload)
        elif final_output_path.endswith(('.mp3', '.flac', '.m4a', '.opus')):
            await bot.send_audio(user_id, audio=final_output_path, caption=caption, parse_mode=ParseMode.HTML, progress=progress_fn_upload, progress_args=prog_args_upload)
        else:
            await bot.send_document(user_id, document=final_output_path, caption=caption, parse_mode=ParseMode.HTML, progress=progress_fn_upload, progress_args=prog_args_upload)

        await db_instance.update_task(task_id, "status", "done")
        if status_message: await status_message.delete()

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        error_message = f"❌ <b>Error Fatal en Tarea</b>\n<code>{escape_html(task.get('original_filename', 'N/A'))}</code>\n\n<b>Motivo:</b>\n<pre>{escape_html(str(e))}</pre>"
        
        await db_instance.update_task(task_id, "status", "error")
        await db_instance.update_task(task_id, "last_error", str(e))
        
        # Intentar editar el mensaje de estado con el error o enviar uno nuevo.
        if status_message:
            try:
                await status_message.edit_text(error_message, parse_mode=ParseMode.HTML)
            except Exception:
                await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)
        else:
            await bot.send_message(user_id, error_message, parse_mode=ParseMode.HTML)

    finally:
        if user_id in progress_tracker: del progress_tracker[user_id]
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath):
                    shutil.rmtree(fpath, ignore_errors=True)
                elif os.path.exists(fpath):
                    os.remove(fpath)
            except Exception as e:
                logger.error(f"No se pudo limpiar {fpath}: {e}")


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
                logger.info(f"Iniciando procesamiento de la tarea {task['_id']} para el usuario {task['user_id']}")
                asyncio.create_task(process_task(bot_instance, task))
            else:
                await asyncio.sleep(2)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(30)