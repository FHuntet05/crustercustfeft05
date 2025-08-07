import logging
import time
import os
import asyncio
import re
import glob
from datetime import datetime
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaVideo

from src.db.mongo_manager import db_instance
from src.helpers.utils import (format_status_message, sanitize_filename, 
                               escape_html, _edit_status_message, ADMIN_USER_ID)
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

async def _progress_callback_pyrogram(current, total, user_id, operation):
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
        filename=ctx.task.get('original_filename', 'archivo'),
        percentage=percentage, 
        processed_bytes=current, 
        total_bytes=total,
        speed=speed, 
        eta=eta, 
        engine="Pyrogram", 
        user_id=user_id,
        user_mention=user_mention
    )
    
    # --- SOLUCIÓN AL PROGRESO VISUAL ---
    # Usar run_coroutine_threadsafe para llamar a la corutina desde el hilo de Pyrogram
    asyncio.run_coroutine_threadsafe(_edit_status_message(user_id, text, progress_tracker), ctx.loop)

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
    
    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("Tarea no encontrada después de recargar.")
        
        config = task.get('processing_config', {})
        initial_message_id = config.get('initial_message_id')
        
        initial_text = f"Iniciando: <code>{escape_html(task.get('original_filename') or task.get('url', 'Tarea'))}</code>"

        if initial_message_id:
            try:
                status_message = await bot.edit_message_text(user_id, initial_message_id, initial_text, parse_mode=ParseMode.HTML)
            except Exception:
                status_message = await bot.send_message(user_id, initial_text, parse_mode=ParseMode.HTML)
        else:
            status_message = await bot.send_message(user_id, initial_text, parse_mode=ParseMode.HTML)

        global progress_tracker
        progress_tracker[user_id] = ProgressContext(bot, status_message, task, asyncio.get_running_loop())
    
        files_to_clean = set()
        
        actual_download_path = ""
        if url := task.get('url'):
            if not (format_id := config.get('download_format_id')): raise Exception("La tarea no tiene 'download_format_id'.")
            actual_download_path = await asyncio.to_thread(downloader.download_from_url, url, os.path.join(DOWNLOAD_DIR, task_id), format_id, progress_tracker=progress_tracker, user_id=user_id)
            if not actual_download_path: raise Exception("La descarga desde la URL falló.")
        elif file_id := task.get('file_id'):
            actual_download_path = os.path.join(DOWNLOAD_DIR, task_id)
            await bot.download_media(message=file_id, file_name=actual_download_path, progress=_progress_callback_pyrogram, progress_args=(user_id, "📥 Descargando..."))
        else:
            raise Exception("La tarea no tiene URL ni file_id.")
        
        files_to_clean.add(actual_download_path)
        logger.info(f"Descarga completada en: {actual_download_path}")
        
        file_type = task.get('file_type', 'document')
        await _edit_status_message(user_id, f"⚙️ Archivo ({file_type}) listo para procesar.", progress_tracker)
        
        base_name_from_config = config.get('final_filename', task.get('original_filename', task_id))
        final_filename_base = os.path.splitext(sanitize_filename(base_name_from_config))[0]

        if 'gif_options' in config: output_path = os.path.join(OUTPUT_DIR, final_filename_base)
        elif file_type == 'audio': output_path = os.path.join(OUTPUT_DIR, f"{final_filename_base}.{config.get('audio_format', 'mp3')}")
        else: output_path = os.path.join(OUTPUT_DIR, f"{final_filename_base}.mp4")
        
        files_to_clean.add(output_path)
        
        watermark_path, thumb_to_use, subs_to_use = None, None, None

        if config.get('watermark', {}).get('type') == 'image' and (wm_file_id := config['watermark'].get('file_id')):
            watermark_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_watermark.png"); await bot.download_media(message=wm_file_id, file_name=watermark_path)
            if os.path.exists(watermark_path): files_to_clean.add(watermark_path)
        
        thumb_file_id = config.get('thumbnail_file_id'); thumb_url = config.get('thumbnail_url') or task.get('url_info', {}).get('thumbnail')
        if thumb_file_id or thumb_url:
            thumb_path = os.path.join(DOWNLOAD_DIR, f"{task_id}.jpg")
            if thumb_file_id: await bot.download_media(message=thumb_file_id, file_name=thumb_path)
            elif thumb_url: await asyncio.to_thread(downloader.download_file, thumb_url, thumb_path)
            if os.path.exists(thumb_path): thumb_to_use = thumb_path; files_to_clean.add(thumb_path)
        
        if subs_file_id := config.get('subs_file_id'):
            subs_path = os.path.join(DOWNLOAD_DIR, f"{task_id}.srt"); await bot.download_media(message=subs_file_id, file_name=subs_path)
            if os.path.exists(subs_path): subs_to_use = subs_path; files_to_clean.add(subs_path)

        commands = ffmpeg.build_ffmpeg_command(task, actual_download_path, output_path, thumb_to_use, watermark_path, subs_to_use)
        
        if commands:
            if 'gif_options' in config: files_to_clean.add(f"{output_path}.palette.png")
            for cmd in commands: await _run_ffmpeg_with_progress(user_id, cmd, actual_download_path)
            
            if 'split_criteria' in config:
                base_name, ext = os.path.splitext(output_path); found_parts = sorted(glob.glob(f"{base_name}_part*{ext}"))
                if not found_parts: raise Exception("La división falló.")
                files_to_clean.update(found_parts)
                await _edit_status_message(user_id, f"⬆️ Subiendo {len(found_parts)} partes...", progress_tracker)
                media_group = [InputMediaVideo(media=p, caption=f"✅ Parte {i+1}/{len(found_parts)}\n<code>{escape_html(os.path.basename(p))}</code>" if i==0 else "") for i, p in enumerate(found_parts)]
                await bot.send_media_group(user_id, media=media_group)
            
            elif 'gif_options' in config:
                gif_path = f"{os.path.splitext(output_path)[0]}.gif"
                if not os.path.exists(gif_path): raise Exception("La creación del GIF falló.")
                files_to_clean.add(gif_path)
                await bot.send_animation(user_id, animation=gif_path, caption=f"✅ <code>{escape_html(os.path.basename(gif_path))}</code>", parse_mode=ParseMode.HTML)
            
            else:
                caption = f"✅ <code>{escape_html(os.path.basename(output_path))}</code>"
                if file_type == 'video': await bot.send_video(user_id, video=output_path, thumb=thumb_to_use, caption=caption, parse_mode=ParseMode.HTML, progress=_progress_callback_pyrogram, progress_args=(user_id, "⬆️ Subiendo..."))
                elif file_type == 'audio': await bot.send_audio(user_id, audio=output_path, thumb=thumb_to_use, caption=caption, parse_mode=ParseMode.HTML, progress=_progress_callback_pyrogram, progress_args=(user_id, "⬆️ Subiendo..."))
        
        await db_instance.update_task(task_id, "status", "done")
        if status_message: await status_message.delete()

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        error_str = str(e)
        await db_instance.update_task(task_id, "status", "error")
        await db_instance.update_task(task_id, "last_error", error_str)
        
        error_message_to_user = f"❌ <b>Error Grave</b>\n\n<code>{escape_html(error_str)}</code>"
        if "Telegram me está bloqueando" in error_str:
            if ADMIN_USER_ID: await bot.send_message(ADMIN_USER_ID, "⚠️ <b>¡Alerta de Mantenimiento, Jefe!</b>\n\nMis cookies de YouTube han expirado.", parse_mode=ParseMode.HTML)
            error_message_to_user = f"❌ <b>Error de Autenticación</b>\n\n<code>{escape_html(error_str)}</code>"
        
        if status_message: await _edit_status_message(user_id, error_message_to_user, progress_tracker)
        else: await bot.send_message(user_id, error_message_to_user, parse_mode=ParseMode.HTML)

    finally:
        if user_id in progress_tracker: del progress_tracker[user_id]
        for fpath in files_to_clean:
            if os.path.exists(fpath): 
                try: os.remove(fpath)
                except Exception as e: logger.error(f"No se pudo limpiar {fpath}: {e}")

async def worker_loop(bot_instance):
    logger.info("[WORKER] Bucle del worker iniciado.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    while True:
        try:
            task = await db_instance.tasks.find_one_and_update(
                {"status": "queued", "user_id": {"$nin": list(progress_tracker.keys())}},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}},
                sort=[('created_at', 1)]
            )
            if task:
                logger.info(f"Iniciando procesamiento de la tarea {task['_id']} para el usuario {task['user_id']}")
                asyncio.create_task(process_task(bot_instance, task))
            else:
                await asyncio.sleep(5)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(30)