from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .utils import escape_html, format_bytes, format_time
import math
from typing import List, Dict

# --- Teclado para Volver ---

def build_back_button(callback_data: str) -> InlineKeyboardMarkup:
    """Construye un teclado simple con un solo botón de 'Volver'."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data=callback_data)]])

# --- Teclados de Gestión de Perfiles ---

def build_profiles_keyboard(task_id: str, presets: List[Dict]) -> InlineKeyboardMarkup:
    """Construye el teclado para aplicar un perfil a una tarea específica."""
    keyboard = []
    row = []
    for preset in presets:
        preset_id = str(preset['_id'])
        preset_name = preset.get('preset_name', 'Perfil sin nombre').capitalize()
        row.append(InlineKeyboardButton(f"⚙️ {preset_name}", callback_data=f"profile_apply_{task_id}_{preset_id}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🛠️ Abrir Panel de Tarea", callback_data=f"p_open_{task_id}")])
    return InlineKeyboardMarkup(keyboard)

def build_profiles_management_keyboard(presets: List[Dict]) -> InlineKeyboardMarkup:
    """Construye el teclado para ver y eliminar perfiles de usuario existentes."""
    keyboard = []
    if not presets:
        keyboard.append([InlineKeyboardButton("No tienes perfiles guardados.", callback_data="noop")])
    else:
        for preset in presets:
            preset_id = str(preset['_id'])
            preset_name = preset.get('preset_name', 'Perfil sin nombre').capitalize()
            keyboard.append([InlineKeyboardButton(f"🗑️ Eliminar '{preset_name}'", callback_data=f"profile_delete_req_{preset_id}")])
    
    keyboard.append([InlineKeyboardButton("❌ Cerrar", callback_data="profile_close")])
    return InlineKeyboardMarkup(keyboard)

def build_profile_delete_confirmation_keyboard(preset_id: str) -> InlineKeyboardMarkup:
    """Pide confirmación antes de eliminar un perfil."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"profile_delete_confirm_{preset_id}"),
            InlineKeyboardButton("❌ No, cancelar", callback_data="profile_open_main")
        ]
    ])

# --- Teclados del Panel de Procesamiento Principal ---

def build_processing_menu(task_id: str, file_type: str, task_data: Dict) -> InlineKeyboardMarkup:
    """Construye el menú principal de configuración para una tarea."""
    keyboard = []
    config = task_data.get('processing_config', {})
    
    # --- Opciones Específicas de Video ---
    if file_type == 'video':
        mute_text = "🔇 Silenciar Video" if not config.get('mute_audio') else "🔊 Restaurar Audio"
        transcode_res = config.get('transcode', {}).get('resolution', 'Original').upper()
        
        keyboard.extend([
            [InlineKeyboardButton(f"📉 Transcodificar ({transcode_res})", callback_data=f"config_transcode_{task_id}")],
            [
                InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"),
                InlineKeyboardButton("🎞️ a GIF", callback_data=f"config_gif_{task_id}")
            ],
            [
                InlineKeyboardButton("💧 Marca de Agua", callback_data=f"config_watermark_{task_id}"),
                InlineKeyboardButton("🖼️ Miniatura", callback_data=f"config_thumbnail_{task_id}")
            ],
            [
                InlineKeyboardButton("📜 Pistas (Audio/Subs)", callback_data=f"config_tracks_{task_id}"),
                InlineKeyboardButton(mute_text, callback_data=f"set_mute_{task_id}_toggle")
            ],
        ])
    # --- Opciones Específicas de Audio ---
    elif file_type == 'audio':
        bitrate, fmt = config.get('audio_bitrate', 'Original'), config.get('audio_format', 'Original')
        convert_text = f"🔊 Convertir ({fmt.upper()}, {bitrate})"
        keyboard.extend([
            [InlineKeyboardButton(convert_text, callback_data=f"config_audioconvert_{task_id}")],
            [
                InlineKeyboardButton("🎧 Efectos de Audio", callback_data=f"config_audioeffects_{task_id}"),
                InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}")
            ],
            [InlineKeyboardButton("📝 Editar Metadatos", callback_data=f"config_audiometadata_{task_id}")],
        ])

    # --- Opciones Comunes y Acciones Finales ---
    keyboard.extend([
        [InlineKeyboardButton("✏️ Renombrar Archivo", callback_data=f"config_rename_{task_id}")],
        [InlineKeyboardButton("💾 Guardar como Perfil", callback_data=f"profile_save_request_{task_id}")],
        [
            InlineKeyboardButton("🗑️ Descartar", callback_data=f"task_delete_{task_id}"),
            InlineKeyboardButton("🔥 Procesar Ahora", callback_data=f"task_queuesingle_{task_id}")
        ]
    ])
    return InlineKeyboardMarkup(keyboard)


# --- Sub-menús de Configuración ---

def build_transcode_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1080p", callback_data=f"set_transcode_{task_id}_resolution_1080p"), InlineKeyboardButton("720p", callback_data=f"set_transcode_{task_id}_resolution_720p")],
        [InlineKeyboardButton("480p", callback_data=f"set_transcode_{task_id}_resolution_480p"), InlineKeyboardButton("360p", callback_data=f"set_transcode_{task_id}_resolution_360p")],
        [InlineKeyboardButton("240p", callback_data=f"set_transcode_{task_id}_resolution_240p"), InlineKeyboardButton("144p", callback_data=f"set_transcode_{task_id}_resolution_144p")],
        [InlineKeyboardButton("❌ Mantener Resolución Original", callback_data=f"set_transcode_{task_id}_remove_all")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_tracks_menu(task_id: str, config: Dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Extraer Pista de Audio", callback_data=f"config_extract_audio_{task_id}")],
        [InlineKeyboardButton("🎼 Reemplazar Audio", callback_data=f"config_replace_audio_{task_id}")],
        [InlineKeyboardButton(f"{'✅' if config.get('remove_subtitles') else '❌'} Quitar Subtítulos Incrustados", callback_data=f"set_trackopt_{task_id}_remove_subtitles_toggle")],
        [InlineKeyboardButton("➕ Incrustar Subtítulos (.srt)", callback_data=f"config_addsubs_{task_id}")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_audio_convert_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("MP3", callback_data=f"set_audioprop_{task_id}_format_mp3"), InlineKeyboardButton("FLAC", callback_data=f"set_audioprop_{task_id}_format_flac"), InlineKeyboardButton("Opus", callback_data=f"set_audioprop_{task_id}_format_opus")],
        [InlineKeyboardButton("128 kbps", callback_data=f"set_audioprop_{task_id}_bitrate_128k"), InlineKeyboardButton("192 kbps", callback_data=f"set_audioprop_{task_id}_bitrate_192k"), InlineKeyboardButton("320 kbps", callback_data=f"set_audioprop_{task_id}_bitrate_320k")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_audio_effects_menu(task_id: str, config: Dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🐌 Slowed & Reverb {'✅' if config.get('slowed') else '❌'}", callback_data=f"set_audioeffect_{task_id}_slowed_toggle")],
        [InlineKeyboardButton(f"🌌 Solo Reverb {'✅' if config.get('reverb') else '❌'}", callback_data=f"set_audioeffect_{task_id}_reverb_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_audio_metadata_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Editar Título, Artista, Álbum", callback_data=f"config_audiotags_{task_id}")],
        [InlineKeyboardButton("🖼️ Añadir/Cambiar Carátula", callback_data=f"config_audiothumb_{task_id}")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_watermark_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Usar Imagen", callback_data=f"config_watermark_image_{task_id}")],
        [InlineKeyboardButton("✏️ Usar Texto", callback_data=f"config_watermark_text_{task_id}")],
        [InlineKeyboardButton("❌ Quitar Marca de Agua", callback_data=f"set_watermark_{task_id}_remove")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_position_menu(task_id: str, origin_menu: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖️ Superior Izquierda", callback_data=f"set_watermark_{task_id}_position_top-left")],
        [InlineKeyboardButton("↗️ Superior Derecha", callback_data=f"set_watermark_{task_id}_position_top-right")],
        [InlineKeyboardButton("↙️ Inferior Izquierda", callback_data=f"set_watermark_{task_id}_position_bottom-left")],
        [InlineKeyboardButton("↘️ Inferior Derecha", callback_data=f"set_watermark_{task_id}_position_bottom-right")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"{origin_menu}_{task_id}")]
    ])
    
def build_thumbnail_menu(task_id: str, config: Dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Añadir/Cambiar Miniatura", callback_data=f"config_thumbnail_add_{task_id}")],
        [InlineKeyboardButton(f"{'✅' if config.get('extract_thumbnail') else '❌'} Extraer Miniatura del Video", callback_data=f"set_thumb_op_{task_id}_extract_toggle")],
        [InlineKeyboardButton(f"{'✅' if config.get('remove_thumbnail') else '❌'} Eliminar Miniatura Existente", callback_data=f"set_thumb_op_{task_id}_remove_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

# --- Teclados de Funciones de Búsqueda y Lote ---

def build_detailed_format_menu(url_info_id: str, formats: List[Dict]) -> InlineKeyboardMarkup:
    k, r = [], []
    # Filtrar y ordenar formatos de video por altura
    v_formats = sorted([f for f in formats if f.get('vcodec') not in ['none', None] and f.get('height')], key=lambda x: x.get('height', 0), reverse=True)
    
    for f in v_formats[:6]: # Mostrar hasta 6 formatos de video
        label = f"🎬 {f.get('height')}p"
        if fsize := f.get('filesize'): label += f" ({format_bytes(fsize)})"
        r.append(InlineKeyboardButton(label, callback_data=f"set_dlformat_{url_info_id}_{f['format_id']}"))
        if len(r) >= 2: k.append(r); r = []
    if r: k.append(r)
            
    k.extend([
        [InlineKeyboardButton("🎵 Mejor Audio (MP3)", callback_data=f"set_dlformat_{url_info_id}_mp3")],
        [InlineKeyboardButton("🏆 Mejor Calidad General", callback_data=f"set_dlformat_{url_info_id}_bestvideo")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel_search_session")] # Callback unificada
    ])
    return InlineKeyboardMarkup(k)

def build_search_results_keyboard(all_results: List[Dict], search_id: str, page: int = 1, page_size: int = 5) -> InlineKeyboardMarkup:
    k = []
    start_index = (page - 1) * page_size
    paginated_results = all_results[start_index : start_index + page_size]
    total_pages = math.ceil(len(all_results) / page_size)

    for res in paginated_results:
        title, artist = (res.get('title', '...')[:30]), (res.get('artist', '...')[:20])
        display_text = f"🎵 {title} - {artist}"
        if duration := res.get('duration'):
             display_text += f" ({format_time(duration)})"
        k.append([InlineKeyboardButton(display_text, callback_data=f"song_select_{res['_id']}")])
    
    nav = []
    if page > 1: nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"search_page_{search_id}_{page - 1}"))
    if total_pages > 1: nav.append(InlineKeyboardButton(f"Pág {page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"search_page_{search_id}_{page + 1}"))
    if nav: k.append(nav)

    k.append([InlineKeyboardButton("❌ Cancelar Búsqueda", callback_data=f"cancel_search_session")])
    return InlineKeyboardMarkup(k)

def build_batch_profiles_keyboard(presets: List[Dict]) -> InlineKeyboardMarkup:
    k, r = [], []
    for preset in presets:
        pid, name = str(preset['_id']), preset.get('preset_name', '...').capitalize()
        r.append(InlineKeyboardButton(f"⚙️ {name}", callback_data=f"batch_apply_{pid}"))
        if len(r) == 2: k.append(r); r = []
    if r: k.append(r)
    k.extend([
        [InlineKeyboardButton("⚙️ Usar Config. Por Defecto", callback_data="batch_apply_default")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="batch_cancel")]
    ])
    return InlineKeyboardMarkup(k)

def build_join_selection_keyboard(tasks: List[Dict], selected_ids: List[str]) -> InlineKeyboardMarkup:
    k = []
    for task in tasks:
        tid, fname = str(task['_id']), task.get('original_filename', '...')[:50]
        prefix = "✅ " if tid in selected_ids else "🎬 "
        k.append([InlineKeyboardButton(f"{prefix}{escape_html(fname)}", callback_data=f"join_select_{tid}")])

    actions = [InlineKeyboardButton("❌ Cancelar", callback_data="join_cancel")]
    if len(selected_ids) > 1:
        actions.insert(0, InlineKeyboardButton(f"✅ Unir {len(selected_ids)} Videos", callback_data="join_confirm"))
    k.append(actions)
    return InlineKeyboardMarkup(k)

def build_zip_selection_keyboard(tasks: List[Dict], selected_ids: List[str]) -> InlineKeyboardMarkup:
    k, emoji_map = [], {'video': '🎬', 'audio': '🎵', 'document': '📄'}
    for task in tasks:
        tid, fname = str(task['_id']), task.get('original_filename', '...')[:45]
        emoji = emoji_map.get(task.get('file_type'), '📦')
        prefix = "✅ " if tid in selected_ids else f"{emoji} "
        k.append([InlineKeyboardButton(f"{prefix}{escape_html(fname)}", callback_data=f"zip_select_{tid}")])
        
    actions = [InlineKeyboardButton("❌ Cancelar", callback_data="zip_cancel")]
    if selected_ids:
        actions.insert(0, InlineKeyboardButton(f"✅ Comprimir {len(selected_ids)} Archivos", callback_data="zip_confirm"))
    k.append(actions)
    return InlineKeyboardMarkup(k)

def build_confirmation_keyboard(action_callback: str, cancel_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Sí, estoy seguro", callback_data=action_callback), InlineKeyboardButton("❌ No, cancelar", callback_data=cancel_callback)]])