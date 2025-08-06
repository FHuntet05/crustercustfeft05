import logging
import time
import os
import asyncio
import zipfile
import shlex
import subprocess
import re
from datetime import datetime
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError
import mutagen
from src.db.mongo_manager import db_instance
from src.helpers.utils import create_progress_bar, format_bytes, format_time, escape_html, sanitize_filename
from src.core import ffmpeg, downloader
from src.core.userbot_manager import userbot_instance

logger = logging.getLogger(__name__)
DOWNLOAD_DIR, OUTPUT_DIR = os.path.join(os.getcwd(), "downloads"), os.path.join(os.getcwd(), "outputs")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
BOT_API_DOWNLOAD_LIMIT, BOT_API_UPLOAD_LIMIT = 20 * 1024 * 1024, 50 * 1024 * 1024
progress_data, bot_instance = {}, None

async def _edit_status_message(text: str):
    if not (bot := progress_data.get('bot')) or not (msg := progress_data.get('message')): return
    try:
        current_time = time.time()
        if current_time - progress_data.get('last_edit_time', 0) > 1.5:
            await bot.edit_message_text(text, chat_id=msg.chat_id, message_id=msg.message_id, parse_mode='HTML')
            progress_data['last_edit_time'] = current_time
    except (BadRequest, NetworkError) as e:
        if "Message is not modified" not in str(e): logger.warning(f"No se pudo editar msg: {e}")

async def progress_callback(current, total, operation: str):
    percentage = (current / total) * 100 if total > 0 else 0
    elapsed, speed = time.time() - progress_data.get('start_time', 0), current / (time.time() - progress_data.get('start_time', 0)) if time.time() - progress_data.get('start_time', 0) > 0 else 0
    eta = ((total - current) / speed) if speed > 0 else 0
    text = (f"<b>{operation}</b>\n\n<code>{create_progress_bar(percentage)} {percentage:.1f}%</code>\n\n"
            f"<b>Progreso:</b> {format_bytes(current)} / {format_bytes(total)}\n"
            f"<b>Velocidad:</b> {format_bytes(speed)}/s\n<b>ETA:</b> {format_time(eta)}")
    await _edit_status_message(text)

async def _run_subprocess(cmd):
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error_message = stderr.decode('utf-8', 'ignore')
        logger.error(f"El proceso falló. Código: {process.returncode}\nError: {error_message}")
        raise Exception(f"El proceso de línea de comandos falló: {error_message}")

async def _upload_file(user_id, output_path, file_type, caption, reply_markup):
    filename = os.path.basename(output_path)
    progress = lambda c, t: progress_callback(c, t, "⬆️ Subiendo...")
    if userbot_instance.is_active():
        await _edit_status_message("⬆️ Subiendo (Userbot)...")
        if file_type == 'video': await userbot_instance.client.send_video(user_id, video=output_path, caption=caption, progress=progress)
        elif file_type == 'audio': await userbot_instance.client.send_audio(user_id, audio=output_path, caption=caption, progress=progress)
        else: await userbot_instance.client.send_document(user_id, document=output_path, caption=caption, progress=progress)
    else:
        if os.path.getsize(output_path) > BOT_API_UPLOAD_LIMIT:
            raise Exception(f"Archivo de salida ({format_bytes(os.path.getsize(output_path))}) excede el límite de 50MB de la API de Bots.")
        await _edit_status_message("⬆️ Subiendo (Bot API)...")
        with open(output_path, 'rb') as f:
            if file_type == 'video': await bot_instance.send_video(user_id, video=f, filename=filename, caption=caption, reply_markup=reply_markup, callback=progress, write_timeout=600)
            elif file_type == 'audio': await bot_instance.send_audio(user_id, audio=f, filename=filename, caption=caption, reply_markup=reply_markup, callback=progress, write_timeout=600)
            else: await bot_instance.send_document(user_id, document=f, filename=filename, caption=caption, reply_markup=reply_markup, callback=progress, write_timeout=600)

async def _download_file_helper(task: dict, download_path: str):
    if os.path.exists(download_path): return
    progress = lambda c, t: progress_callback(c, t, "⬇️ Descargando...")
    if userbot_instance.is_active() and task.get('chat_id') and task.get('message_id'):
        await _edit_status_message("⬇️ Descargando (Userbot)...")
        await userbot_instance.download_file(task['chat_id'], task['message_id'], download_path, progress)
    elif task.get('file_id') and task.get('file_size', 0) <= BOT_API_DOWNLOAD_LIMIT:
        await _edit_status_message("⬇️ Descargando (Bot API)...")
        await (await bot_instance.get_file(task['file_id'])).download_to_drive(download_path, callback=progress)
    else:
        raise Exception("Archivo requiere Userbot para descargar (grande o contexto faltante).")

async def process_task(task: dict):
    task_id, user_id = task['_id'], task['user_id']
    status_message = await bot_instance.send_message(user_id, f"Iniciando: <code>{escape_html(task.get('original_filename') or task.get('url', 'Tarea'))}</code>", parse_mode='HTML')
    global progress_data; progress_data = {'start_time': time.time(), 'last_edit_time': 0, 'bot': bot_instance, 'message': status_message, 'loop': asyncio.get_running_loop()}
    files_to_clean = set()
    try:
        download_path = os.path.join(DOWNLOAD_DIR, str(task_id)); files_to_clean.add(download_path)
        progress_data['start_time'] = time.time()
        if url := task.get('url'):
            if not downloader.download_from_url(url, download_path, task.get('processing_config', {}).get('download_format_id', 'best')):
                raise Exception("La descarga desde la URL falló.")
        elif file_id := task.get('file_id'):
            await _download_file_helper(task, download_path)
        
        await _edit_status_message("⚙️ Preparando..."); progress_data['start_time'] = time.time()
        config = task.get('processing_config', {})
        base_name, _ = os.path.splitext(task.get('original_filename', 'archivo')); final_filename_base = sanitize_filename(config.get('final_filename', base_name))
        ext_map = {'audio': f".{config.get('audio_format', 'mp3')}", 'video': ".mp4", 'document': os.path.splitext(task.get('original_filename', ''))[1]}
        ext = ".gif" if 'gif_options' in config else ext_map.get(task.get('file_type'), '.dat')
        final_filename = f"{final_filename_base}{ext}"; output_path = os.path.join(OUTPUT_DIR, final_filename); files_to_clean.add(output_path)
        commands = ffmpeg.build_ffmpeg_command(task, download_path, output_path)
        for i, cmd in enumerate(commands):
            if not cmd: continue
            await _edit_status_message(f"⚙️ Procesando (Paso {i+1}/{len(commands)})..."); await _run_subprocess(cmd)
        
        await _upload_file(user_id, output_path, task.get('file_type'), config.get('final_caption', f"✅ Proceso completado."), None)
        db_instance.update_task(task_id, "status", "done"); await status_message.delete()
    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=False)
        db_instance.update_task(task_id, "status", "error"); await _edit_status_message(f"❌ Error grave:\n<code>{escape_html(str(e))}</code>")
    finally:
        for fpath in files_to_clean:
            if os.path.exists(fpath): 
                try: os.remove(fpath)
                except: pass

def worker_thread_runner():
    global bot_instance; token = os.getenv("TELEGRAM_TOKEN")
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    bot_instance = Bot(token)
    logger.info("[WORKER] Bucle del worker iniciado.")
    while True:
        try:
            task = db_instance.tasks.find_one_and_update({"status": "queued"}, {"$set": {"status": "processing", "processed_at": datetime.utcnow()}})
            if task: loop.run_until_complete(process_task(task))
            else: time.sleep(5)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle falló críticamente: {e}", exc_info=True); time.sleep(30)