from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .utils import escape_html, format_bytes, format_time
import math

# --- Teclados de Gestión de Perfiles ---

def build_profiles_keyboard(task_id: str, presets: list) -> InlineKeyboardMarkup:
    """Construye el teclado para aplicar un perfil a una tarea."""
    keyboard = []
    row = []
    for preset in presets:
        preset_id = str(preset['_id'])
        preset_name = preset.get('preset_name', 'Perfil sin nombre').capitalize()
        row.append(InlineKeyboardButton(f"⚙️ {preset_name}", callback_data=f"profile_apply_{task_id}_{preset_id}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🛠️ Abrir Panel de Tarea", callback_data=f"p_open_{task_id}")])
    return InlineKeyboardMarkup(keyboard)

def build_profiles_management_keyboard(presets: list) -> InlineKeyboardMarkup:
    """Construye el teclado para ver y eliminar perfiles existentes."""
    keyboard = []
    if not presets:
        keyboard.append([InlineKeyboardButton("No tienes perfiles guardados.", callback_data="noop")])
    else:
        for preset in presets:
            preset_id = str(preset['_id'])
            preset_name = preset.get('preset_name', 'Perfil sin nombre').capitalize()
            keyboard.append([InlineKeyboardButton(f"🗑️ {preset_name}", callback_data=f"profile_delete_req_{preset_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="profiles_close")])
    return InlineKeyboardMarkup(keyboard)

def build_profile_delete_confirmation_keyboard(preset_id: str) -> InlineKeyboardMarkup:
    """Pide confirmación antes de eliminar un perfil."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"profile_delete_confirm_{preset_id}"), InlineKeyboardButton("❌ No", callback_data="profiles_open_main")]])

# --- Teclados del Panel de Procesamiento ---

def build_processing_menu(task_id: str, file_type: str, task_data: dict) -> InlineKeyboardMarkup:
    keyboard = []
    config = task_data.get('processing_config', {})
    
    if file_type == 'video':
        mute_text = "🔇 Silenciar" if not config.get('mute_audio') else "🔊 Desilenciar"
        transcode_res = config.get('transcode', {}).get('resolution', 'No')
        keyboard.extend([
            [InlineKeyboardButton(f"📉 Transcodificar ({transcode_res})", callback_data=f"config_transcode_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"), InlineKeyboardButton("🧩 Dividir", callback_data=f"config_split_{task_id}")],
            [InlineKeyboardButton("🎞️ a GIF", callback_data=f"config_gif_{task_id}"), InlineKeyboardButton("💧 Marca de Agua", callback_data=f"config_watermark_{task_id}")],
            [InlineKeyboardButton("🖼️ Miniatura", callback_data=f"config_thumbnail_{task_id}"), InlineKeyboardButton("📜 Pistas", callback_data=f"config_tracks_{task_id}")],
            [InlineKeyboardButton(mute_text, callback_data=f"set_mute_{task_id}_toggle")],
        ])
    elif file_type == 'audio':
        bitrate, fmt = config.get('audio_bitrate', '192k'), config.get('audio_format', 'mp3')
        keyboard.extend([
            [InlineKeyboardButton(f"🔊 Convertir ({fmt.upper()}, {bitrate})", callback_data=f"config_audioconvert_{task_id}")],
            [InlineKeyboardButton("🎧 Efectos", callback_data=f"config_audioeffects_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"), InlineKeyboardButton("🖼️ Editar Metadatos", callback_data=f"config_audiometadata_{task_id}")],
        ])

    keyboard.extend([
        [InlineKeyboardButton("✏️ Renombrar", callback_data=f"config_rename_{task_id}")],
        [InlineKeyboardButton("💾 Guardar como Perfil", callback_data=f"profile_save_request_{task_id}")],
        [InlineKeyboardButton("🗑️ Descartar Tarea", callback_data=f"task_delete_{task_id}"), InlineKeyboardButton("🔥 Procesar Ahora", callback_data=f"task_queuesingle_{task_id}")]
    ])
    return InlineKeyboardMarkup(keyboard)

def build_transcode_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1080p", callback_data=f"set_transcode_{task_id}_resolution_1080p"), InlineKeyboardButton("720p", callback_data=f"set_transcode_{task_id}_resolution_720p")],
        [InlineKeyboardButton("480p", callback_data=f"set_transcode_{task_id}_resolution_480p"), InlineKeyboardButton("360p", callback_data=f"set_transcode_{task_id}_resolution_360p")],
        [InlineKeyboardButton("❌ Quitar Transcodificación", callback_data=f"set_transcode_{task_id}_remove_all")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_tracks_menu(task_id: str, config: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Quitar Subs" if config.get('remove_subtitles') else "❌ Quitar Subs", callback_data=f"set_trackopt_{task_id}_remove_subtitles_toggle")],
        [InlineKeyboardButton("➕ Añadir Subs (.srt)", callback_data=f"config_addsubs_{task_id}")],
        [InlineKeyboardButton("🎵 Extraer Audio", callback_data=f"config_extract_audio_{task_id}")],
        [InlineKeyboardButton("🎼 Reemplazar Audio", callback_data=f"config_replace_audio_{task_id}")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_audio_convert_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("MP3", callback_data=f"set_audioprop_{task_id}_format_mp3"), InlineKeyboardButton("FLAC", callback_data=f"set_audioprop_{task_id}_format_flac"), InlineKeyboardButton("Opus", callback_data=f"set_audioprop_{task_id}_format_opus")],
        [InlineKeyboardButton("128k", callback_data=f"set_audioprop_{task_id}_bitrate_128k"), InlineKeyboardButton("192k", callback_data=f"set_audioprop_{task_id}_bitrate_192k"), InlineKeyboardButton("320k", callback_data=f"set_audioprop_{task_id}_bitrate_320k")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_audio_effects_menu(task_id: str, config: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🐌 Slowed {'✅' if config.get('slowed') else '❌'}", callback_data=f"set_audioeffect_{task_id}_slowed_toggle")],
        [InlineKeyboardButton(f"🌌 Reverb {'✅' if config.get('reverb') else '❌'}", callback_data=f"set_audioeffect_{task_id}_reverb_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_audio_metadata_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Editar Texto", callback_data=f"config_audiotags_{task_id}")],
        [InlineKeyboardButton("🖼️ Añadir Carátula", callback_data=f"config_audiothumb_{task_id}")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_watermark_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Imagen", callback_data=f"config_watermark_image_{task_id}")],
        [InlineKeyboardButton("✏️ Texto", callback_data=f"config_watermark_text_{task_id}")],
        [InlineKeyboardButton("❌ Quitar Marca", callback_data=f"set_watermark_{task_id}_remove")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_position_menu(task_id: str, origin_menu: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖️", callback_data=f"set_watermark_{task_id}_position_top-left"), InlineKeyboardButton("↗️", callback_data=f"set_watermark_{task_id}_position_top-right")],
        [InlineKeyboardButton("↙️", callback_data=f"set_watermark_{task_id}_position_bottom-left"), InlineKeyboardButton("↘️", callback_data=f"set_watermark_{task_id}_position_bottom-right")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"{origin_menu}_{task_id}")]
    ])
    
def build_thumbnail_menu(task_id: str, config: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Añadir/Cambiar", callback_data=f"config_thumbnail_add_{task_id}")],
        [InlineKeyboardButton(f"{'✅' if config.get('extract_thumbnail') else '❌'} Extraer Miniatura", callback_data=f"set_thumb_op_{task_id}_extract_toggle")],
        [InlineKeyboardButton(f"{'✅' if config.get('remove_thumbnail') else '❌'} Quitar Miniatura", callback_data=f"set_thumb_op_{task_id}_remove_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_detailed_format_menu(url_info_id: str, formats: list) -> InlineKeyboardMarkup:
    k, r = [], []
    v_formats = sorted([f for f in formats if f.get('vcodec') not in ['none', None] and f.get('height')], key=lambda x: x.get('height', 0), reverse=True)
    
    for f in v_formats[:8]:
        label = f"🎬 {f.get('height')}p"
        if fsize := f.get('filesize'): label += f" ({format_bytes(fsize)})"
        r.append(InlineKeyboardButton(label, callback_data=f"set_dlformat_{url_info_id}_{f['format_id']}"))
        if len(r) >= 2: k.append(r); r = []
    if r: k.append(r)
            
    k.extend([
        [InlineKeyboardButton("🎵 MP3 (Mejor)", callback_data=f"set_dlformat_{url_info_id}_mp3")],
        [InlineKeyboardButton("🏆 Mejor Video", callback_data=f"set_dlformat_{url_info_id}_bestvideo")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"task_delete_{url_info_id}")]
    ])
    return InlineKeyboardMarkup(k)

def build_search_results_keyboard(all_results: list, search_id: str, page: int = 1, page_size: int = 5) -> InlineKeyboardMarkup:
    k = []
    paginated_results = all_results[(page - 1) * page_size : page * page_size]
    total_pages = math.ceil(len(all_results) / page_size)

    for res in paginated_results:
        title, artist = (res.get('title', '...')[:30]), (res.get('artist', '...')[:20])
        display_text = f"🎵 {title} - {artist} ({format_time(res.get('duration'))})"
        k.append([InlineKeyboardButton(display_text, callback_data=f"song_select_{res['_id']}")])
    
    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"search_page_{search_id}_{page - 1}"))
    if total_pages > 1: nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"search_page_{search_id}_{page + 1}"))
    if nav: k.append(nav)

    k.append([InlineKeyboardButton("❌ Cancelar Búsqueda", callback_data=f"cancel_search_{search_id}")])
    return InlineKeyboardMarkup(k)

def build_batch_profiles_keyboard(presets: list) -> InlineKeyboardMarkup:
    k, r = [], []
    for preset in presets:
        pid, name = str(preset['_id']), preset.get('preset_name', '...').capitalize()
        r.append(InlineKeyboardButton(f"⚙️ {name}", callback_data=f"batch_apply_{pid}"))
        if len(r) == 2: k.append(r); r = []
    if r: k.append(r)
    k.extend([[InlineKeyboardButton("⚙️ Usar Config. Default", callback_data="batch_apply_default")], [InlineKeyboardButton("❌ Cancelar", callback_data="batch_cancel")]])
    return InlineKeyboardMarkup(k)

def build_join_selection_keyboard(tasks: list, selected_ids: list) -> InlineKeyboardMarkup:
    k = []
    for task in tasks:
        tid, fname = str(task['_id']), task.get('original_filename', '...')[:50]
        prefix = "✅ " if tid in selected_ids else "🎬 "
        k.append([InlineKeyboardButton(f"{prefix}{escape_html(fname)}", callback_data=f"join_select_{tid}")])

    actions = [InlineKeyboardButton("❌ Cancelar", callback_data="join_cancel")]
    if len(selected_ids) > 1:
        actions.insert(0, InlineKeyboardButton("✅ Unir Seleccionados", callback_data="join_confirm"))
    k.append(actions)
    return InlineKeyboardMarkup(k)

def build_zip_selection_keyboard(tasks: list, selected_ids: list) -> InlineKeyboardMarkup:
    k, emoji_map = [], {'video': '🎬', 'audio': '🎵', 'document': '📄'}
    for task in tasks:
        tid, fname = str(task['_id']), task.get('original_filename', '...')[:45]
        emoji = emoji_map.get(task.get('file_type'), '📦')
        prefix = "✅ " if tid in selected_ids else f"{emoji} "
        k.append([InlineKeyboardButton(f"{prefix}{escape_html(fname)}", callback_data=f"zip_select_{tid}")])
        
    actions = [InlineKeyboardButton("❌ Cancelar", callback_data="zip_cancel")]
    if selected_ids:
        actions.insert(0, InlineKeyboardButton("✅ Comprimir", callback_data="zip_confirm"))
    k.append(actions)
    return InlineKeyboardMarkup(k)

def build_confirmation_keyboard(action_callback: str, cancel_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Sí", callback_data=action_callback), InlineKeyboardButton("❌ No", callback_data=cancel_callback)]])

def build_back_button(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data=callback_data)]])