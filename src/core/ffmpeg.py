# src/core/ffmpeg.py
import asyncio
import logging
import subprocess
import shlex
import os
import re
import json

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    """
    Usa ffprobe para obtener información detallada de un archivo como un diccionario Python.
    Devuelve un diccionario vacío si falla.
    """
    if not os.path.exists(file_path):
        logger.error(f"ffprobe no puede encontrar el archivo: {file_path}")
        return {}
        
    command = [
        "ffprobe",
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "default=noprint_wrappers=1",
        "-print_format", "json",
        file_path
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
        stdout, stderr = process.communicate(timeout=60)
        
        if process.returncode != 0:
            logger.error(f"ffprobe falló para {file_path}. Error: {stderr.strip()}")
            return {}
            
        return json.loads(stdout)
    except subprocess.TimeoutExpired:
        logger.error(f"ffprobe timed out para {file_path}.")
        process.kill()
        return {}
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"No se pudo obtener info de {file_path} (JSON o genérico): {e}")
        return {}

def build_ffmpeg_command(task: dict, input_path: str, output_path: str) -> list:
    """
    Construye el comando FFmpeg completo basado en la configuración de la tarea.
    """
    config = task.get('processing_config', {})
    
    if 'gif_options' in config:
        return build_gif_command(config, input_path, output_path)
    if 'split_criteria' in config:
        return [build_split_command(config, input_path, output_path)]

    command_parts = ["ffmpeg", "-y"]
    
    if 'trim_times' in config:
        start, _, end = config['trim_times'].partition('-')
        if start.strip(): command_parts.append(f"-ss {shlex.quote(start.strip())}")
        if end.strip(): command_parts.append(f"-to {shlex.quote(end.strip())}")
    
    command_parts.append(f"-i {shlex.quote(input_path)}")
    
    video_codec_options = []
    audio_codec_options = []
    subtitle_codec_options = []
    
    force_recode_video = False
    force_recode_audio = False
    
    input_ext = os.path.splitext(input_path)[1].lower()
    output_ext = os.path.splitext(output_path)[1].lower()
    
    if 'quality' in config and config['quality'] != 'Original':
        force_recode_video = True
        force_recode_audio = True

    if input_ext != output_ext:
        logger.info(f"Cambio de contenedor detectado ({input_ext} -> {output_ext}). Forzando recodificación.")
        if task.get('file_type') == 'video': force_recode_video = True
        force_recode_audio = True
            
    # Asignar códecs basados en si se necesita recodificar o no
    if task.get('file_type') == 'video':
        if force_recode_video:
            profile_map = {'1080p': '22', '720p': '24', '480p': '28', '360p': '32'}
            crf = profile_map.get(config.get('quality', '720p'), '24')
            video_codec_options.extend(["-c:v libx264", "-preset veryfast", f"-crf {crf}", "-pix_fmt yuv420p"])
        else:
            video_codec_options.append("-c:v copy")

        if force_recode_audio:
            audio_codec_options.extend(["-c:a aac", "-b:a 192k"])
        else:
            audio_codec_options.append("-c:a copy")
            
        subtitle_codec_options.append("-c:s mov_text" if output_ext == '.mp4' else "-c:s copy")

    elif task.get('file_type') == 'audio':
        fmt = config.get('audio_format', 'mp3')
        bitrate = config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        audio_codec_options.append(f"-c:a {codec_map.get(fmt, 'libmp3lame')}")
        if fmt != 'flac': audio_codec_options.append(f"-b:a {bitrate}")

    # Aplicar al comando final
    command_parts.extend(video_codec_options)
    command_parts.extend(audio_codec_options)
    command_parts.extend(subtitle_codec_options)

    # Mapear todos los streams del archivo de entrada
    command_parts.append("-map 0")
    
    command_parts.append(shlex.quote(output_path))
    
    final_command = " ".join(part for part in command_parts if part)
    logger.info(f"Comando FFmpeg construido: {final_command}")
    return [final_command]

def build_gif_command(config, input_path, output_path):
    gif_opts = config['gif_options']
    duration, fps = gif_opts['duration'], gif_opts['fps']
    palette_path = f"{output_path}.palette.png"
    filters = f"fps={fps},scale=480:-1:flags=lanczos"
    cmd1 = (f"ffmpeg -y -i {shlex.quote(input_path)} -t {duration} -vf \"{filters},palettegen\" {shlex.quote(palette_path)}")
    cmd2 = (f"ffmpeg -i {shlex.quote(input_path)} -i {shlex.quote(palette_path)} -t {duration} "
            f"-lavfi \"{filters} [x]; [x][1:v] paletteuse\" {shlex.quote(output_path.replace('.mkv', '.gif').replace('.mp4', '.gif'))}")
    return [cmd1, cmd2]

def build_split_command(config, input_path, output_path):
    criteria = config['split_criteria']
    base_name, ext = os.path.splitext(output_path)
    if 's' in criteria.lower():
        return (f"ffmpeg -y -i {shlex.quote(input_path)} -c copy -map 0 "
                f"-segment_time {criteria.lower().replace('s', '')} -f segment -reset_timestamps 1 {shlex.quote(base_name)}_part%03d{ext}")
    return ""