# --- START OF FILE src/helpers/keyboards.py ---

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import math
from typing import List, Dict

from .utils import escape_html, format_bytes, format_time

# --- Teclado para Volver (Componente Reutilizable) ---
def build_back_button(callback_data: str) -> InlineKeyboardMarkup:
    """Construye un teclado simple con un solo botón de 'Volver'."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data=callback_data)]])

# --- Teclados de Gestión de Perfiles (Presets) ---

def build_profiles_keyboard(task_id: str, presets: List[Dict]) -> InlineKeyboardMarkup:
    """Construye el teclado para aplicar un perfil a una tarea específica."""
    keyboard, row = [], []
    for preset in presets:
        preset_id, preset_name = str(preset['_id']), preset.get('preset_name', '...').capitalize()
        row.append(InlineKeyboardButton(f"⚙️ {preset_name}", callback_data=f"profile_apply_{task_id}_{preset_id}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🛠️ Abrir Configuración Manual", callback_data=f"p_open_{task_id}")])
    return InlineKeyboardMarkup(keyboard)

def build_profiles_management_keyboard(presets: List[Dict]) -> InlineKeyboardMarkup:
    keyboard = []
    if not presets:
        keyboard.append([InlineKeyboardButton("No tienes perfiles guardados.", callback_data="noop")])
    else:
        for preset in presets:
            preset_id, preset_name = str(preset['_id']), preset.get('preset_name', '...').capitalize()
            keyboard.append([InlineKeyboardButton(f"🗑️ Eliminar '{preset_name}'", callback_data=f"profile_delete_req_{preset_id}")])
    keyboard.append([InlineKeyboardButton("❌ Cerrar", callback_data="profile_close")])
    return InlineKeyboardMarkup(keyboard)

def build_profile_delete_confirmation_keyboard(preset_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"profile_delete_confirm_{preset_id}"),
        InlineKeyboardButton("❌ No, cancelar", callback_data="profile_open_main")
    ]])

# --- Teclado Principal de Procesamiento ---

def build_quality_menu(task_id: str) -> InlineKeyboardMarkup:
    """Construye el menú de selección de calidad"""
    keyboard = [
        [InlineKeyboardButton("🎬 4K (2160p)", callback_data=f"quality_{task_id}_2160")],
        [InlineKeyboardButton("🎥 FHD (1080p)", callback_data=f"quality_{task_id}_1080")],
        [InlineKeyboardButton("📺 HD (720p)", callback_data=f"quality_{task_id}_720")],
        [InlineKeyboardButton("📱 SD (480p)", callback_data=f"quality_{task_id}_480")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"menu_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_processing_menu(task_id: str, file_type: str, task_data: Dict) -> InlineKeyboardMarkup:
    keyboard, config = [], task_data.get('processing_config', {})
    
    # Sección de Compresión/Calidad para videos
    if file_type == 'video':
        quality = config.get('quality', '1080p')
        mute_text = "🔇 Silenciar" if not config.get('mute_audio') else "🔊 Restaurar Audio"
        
        keyboard.extend([
            # Calidad y Compresión
            [InlineKeyboardButton(f"{quality_emoji} Tipo: {quality.title()}", callback_data=f"set_content_type_{task_id}")],
            [InlineKeyboardButton(f"📉 Calidad: {transcode_res}", callback_data=f"config_transcode_{task_id}")],
            # Herramientas de Video
            [
                InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"),
                InlineKeyboardButton("🎞️ GIF", callback_data=f"config_gif_{task_id}"),
                InlineKeyboardButton(mute_text, callback_data=f"set_mute_{task_id}_toggle")
            ],
            # Personalización
            [
                InlineKeyboardButton("💧 Marca Agua", callback_data=f"config_watermark_{task_id}"),
                InlineKeyboardButton("🖼️ Miniatura", callback_data=f"config_thumbnail_{task_id}")
            ],
            # Pistas y Subtítulos
            [InlineKeyboardButton("📜 Pistas (Audio/Subs)", callback_data=f"config_tracks_{task_id}")]
        ])
    elif file_type == 'audio':
        keyboard.extend([
            [
                InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"),
                InlineKeyboardButton("📝 Metadatos", callback_data=f"config_audiometadata_{task_id}")
            ],
        ])
        
    # Controles Comunes (para todos los tipos)
    keyboard.extend([
        # Opciones de Archivo
        [
            InlineKeyboardButton("✏️ Renombrar", callback_data=f"config_rename_{task_id}"),
            InlineKeyboardButton("💾 Guardar Perfil", callback_data=f"profile_save_request_{task_id}")
        ],
    ])
    
    # Botones de Acción Principal
    keyboard.append([
        InlineKeyboardButton("❌ Cancelar", callback_data=f"task_delete_{task_id}"),
        InlineKeyboardButton("⚡️ Procesar", callback_data=f"task_queuesingle_{task_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)
    
def build_cancel_button(task_id: str) -> InlineKeyboardMarkup:
    """Construye un teclado con solo el botón de cancelar"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancelar Proceso", callback_data=f"cancel_task_{task_id}")
    ]])

# --- Sub-menús de Configuración ---

def build_transcode_menu(task_id: str) -> InlineKeyboardMarkup:
    resolutions = ["1080p", "720p", "480p", "360p", "240p", "144p"]
    keyboard = [[InlineKeyboardButton(res, callback_data=f"set_transcode_{task_id}_resolution_{res}")] for res in resolutions]
    keyboard.append([InlineKeyboardButton("❌ Mantener Resolución Original", callback_data=f"set_transcode_{task_id}_remove_all")])
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")])
    return InlineKeyboardMarkup(keyboard)

def build_tracks_menu(task_id: str, config: Dict) -> InlineKeyboardMarkup:
    remove_subs_text = f"{'✅' if config.get('remove_subtitles') else '❌'} Quitar Subtítulos Incrustados"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Extraer Pista de Audio", callback_data=f"config_extract_audio_{task_id}")],
        [InlineKeyboardButton("🎼 Reemplazar Audio", callback_data=f"config_replace_audio_{task_id}")],
        [InlineKeyboardButton(remove_subs_text, callback_data=f"set_trackopt_{task_id}_remove_subtitles_toggle")],
        # [IMPLEMENTACIÓN] Se habilita el botón para incrustar subtítulos.
        [InlineKeyboardButton("➕ Incrustar Subtítulos (.srt)", callback_data=f"config_addsubs_{task_id}")],
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
        [InlineKeyboardButton(f"{'✅' if config.get('remove_thumbnail') else '❌'} Eliminar Miniatura Existente", callback_data=f"set_thumb_op_{task_id}_remove_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"p_open_{task_id}")]
    ])

def build_detailed_format_menu(url_info_id: str = None, formats: List[Dict] = None) -> InlineKeyboardMarkup:
    """
    Construye el menú de formatos detallados.
    Si no se proporcionan parámetros, devuelve un menú básico para videos.
    """
    keyboard = []
    
    if url_info_id and formats:
        # Menú detallado para descarga de videos
        video_formats = sorted([f for f in formats if f.get('vcodec', 'none') != 'none' and f.get('height')], 
                             key=lambda x: x['height'], reverse=True)
        for f in video_formats[:6]:
            label = f"🎬 {f['height']}p ({f['ext']})"
            if fsize := f.get('filesize'): label += f" - {format_bytes(fsize)}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"set_dlformat_{url_info_id}_{f['format_id']}")])
        keyboard.extend([
            [InlineKeyboardButton("🎵 Mejor Audio (MP3)", callback_data=f"set_dlformat_{url_info_id}_mp3")],
            [InlineKeyboardButton("🏆 Mejor Calidad General", callback_data=f"set_dlformat_{url_info_id}_best")],
        ])
    else:
        # Menú básico para videos locales
        keyboard.extend([
            [InlineKeyboardButton("🎬 Procesar Video", callback_data="process_video")],
            [InlineKeyboardButton("✂️ Cortar Video", callback_data="trim_video")],
            [InlineKeyboardButton("🎵 Extraer Audio", callback_data="extract_audio")],
        ])
    
    # Botón de cancelar común para ambos casos
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancel_operation")])
    return InlineKeyboardMarkup(keyboard)

def build_search_results_keyboard(all_results: List[Dict], search_id: str, page: int = 1, page_size: int = 5) -> InlineKeyboardMarkup:
    keyboard, start_index = [], (page - 1) * page_size
    paginated_results = all_results[start_index : start_index + page_size]
    total_pages = math.ceil(len(all_results) / page_size)
    for res in paginated_results:
        title, artist = res.get('title', '...')[:30], res.get('artist', '...')[:20]
        display_text = f"🎵 {title} - {artist}"
        if duration := res.get('duration'): display_text += f" ({format_time(duration)})"
        keyboard.append([InlineKeyboardButton(display_text, callback_data=f"song_select_{res['_id']}")])
    nav_row = []
    if page > 1: nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"search_page_{search_id}_{page - 1}"))
    if total_pages > 1: nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav_row.append(InlineKeyboardButton("➡️", callback_data=f"search_page_{search_id}_{page + 1}"))
    if nav_row: keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("❌ Cancelar Búsqueda", callback_data=f"cancel_search_session")])
    return InlineKeyboardMarkup(keyboard)

def build_batch_profiles_keyboard(presets: List[Dict]) -> InlineKeyboardMarkup:
    k, r = [], []
    for preset in presets:
        pid, name = str(preset['_id']), preset.get('preset_name', '...').capitalize()
        r.append(InlineKeyboardButton(f"⚙️ {name}", callback_data=f"batch_apply_{pid}"))
        if len(r) == 2: k.append(r); r = []
    if r: k.append(r)
    k.extend([
        [InlineKeyboardButton("⚙️ Sin Perfil (Default)", callback_data="batch_apply_default")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="batch_cancel")]
    ])
    return InlineKeyboardMarkup(k)

def build_join_selection_keyboard(tasks: List[Dict], selected_ids: List[str]) -> InlineKeyboardMarkup:
    k = []
    for task in tasks:
        tid, fname = str(task['_id']), (task.get('original_filename') or 'Video sin nombre')[:45]
        prefix = "✅ " if tid in selected_ids else "🎬 "
        k.append([InlineKeyboardButton(f"{prefix}{escape_html(fname)}", callback_data=f"join_select_{tid}")])
    actions = [InlineKeyboardButton("❌ Cancelar", callback_data="join_cancel")]
    if len(selected_ids) > 1:
        actions.insert(0, InlineKeyboardButton(f"🔗 Unir {len(selected_ids)} Videos", callback_data="join_confirm"))
    k.append(actions)
    return InlineKeyboardMarkup(k)

def build_zip_selection_keyboard(tasks: List[Dict], selected_ids: List[str]) -> InlineKeyboardMarkup:
    k, emoji_map = [], {'video': '🎬', 'audio': '🎵', 'document': '📄'}
    for task in tasks:
        tid, fname = str(task['_id']), (task.get('original_filename') or 'Archivo sin nombre')[:45]
        emoji, prefix = emoji_map.get(task.get('file_type'), '📦'), "✅ " if tid in selected_ids else f"{emoji} "
        k.append([InlineKeyboardButton(f"{prefix}{escape_html(fname)}", callback_data=f"zip_select_{tid}")])
    actions = [InlineKeyboardButton("❌ Cancelar", callback_data="zip_cancel")]
    if selected_ids:
        actions.insert(0, InlineKeyboardButton(f"🗜️ Comprimir {len(selected_ids)} Archivos", callback_data="zip_confirm"))
    k.append(actions)
    return InlineKeyboardMarkup(k)

def build_confirmation_keyboard(action_callback: str, cancel_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Sí, estoy seguro", callback_data=action_callback), 
        InlineKeyboardButton("❌ No, cancelar", callback_data=cancel_callback)
    ]])