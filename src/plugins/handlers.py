# src/plugins/handlers.py

import asyncio
import os
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from bson import ObjectId

from src.db.mongo_manager import db
from src.core.downloader import downloader, Downloader
from src.helpers.keyboards import create_search_results_keyboard, create_quality_selection_keyboard, create_processing_menu
from src.helpers.utils import get_sanitized_filename, is_valid_timestamp

@Client.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def file_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    try:
        file_id = getattr(message, message.media.value).file_id
        file_name = getattr(message, message.media.value).file_name or "archivo_recibido"
        
        task = {
            "user_id": user_id,
            "file_id": file_id,
            "file_name": file_name,
            "original_message_id": message.id,
            "status": "awaiting_action",
            "processing_steps": [],
        }
        
        result = await db.tasks.insert_one(task)
        task_id = result.inserted_id

        await message.reply_text(
            f"📄 **Archivo recibido:** `{file_name}`\n\n¿Qué quieres hacer con él?",
            reply_markup=create_processing_menu(str(task_id))
        )
    except Exception as e:
        await message.reply(f"❌ Ocurrió un error al procesar tu archivo: {e}")

@Client.on_message(filters.private & filters.text & ~filters.command(["start", "panel", "cancelar", "historial"]))
async def text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text

    pending_task = await db.tasks.find_one({"user_id": user_id, "status": "awaiting_input"})
    if pending_task:
        prompt_type = pending_task.get("input_prompt")
        
        if prompt_type in ["trim_start_time", "trim_end_time"]:
            if not is_valid_timestamp(text):
                await message.reply("❌ Formato de tiempo no válido. Inténtalo de nuevo (ej. `01:23`).")
                return

            if prompt_type == "trim_start_time":
                await db.tasks.update_one(
                    {"_id": pending_task["_id"]},
                    {"$set": {"input_prompt": "trim_end_time", "processing_data.trim_start": text}}
                )
                await message.reply("✅ Tiempo de inicio guardado.\n\nAhora, envíame el **tiempo de fin** (ej. `01:45`).")
                return

            elif prompt_type == "trim_end_time":
                start_time = pending_task.get("processing_data", {}).get("trim_start")
                end_time = text
                await db.tasks.update_one(
                    {"_id": pending_task["_id"]},
                    {
                        "$set": {
                            "status": "queued",
                            "processing_steps": [{"type": "trim", "start": start_time, "end": end_time}]
                        },
                        "$unset": {"input_prompt": "", "processing_data": ""}
                    }
                )
                await message.reply(f"✅ ¡Perfecto! La tarea para cortar desde `{start_time}` hasta `{end_time}` ha sido encolada.")
                return
        return

    if text.startswith("http://") or text.startswith("https://"):
        sent_message = await message.reply("🔎 Analizando enlace...")
        try:
            info = await asyncio.to_thread(downloader.get_url_info, text)
            
            task_data = {
                "user_id": user_id,
                "url": text,
                "title": info.get('title', 'Sin título'),
                "status": "awaiting_quality",
                "formats": info.get('formats', []),
            }
            result = await db.tasks.insert_one(task_data)
            task_id = result.inserted_id

            await sent_message.edit_text(
                "✅ Enlace analizado. Por favor, selecciona la calidad deseada:",
                reply_markup=create_quality_selection_keyboard(info, str(task_id))
            )
        except Downloader.DownloaderError as e:
            await sent_message.edit_text(
                f"❌ **Error al obtener información del enlace.**\n\n"
                f"**Motivo:** `{e}`\n\n"
                "Por favor, verifica el enlace. Si es un video de YouTube y el problema persiste, es posible que las cookies hayan expirado."
            )
        except Exception as e:
            await sent_message.edit_text(f"❌ Ocurrió un error inesperado al procesar el enlace: {e}")
        return

    sent_message = await message.reply("🎶 Buscando música...")
    try:
        results = await asyncio.to_thread(downloader.search_music, text)
        if not results:
            await sent_message.edit_text("No se encontraron resultados para tu búsqueda.")
            return
        
        await db.create_search_session(user_id, results)
        keyboard, text_content = await create_search_results_keyboard(user_id, 0)

        await sent_message.edit_text(text_content, reply_markup=keyboard)

    except Downloader.DownloaderError as e:
        await sent_message.edit_text(f"❌ Error durante la búsqueda: {e}")
    except Exception as e:
        await sent_message.edit_text(f"❌ Ocurrió un error inesperado durante la búsqueda: {e}")