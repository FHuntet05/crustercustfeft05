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

from src.db.mongo_manager import db_instance
from src.helpers.utils import create_progress_bar, format_bytes, format_time, escape_html, sanitize_filename
from src.core import ffmpeg, downloader
from src.core.userbot_manager import userbot_instance
import mutagen

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

BOT_API_DOWNLOAD_LIMIT = 20 * 1024 * 1024
progress_data = {}
bot_instance = None

# ... (Las funciones _edit_status_message, progress_callback, ffmpeg_progress_callback, _run_subprocess_with_progress, _apply_audio_tags, y _upload_file no cambian)
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
    elapsed_time = time.time() - progress_data.get('start_time', 0)
    speed = current / elapsed_time if elapsed_time > 0 else 0
    eta = ((total - current) / speed) if speed > 0 and current > 0 else 0
    text = (f"<b>{operation}</b>\n\n<code>{create_progress_bar(percentage)} {percentage:.1f}%</code>\n\n<b>Progreso:</b> {format_bytes(current)} / {format_bytes(total)}\n<b>Velocidad:</b> {format_bytes(speed)}/s\n<b>ETA:</b> {format_time(eta)}")
    await _edit_status_message(text)

def ffmpeg_progress_callback(percentage, time_processed, duration):
    loop = progress_data.get('loop')
    if loop and loop.is_running():
        text = (f"<b>⚙️ Procesando...</b>\n\n<code>{create_progress_bar(percentage)} {percentage:.1f}%</code>\n\n<b>Tiempo:</b> {format_time(time_processed)} / {format_time(duration)}")
        asyncio.run_coroutine_threadsafe(_edit_status_message(text), loop)

async def _run_subprocess_with_progress(cmd, duration):
    process = subprocess.Popen(shlex.split(cmd), stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8', errors='ignore')
    for line in iter(process.stderr.readline, ''):
        match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
        if match and duration > 0:
            h, m, s, _ = map(int, match.groups())
            time_processed = h * 3600 + m * 60 + s
            percentage = (time_processed / duration) * 100
            ffmpeg_progress_callback(min(100, percentage), time_processed, duration)
    process.wait()
    if process.returncode != 0: raise Exception(f"El proceso FFmpeg falló. Código de salida: {process.returncode}")

async def _apply_audio_tags(output_path, audio_tags, files_to_clean):
    await _edit_status_message("🖼️ Aplicando metadatos...")
    try:
        ext = os.path.splitext(output_path)[1].lower()
        if ext == '.mp3': audio_file = mutagen.mp3.MP3(output_path, ID3=mutagen.easyid3.EasyID3)
        elif ext == '.flac': audio_file = mutagen.flac.FLAC(output_path)
        else: return
        if 'title' in audio_tags: audio_file['title'] = audio_tags['title']
        if 'artist' in audio_tags: audio_file['artist'] = audio_tags['artist']
        if 'album' in audio_tags: audio_file['album'] = audio_tags['album']
        audio_file.save()
        if 'cover_file_id' in audio_tags:
            cover_path = os.path.join(DOWNLOAD_DIR, audio_tags['cover_file_id'])
            files_to_clean.add(cover_path)
            if not os.path.exists(cover_path):
                await _download_file_helper(audio_tags['cover_file_id'], None, cover_path)
            with open(cover_path, 'rb') as art:
                cover_data = art.read()
                if ext == '.mp3':
                    audio_file_complex = mutagen.mp3.MP3(output_path)
                    audio_file_complex.tags.add(mutagen.id3.APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
                    audio_file_complex.save(v2_version=3)
                elif ext == '.flac':
                    audio_file_complex = mutagen.flac.FLAC(output_path)
                    pic = mutagen.flac.Picture(); pic.type = 3; pic.mime = 'image/jpeg'; pic.data = cover_data
                    audio_file_complex.add_picture(pic); audio_file_complex.save()
    except Exception as e:
        logger.error(f"Fallo al aplicar tags con mutagen: {e}")
        await _edit_status_message("⚠️ No se pudieron aplicar los metadatos.")

async def _upload_file(user_id, file_handle, filename, file_type, caption, reply_markup):
    common_args = {'caption': caption, 'reply_markup': reply_markup, 'write_timeout': 300, 'connect_timeout': 30, 'read_timeout': 300, 'pool_timeout': 300, 'callback': lambda c, t: progress_callback(c, t, "⬆️ Subiendo...")}
    if os.path.splitext(filename)[1].lower() == '.gif': await bot_instance.send_animation(user_id, animation=file_handle, filename=filename, **common_args)
    elif file_type == 'video': await bot_instance.send_video(user_id, video=file_handle, filename=filename, **common_args)
    elif file_type == 'audio': await bot_instance.send_audio(user_id, audio=file_handle, filename=filename, **common_args)
    else: await bot_instance.send_document(user_id, document=file_handle, filename=filename, **common_args)

async def _download_file_helper(file_id: str, file_size: int | None, download_path: str):
    """Función auxiliar para descargar un archivo, priorizando el Userbot."""
    if os.path.exists(download_path):
        return
    
    if userbot_instance.is_active():
        await _edit_status_message("⬇️ Descargando (Userbot)...")
        await userbot_instance.download_file(
            file_id, download_path, lambda c, t: progress_callback(c, t, "⬇️ Descargando...")
        )
    elif file_size and file_size <= BOT_API_DOWNLOAD_LIMIT:
        await _edit_status_message("⬇️ Descargando (Bot API)...")
        file = await bot_instance.get_file(file_id)
        await file.download_to_drive(download_path, callback=lambda c, t: progress_callback(c, t, "⬇️ Descargando..."))
    else:
        raise Exception("Archivo excede el límite de 20MB y el Userbot no está configurado/activo.")

async def _handle_standard_task(task, files_to_clean):
    task_id = task['_id']; config = task.get('processing_config', {})
    download_path = os.path.join(DOWNLOAD_DIR, str(task_id)); files_to_clean.add(download_path)
    progress_data['start_time'] = time.time()
    
    if url := task.get('url'):
        if not downloader.download_from_url(url, download_path, config.get('download_format_id', 'best')): 
            raise Exception("La descarga desde la URL falló.")
    elif file_id := task.get('file_id'):
        await _download_file_helper(file_id, task.get('file_size'), download_path)
            
    if audio_file_id := config.get('add_audio_file_id'):
        path = os.path.join(DOWNLOAD_DIR, audio_file_id); files_to_clean.add(path)
        await _download_file_helper(audio_file_id, None, path); config['add_audio_file_path'] = path
    if subs_file_id := config.get('add_subtitle_file_id'):
        path = os.path.join(DOWNLOAD_DIR, subs_file_id); files_to_clean.add(path)
        await _download_file_helper(subs_file_id, None, path); config['add_subtitle_file_path'] = path
    
    await _edit_status_message("⚙️ Preparando..."); progress_data['start_time'] = time.time()
    media_info = ffmpeg.get_media_info(download_path); duration = float(media_info.get('format', {}).get('duration', 0))
    base_name, _ = os.path.splitext(task.get('original_filename', 'archivo')); final_filename_base = sanitize_filename(config.get('final_filename', base_name))
    ext = ".mp4"; file_type = task.get('file_type')
    if file_type == 'audio': ext = f".{config.get('audio_format', 'mp3')}"
    elif 'gif_options' in config: ext = ".gif"
    elif 'subtitle_convert_to' in config: ext = f".{config['subtitle_convert_to']}"
    elif file_type == 'document': ext = os.path.splitext(task.get('original_filename', ''))[1]
    final_filename = f"{final_filename_base}{ext}"; output_path = os.path.join(OUTPUT_DIR, final_filename); files_to_clean.add(output_path)
    if 'subtitle_convert_to' in config: commands = [ffmpeg.build_subtitle_convert_command(download_path, output_path)]
    else: commands = ffmpeg.build_ffmpeg_command(task, download_path, output_path)
    for i, cmd in enumerate(commands):
        if not cmd: continue
        await _edit_status_message(f"⚙️ Procesando (Paso {i+1}/{len(commands)})..."); await _run_subprocess_with_progress(cmd, duration)
    if file_type == 'audio' and 'audio_tags' in config: await _apply_audio_tags(output_path, config['audio_tags'], files_to_clean)
    return output_path

# ... (_handle_special_task, process_task, y worker_thread_runner no cambian significativamente en su lógica principal, pero se benefician de la descarga helper)

async def _handle_special_task(task, files_to_clean):
    special_type = task.get('special_type'); task_id = task['_id']; config = task.get('processing_config', {}); source_task_ids = config.get('source_task_ids', []); source_tasks = db_instance.get_multiple_tasks(source_task_ids)
    if special_type == "zip_bulk":
        output_path = os.path.join(OUTPUT_DIR, f"Bulk_Archive_{task_id}.zip"); files_to_clean.add(output_path)
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for i, t_zip in enumerate(source_tasks):
                await _edit_status_message(f"📦 Comprimiendo {i+1}/{len(source_tasks)}...");
                file_path = os.path.join(DOWNLOAD_DIR, str(t_zip['_id']))
                try:
                    await _download_file_helper(t_zip['file_id'], t_zip.get('file_size'), file_path)
                    files_to_clean.add(file_path)
                    zipf.write(file_path, config.get('final_filename') or t_zip.get('original_filename'))
                except Exception as e:
                    logger.warning(f"Omitiendo archivo {t_zip['_id']} en ZIP. Causa: {e}")
                    continue
        return output_path
    elif special_type == "unify_videos":
        file_list_path = os.path.join(DOWNLOAD_DIR, f"filelist_{task_id}.txt"); files_to_clean.add(file_list_path)
        with open(file_list_path, 'w', encoding='utf-8') as f:
            for i, t_unify in enumerate(source_tasks):
                await _edit_status_message(f"📥 Preparando video {i+1}/{len(source_tasks)}...");
                file_path = os.path.join(DOWNLOAD_DIR, str(t_unify['_id']))
                try:
                    await _download_file_helper(t_unify['file_id'], t_unify.get('file_size'), file_path)
                    files_to_clean.add(file_path)
                    f.write(f"file '{os.path.abspath(file_path)}'\n")
                except Exception as e:
                    logger.warning(f"Omitiendo video {t_unify['_id']} en unificación. Causa: {e}")
                    continue
        output_path = os.path.join(OUTPUT_DIR, f"Unified_Video_{task_id}.mp4"); files_to_clean.add(output_path)
        command = ffmpeg.build_unify_command(file_list_path, output_path); await _edit_status_message("🔄 Unificando videos...")
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if result.returncode != 0: logger.error(f"Fallo al unificar: {result.stderr}"); raise Exception("Fallo al unificar videos.")
        return output_path

async def process_task(task: dict):
    global progress_data; task_id, user_id = task['_id'], task['user_id']
    status_message = await bot_instance.send_message(user_id, f"Iniciando: <code>{escape_html(task.get('original_filename') or task.get('url', 'Tarea'))}</code>", parse_mode='HTML')
    progress_data = {'start_time': time.time(), 'last_edit_time': 0, 'bot': bot_instance, 'message': status_message, 'loop': asyncio.get_running_loop()}
    files_to_clean = set(); output_path = None
    try:
        if special_type := task.get('special_type'): output_path = await _handle_special_task(task, files_to_clean)
        else: output_path = await _handle_standard_task(task, files_to_clean)
        if not output_path or not os.path.exists(output_path): raise Exception("No se generó el archivo de salida.")
        await _edit_status_message("⬆️ Subiendo resultado..."); progress_data['start_time'] = time.time()
        config = task.get('processing_config', {}); caption = config.get('final_caption', f"✅ Proceso completado, Jefe.")
        reply_markup = InlineKeyboardMarkup.from_dict(config['reply_markup']) if 'reply_markup' in config else None
        with open(output_path, 'rb') as f: await _upload_file(user_id, f, os.path.basename(output_path), task.get('file_type'), caption, reply_markup)
        db_instance.update_task(task_id, "status", "done"); await status_message.delete()
    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        db_instance.update_task(task_id, "status", "error"); await _edit_status_message(f"❌ Error grave:\n<code>{escape_html(str(e))}</code>")
    finally:
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath): import shutil; shutil.rmtree(fpath)
                elif os.path.exists(fpath): os.remove(fpath)
            except Exception as e: logger.error(f"Error al limpiar {fpath}: {e}")

def worker_thread_runner():
    global bot_instance; token = os.getenv("TELEGRAM_TOKEN")
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    bot_instance = Bot(token)
    logger.info("[WORKER] Bucle del worker iniciado.")
    while True:
        try:
            task = db_instance.tasks.find_one_and_update({"status": "queued"}, {"$set": {"status": "processing", "processed_at": datetime.utcnow()}})
            if task: logger.info(f"[WORKER] Procesando tarea {task['_id']}"); loop.run_until_complete(process_task(task))
            else: time.sleep(5)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle falló críticamente: {e}", exc_info=True); time.sleep(30)