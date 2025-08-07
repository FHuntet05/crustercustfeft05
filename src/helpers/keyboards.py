# src/helpers/keyboards.py

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .utils import escape_html, format_bytes, format_time
import math

# Nota: Los objetos de teclado se construyen directamente creando listas de listas de botones.

def build_profiles_keyboard(task_id: str, presets: list) -> InlineKeyboardMarkup:
    """Construye el teclado para seleccionar un perfil o ir a configuración manual."""
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
    
    keyboard.append([InlineKeyboardButton("🛠️ Configuración Manual", callback_data=f"task_manual_config_{task_id}")])
    return InlineKeyboardMarkup(keyboard)

def build_processing_menu(task_id: str, file_type: str, task_data: dict) -> InlineKeyboardMarkup:
    """Construye el menú principal de procesamiento para una tarea."""
    keyboard = []
    task_config = task_data.get('processing_config', {})
    
    if task_data.get('url_info') and not task_config.get('download_format_id'):
         keyboard.append([InlineKeyboardButton("💿 Elegir Calidad de Descarga", callback_data=f"config_dlquality_{task_id}")])

    if file_type == 'video':
        mute_text = "🔇 Silenciar" if not task_config.get('mute_audio') else "🔊 Desilenciar"
        transcode_text = f"📉 Transcodificar ({task_config.get('transcode', {}).get('resolution', 'No')})"
        keyboard.extend([
            [InlineKeyboardButton(transcode_text, callback_data=f"config_transcode_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"), InlineKeyboardButton("🧩 Dividir", callback_data=f"config_split_{task_id}")],
            [InlineKeyboardButton("🎞️ a GIF", callback_data=f"config_gif_{task_id}"), InlineKeyboardButton("💧 Marca de Agua", callback_data=f"config_watermark_{task_id}")],
            [InlineKeyboardButton("🖼️ Miniatura", callback_data=f"config_thumbnail_{task_id}")],
            [InlineKeyboardButton("📜 Pistas", callback_data=f"config_tracks_{task_id}")],
            [InlineKeyboardButton(mute_text, callback_data=f"set_mute_{task_id}_toggle")],
        ])
    elif file_type == 'audio':
        bitrate = task_config.get('audio_bitrate', '192k')
        audio_format = task_config.get('audio_format', 'mp3')
        keyboard.extend([
            [InlineKeyboardButton(f"🔊 Convertir ({audio_format.upper()}, {bitrate})", callback_data=f"config_audioconvert_{task_id}")],
            [InlineKeyboardButton("🎧 Efectos", callback_data=f"config_audioeffects_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}")],
            [InlineKeyboardButton("🖼️ Editar Metadatos", callback_data=f"config_audiometadata_{task_id}")],
        ])

    keyboard.extend([
        [InlineKeyboardButton("✏️ Renombrar", callback_data=f"config_rename_{task_id}")],
        [InlineKeyboardButton("💾 Guardar como Perfil", callback_data=f"profile_save_request_{task_id}")],
        [InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show_text"), InlineKeyboardButton("🔥 Procesar Ahora", callback_data=f"task_queuesingle_{task_id}")]
    ])
    
    return InlineKeyboardMarkup(keyboard)

def build_transcode_menu(task_id: str) -> InlineKeyboardMarkup:
    """Construye el menú para seleccionar la resolución de transcodificación."""
    keyboard = [
        [
            InlineKeyboardButton("1080p", callback_data=f"set_transcode_{task_id}_resolution_1080p"),
            InlineKeyboardButton("720p", callback_data=f"set_transcode_{task_id}_resolution_720p")
        ],
        [
            InlineKeyboardButton("480p", callback_data=f"set_transcode_{task_id}_resolution_480p"),
            InlineKeyboardButton("360p", callback_data=f"set_transcode_{task_id}_resolution_360p")
        ],
        [
            InlineKeyboardButton("240p", callback_data=f"set_transcode_{task_id}_resolution_240p"),
            InlineKeyboardButton("144p", callback_data=f"set_transcode_{task_id}_resolution_144p")
        ],
        [InlineKeyboardButton("❌ Quitar Transcodificación", callback_data=f"set_transcode_{task_id}_remove_all")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_tracks_menu(task_id: str, config: dict) -> InlineKeyboardMarkup:
    """Construye el menú para manipular pistas de audio y subtítulos."""
    remove_subs_text = "✅ Quitar Subtítulos" if config.get('remove_subtitles') else "❌ Quitar Subtítulos"
    keyboard = [
        [InlineKeyboardButton(remove_subs_text, callback_data=f"set_trackopt_{task_id}_remove_subtitles_toggle")],
        [InlineKeyboardButton("➕ Añadir Subtítulos (.srt)", callback_data=f"config_addsubs_{task_id}")],
        [InlineKeyboardButton("🎵 Extraer Pista de Audio", callback_data=f"config_extract_audio_{task_id}")],
        [InlineKeyboardButton("🎼 Reemplazar Pista de Audio", callback_data=f"config_replace_audio_{task_id}")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_detailed_format_menu(task_id: str, formats: list) -> InlineKeyboardMarkup:
    keyboard, row = [], []
    video_formats = sorted([f for f in formats if f.get('vcodec') not in ['none', None] and f.get('height')], key=lambda x: (x.get('height', 0), x.get('fps', 0) or 0), reverse=True)
    
    for f in video_formats:
        if not (format_id := f.get('format_id')): continue
        height, fps, ext, filesize = f.get('height'), int(f.get('fps', 0)), f.get('ext'), f.get('filesize')
        fps_str = f"p{fps}" if fps > 0 else "p"
        label = f"🎬 {height}{fps_str} {ext.upper()}" + (f" ({format_bytes(filesize)})" if filesize else "")
        row.append(InlineKeyboardButton(label, callback_data=f"set_dlformat_{task_id}_{format_id}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)
            
    keyboard.extend([
        [InlineKeyboardButton("🎵 MP3", callback_data=f"set_dlformat_{task_id}_mp3"), InlineKeyboardButton("🔊 Mejor Audio", callback_data=f"set_dlformat_{task_id}_bestaudio")],
        [InlineKeyboardButton("🏆 Mejor Video", callback_data=f"set_dlformat_{task_id}_bestvideo"), InlineKeyboardButton("❌ Cancelar", callback_data=f"task_delete_{task_id}")]
    ])
    return InlineKeyboardMarkup(keyboard)

def build_search_results_keyboard(all_results: list, search_id: str, page: int = 1, page_size: int = 5) -> InlineKeyboardMarkup:
    keyboard = []
    start_index, end_index = (page - 1) * page_size, page * page_size
    paginated_results = all_results[start_index:end_index]
    total_pages = math.ceil(len(all_results) / page_size)

    for res in paginated_results:
        res_id = str(res['_id'])
        duration = format_time(res.get('duration'))
        title = res.get('title', '...')
        artist = res.get('artist', '...')
        display_text = f"🎵 {title} - {artist} ({duration})"
        short_text = (display_text[:60] + '...') if len(display_text) > 64 else display_text
        keyboard.append([InlineKeyboardButton(short_text, callback_data=f"song_select_{res_id}")])
    
    nav_buttons = []
    if page > 1: nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"search_page_{search_id}_{page - 1}"))
    if total_pages > 1: nav_buttons.append(InlineKeyboardButton(f"Pág {page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav_buttons.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"search_page_{search_id}_{page + 1}"))
    if nav_buttons: keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("❌ Cancelar Búsqueda", callback_data=f"cancel_search_{search_id}")])
    return InlineKeyboardMarkup(keyboard)

def build_audio_convert_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("MP3", callback_data=f"set_audioprop_{task_id}_format_mp3"), InlineKeyboardButton("FLAC", callback_data=f"set_audioprop_{task_id}_format_flac"), InlineKeyboardButton("Opus", callback_data=f"set_audioprop_{task_id}_format_opus")],
        [InlineKeyboardButton("128k", callback_data=f"set_audioprop_{task_id}_bitrate_128k"), InlineKeyboardButton("192k", callback_data=f"set_audioprop_{task_id}_bitrate_192k"), InlineKeyboardButton("320k", callback_data=f"set_audioprop_{task_id}_bitrate_320k")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_audio_effects_menu(task_id: str, config: dict) -> InlineKeyboardMarkup:
    slowed, reverb = ("✅" if config.get('slowed') else "❌"), ("✅" if config.get('reverb') else "❌")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🐌 Slowed {slowed}", callback_data=f"set_audioeffect_{task_id}_slowed_toggle")],
        [InlineKeyboardButton(f"🌌 Reverb {reverb}", callback_data=f"set_audioeffect_{task_id}_reverb_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_audio_metadata_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Editar Texto (Título, Artista...)", callback_data=f"config_audiotags_{task_id}")],
        [InlineKeyboardButton("🖼️ Añadir/Cambiar Carátula (Audio)", callback_data=f"config_audiothumb_{task_id}")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_watermark_menu(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Añadir Imagen", callback_data=f"set_watermark_image_{task_id}")],
        [InlineKeyboardButton("✏️ Añadir Texto", callback_data=f"set_watermark_text_{task_id}")],
        [InlineKeyboardButton("❌ Quitar Marca de Agua", callback_data=f"set_watermark_remove_{task_id}")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_position_menu(task_id: str, origin_menu: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖️ Sup. Izq.", callback_data=f"set_watermark_position_{task_id}_top_left")],
        [InlineKeyboardButton("↗️ Sup. Der.", callback_data=f"set_watermark_position_{task_id}_top_right")],
        [InlineKeyboardButton("↙️ Inf. Izq.", callback_data=f"set_watermark_position_{task_id}_bottom_left")],
        [InlineKeyboardButton("↘️ Inf. Der.", callback_data=f"set_watermark_position_{task_id}_bottom_right")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"{origin_menu}_{task_id}")]
    ])
    
def build_thumbnail_menu(task_id: str, config: dict) -> InlineKeyboardMarkup:
    """Construye el menú para gestionar la miniatura de un video."""
    extract_text = "✅ Extraer Miniatura" if config.get('extract_thumbnail') else "❌ Extraer Miniatura"
    remove_text = "✅ Quitar Miniatura" if config.get('remove_thumbnail') else "❌ Quitar Miniatura"
    
    keyboard = [
        [InlineKeyboardButton("🖼️ Añadir/Cambiar Miniatura", callback_data=f"config_thumbnail_add_{task_id}")],
        [InlineKeyboardButton(extract_text, callback_data=f"set_thumb_op_{task_id}_extract_toggle")],
        [InlineKeyboardButton(remove_text, callback_data=f"set_thumb_op_{task_id}_remove_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_batch_profiles_keyboard(presets: list) -> InlineKeyboardMarkup:
    """Construye el teclado para seleccionar un perfil para una acción en lote."""
    keyboard = []
    row = []
    for preset in presets:
        preset_id = str(preset['_id'])
        preset_name = preset.get('preset_name', 'Perfil sin nombre').capitalize()
        row.append(InlineKeyboardButton(f" профиль: {preset_name}", callback_data=f"batch_apply_profile_{preset_id}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("⚙️ Usar Config. Default", callback_data="batch_apply_default")])
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="batch_cancel")])
    return InlineKeyboardMarkup(keyboard)

# --- NUEVA FUNCIÓN ---
def build_join_selection_keyboard(tasks: list, selected_ids: list) -> InlineKeyboardMarkup:
    """Construye el teclado interactivo para seleccionar videos a unir."""
    keyboard = []
    row = []
    for task in tasks:
        task_id = str(task['_id'])
        filename = task.get('original_filename', 'Video sin nombre')
        short_name = (filename[:50] + '...') if len(filename) > 53 else filename
        
        prefix = "✅ " if task_id in selected_ids else "🎬 "
        button_text = f"{prefix}{escape_html(short_name)}"
        
        row.append(InlineKeyboardButton(button_text, callback_data=f"join_select_{task_id}"))
        if len(row) == 1: # Un botón por fila para mayor claridad
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    action_row = []
    if selected_ids:
        action_row.append(InlineKeyboardButton("✅ Unir Videos Seleccionados", callback_data="join_confirm"))
    action_row.append(InlineKeyboardButton("❌ Cancelar", callback_data="join_cancel"))
    keyboard.append(action_row)

    return InlineKeyboardMarkup(keyboard)

def build_confirmation_keyboard(action_callback: str, cancel_callback: str) -> InlineKeyboardMarkup:
    """Crea un teclado de confirmación genérico (Sí/No)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sí, proceder", callback_data=action_callback),
            InlineKeyboardButton("❌ No, cancelar", callback_data=cancel_callback)
        ]
    ])

def build_back_button(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data=callback_data)]])