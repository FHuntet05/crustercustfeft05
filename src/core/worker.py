import logging
import time
import os
import asyncio
import re
from datetime import datetime
from pyrogram.enums import ParseMode  # --- IMPORTACIÓN CRÍTICA ---

from src.db.mongo_manager import db_instance
from src.helpers.utils import format_status_message, sanitize_filename, escape_html
from src.core import ffmpeg, downloader
from src.core.ffmpeg import get_media_info

logger = logging.getLogger(__name__)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

class ProgressContext:
    def __init__(self, bot, message, task):
        self.bot = bot
        self.message = message
        self.task = task
        self.start_time = time.time()
        self.last_edit_time = 0
        self.last_update_text = ""

progress_tracker = {}

async def _edit_status_message(user_id: int, text: str):
    if user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    if text == ctx.last_update_text: return
    ctx.last_update_text = text
    
    current_time = time.time()
    if current_time - ctx.last_edit_time > 1.5:
        try:
            await ctx.bot.edit_message_text(
                chat_id=ctx.message.chat.id, 
                message_id=ctx.message.id, 
                text=text, 
                parse_mode=ParseMode.HTML  # --- CORRECCIÓN ---
            )
            ctx.last_edit_time = current_time
        except Exception:
            pass

async def progress_callback(current, total, user_id, operation):
    if user_id not in progress_tracker: return
    ctx = progress_tracker[user_id]
    
    percentage = (current / total) * 100 if total > 0 else 0
    elapsed = time.time() - ctx.start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    
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
    await _edit_status_message(user_id, text)

async def _run_ffmpeg_with_progress(user_id: int, cmd: str, input_path: str):
    duration_info = get_media_info(input_path)
    total_duration_sec = float(duration_info.get('format', {}).get('duration', 0))
    if total_duration_sec == 0:
        logger.warning("No se pudo obtener la duración, el progreso de FFmpeg no funcionará.")

    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    
    while True:
        line = await process.stderr.readline()
        if not line: break
        
        line_str = line.decode('utf-8', 'ignore').strip()
        match = time_pattern.search(line_str)
        if match and total_duration_sec > 0:
            h, m, s, ms = map(int, match.groups())
            processed_sec = h * 3600 + m * 60 + s + ms / 100
            await progress_callback(processed_sec, total_duration_sec, user_id, "⚙️ Codificando...")
    
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error_message = stderr.decode('utf-8', 'ignore')
        logger.error(f"FFmpeg falló. Código: {process.returncode}\nError: {error_message}")
        raise Exception(f"El proceso de FFmpeg falló: {error_message[-500:]}")

async def process_task(bot, task: dict):
    task_id, user_id = str(task['_id']), task['user_id']
    # --- CORRECCIÓN ---
    status_message = await bot.send_message(user_id, f"Iniciando: <code>{task.get('original_filename') or task.get('url', 'Tarea')}</code>", parse_mode=ParseMode.HTML)
    
    global progress_tracker
    progress_tracker[user_id] = ProgressContext(bot, status_message, task)
    
    files_to_clean = set()
    output_path = ""
    try:
        task = await db_instance.get_task(task_id)
        if not task: raise Exception("La tarea fue eliminada o no se encontró en la DB.")
        progress_tracker[user_id].task = task

        download_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{sanitize_filename(task.get('original_filename', 'file'))}")
        files_to_clean.add(download_path)

        if url := task.get('url'):
            logger.info(f"Iniciando descarga de URL para la tarea {task_id}")
            format_id = task.get('processing_config', {}).get('download_format_id', 'best')
            if not downloader.download_from_url(url, download_path, format_id, lambda d: None):
                raise Exception("La descarga desde la URL falló.")
        elif file_id := task.get('file_id'):
            logger.info(f"Iniciando descarga de Telegram para la tarea {task_id}")
            await bot.download_media(
                message=file_id,
                file_name=download_path,
                progress=progress_callback,
                progress_args=(user_id, "📥 Descargando...")
            )
        else:
            raise Exception("La tarea no tiene URL ni file_id para descargar.")
        
        logger.info(f"Descarga de la tarea {task_id} completada en: {download_path}")

        await _edit_status_message(user_id, "⚙️ Preparando para procesar...")
        config = task.get('processing_config', {})
        base_name, _ = os.path.splitext(task.get('original_filename', 'archivo'))
        final_filename_base = sanitize_filename(config.get('final_filename', base_name))
        
        ext_map = {'audio': f".{config.get('audio_format', 'mp3')}", 'video': ".mp4", 'document': os.path.splitext(task.get('original_filename', ''))[1]}
        ext = ".gif" if 'gif_options' in config else ext_map.get(task.get('file_type'), '.dat')
        final_filename = f"{final_filename_base}{ext}"
        
        output_path = os.path.join(OUTPUT_DIR, final_filename)
        files_to_clean.add(output_path)
        
        commands = ffmpeg.build_ffmpeg_command(task, download_path, output_path)
        for i, cmd in enumerate(commands):
            if not cmd: continue
            await _run_ffmpeg_with_progress(user_id, cmd, download_path)
        
        caption = config.get('final_caption', f"✅ Proceso completado.")
        file_type = task.get('file_type')
        
        if file_type == 'video':
            await bot.send_video(user_id, video=output_path, caption=caption, progress=progress_callback, progress_args=(user_id, "⬆️ Subiendo..."))
        elif file_type == 'audio':
            await bot.send_audio(user_id, audio=output_path, caption=caption, progress=progress_callback, progress_args=(user_id, "⬆️ Subiendo..."))
        else:
            await bot.send_document(user_id, document=output_path, caption=caption, progress=progress_callback, progress_args=(user_id, "⬆️ Subiendo..."))
        
        await db_instance.update_task(task_id, "status", "done")
        await status_message.delete()

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        await db_instance.update_task(task_id, "status", "error")
        await db_instance.update_task(task_id, "last_error", str(e))
        # --- CORRECCIÓN ---
        await _edit_status_message(user_id, f"❌ <b>Error Grave</b>\n\n<code>{escape_html(str(e))}</code>")
    finally:
        if user_id in progress_tracker:
            del progress_tracker[user_id]
        for fpath in files_to_clean:
            if os.path.exists(fpath): 
                try: 
                    os.remove(fpath)
                    logger.info(f"Archivo temporal limpiado: {fpath}")
                except Exception as e: 
                    logger.error(f"No se pudo limpiar el archivo {fpath}: {e}")

async def worker_loop(bot_instance):
    logger.info("[WORKER] Bucle del worker iniciado.")
    while True:
        try:
            task = await db_instance.tasks.find_one_and_update(
                {"status": "queued"},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}}
            )
            
            if task:
                user_id = task['user_id']
                if user_id in progress_tracker:
                    logger.warning(f"El usuario {user_id} ya tiene una tarea en proceso. Re-encolando.")
                    await db_instance.update_task(str(task['_id']), "status", "queued")
                    await asyncio.sleep(10)
                    continue
                
                logger.info(f"Iniciando procesamiento de la tarea {task['_id']} para el usuario {user_id}")
                task_obj = asyncio.create_task(process_task(bot_instance, task))
                task_obj.add_done_callback(
                    lambda t: logger.error(f"La tarea falló con una excepción no recuperada: {t.exception()}", exc_info=t.exception()) if t.exception() else None
                )
            else:
                await asyncio.sleep(5)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            await asyncio.sleep(30)