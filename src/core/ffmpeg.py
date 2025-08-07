# --- START OF FILE src/core/ffmpeg.py ---

import asyncio
import logging
import subprocess
import shlex
import os
import re
import json

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    if not os.path.exists(file_path):
        logger.error(f"ffprobe no puede encontrar el archivo: {file_path}")
        return {}
        
    command = [
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "default=noprint_wrappers=1", "-print_format", "json", shlex.quote(file_path)
    ]
    try:
        process = subprocess.Popen(shlex.join(command), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
        stdout, stderr = process.communicate(timeout=60)
        
        if process.returncode != 0:
            logger.error(f"ffprobe falló para {file_path}. Error: {stderr.strip()}")
            return {}
            
        return json.loads(stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path} (JSON o genérico): {e}")
        return {}

def build_ffmpeg_command(task: dict, input_path: str, output_path: str, thumbnail_path: str = None, watermark_path: str = None) -> list:
    """
    Construye el comando FFmpeg, ahora con soporte para marcas de agua dinámicas y metadatos de audio.
    """
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    if 'gif_options' in config: return build_gif_command(config, input_path, output_path)
    if 'split_criteria' in config: return [build_split_command(config, input_path, output_path)]

    command_parts = ["ffmpeg", "-y"]
    
    if 'trim_times' in config:
        start, _, end = config['trim_times'].partition('-')
        if start.strip(): command_parts.append(f"-ss {shlex.quote(start.strip())}")
        if end.strip(): command_parts.append(f"-to {shlex.quote(end.strip())}")
    
    command_parts.append(f"-i {shlex.quote(input_path)}")
    input_count = 1
    
    # El thumbnail ahora puede ser para audio (carátula) o video (miniatura)
    if thumbnail_path:
        command_parts.append(f"-i {shlex.quote(thumbnail_path)}")
        thumb_map_idx = input_count
        input_count += 1

    if watermark_path:
        command_parts.append(f"-i {shlex.quote(watermark_path)}")
        watermark_map_idx = input_count
        input_count += 1

    codec_opts = []
    map_opts = []
    metadata_opts = []
    
    watermark_config = config.get('watermark')
    filter_complex_parts = []
    
    # Manejo de Marca de Agua
    if file_type == 'video' and watermark_config:
        position = watermark_config.get('position', 'top_right')
        
        if watermark_config.get('type') == 'text':
            text = watermark_config.get('text', '').replace("'", "’").replace(':', r'\:')
            pos_map = {
                'top_left': 'x=10:y=10',
                'top_right': 'x=w-text_w-10:y=10',
                'bottom_left': 'x=10:y=h-text_h-10',
                'bottom_right': 'x=w-text_w-10:y=h-text_h-10'
            }
            drawtext_filter = f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{pos_map.get(position)}"
            filter_complex_parts.append(f"[0:v]{drawtext_filter}[outv]")
        
        elif watermark_config.get('type') == 'image' and watermark_path:
            pos_map = {
                'top_left': '10:10',
                'top_right': 'W-w-10:10',
                'bottom_left': '10:H-h-10',
                'bottom_right': 'W-w-10:H-h-10'
            }
            overlay_filter = f"[0:v][{watermark_map_idx}:v]overlay={pos_map.get(position)}[outv]"
            filter_complex_parts.append(overlay_filter)
    
    if filter_complex_parts:
        command_parts.append(f'-filter_complex "{";".join(filter_complex_parts)}"')
        map_opts.append("-map [outv]")
    else:
        map_opts.append("-map 0:v?")

    # Manejo de Audio y Códecs
    if file_type == 'audio':
        fmt = config.get('audio_format', 'mp3')
        bitrate = config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        
        audio_filters = []
        if config.get('slowed'): audio_filters.append("atempo=0.8")
        if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")

        if audio_filters:
            codec_opts.append(f"-af {shlex.quote(','.join(audio_filters))}")
        
        codec_opts.append("-vn") # Quitar cualquier stream de video original
        codec_opts.append(f"-c:a {codec_map.get(fmt, 'libmp3lame')}")
        if fmt != 'flac': codec_opts.append(f"-b:a {bitrate}")
        map_opts.append("-map 0:a:0")
        
        # --- NUEVA LÓGICA: Metadatos y Carátula para Audio ---
        if 'audio_tags' in config:
            tags = config['audio_tags']
            if 'title' in tags: metadata_opts.append(f"-metadata title={shlex.quote(tags['title'])}")
            if 'artist' in tags: metadata_opts.append(f"-metadata artist={shlex.quote(tags['artist'])}")
            if 'album' in tags: metadata_opts.append(f"-metadata album={shlex.quote(tags['album'])}")
        
        if thumbnail_path:
            map_opts.append(f"-map {thumb_map_idx}")
            codec_opts.append("-c:v copy") # Copiar el stream de imagen tal cual
            codec_opts.append("-disposition:v:0 attached_pic") # Designarla como carátula
    
    elif file_type == 'video':
        codec_opts.append("-c:a copy")
        map_opts.append("-map 0:a?")
        # --- LÓGICA ANTERIOR DE THUMBNAIL PARA VIDEO ---
        # (Esto estaba mal, se corrige para que la miniatura se adjunte al contenedor, no como stream de subtítulos)
        if thumbnail_path:
            map_opts.append(f"-map {thumb_map_idx}")
            # Correcto: adjuntar como imagen (funciona en más reproductores)
            codec_opts.append("-c:v:1 mjpeg -disposition:v:1 attached_pic")

    if config.get('mute_audio'):
        codec_opts = [opt for opt in codec_opts if '-c:a' not in opt and '-b:a' not in opt]
        map_opts = [opt for opt in map_opts if '-map 0:a' not in opt]
        codec_opts.append("-an")

    command_parts.extend(codec_opts)
    command_parts.extend(metadata_opts) # Añadir los metadatos
    command_parts.extend(map_opts)
    command_parts.append(shlex.quote(output_path))
    
    final_command = " ".join(part for part in command_parts if part)
    logger.info(f"Comando FFmpeg construido: {final_command}")
    return [final_command]

def build_gif_command(config, input_path, output_path):
    gif_opts = config['gif_options']
    duration, fps = gif_opts['duration'], gif_opts['fps']
    palette_path = f"{output_path}.palette.png"
    filters = f"fps={fps},scale=480:-1:flags=lanczos"
    
    cmd1 = (f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} "
            f"-vf \"{filters},palettegen\" {shlex.quote(palette_path)}")
            
    base_name, _ = os.path.splitext(output_path)
    final_gif_path = f"{base_name}.gif"
    cmd2 = (f"ffmpeg -ss 0 -t {duration} -i {shlex.quote(input_path)} -i {shlex.quote(palette_path)} "
            f"-lavfi \"{filters} [x]; [x][1:v] paletteuse\" {shlex.quote(final_gif_path)}")
            
    return [cmd1, cmd2]

def build_split_command(config, input_path, output_path):
    criteria = config['split_criteria']
    base_name, ext = os.path.splitext(output_path)
    if 's' in criteria.lower():
        return (f"ffmpeg -y -i {shlex.quote(input_path)} -c copy -map 0 "
                f"-segment_time {criteria.lower().replace('s', '')} -f segment -reset_timestamps 1 {shlex.quote(base_name)}_part%03d{ext}")
    return ""
# --- END OF FILE src/core/ffmpeg.py ---