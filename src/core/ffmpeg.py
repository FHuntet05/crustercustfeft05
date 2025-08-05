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
    Devuelve una lista de comandos para soportar operaciones de múltiples pasadas.
    """
    config = task.get('processing_config', {})
    
    # --- Comandos Especiales que anulan el flujo normal ---
    if 'gif_options' in config:
        return build_gif_command(config, input_path, output_path)
    if 'split_criteria' in config:
        return [build_split_command(config, input_path, output_path)]

    # --- Flujo de Transcodificación Normal ---
    command_parts = ["ffmpeg", "-y"]
    
    # 1. Opciones de Input
    if 'trim_times' in config:
        start, _, end = config['trim_times'].partition('-')
        if start.strip(): command_parts.append(f"-ss {shlex.quote(start.strip())}")
        if end.strip(): command_parts.append(f"-to {shlex.quote(end.strip())}")
    
    if 'sample_duration' in config:
        command_parts.append(f"-t {shlex.quote(config['sample_duration'])}")

    command_parts.append(f"-i {shlex.quote(input_path)}")
    
    input_map = {0: '0'} # Mapeo de inputs: 0 es el archivo principal
    current_input_index = 1
    if audio_path := config.get('add_audio_file_path'):
        command_parts.append(f"-i {shlex.quote(audio_path)}")
        input_map['audio'] = str(current_input_index)
        current_input_index += 1
    if subs_path := config.get('add_subtitle_file_path'):
        command_parts.append(f"-i {shlex.quote(subs_path)}")
        input_map['subtitle'] = str(current_input_index)

    # 2. Filtros
    video_filters = []
    audio_filters = []
    
    profile_map = {'1080p': ('1920:1080', '22', '192k'), '720p': ('1280:720', '24', '128k'),
                   '480p': ('854:480', '28', '96k'), '360p': ('640:360', '32', '64k')}
    if 'quality' in config and task.get('file_type') == 'video':
        res, _, _ = profile_map.get(config['quality'], profile_map['720p'])
        video_filters.append(f"scale={res}:force_original_aspect_ratio=decrease")
        video_filters.append(f"pad={res}:(ow-iw)/2:(oh-ih)/2:color=black")
    
    if config.get('slowed'): audio_filters.append("atempo=0.8")
    if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")
    
    if video_filters: command_parts.append(f'-vf "{",".join(video_filters)}"')
    if audio_filters: command_parts.append(f'-af "{",".join(audio_filters)}"')

    # 3. Codecs
    video_codec_options = []
    audio_codec_options = []
    re_encode_all = False # Flag para forzar re-codificación si se añaden filtros/pistas

    if video_filters or audio_filters or 'add_audio_file_path' in config:
        re_encode_all = True

    if task.get('file_type') == 'video':
        if 'quality' in config or re_encode_all:
            _, crf, abr = profile_map.get(config.get('quality', '720p'), profile_map['720p'])
            video_codec_options.extend([f"-c:v libx264", "-preset veryfast", f"-crf {crf}", "-pix_fmt yuv420p"])
            audio_codec_options.extend([f"-c:a aac", f"-b:a {abr}"])
        else:
            video_codec_options.append("-c:v copy")
            audio_codec_options.append("-c:a copy")
    
    elif task.get('file_type') == 'audio':
        fmt = config.get('audio_format', 'mp3')
        bitrate = config.get('audio_bitrate', '128k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        audio_codec_options.append(f"-c:a {codec_map.get(fmt, 'libmp3lame')}")
        if fmt != 'flac': audio_codec_options.append(f"-b:a {bitrate}")

    command_parts.extend(video_codec_options)
    command_parts.extend(audio_codec_options)

    # 4. Mapeo de Pistas
    map_options = ["-map 0:v?"] # Video del input principal, si existe
    if config.get('mute_audio'):
        command_parts.append("-an")
    else:
        map_options.append("-map 0:a?") # Audios del input principal
    map_options.append("-map 0:s?") # Subtítulos del input principal

    # Eliminar pistas seleccionadas
    for track_index in config.get('remove_audio_indices', []):
        map_options.append(f"-map -0:a:{track_index}")
    for track_index in config.get('remove_subtitle_indices', []):
        map_options.append(f"-map -0:s:{track_index}")
        
    # Añadir nuevas pistas
    if 'audio' in input_map: map_options.append(f"-map {input_map['audio']}:a")
    if 'subtitle' in input_map: map_options.append(f"-map {input_map['subtitle']}:s")
    
    command_parts.extend(map_options)
    
    if 'subtitle' in input_map and output_path.endswith('.mp4'):
        command_parts.append("-c:s mov_text")
    
    command_parts.append(shlex.quote(output_path))
    
    final_command = " ".join(part for part in command_parts if part)
    logger.info(f"Comando FFmpeg construido: {final_command}")
    return [final_command]

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
        # La división por tamaño es más compleja y requiere re-codificación, no se implementa como comando simple por ahora.
        logger.warning("División por tamaño no soportada directamente, se requiere un script más complejo.")
        return "" # Devuelve un comando vacío para indicar que no se puede procesar
    return ""

def build_unify_command(file_list_path: str, output_path: str) -> str:
    return (f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(file_list_path)} "
            f"-c copy {shlex.quote(output_path)}")

def build_subtitle_convert_command(input_path: str, output_path: str) -> str:
    return f"ffmpeg -y -i {shlex.quote(input_path)} {shlex.quote(output_path)}"

def generate_screenshot_command(timestamp: str, input_path: str, output_path: str) -> str:
    return f"ffmpeg -y -ss {shlex.quote(timestamp)} -i {shlex.quote(input_path)} -frames:v 1 -q:v 2 {shlex.quote(output_path)}"

def build_extract_command(archive_path: str, output_dir: str, password: str = None) -> str or None:
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