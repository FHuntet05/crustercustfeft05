import logging
import subprocess
import shlex
import os
import re
import json

logger = logging.getLogger(__name__)

def get_media_info(file_path: str):
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
        # Usamos Popen para manejar mejor la salida y errores
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        stdout, stderr = process.communicate(timeout=60) # Timeout para evitar bloqueos
        
        if process.returncode != 0:
            logger.error(f"ffprobe falló para {file_path}. Error: {stderr.strip()}")
            return {}
            
        return json.loads(stdout)
    except subprocess.TimeoutExpired:
        logger.error(f"ffprobe timed out para {file_path}.")
        process.kill()
        return {}
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return {}

def build_ffmpeg_command(task: dict, input_path: str, output_path: str) -> list:
    """
    Construye el comando FFmpeg completo basado en la configuración de la tarea.
    Devuelve una lista de comandos para soportar operaciones de múltiples pasadas.
    """
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    # --- Comandos Especiales que anulan el flujo normal ---
    if 'gif_options' in config:
        return build_gif_command(config, input_path, output_path)
    if 'split_criteria' in config:
        return [build_split_command(config, input_path, output_path)]

    # --- Flujo de Transcodificación Normal ---
    command_parts = ["nice -n 19", "ionice -c 3", "ffmpeg -y"]
    
    # 1. Opciones de Input (Trimmer debe ir aquí)
    if 'trim_times' in config:
        start, _, end = config['trim_times'].partition('-')
        if start.strip(): command_parts.append(f"-ss {shlex.quote(start.strip())}")
        if end.strip(): command_parts.append(f"-to {shlex.quote(end.strip())}")

    command_parts.append(f"-i {shlex.quote(input_path)}")
    
    # Inputs Adicionales (para Muxer)
    input_count = 1
    if config.get('add_audio_file_id'):
        command_parts.append(f"-i {shlex.quote(os.path.join('downloads', config['add_audio_file_id']))}")
        input_count += 1
    if config.get('add_subtitle_file_id'):
        command_parts.append(f"-i {shlex.quote(os.path.join('downloads', config['add_subtitle_file_id']))}")
        input_count += 1

    # 2. Filtros (Video y Audio)
    video_filters = []
    audio_filters = []
    
    # Perfil de Calidad/Escalado
    profile_map = {'1080p': ('1920:1080', '22', '192k'), '720p': ('1280:720', '24', '128k'),
                   '480p': ('854:480', '28', '96k'), '360p': ('640:360', '32', '64k'),
                   '240p': ('426:240', '34', '64k'), '144p': ('256:144', '36', '48k')}
    if 'quality' in config and file_type == 'video':
        res, _, _ = profile_map.get(config['quality'], profile_map['720p'])
        video_filters.append(f"scale={res}:force_original_aspect_ratio=decrease")
        video_filters.append(f"pad={res}:(ow-iw)/2:(oh-ih)/2:color=black")
    
    # Filtros de Audio
    if config.get('slowed'): audio_filters.append("atempo=0.8")
    if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")
    if config.get('eight_d'): audio_filters.append("apulsator=hz=0.125")
    if 'volume' in config: audio_filters.append(f"volume={shlex.quote(config['volume'])}")
    if 'bass' in config: audio_filters.append(f"equalizer=f=60:width_type=h:width=20:g={shlex.quote(config['bass'])}")
    if 'treble' in config: audio_filters.append(f"equalizer=f=14000:width_type=h:width=2000:g={shlex.quote(config['treble'])}")
    
    if video_filters: command_parts.append(f'-vf "{",".join(video_filters)}"')
    if audio_filters: command_parts.append(f'-af "{",".join(audio_filters)}"')

    # 3. Codecs
    if file_type == 'video':
        if 'quality' in config:
            _, crf, abr = profile_map.get(config['quality'], profile_map['720p'])
            command_parts.extend([f"-c:v libx264", "-preset veryfast", f"-crf {crf}", "-pix_fmt yuv420p"])
            command_parts.extend([f"-c:a aac", f"-b:a {abr}"])
        else: # Si no se especifica calidad, intentar copiar codecs si no hay filtros
            if not video_filters and not audio_filters:
                command_parts.extend(["-c:v copy", "-c:a copy"])
    elif file_type == 'audio':
        fmt = config.get('audio_format', 'mp3')
        bitrate = config.get('audio_bitrate', '128k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        command_parts.append(f"-c:a {codec_map.get(fmt, 'libmp3lame')}")
        if fmt != 'flac': command_parts.append(f"-b:a {bitrate}")
    
    # 4. Mapeo de Pistas
    map_options = ["-map 0:v?"] # Mapear video del primer input si existe
    if not config.get('mute_audio'):
        map_options.append("-map 0:a?") # Mapear audio del primer input si no está muteado
    else:
        command_parts.append("-an") # Opción más simple para mutear

    if config.get('add_audio_file_id'): map_options.append("-map 1:a")
    if config.get('add_subtitle_file_id'): map_options.append(f"-map {input_count-1}:s")
    
    command_parts.extend(map_options)
    
    # Opciones de post-mapeo (ej. codec de subtítulos)
    if config.get('add_subtitle_file_id') and output_path.endswith('.mp4'):
        command_parts.append("-c:s mov_text")
    
    # 5. Output
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
    if criteria.endswith('s'):
        return (f"ffmpeg -y -i {shlex.quote(input_path)} -c copy -map 0 "
                f"-segment_time {criteria[:-1]} -f segment -reset_timestamps 1 {shlex.quote(base_name)}_%03d{ext}")
    return ""

def build_unify_command(file_list_path: str, output_path: str) -> str:
    return (f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(file_list_path)} "
            f"-c copy {shlex.quote(output_path)}")

def generate_screenshot_command(timestamp: str, input_path: str, output_path: str) -> str:
    return f"ffmpeg -y -ss {shlex.quote(timestamp)} -i {shlex.quote(input_path)} -frames:v 1 -q:v 2 {shlex.quote(output_path)}"

def build_extract_command(archive_path: str, output_dir: str) -> str or None:
    ext = os.path.splitext(archive_path)[1].lower()
    if ext == '.zip':
        return f"unzip -o {shlex.quote(archive_path)} -d {shlex.quote(output_dir)}"
    elif ext == '.rar':
        return f"unrar x -o+ {shlex.quote(archive_path)} {shlex.quote(output_dir)}/"
    elif ext == '.7z':
        return f"7z x -o{shlex.quote(output_dir)} {shlex.quote(archive_path)} -y"
    return None