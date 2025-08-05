import logging
import time
import os
import asyncio
import json
import zipfile
import glob
import shlex
import subprocess
import re
from datetime import datetime
from bson.objectid import ObjectId
from telegram import Update, Bot
from telegram.ext import Application, CallbackContext
from telegram.error import BadRequest, NetworkError
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.id3 import APIC
from mutagen.easyid3 import EasyID3
import mutagen.flac

from src.db.mongo_manager import db_instance
from src.helpers.utils import create_progress_bar, format_bytes, format_time, escape_html, sanitize_filename
from src.core import ffmpeg, downloader

logger = logging.getLogger(__name__)

# --- Constantes y Configuración ---
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Gestión de Estado de Progreso ---
progress_data = {}

async def _edit_status_message(text: str):
    """Función interna segura para editar el mensaje de estado de la tarea."""
    if not (ctx := progress_data.get('context')) or not (msg := progress_data.get('message')): return
    try:
        current_time = time.time()
        if current_time - progress_data.get('last_edit_time', 0) > 1.5: # Limitar la tasa de edición a 1.5s
            await ctx.bot.edit_message_text(
                text, chat_id=msg.chat_id, message_id=msg.message_id, parse_mode='HTML'
            )
            progress_data['last_edit_time'] = current_time
    except BadRequest as e:
        if "Message is not modified" not in str(e): 
            logger.warning(f"No se pudo editar el mensaje de estado: {e}")
    except NetworkError as e:
        logger.warning(f"Error de red al editar mensaje: {e}")

# --- Callbacks de Progreso ---
async def progress_callback(current, total, operation: str):
    """Callback unificado para el progreso de descarga/subida de Telegram."""
    percentage = (current / total) * 100 if total > 0 else 0
    elapsed_time = time.time() - progress_data.get('start_time', 0)
    speed = current / elapsed_time if elapsed_time > 0 else 0
    eta = ((total - current) / speed) if speed > 0 and current > 0 else 0
    
    progress_bar = create_progress_bar(percentage)
    text = (f"<b>{operation}</b>\n\n<code>{progress_bar} {percentage:.1f}%</code>\n\n"
            f"<b>Progreso:</b> {format_bytes(current)} / {format_bytes(total)}\n"
            f"<b>Velocidad:</b> {format_bytes(speed)}/s\n"
            f"<b>ETA:</b> {format_time(eta)}")
    await _edit_status_message(text)

def ffmpeg_progress_callback(percentage, time_processed, duration):
    """Callback síncrono para el progreso de FFmpeg. Se comunica con el bucle de eventos."""
    progress_bar = create_progress_bar(percentage)
    text = (f"<b>⚙️ Procesando...</b>\n\n<code>{progress_bar} {percentage:.1f}%</code>\n\n"
            f"<b>Tiempo:</b> {format_time(time_processed)} / {format_time(duration)}")
    
    loop = progress_data.get('loop')
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(_edit_status_message(text), loop)

# --- Lógica Principal del Worker ---
async def process_task(task: dict, app: Application):
    """Procesa una única tarea de la cola."""
    global progress_data
    task_id, user_id = task['_id'], task['user_id']
    
    status_message = await app.bot.send_message(user_id, f"Iniciando: <code>{escape_html(task.get('original_filename') or task.get('url', 'Tarea desconocida'))}</code>", parse_mode='HTML')
    
    progress_data = {
        'start_time': time.time(),
        'last_edit_time': 0,
        'context': CallbackContext(app),
        'message': status_message,
        'loop': asyncio.get_running_loop()
    }
    
    files_to_clean = set()
    output_path = None
    
    try:
        config = task.get('processing_config', {})
        special_type = task.get('special_type')
        
        # --- CASO 1: TAREAS ESPECIALES (BULK) ---
        if special_type:
            output_path = await _handle_special_task(task, app, files_to_clean)
        
        # --- CASO 2: TAREA INDIVIDUAL ESTÁNDAR ---
        else:
            output_path = await _handle_standard_task(task, app, files_to_clean)

        # --- FASE 3: SUBIDA DEL RESULTADO ---
        if not output_path or not os.path.exists(output_path):
            raise Exception("No se generó el archivo de salida para la tarea.")

        await _edit_status_message("⬆️ Subiendo resultado...")
        progress_data['start_time'] = time.time()
        
        caption = config.get('final_caption', f"✅ Proceso completado, Jefe.")
        
        with open(output_path, 'rb') as f:
            file_ext = os.path.splitext(output_path)[1].lower()
            if file_ext == '.gif':
                await app.bot.send_animation(user_id, animation=f, filename=os.path.basename(output_path), caption=caption, write_timeout=180)
            elif task['file_type'] == 'video':
                await app.bot.send_video(user_id, video=f, filename=os.path.basename(output_path), caption=caption, write_timeout=180, read_timeout=180, pool_timeout=180, callback=lambda c, t: progress_callback(c, t, "⬆️ Subiendo..."))
            elif task['file_type'] == 'audio':
                await app.bot.send_audio(user_id, audio=f, filename=os.path.basename(output_path), caption=caption, write_timeout=180, read_timeout=180, pool_timeout=180, callback=lambda c, t: progress_callback(c, t, "⬆️ Subiendo..."))
            else:
                await app.bot.send_document(user_id, document=f, filename=os.path.basename(output_path), caption=caption, write_timeout=180, read_timeout=180, pool_timeout=180, callback=lambda c, t: progress_callback(c, t, "⬆️ Subiendo..."))

        db_instance.update_task(task_id, "status", "done")
        await status_message.delete()

    except Exception as e:
        logger.critical(f"Error al procesar la tarea {task_id}: {e}", exc_info=True)
        db_instance.update_task(task_id, "status", "error")
        await _edit_status_message(f"❌ Lo siento, Jefe. Ocurrió un error grave:\n<code>{escape_html(str(e))}</code>")
    finally:
        # --- FASE 4: LIMPIEZA ---
        for fpath in files_to_clean:
            try:
                if os.path.isdir(fpath):
                    import shutil
                    shutil.rmtree(fpath)
                elif os.path.exists(fpath):
                    os.remove(fpath)
            except Exception as e:
                logger.error(f"Error al limpiar archivo {fpath}: {e}")

async def _handle_standard_task(task, app, files_to_clean):
    """Maneja el flujo de una tarea estándar (descarga -> proceso -> postproceso)."""
    task_id = task['_id']
    config = task.get('processing_config', {})
    
    # --- FASE 1: DESCARGA ---
    download_path = os.path.join(DOWNLOAD_DIR, str(task_id))
    files_to_clean.add(download_path)
    
    progress_data['start_time'] = time.time()
    if url := task.get('url'):
        if not downloader.download_from_url(url, download_path, config.get('download_format_id', 'best')):
            raise Exception("La descarga desde la URL falló.")
    elif not os.path.exists(download_path):
        await _edit_status_message("⬇️ Descargando de Telegram...")
        file_to_download = await app.bot.get_file(task['file_id'])
        await file_to_download.download_to_drive(custom_path=download_path, callback=lambda c, t: progress_callback(c, t, "⬇️ Descargando..."))

    # --- FASE 2: PROCESAMIENTO ---
    await _edit_status_message("⚙️ Preparando para procesar...")
    progress_data['start_time'] = time.time()
    
    media_info = ffmpeg.get_media_info(download_path)
    duration = float(media_info.get('format', {}).get('duration', 0))
    
    base_name, _ = os.path.splitext(task.get('original_filename', 'archivo'))
    final_filename_base = sanitize_filename(task.get('final_filename', base_name))
    
    ext = ".mp4" # default
    if task['file_type'] == 'audio': ext = f".{config.get('audio_format', 'mp3')}"
    if 'gif_options' in config: ext = ".gif"
    
    final_filename = f"{final_filename_base}{ext}"
    output_path = os.path.join(OUTPUT_DIR, final_filename)
    files_to_clean.add(output_path)
    
    commands = ffmpeg.build_ffmpeg_command(task, download_path, output_path)
    
    for i, cmd in enumerate(commands):
        await _edit_status_message(f"⚙️ Procesando (Paso {i+1}/{len(commands)})...")
        process = subprocess.Popen(shlex.split(cmd), stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8', errors='ignore')
        
        for line in iter(process.stderr.readline, ''):
            match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
            if match and duration > 0:
                h, m, s, _ = map(int, match.groups())
                time_processed = h * 3600 + m * 60 + s
                percentage = (time_processed / duration) * 100
                ffmpeg_progress_callback(min(100, percentage), time_processed, duration)

        process.wait()
        if process.returncode != 0:
            raise Exception(f"El proceso FFmpeg falló en el comando {i+1}.")

    if task.get('file_type') == 'audio' and 'audio_tags' in config:
        await _apply_audio_tags(output_path, config['audio_tags'], app.bot, files_to_clean)
        
    return output_path

async def _handle_special_task(task: dict, app: Application, files_to_clean: set):
    """Maneja tareas especiales como compresión o unificación en lote."""
    special_type = task.get('special_type')
    task_id = task['_id']
    config = task.get('processing_config', {})
    source_task_ids = config.get('source_task_ids', [])
    source_tasks = db_instance.get_multiple_tasks(source_task_ids)
    
    output_path = None

    if special_type == "zip_bulk":
        output_path = os.path.join(OUTPUT_DIR, f"Bulk_Archive_{task_id}.zip")
        files_to_clean.add(output_path)
        
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for i, t_zip in enumerate(source_tasks):
                await _edit_status_message(f"📦 Comprimiendo {i+1}/{len(source_tasks)}: {escape_html(t_zip.get('original_filename'))}")
                file_path = os.path.join(DOWNLOAD_DIR, str(t_zip['_id']))
                if not os.path.exists(file_path):
                    await (await app.bot.get_file(t_zip['file_id'])).download_to_drive(file_path)
                files_to_clean.add(file_path)
                zipf.write(file_path, t_zip.get('final_filename') or t_zip.get('original_filename'))
    
    elif special_type == "unify_videos":
        file_list_path = os.path.join(DOWNLOAD_DIR, f"filelist_{task_id}.txt")
        files_to_clean.add(file_list_path)
        
        with open(file_list_path, 'w', encoding='utf-8') as f:
            for i, t_unify in enumerate(source_tasks):
                await _edit_status_message(f"📥 Preparando video {i+1}/{len(source_tasks)}...")
                file_path = os.path.join(DOWNLOAD_DIR, str(t_unify['_id']))
                if not os.path.exists(file_path):
                    await (await app.bot.get_file(t_unify['file_id'])).download_to_drive(file_path)
                files_to_clean.add(file_path)
                f.write(f"file '{os.path.abspath(file_path)}'\n")
        
        output_path = os.path.join(OUTPUT_DIR, f"Unified_Video_{task_id}.mp4")
        files_to_clean.add(output_path)
        command = ffmpeg.build_unify_command(file_list_path, output_path)
        
        await _edit_status_message("🔄 Unificando videos...")
        result = subprocess.run(shlex.split(command), capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Fallo al unificar videos: {result.stderr}")
            raise Exception("Fallo al unificar videos. Asegúrese de que tengan codecs y resoluciones similares.")
    
    return output_path

async def _apply_audio_tags(output_path: str, audio_tags: dict, bot: Bot, files_to_clean: set):
    """Aplica metadatos y carátula a un archivo de audio usando mutagen."""
    await _edit_status_message("🖼️ Aplicando metadatos...")
    try:
        audio_file = None
        ext = os.path.splitext(output_path)[1].lower()

        if ext == '.mp3':
            audio_file = MP3(output_path, ID3=EasyID3)
        elif ext == '.flac':
            audio_file = FLAC(output_path)
        
        if not audio_file: return
        
        # Aplicar tags de texto simples
        if 'title' in audio_tags: audio_file['title'] = audio_tags['title']
        if 'artist' in audio_tags: audio_file['artist'] = audio_tags['artist']
        if 'album' in audio_tags: audio_file['album'] = audio_tags['album']
        audio_file.save() # Guardar tags simples

        # Aplicar carátula (requiere recargar el archivo sin EasyID3 para MP3)
        if 'cover_file_id' in audio_tags:
            cover_path = os.path.join(DOWNLOAD_DIR, audio_tags['cover_file_id'])
            files_to_clean.add(cover_path)
            if not os.path.exists(cover_path):
                cover_file = await bot.get_file(audio_tags['cover_file_id'])
                await cover_file.download_to_drive(cover_path)
            
            with open(cover_path, 'rb') as art:
                cover_data = art.read()
                if ext == '.mp3':
                    audio_file_complex = MP3(output_path)
                    audio_file_complex.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
                    audio_file_complex.save()
                elif ext == '.flac':
                    audio_file_complex = FLAC(output_path)
                    pic = mutagen.flac.Picture()
                    pic.type = 3
                    pic.mime = 'image/jpeg'
                    pic.data = cover_data
                    audio_file_complex.add_picture(pic)
                    audio_file_complex.save()
    except Exception as e:
        logger.error(f"Fallo al aplicar tags con mutagen: {e}")
        await _edit_status_message("⚠️ No se pudieron aplicar los metadatos.")

def worker_thread_runner(application: Application):
    """El bucle principal del worker que se ejecuta en un hilo separado."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("[WORKER] Bucle del worker iniciado.")
    
    while True:
        try:
            task = db_instance.tasks.find_one_and_update(
                {"status": "queued"},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}}
            )
            if task:
                logger.info(f"[WORKER] Procesando tarea {task['_id']} para usuario {task['user_id']}")
                loop.run_until_complete(process_task(task, application))
            else:
                time.sleep(5)
        except Exception as e:
            logger.critical(f"[WORKER] Bucle del worker falló críticamente: {e}", exc_info=True)
            time.sleep(30)