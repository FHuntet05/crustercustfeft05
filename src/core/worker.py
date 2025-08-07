# src/core/worker.py

import asyncio
import logging
import os
from pyrogram.errors import FloodWait

from src.db.mongo_manager import db
from src.core import downloader, ffmpeg
from src.helpers.utils import format_bytes, format_time

# Configuración del logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Worker:
    def __init__(self, app):
        self.app = app
        self.user_locks = {}

    async def start(self):
        """
        Inicia el bucle principal del worker que busca tareas encoladas.
        """
        logger.info("Worker iniciado. Escuchando nuevas tareas...")
        while True:
            tasks_by_user = await db.get_queued_tasks_by_user()
            if tasks_by_user:
                for user_id, user_tasks in tasks_by_user.items():
                    if user_id not in self.user_locks:
                        self.user_locks[user_id] = asyncio.Lock()
                    
                    if not self.user_locks[user_id].locked():
                        task = user_tasks[0] # Procesar la tarea más antigua del usuario
                        asyncio.create_task(self.process_task_wrapper(task, user_id))
            
            await asyncio.sleep(5) # Esperar 5 segundos antes de volver a consultar la DB

    async def process_task_wrapper(self, task, user_id):
        """
        Wrapper para manejar el bloqueo por usuario y los errores irrecuperables.
        """
        async with self.user_locks[user_id]:
            try:
                await self.process_task(task)
            except Exception as e:
                task_id_str = str(task['_id'])
                logger.error(f"Error irrecuperable procesando la tarea {task_id_str}: {e}", exc_info=True)
                await db.update_task(task_id_str, {"status": "failed", "error_message": str(e)})
                try:
                    await self.app.send_message(
                        chat_id=task['chat_id'],
                        text=f"❌ La tarea '{task.get('title', 'Desconocido')}' ha fallado.\nMotivo: {e}"
                    )
                except Exception as send_e:
                    logger.error(f"No se pudo notificar al usuario {user_id} sobre la tarea fallida: {send_e}")

    async def process_task(self, task):
        """
        Lógica principal para procesar una única tarea.
        """
        task_id = str(task['_id'])
        chat_id = task['chat_id']
        title = task.get('title', 'media')
        
        logger.info(f"Comenzando a procesar la tarea {task_id} para el usuario {task['user_id']}")
        await db.update_task(task_id, {"status": "processing"})

        progress_message = await self.app.send_message(chat_id, f"Iniciando proceso para '{title}'...")
        
        # --- Lógica de Progreso de Descarga ---
        last_progress_update = {"time": 0}
        def progress_hook(d):
            if d['status'] == 'downloading':
                now = asyncio.get_event_loop().time()
                # Limitar actualizaciones a una por segundo para no exceder los límites de Telegram
                if now - last_progress_update["time"] < 1.5:
                    return
                last_progress_update["time"] = now

                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                if total_bytes > 0:
                    downloaded_bytes = d.get('downloaded_bytes', 0)
                    speed = d.get('speed', 0)
                    eta = d.get('eta', 0)
                    percentage = (downloaded_bytes / total_bytes) * 100
                    
                    asyncio.create_task(progress_message.edit_text(
                        f"📥 **Descargando '{title}'...**\n"
                        f"Progreso: {percentage:.1f}%\n"
                        f"({format_bytes(downloaded_bytes)} / {format_bytes(total_bytes)})\n"
                        f"Velocidad: {format_bytes(speed)}/s\n"
                        f"ETA: {format_time(eta)}"
                    ))

        # --- Flujo de Trabajo: Descarga -> Procesa -> Sube -> Limpia ---
        download_dir = f"downloads/{task_id}/"
        os.makedirs(download_dir, exist_ok=True)
        temp_files_to_clean = []
        
        try:
            # 1. DESCAGAR COMPONENTES
            await progress_message.edit_text(f"📥 Preparando la descarga de '{title}'...")
            original_media_path = await downloader.download_media(task['url'], task['selected_format_id'], os.path.join(download_dir, title), progress_hook)
            if not original_media_path:
                raise RuntimeError("La descarga del archivo principal falló.")
            temp_files_to_clean.append(download_dir)
            
            thumbnail_path = None
            if task.get("embed_thumbnail") and task.get("thumbnail_url"):
                await progress_message.edit_text(f"🖼️ Descargando carátula para '{title}'...")
                thumb_download_path = os.path.join(download_dir, "thumbnail.jpg")
                thumbnail_path = await downloader.download_thumbnail(task["thumbnail_url"], thumb_download_path)
            
            lyrics_text = None
            if task.get("download_lyrics"):
                await progress_message.edit_text(f"📝 Buscando letras para '{title}'...")
                lyrics_text = await downloader.get_lyrics(task['url'], temp_dir=download_dir)

            # 2. PROCESAR CON FFMPEG (SI ES NECESARIO)
            await progress_message.edit_text(f"⚙️ Procesando '{title}'...")
            
            final_media_path = original_media_path
            if task.get("file_type") == "audio" and thumbnail_path and os.path.exists(thumbnail_path):
                processed_media_path = ffmpeg.get_safe_output_path(original_media_path, "final", "m4a")
                await ffmpeg.embed_thumbnail(original_media_path, thumbnail_path, processed_media_path)
                final_media_path = processed_media_path

            # 3. SUBIR EL ARCHIVO FINAL
            await progress_message.edit_text(f"📤 Subiendo '{title}'...")
            
            # Callback de progreso de subida
            async def upload_progress_callback(current, total):
                now = asyncio.get_event_loop().time()
                if now - last_progress_update["time"] < 2.0: return
                last_progress_update["time"] = now
                percentage = int((current / total) * 100)
                try:
                    await progress_message.edit_text(
                        f"📤 **Subiendo '{title}'...**\n"
                        f"Progreso: {percentage}% ({format_bytes(current)} / {format_bytes(total)})"
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception: pass
            
            file_to_send = open(final_media_path, 'rb')
            if task.get("file_type") == "audio":
                await self.app.send_audio(
                    chat_id,
                    audio=file_to_send,
                    caption=f"✅ Aquí tienes tu canción:\n`{title}`",
                    thumb=thumbnail_path,
                    progress=upload_progress_callback
                )
            else: # Video u otro
                await self.app.send_video(
                    chat_id,
                    video=file_to_send,
                    caption=f"✅ Aquí tienes tu video:\n`{title}`",
                    thumb=thumbnail_path,
                    progress=upload_progress_callback
                )
            file_to_send.close()

            # 4. ENVIAR EXTRAS (LETRAS)
            if lyrics_text:
                # Dividir el mensaje si es muy largo
                for i in range(0, len(lyrics_text), 4096):
                    await self.app.send_message(chat_id, f"**Letra de '{title}':**\n\n{lyrics_text[i:i+4096]}")
            
            await progress_message.delete()
            await db.update_task(task_id, {"status": "completed"})
            logger.info(f"Tarea {task_id} completada con éxito.")

        finally:
            # 5. LIMPIEZA DE ARCHIVOS TEMPORALES
            logger.info(f"Limpiando directorio temporal para la tarea {task_id}: {download_dir}")
            try:
                import shutil
                if os.path.exists(download_dir):
                    shutil.rmtree(download_dir)
            except Exception as e:
                logger.error(f"Error al limpiar el directorio {download_dir}: {e}")