import logging
import subprocess
import shlex
import os
import re
import json

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    # ... (esta función no cambia, es correcta)
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
    config = task.get('processing_config', {})
    
    # --- Comandos Especiales (sin cambios) ---
    if 'gif_options' in config:
        return build_gif_command(config, input_path, output_path)
    if 'split_criteria' in config:
        return [build_split_command(config, input_path, output_path)]

    # --- Flujo de Transcodificación Normal ---
    command_parts = ["ffmpeg", "-y"]
    
    # 1. Opciones de Input (sin cambios)
    if 'trim_times' in config:
        start, _, end = config['trim_times'].partition('-')
        if start.strip(): command_parts.append(f"-ss {shlex.quote(start.strip())}")
        if end.strip(): command_parts.append(f"-to {shlex.quote(end.strip())}")
    
    command_parts.append(f"-i {shlex.quote(input_path)}")
    
    # --- LÓGICA DE RECODIFICACIÓN MEJORADA ---
    video_codec_options = []
    audio_codec_options = []
    
    # Determinar si se necesita recodificar
    force_recode = False
    input_ext = os.path.splitext(input_path)[1].lower()
    output_ext = os.path.splitext(output_path)[1].lower()
    
    if input_ext != output_ext:
        logger.info(f"Cambio de contenedor detectado ({input_ext} -> {output_ext}). Forzando recodificación a códecs compatibles.")
        force_recode = True
        
    if 'quality' in config:
        force_recode = True
        
    # Aplicar códecs
    if task.get('file_type') == 'video':
        if force_recode:
            profile_map = {'1080p': '22', '720p': '24', '480p': '28', '360p': '32'}
            crf = profile_map.get(config.get('quality', '720p'), '24')
            video_codec_options.extend(["-c:v libx264", "-preset veryfast", f"-crf {crf}", "-pix_fmt yuv420p"])
            audio_codec_options.extend(["-c:a aac", "-b:a 192k"]) # Usar AAC para máxima compatibilidad con MP4
        else:
            video_codec_options.append("-c:v copy")
            audio_codec_options.append("-c:a copy")
    
    elif task.get('file_type') == 'audio':
        fmt = config.get('audio_format', 'mp3')
        bitrate = config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        audio_codec_options.append(f"-c:a {codec_map.get(fmt, 'libmp3lame')}")
        if fmt != 'flac': audio_codec_options.append(f"-b:a {bitrate}")

    command_parts.extend(video_codec_options)
    command_parts.extend(audio_codec_options)

    # 4. Mapeo de Pistas (sin cambios)
    map_options = ["-map 0:v?", "-map 0:a?", "-map 0:s?"]
    command_parts.extend(map_options)
    
    command_parts.append(shlex.quote(output_path))
    
    final_command = " ".join(part for part in command_parts if part)
    logger.info(f"Comando FFmpeg construido: {final_command}")
    return [final_command]

# --- El resto de funciones de build_... (gif, split, etc.) no necesitan cambios ---
def build_gif_command(config, input_path, output_path):
    gif_opts = config['gif_options']
    duration, fps = gif_opts['duration'], gif_opts['fps']
    palette_path = f"{output_path}.palette.png"
    filters = f"fps={fps},scale=480:-1:flags=lanczos"
    cmd1 = (f"ffmpeg -y -i {shlex.quote(input_path)} -t {duration} -vf '{filters},palettegen' {shlex.quote(palette_path)}")
    cmd2 = (f"ffmpeg -i {shlex.quote(input_path)} -i {shlex.quote(palette_path)} -t {duration} "
            f"-lavfi '{filters} [x]; [x][1:v] paletteuse' {shlex.quote(output_path.replace('.mp4', '.gif'))}")
    return [cmd1, cmd2]

def build_split_command(config, input_path, output_path):
    criteria = config['split_criteria']
    base_name, ext = os.path.splitext(output_path)
    if 's' in criteria.lower():
        return (f"ffmpeg -y -i {shlex.quote(input_path)} -c copy -map 0 "
                f"-segment_time {criteria.lower().replace('s', '')} -f segment -reset_timestamps 1 {shlex.quote(base_name)}_part%03d{ext}")
    elif 'mb' in criteria.lower():
        logger.warning("División por tamaño no soportada directamente.")
        return ""
    return ""

def build_unify_command(file_list_path: str, output_path: str) -> str:
    return (f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(file_list_path)} "
            f"-c copy {shlex.quote(output_path)}")

def build_subtitle_convert_command(input_path: str, output_path: str) -> str:
    return f"ffmpeg -y -i {shlex.quote(input_path)} {shlex.quote(output_path)}"

def generate_screenshot_command(timestamp: str, input_path: str, output_path: str) -> str:
    return f"ffmpeg -y -ss {shlex.quote(timestamp)} -i {shlex.quote(input_path)} -frames:v 1 -q:v 2 {shlex.quote(output_path)}"

def build_extract_command(archive_path: str, output_dir: str, password: str = None) -> str or None:
    # ... (sin cambios)
    ext = os.path.splitext(archive_path)[1].lower()
    password_part = ""
    if password:
        if ext == '.zip': password_part = f"-P {shlex.quote(password)}"
        elif ext == '.rar': password_part = f"-p{shlex.quote(password)}"
        elif ext == '.7z': password_part = f"-p{shlex.quote(password)}"
    if ext == '.zip':
        return f"unzip -o {password_part} {shlex.quote(archive_path)} -d {shlex.quote(output_dir)}"
    elif ext == '.rar':
        return f"unrar x {password_part} -o+ {shlex.quote(archive_path)} {shlex.quote(output_dir)}/"
    elif ext == '.7z':
        return f"7z x {password_part} -o{shlex.quote(output_dir)} {shlex.quote(archive_path)} -y"
    return None