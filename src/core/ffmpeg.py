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

def build_ffmpeg_command(task: dict, input_path: str, output_path: str, thumbnail_path: str = None) -> list:
    """
    Construye el comando FFmpeg de forma más segura, basándose en el tipo de archivo real.
    Devuelve una lista de comandos para soportar procesos de múltiples pasos.
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
    if thumbnail_path:
        command_parts.append(f"-i {shlex.quote(thumbnail_path)}")

    codec_opts = []
    map_opts = ["-map 0"]
    if thumbnail_path:
        map_opts.append("-map 1")
        codec_opts.append("-c:v:1 mjpeg -disposition:v:1 attached_pic")


    if file_type == 'audio':
        fmt = config.get('audio_format', 'mp3')
        bitrate = config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        
        codec_opts.append("-vn")
        
        codec_opts.append(f"-c:a {codec_map.get(fmt, 'libmp3lame')}")
        if fmt != 'flac': codec_opts.append(f"-b:a {bitrate}")
        
        map_opts[0] = "-map 0:a:0"

    elif file_type == 'video':
        if config.get('quality') and config['quality'] != 'Original':
            profile_map = {'1080p': '22', '720p': '24', '480p': '28', '360p': '32'}
            crf = profile_map.get(config.get('quality'), '24')
            codec_opts.extend(["-c:v:0 libx264", "-preset veryfast", f"-crf {crf}", "-pix_fmt yuv420p"])
            codec_opts.extend(["-c:a copy"])
        else:
            codec_opts.extend(["-c:v:0 copy", "-c:a copy", "-c:s copy"])
        
        output_ext = os.path.splitext(output_path)[1].lower()
        if output_ext == '.mp4':
            codec_opts.append("-c:s mov_text")

    else: # Documentos
        return []

    if config.get('mute_audio'):
        codec_opts = [opt for opt in codec_opts if '-c:a' not in opt and '-b:a' not in opt]
        codec_opts.append("-an")

    command_parts.extend(codec_opts)
    command_parts.extend(map_opts)
    command_parts.append(shlex.quote(output_path))
    
    final_command = " ".join(part for part in command_parts if part)
    logger.info(f"Comando FFmpeg construido: {final_command}")
    return [final_command]

def build_gif_command(config, input_path, output_path):
    """Construye los dos comandos necesarios para un GIF de alta calidad."""
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