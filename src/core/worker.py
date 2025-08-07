# --- START OF FILE src/core/worker.py ---

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
    if user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    elapsed = time.time() - ctx.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    percentage = (current / total) * 100 if total > 0 else 0
    
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
    await _edit_status_message(user_id, text, progress_tracker)

async def _run_ffmpeg_with_progress(user_id: int, cmd: str, input_path: str):
    duration_info = get_media_info(input_path)
    total_duration_sec = float(duration_info.get('format', {}).get('duration', 0))
    if total_duration_sec == 0: logger.warning("No se pudo obtener la duración.")
    
    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    
    ctx = progress_tracker.get(user_id)
    start_time = time.time()

    all_stderr_lines = []
    while True:
        line = await process.stderr.readline()
        if not line: break
        line_str = line.decode('utf-8', 'ignore').strip()
        all_stderr_lines.append(line_str)
        if match := time_pattern.search(line_str):
            if total_duration_sec > 0:
                h, m, s, ms = map(int, match.groups())
                processed_sec = h * 3600 + m * 60 + s + ms / 100
                percentage = (processed_sec / total_duration_sec) * 100
                elapsed = time.time() - start_time
                speed_factor = processed_sec / elapsed if elapsed > 0 else 0
                eta = (total_duration_sec - processed_sec) / speed_factor if speed_factor > 0 else 0
                
                user_mention = "Usuario"
                if hasattr(ctx.message, 'from_user') and ctx.message.from_user:
                    user_mention = ctx.message.from_user.mention
                
                text = format_status_message(
                    operation="⚙️ Procesando...", filename=ctx.task.get('original_filename', 'archivo'),
                    percentage=percentage, processed_bytes=processed_sec, total_bytes=total_duration_sec,
                    speed=speed_factor, eta=eta, engine="FFmpeg", user_id=user_id,
                    user_mention=user_mention
                )
                await _edit_status_message(user_id, text, progress_tracker)
            
    await process.wait()
    if process.returncode != 0:
        error_message = "\n".join(all_stderr_lines)
        logger.error(f"FFmpeg falló. Código: {process.returncode}\nError: {error_message}")
        raise Exception(f"El proceso de FFmpeg falló: {error_message[-500:]}")

async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    status_message = await bot.send_message(user_id, f"Iniciando: <code>{escape_html(task.get('original_filename') or task.get('url', 'Tarea'))}</code>", parse_mode=ParseMode.HTML)
    
    global progress_tracker
    progress_tracker[user_id] = ProgressContext(bot, status_message, task, asyncio.get_running_loop())
    
    files_to_clean = set()
    output_path = ""
    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("Tarea no encontrada.")
        progress_tracker[user_id].task = task
        config = task.get('processing_config', {})

        base_filename = os.path.join(DOWNLOAD_DIR, task_id)
        actual_download_path = ""

        if url := task.get('url'):
            format_id = config.get('download_format_id')
            if not format_id: raise Exception("La tarea no tiene 'download_format_id'.")
            
            actual_download_path = await asyncio.to_thread(
                downloader.download_from_url, url, base_filename, format_id, 
                progress_tracker=progress_tracker, user_id=user_id
            )

            if not actual_download_path:
                raise Exception("La descarga desde la URL falló o no se generó ningún archivo.")

        elif file_id := task.get('file_id'):
            actual_download_path = base_filename
            await bot.download_media(message=file_id, file_name=actual_download_path, progress=_progress_callback_pyrogram, progress_args=(user_id, "📥 Descargando..."))
        else:
            raise Exception("La tarea no tiene URL ni file_id.")
        
        files_to_clean.add(actual_download_path)
        logger.info(f"Descarga completada en: {actual_download_path}")
        
        file_type = task.get('file_type', 'document')
        await _edit_status_message(user_id, f"⚙️ Archivo ({file_type}) listo para procesar.", progress_tracker)
        
        final_filename_base = sanitize_filename(config.get('final_filename', task.get('original_filename', task_id)))
        final_ext = f".{config.get('audio_format', 'mp3')}" if file_type == 'audio' else ".mp4"
        if 'gif_options' in config: final_ext = ""

        final_filename = f"{final_filename_base}{final_ext}"
        output_path = os.path.join(OUTPUT_DIR, final_filename)
        if 'gif_options' in config:
            output_path = os.path.join(OUTPUT_DIR, final_filename_base)

        files_to_clean.add(output_path)
        
        watermark_path = None
        watermark_config = config.get('watermark')
        if watermark_config and watermark_config.get('type') == 'image' and watermark_config.get('file_id'):
            watermark_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_watermark.png")
            await bot.download_media(message=watermark_config['file_id'], file_name=watermark_path)
            if not os.path.exists(watermark_path):
                logger.warning("No se pudo descargar la imagen de la marca de agua.")
                watermark_path = None
            else:
                files_to_clean.add(watermark_path)
        
        thumb_path = os.path.join(DOWNLOAD_DIR, f"{task_id}.jpg")
        thumb_to_use = None
        if config.get('thumbnail_url') and await asyncio.to_thread(downloader.download_file, config['thumbnail_url'], thumb_path):
            thumb_to_use = thumb_path
            files_to_clean.add(thumb_path)

        commands = ffmpeg.build_ffmpeg_command(task, actual_download_path, output_path, thumb_to_use, watermark_path)
        sent_messages = []
        
        if commands:
            if 'gif_options' in config:
                palette_path = f"{output_path}.palette.png"
                files_to_clean.add(palette_path)

            for cmd in commands:
                await _run_ffmpeg_with_progress(user_id, cmd, actual_download_path)

            if 'split_criteria' in config:
                base_name, ext = os.path.splitext(output_path)
                search_pattern = f"{base_name}_part*{ext}"
                found_parts = sorted(glob.glob(search_pattern))

                if not found_parts:
                    raise Exception("La división falló, no se encontraron archivos de salida.")
                
                files_to_clean.update(found_parts)
                
                await _edit_status_message(user_id, f"⬆️ Subiendo {len(found_parts)} partes...", progress_tracker)
                
                media_group = []
                for i, part_path in enumerate(found_parts):
                    caption = f"✅ Parte {i+1}/{len(found_parts)}\n<code>{escape_html(os.path.basename(part_path))}</code>" if i == 0 else ""
                    media_group.append(InputMediaVideo(media=part_path, caption=caption, parse_mode=ParseMode.HTML))
                
                sent_messages = await bot.send_media_group(user_id, media=media_group)
            
            elif 'gif_options' in config:
                base, _ = os.path.splitext(output_path)
                gif_path = f"{base}.gif"
                if not os.path.exists(gif_path):
                    raise Exception("La creación del GIF falló, no se encontró el archivo de salida.")
                
                files_to_clean.add(gif_path)
                caption = f"✅ <code>{escape_html(os.path.basename(gif_path))}</code>"
                msg = await bot.send_animation(user_id, animation=gif_path, caption=caption, parse_mode=ParseMode.HTML)
                sent_messages.append(msg)
            
            else:
                processed_file = output_path
                final_filename_with_ext = os.path.basename(processed_file) if file_type != 'audio' else f"{final_filename_base}.{config.get('audio_format', 'mp3')}"
                caption = f"✅ <code>{escape_html(final_filename_with_ext)}</code>"
                if file_type == 'video':
                    msg = await bot.send_video(user_id, video=processed_file, thumb=thumb_to_use, caption=caption, parse_mode=ParseMode.HTML, progress=_progress_callback_pyrogram, progress_args=(user_id, "⬆️ Subiendo..."))
                    sent_messages.append(msg)
                elif file_type == 'audio':
                    msg = await bot.send_audio(user_id, audio=processed_file, thumb=thumb_to_use, caption=caption, parse_mode=ParseMode.HTML, progress=_progress_callback_pyrogram, progress_args=(user_id, "⬆️ Subiendo..."))
                    sent_messages.append(msg)
        
        if sent_messages and (lyrics := config.get('lyrics')):
            await bot.send_message(user_id, text=f"📜 <b>Letra</b>\n\n{escape_html(lyrics)}", reply_to_message_id=sent_messages[0].id, parse_mode=ParseMode.HTML)

        await db_instance.update_task(task_id, "status", "done")
        await status_message.delete()

    except AuthenticationError as e:
        logger.critical(f"Error de autenticación de YouTube: {e}")
        error_msg = "YouTube me está bloqueando. Necesito nuevas cookies para funcionar."
        await db_instance.update_task(task_id, "status", "error")
        await db_instance.update_task(task_id, "last_error", error_msg)
        await _edit_status_message(user_id, f"❌ <b>Error de Autenticación</b>\n\n<code>{escape_html(error_msg)}</code>", progress_tracker)
        if ADMIN_USER_ID:
            await bot.send_message(ADMIN_USER_ID, "⚠️ <b>¡Alerta de Mantenimiento, Jefe!</b>\n\nMis cookies de YouTube han expirado o han sido invalidadas.", parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        await db_instance.update_task(task_id, "status", "error")
        await db_instance.update_task(task_id, "last_error", str(e))
        await _edit_status_message(user_id, f"❌ <b>Error Grave</b>\n\n<code>{escape_html(str(e))}</code>", progress_tracker)
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
# --- END OF FILE src/core/worker.py ---