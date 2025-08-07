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
        shlex.quote(file_path)
    ]
    try:
        # Usamos shlex.join para sistemas Windows/Linux
        process = subprocess.Popen(shlex.join(command), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
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

def build_ffmpeg_command(task: dict, input_path: str, output_path: str, thumbnail_path: str = None) -> list:
    """
    Construye el comando FFmpeg completo basado en la configuración de la tarea.
    """
    config = task.get('processing_config', {})
    
    # Comandos especiales que reemplazan el flujo normal
    if 'gif_options' in config:
        return build_gif_command(config, input_path, output_path)
    if 'split_criteria' in config:
        return [build_split_command(config, input_path, output_path)]

    # Flujo de construcción de comando estándar
    command_parts = ["ffmpeg", "-y"]
    
    # Opciones de entrada (ej. corte)
    if 'trim_times' in config:
        start, _, end = config['trim_times'].partition('-')
        if start.strip(): command_parts.append(f"-ss {shlex.quote(start.strip())}")
        if end.strip(): command_parts.append(f"-to {shlex.quote(end.strip())}")
    
    command_parts.append(f"-i {shlex.quote(input_path)}")

    if thumbnail_path:
        command_parts.append(f"-i {shlex.quote(thumbnail_path)}")
    
    # --- Lógica de Códecs ---
    video_opts, audio_opts, subtitle_opts, metadata_opts = [], [], [], []
    
    # Audio
    if task.get('file_type') == 'audio':
        fmt = config.get('audio_format', 'mp3')
        bitrate = config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        audio_opts.append(f"-c:a {codec_map.get(fmt, 'libmp3lame')}")
        if fmt != 'flac': audio_opts.append(f"-b:a {bitrate}")
    
    # Video
    elif task.get('file_type') == 'video':
        if 'quality' in config and config['quality'] != 'Original':
            profile_map = {'1080p': '22', '720p': '24', '480p': '28', '360p': '32'}
            crf = profile_map.get(config.get('quality'), '24')
            video_opts.extend(["-c:v libx264", "-preset veryfast", f"-crf {crf}", "-pix_fmt yuv420p"])
            audio_opts.extend(["-c:a aac", "-b:a 192k"]) # Recodificar audio junto con video
        else:
            video_opts.append("-c:v copy")
            audio_opts.append("-c:a copy")
        
        output_ext = os.path.splitext(output_path)[1].lower()
        subtitle_opts.append("-c:s mov_text" if output_ext == '.mp4' else "-c:s copy")

    # Si no hay opciones específicas, copiar todo
    if not video_opts and not audio_opts:
        video_opts.append("-c:v copy")
        audio_opts.append("-c:a copy")
    if not subtitle_opts:
        subtitle_opts.append("-c:s copy")

    # Aplicar efectos de audio
    if any(k in config for k in ['slowed', 'reverb']):
        audio_filters = []
        if config.get('slowed'): audio_filters.append("atempo=0.8")
        if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")
        if audio_filters: audio_opts.append(f'-af "{",".join(audio_filters)}"')

    if config.get('mute_audio'):
        audio_opts = ["-an"] # Sobrescribir opciones de audio para silenciar

    # Mapeo de streams
    map_opts = ["-map 0"]
    if thumbnail_path:
        map_opts.append("-map 1")
        metadata_opts.extend(['-metadata:s:v:0 title="Album cover"', '-metadata:s:v:0 comment="Cover (front)"'])
        # Si hay carátula, asumimos que es un audio y queremos que sea el stream de video principal
        if task.get('file_type') == 'audio':
             video_opts = ["-c:v:1 copy" if thumbnail_path.endswith(('.jpg', '.jpeg')) else "-c:v:1 mjpeg"]
             # Reordenar mapas para que la imagen sea el primer stream de video
             map_opts = ["-map 0:a", "-map 1:v"]
        else: # Para videos, solo copiar la carátula
             video_opts.append("-c:v:1 copy")


    # Ensamblaje final
    command_parts.extend(video_opts)
    command_parts.extend(audio_opts)
    command_parts.extend(subtitle_opts)
    command_parts.extend(map_opts)
    command_parts.extend(metadata_opts)
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