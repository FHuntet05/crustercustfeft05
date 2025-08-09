# --- START OF FILE src/core/ffmpeg.py ---

import logging
import shlex
import os
import json
import subprocess
from typing import List, Tuple, Dict

logger = logging.getLogger(__name__)

def sanitize_drawtext(text: str) -> str:
    """
    Escapes special characters in a string for use with FFmpeg's drawtext filter.
    """
    if not isinstance(text, str):
        return ''
    escape_chars = r"\'%:"
    sanitized = text.replace('\\', '\\\\')
    for char in escape_chars:
        sanitized = sanitized.replace(char, f'\\{char}')
    return sanitized

def get_media_info(file_path: str) -> dict:
    if not os.path.exists(file_path):
        return {}
    
    # [DEFINITIVE FIX - shlex]
    # No usamos shlex.quote aquí. Pasamos la ruta directamente.
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", file_path]
    try:
        # Usamos shlex.join para un logging seguro del comando.
        logger.info(f"Ejecutando ffprobe: {shlex.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return {}

def build_command_for_task(task: Dict, input_path: str, output_path_base: str, watermark_path: str = None) -> Tuple[List[str], str]:
    config = task.get('processing_config', {})

    if config.get('extract_audio'):
        return _build_extract_audio_command(input_path, output_path_base)
    if 'gif_options' in config:
        return _build_gif_command(config, input_path, output_path_base)
    
    return _build_standard_ffmpeg_command(task, input_path, output_path_base, watermark_path)

# [DEFINITIVE FIX - shlex]
# Reescritura completa de la construcción de comandos para evitar el doble escapado.
# shlex.quote() ha sido eliminado. Los argumentos se añaden directamente a la lista `command`.
# shlex.join() se usa al final solo para crear una representación segura del comando para el log.
def _build_standard_ffmpeg_command(task: Dict, input_path: str, output_path_base: str, watermark_path: str = None) -> Tuple[List[str], str]:
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    if file_type == 'audio':
        ext = f".{config.get('audio_format', 'mp3')}"
    else:
        ext = ".mp4"
    final_output_path = f"{output_path_base}{ext}"

    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", input_path]
    
    filter_complex_parts = []
    video_filters = []
    video_chain = "[0:v]"

    if file_type == 'video':
        if transcode_config := config.get('transcode'):
            if res := transcode_config.get('resolution'):
                video_filters.append(f"scale=-2:{res.replace('p', '')}")
        
        if watermark_config := config.get('watermark'):
            if watermark_config.get('type') == 'text':
                text = sanitize_drawtext(watermark_config.get('text', ''))
                pos_map = {'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10', 'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'}
                video_filters.append(f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{pos_map.get(watermark_config.get('position', 'top_right'))}")

    if video_filters:
        filter_complex_parts.append(f"{video_chain}{','.join(video_filters)}[v_filtered]")
        video_chain = "[v_filtered]"

    if watermark_path and config.get('watermark', {}).get('type') == 'image':
        command.extend(["-i", watermark_path])
        pos_map = {'top_left': '10:10', 'top_right': 'W-w-10:10', 'bottom_left': '10:H-h-10', 'bottom_right': 'W-w-10:H-h-10'}
        filter_complex_parts.append(f"{video_chain}[1:v]overlay={pos_map.get(config['watermark'].get('position', 'top_right'))}[v_watermarked]")
        video_chain = "[v_watermarked]"

    if filter_complex_parts:
        command.extend(["-filter_complex", ";".join(filter_complex_parts)])

    if file_type == 'video':
        command.extend(["-map", video_chain])
        if not config.get('mute_audio'):
            command.extend(["-map", "0:a?"])
        
        if config.get('transcode'):
            command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"])
        else:
            command.extend(["-c:v", "copy"])
        
        if not config.get('mute_audio'):
            command.extend(["-c:a", "aac", "-b:a", "128k"])
        else:
            command.append("-an")
    
    elif file_type == 'audio':
        command.extend(["-map", "0:a?"])
        fmt, bitrate = config.get('audio_format', 'mp3'), config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        command.extend(["-c:a", codec_map.get(fmt, 'libmp3lame')])
        if fmt != 'flac':
            command.extend(["-b:a", bitrate])
        command.append("-vn")

    command.append(final_output_path)
    
    # Unimos la lista en un solo string de comando para el shell.
    # shlex.join se encarga del escapado correcto de cada argumento.
    final_command_str = shlex.join(command)
    logger.info(f"Comando FFmpeg construido: {final_command_str}")
    return [final_command_str], final_output_path

def _build_extract_audio_command(input_path: str, output_path_base: str) -> Tuple[List[str], str]:
    media_info = get_media_info(input_path)
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    ext = ".m4a"
    if audio_stream and (codec_name := audio_stream.get('codec_name')):
        ext = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}.get(codec_name, '.m4a')
    
    final_output_path = f"{output_path_base}{ext}"
    command = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "copy", final_output_path]
    return [shlex.join(command)], final_output_path

def _build_gif_command(config: dict, input_path: str, output_path: str) -> Tuple[List[str], str]:
    gif_opts = config['gif_options']
    duration, fps = gif_opts.get('duration', 5), gif_opts.get('fps', 15)
    
    final_output_path = f"{os.path.splitext(output_path)[0]}.gif"
    palette_path = f"{os.path.splitext(output_path)[0]}.palette.png"

    filters = f"fps={fps},scale=480:-1:flags=lanczos"

    cmd1_list = ["ffmpeg", "-y", "-ss", "0", "-t", str(duration), "-i", input_path, "-vf", f"{filters},palettegen", "-y", palette_path]
    cmd2_list = ["ffmpeg", "-y", "-ss", "0", "-t", str(duration), "-i", input_path, "-i", palette_path, "-lavfi", f"{filters} [x]; [x][1:v] paletteuse", "-y", final_output_path]
    
    return [shlex.join(cmd1_list), shlex.join(cmd2_list)], final_output_path