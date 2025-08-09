import logging
import re
from datetime import datetime
import asyncio
import os

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import *
from src.helpers.utils import (get_greeting, escape_html, sanitize_filename,
                               format_time, format_task_details_rich)
from src.core import downloader
from src.core.exceptions import AuthenticationError, NetworkError
from . import processing_handler

logger = logging.getLogger(__name__)

URL_REGEX = r'(https?://[^\s]+)'
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = 0

async def reset_user_state_if_needed(client: Client, user_id: int):
    """
    Resetea el estado del usuario si no está 'idle', para prevenir bloqueos.
    """
    user_state = await db_instance.get_user_state(user_id)
    if user_state.get("status") != "idle":
        logger.warning(f"Reseteando estado obsoleto '{user_state.get('status')}' para el usuario {user_id}.")
        # Si el estado obsoleto estaba asociado a un mensaje de configuración, intentamos borrarlo.
        if source_id := user_state.get("data", {}).get("source_message_id"):
            try:
                await client.delete_messages(user_id, source_id)
            except Exception:
                pass # El mensaje puede no existir, no es crítico.
        await db_instance.set_user_state(user_id, "idle")

@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    await reset_user_state_if_needed(client, message.from_user.id)
    greeting = get_greeting(message.from_user.id)
    start_message = (
        f"¡A sus órdenes, {greeting}! Bienvenido a la <b>Suite de Medios v17.0 (Estable)</b>.\n\n"
        "He reseteado su estado y estoy listo para nuevas tareas.\n\n"
        "<b>📋 Comandos Principales:</b>\n"
        "• /panel - Muestra su mesa de trabajo con las tareas pendientes.\n"
        "• /p <code>[ID]</code> - Abre el menú de configuración para una tarea específica.\n"
        "• /p clean - Limpia todas las tareas de su panel.\n"
        "• /profiles - Gestiona sus perfiles de configuración guardados.\n\n"
        "<b>🛠️ Herramientas de Lote:</b>\n"
        "• /join - Une varios videos de su panel en un solo archivo.\n"
        "• /zip - Comprime varias tareas de su panel en un archivo .zip.\n"
        "• /p_all - Procesa todas las tareas del panel aplicando un perfil.\n\n"
        "Para empezar, envíe un archivo, un enlace (ej. YouTube), o un texto para buscar música."
    )
    await message.reply(start_message, parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("panel") & filters.private)
async def panel_command(client: Client, message: Message):
    user_id = message.from_user.id
    greeting = get_greeting(user_id).replace(',', '')
    # Obtenemos solo las tareas que el usuario debe poder configurar
    pending_tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    
    if not pending_tasks:
        return await message.reply(f"✅ ¡{greeting}, su mesa de trabajo está vacía!")
        
    response = [f"📋 <b>{greeting}, su mesa de trabajo actual:</b>"]
    for i, task in enumerate(pending_tasks):
        response.append(format_task_details_rich(task, i + 1))
    
    response.extend([f"\nUse /p <code>[ID]</code> para configurar una tarea.", f"Use /p clean para limpiar todo el panel."])
    await message.reply("\n".join(response), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@Client.on_message(filters.command("p") & filters.private)
async def process_command(client: Client, message: Message):
    user_id = message.from_user.id
    parts = message.text.split()
    
    if len(parts) < 2:
        return await message.reply("Uso: `/p [ID]` para abrir una tarea o `/p clean` para limpiar el panel.", parse_mode=ParseMode.MARKDOWN)
    
    action = parts[1].lower()
    
    if action == "clean":
        return await message.reply("¿Seguro que desea eliminar TODAS las tareas de su panel?", reply_markup=build_confirmation_keyboard("panel_delete_all_confirm", "panel_delete_all_cancel"))
    
    if not action.isdigit():
        return await message.reply("El ID debe ser un número. Use `/panel` para ver los IDs de sus tareas.")
        
    task_index = int(action) - 1
    pending_tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    
    if 0 <= task_index < len(pending_tasks):
        await processing_handler.open_task_menu_from_p(client, message, str(pending_tasks[task_index]['_id']))
    else:
        await message.reply(f"❌ ID inválido. Tiene {len(pending_tasks)} tareas en el panel. Use `/panel` para ver los IDs correctos.")

@Client.on_message(filters.command("profiles") & filters.private)
async def profiles_command(client: Client, message: Message):
    user_id = message.from_user.id
    presets = await db_instance.get_user_presets(user_id)
    await message.reply(
        "<b>Gestión de Perfiles:</b>\nAquí puede eliminar perfiles de configuración guardados.",
        reply_markup=build_profiles_management_keyboard(presets),
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.command("join") & filters.private)
async def join_command(client: Client, message: Message):
    user_id = message.from_user.id
    video_tasks = await db_instance.get_pending_tasks(user_id, file_type_filter="video", status_filter="pending_processing")
    
    if len(video_tasks) < 2:
        return await message.reply("❌ Necesita al menos 2 videos en su panel para usar /join.")
        
    await db_instance.set_user_state(user_id, "selecting_join_files", data={"selected_ids": []})
    await message.reply(
        "🎬 <b>Modo de Unión</b>\nSeleccione los videos que desea unir en el orden correcto:",
        reply_markup=build_join_selection_keyboard(video_tasks, []),
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.command("zip") & filters.private)
async def zip_command(client: Client, message: Message):
    user_id = message.from_user.id
    tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    
    if not tasks:
        return await message.reply("❌ Su panel está vacío. No hay nada que comprimir.")
        
    await db_instance.set_user_state(user_id, "selecting_zip_files", data={"selected_ids": []})
    await message.reply(
        "📦 <b>Modo de Compresión</b>\nSeleccione las tareas que desea incluir en el archivo .zip:",
        reply_markup=build_zip_selection_keyboard(tasks, []),
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.command("p_all") & filters.private)
async def process_all_command(client: Client, message: Message):
    user_id = message.from_user.id
    tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    
    if not tasks:
        return await message.reply("❌ No hay tareas pendientes en su panel para procesar.")
        
    presets = await db_instance.get_user_presets(user_id)
    await message.reply(
        f"Va a procesar en lote <b>{len(tasks)}</b> tareas.\nSeleccione un perfil para aplicar a todas o use la configuración por defecto.",
        reply_markup=build_batch_profiles_keyboard(presets),
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.media & filters.private, group=1)
async def media_gatekeeper(client: Client, message: Message):
    user_id = message.from_user.id
    user_state = await db_instance.get_user_state(user_id)
    
    # Si el bot está esperando un archivo (ej. una miniatura), se delega al handler de estado.
    if user_state.get("status") != "idle":
        return await processing_handler.handle_media_input_for_state(client, message, user_state)
    
    media = message.video or message.audio or message.document
    file_type = 'video' if message.video else 'audio' if message.audio else 'document'
    
    # Simplificación: Todos los archivos se ponen en 'pending_processing'
    status = "pending_processing"
    
    metadata = {}
    file_name = sanitize_filename(getattr(media, 'file_name', f"{file_type}_{datetime.utcnow().timestamp()}"))
    
    if file_type == 'video':
        metadata = {"resolution": f"{media.width}x{media.height}" if media.width else None, "duration": media.duration}
    elif file_type == 'audio':
        metadata = {"duration": media.duration}
    
    metadata['size'] = media.file_size
    
    task_id = await db_instance.add_task(
        user_id=user_id, file_type=file_type, file_name=file_name,
        file_id=media.file_id, status=status, metadata=metadata
    )
    if not task_id:
        return await message.reply("❌ Error al registrar la tarea en la base de datos.")
    
    # [ROBUST FIX] Contar tareas DESPUÉS de añadir la nueva para un número preciso.
    count = await db_instance.tasks.count_documents({'user_id': user_id, 'status': 'pending_processing'})
    
    reply_text = f"✅ Añadido al panel como tarea <b>#{count}</b>."
    status_msg = await message.reply(reply_text, parse_mode=ParseMode.HTML)
    
    # Ofrecer perfiles si existen
    if presets := await db_instance.get_user_presets(user_id):
        await status_msg.edit("¿Desea aplicar un perfil de configuración a esta tarea?", reply_markup=build_profiles_keyboard(str(task_id), presets))


@Client.on_message(filters.text & filters.private, group=2)
async def text_gatekeeper(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Ignorar comandos
    if text.startswith('/'):
        return
        
    user_state = await db_instance.get_user_state(user_id)
    if user_state.get("status") != "idle":
        return await processing_handler.handle_text_input_for_state(client, message, user_state)
    
    if re.search(URL_REGEX, text):
        return await handle_url_input(client, message, text)
    
    # Si no es un comando, ni un estado, ni una URL, asumimos que es una búsqueda de música.
    await handle_music_search(client, message, text)


async def handle_url_input(client: Client, message: Message, url: str):
    status_msg = await message.reply("🔎 Analizando enlace...")
    try:
        info = await asyncio.to_thread(downloader.get_url_info, url)
        if not info:
            raise ValueError("No se pudo obtener información del enlace. Podría no ser compatible o estar caído.")
            
        caption = f"<b>📝 Título:</b> {escape_html(info['title'])}\n<b>🕓 Duración:</b> {format_time(info.get('duration'))}"
        
        # Guardar la info completa en una colección temporal con TTL
        temp_info_id = str((await db_instance.search_results.insert_one({
            'user_id': message.from_user.id,
            'data': info,
            'created_at': datetime.utcnow()
        })).inserted_id)
        
        keyboard = build_detailed_format_menu(temp_info_id, info['formats'])
        await status_msg.delete()
        
        if info.get('thumbnail'):
            await client.send_photo(
                message.from_user.id, photo=info['thumbnail'],
                caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
        else:
            await client.send_message(
                message.from_user.id, caption, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
            
    except AuthenticationError as e:
        logger.error(f"Error de autenticación procesando URL {url}: {e}")
        await status_msg.edit_text(f"❌ Error de autenticación: <code>{escape_html(str(e))}</code>. Las cookies de YouTube pueden haber expirado.")
    except (NetworkError, ValueError) as e:
        logger.error(f"Error de red/valor procesando URL {url}: {e}")
        await status_msg.edit_text(f"❌ Error: <code>{escape_html(str(e))}</code>")
    except Exception as e:
        logger.error(f"Error inesperado procesando URL {url}: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Error inesperado: <code>{escape_html(str(e))}</code>")


async def handle_music_search(client: Client, message: Message, query: str):
    status_msg = await message.reply(f"🔎 Buscando música: <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
    try:
        results = await asyncio.to_thread(downloader.search_music, query, limit=10)
        if not results:
            return await status_msg.edit_text("❌ No encontré resultados. Intente con otro término de búsqueda.")
        
        search_id = str((await db_instance.search_sessions.insert_one({'user_id': message.from_user.id, 'created_at': datetime.utcnow()})).inserted_id)
        
        # Preparar documentos para inserción masiva
        docs = [{'search_id': search_id, 'created_at': datetime.utcnow(), **res} for res in results]
        await db_instance.search_results.insert_many(docs)
        
        await status_msg.edit_text("✅ He encontrado esto. Seleccione una opción para descargar:", reply_markup=build_search_results_keyboard(docs, search_id))
    
    except Exception as e:
        logger.error(f"Error en búsqueda de música para '{query}': {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Ocurrió un error durante la búsqueda: <code>{escape_html(str(e))}</code>")


@Client.on_callback_query(filters.regex(r"^set_dlformat_"))
async def set_download_format_callback(client: Client, query: CallbackQuery):
    await query.answer("Preparando tarea...", show_alert=False)
    parts = query.data.split("_")
    temp_info_id, format_id = parts[2], "_".join(parts[3:])
    
    info_doc = await db_instance.search_results.find_one_and_delete({"_id": ObjectId(temp_info_id)})
    if not info_doc:
        return await query.message.edit_text("❌ Esta selección ha expirado. Por favor, envíe el enlace de nuevo.")
        
    info = info_doc['data']
    file_type = 'audio' if 'audio' in format_id or 'mp3' in format_id else 'video'
    
    task_id = await db_instance.add_task(
        user_id=query.from_user.id,
        file_type=file_type,
        file_name=sanitize_filename(info['title']),
        final_filename=sanitize_filename(info['title']), # Mismo nombre inicial
        url=info.get('webpage_url') or info.get('url'),
        processing_config={"download_format_id": format_id},
        url_info=info,
        status="queued"
    )
    
    await query.message.edit_text(f"✅ <b>¡Enviado a la cola!</b>\n🔗 <code>{escape_html(info['title'])}</code>", parse_mode=ParseMode.HTML)

# Este router es necesario para las callbacks de búsqueda de música que no están en processing_handler
@Client.on_callback_query(filters.regex(r"^(song_select_|search_page_|cancel_search_)"))
async def search_callbacks_router(client: Client, query: CallbackQuery):
    try:
        data = query.data
        if data.startswith("song_select_"): await select_song_from_search(client, query)
        elif data.startswith("search_page_"): await handle_search_pagination(client, query)
        elif data.startswith("cancel_search_"): await cancel_search_session(client, query)
    except MessageNotModified: await query.answer("Nada que cambiar.")
    except Exception as e: logger.error(f"Error en search_callbacks_router: {e}", exc_info=True)

async def select_song_from_search(client: Client, query: CallbackQuery):
    await query.answer("Preparando descarga...", show_alert=False)
    result_id = query.data.split("_")[2]
    
    search_result = await db_instance.search_results.find_one({"_id": ObjectId(result_id)})
    if not search_result:
        return await query.message.edit_text("❌ Este resultado de búsqueda ha expirado. Por favor, inicie una nueva búsqueda.")

    search_term = search_result.get('search_term')
    display_title = f"{search_result.get('artist', '')} - {search_result.get('title', 'Canción Desconocida')}"
    
    await query.message.edit_text(f"🔎 Obteniendo mejor fuente de audio para:\n<code>{escape_html(display_title)}</code>", parse_mode=ParseMode.HTML)
    
    try:
        url_info = await asyncio.to_thread(downloader.get_url_info, f"ytsearch1:{search_term}")
        if not url_info or not (url_info.get('webpage_url') or url_info.get('url')):
            return await query.message.edit_text("❌ No pude encontrar una fuente de audio descargable para esta canción.")
        
        final_filename = sanitize_filename(f"{search_result['artist']} - {search_result['title']}")
        
        task_id = await db_instance.add_task(
            user_id=query.from_user.id,
            file_type='audio',
            file_name=f"{final_filename}.mp3", # Nombre esperado
            final_filename=final_filename,
            url=url_info.get('webpage_url'),
            status="queued",
            processing_config={
                "download_format_id": downloader.get_best_audio_format_id(url_info.get('formats', [])),
                "audio_tags": {'title': search_result['title'], 'artist': search_result['artist'], 'album': search_result.get('album')}
            },
            url_info=url_info
        )
        
        if thumbnail_url := search_result.get('thumbnail'):
            # El worker se encargará de descargar esto si es necesario.
            await db_instance.update_task_config(str(task_id), "thumbnail_url", thumbnail_url)
        
        await query.message.edit_text(f"✅ <b>¡Enviado a la cola!</b>\n🎧 <code>{escape_html(display_title)}</code>", parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error al procesar selección de canción: {e}", exc_info=True)
        await query.message.edit_text(f"❌ Error al obtener la fuente: <code>{escape_html(str(e))}</code>")

async def handle_search_pagination(client: Client, query: CallbackQuery):
    await query.answer()
    _, search_id, page_str = query.data.split("_")
    page = int(page_str)
    
    results = await db_instance.search_results.find({"search_id": search_id}).sort('created_at', 1).to_list(length=100)
    if not results:
        return await query.message.edit_text("❌ La sesión de búsqueda ha expirado.")
        
    await query.message.edit_reply_markup(reply_markup=build_search_results_keyboard(results, search_id, page))

async def cancel_search_session(client: Client, query: CallbackQuery):
    await query.answer("Búsqueda cancelada.")
    await query.message.delete()