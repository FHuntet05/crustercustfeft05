import logging
import subprocess
import shlex
import os
import re

logger = logging.getLogger(__name__)

def get_media_info(file_path):
    """Usa ffprobe para obtener información detallada de un archivo."""
    command = f"ffprobe -v error -show_format -show_streams -of default=noprint_wrappers=1 -print_format json {shlex.quote(file_path)}"
    try:
        result = subprocess.check_output(shlex.split(command), universal_newlines=True)
        return result # Devuelve el JSON como string
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return "{'error': 'No se pudo analizar el archivo.'}"

def build_ffmpeg_command(task, input_path, output_path):
    """Construye el comando FFmpeg completo basado en la configuración de la tarea."""
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    # --- Partes del Comando ---
    input_options = f"-i {shlex.quote(input_path)}"
    video_codec_options = ""
    audio_codec_options = ""
    filter_complex_parts = []
    map_options = ""
    
    # --- Lógica de Procesamiento de VIDEO ---
    if file_type == 'video':
        # Perfil de Calidad (Default: 720p)
        profile = config.get('quality', '720p')
        profiles = {'1080p': ('1920x1080', '22', '192k'), '720p': ('1280x720', '24', '128k'),
                    '480p': ('854x480', '28', '96k'), '360p': ('640x360', '32', '64k')}
        res, crf, abr = profiles.get(profile, profiles['720p'])
        
        video_codec_options = f"-c:v libx264 -preset veryfast -crf {crf}"
        audio_codec_options = f"-c:a aac -b:a {abr}"
        
        # Filtro de escalado es el base
        filter_complex_parts.append(f"[0:v]scale={res}:force_original_aspect_ratio=decrease,pad={res}:-1:-1:color=black,format=yuv420p[v_scaled]")
        current_video_stream = "[v_scaled]"

        # Marca de Agua
        if config.get('watermark_enabled'):
            watermark_path = "assets/watermark.png"
            if os.path.exists(watermark_path):
                input_options += f" -i {shlex.quote(watermark_path)}"
                pos = config.get('watermark_position', 'W-w-10:H-h-10') # Default bottom-right
                filter_complex_parts.append(f"{current_video_stream}[1:v]overlay={pos}[v_watermarked]")
                current_video_stream = "[v_watermarked]"
        
        # Mute
        if config.get('mute_audio'):
            map_options = f"-map {shlex.quote(current_video_stream)} -an"
        else:
            map_options = f"-map {shlex.quote(current_video_stream)} -map 0:a:0?"

        # Trimmer (Cortar)
        if 'trim_times' in config:
            start, _, end = config['trim_times'].partition('-')
            trim_options = f"-ss {start} " + (f"-to {end}" if end else "")
            input_options = f"{trim_options} {input_options}" # El trim va antes del input
            # Para evitar recodificar si solo se corta
            if len(config) == 1:
                video_codec_options = "-c:v copy"
                audio_codec_options = "-c:a copy"
                filter_complex_parts = [] # Anular filtros si solo es cortar

    # --- Lógica de Procesamiento de AUDIO ---
    elif file_type == 'audio':
        bitrate = config.get('audio_bitrate', '128k')
        audio_codec_options = f"-c:a libmp3lame -b:a {bitrate}" # Asumimos conversión a MP3 por defecto
        map_options = "-map 0:a:0"
        
    # --- Ensamblaje Final del Comando ---
    filter_complex = f'-filter_complex "{";".join(filter_complex_parts)}"' if filter_complex_parts else ""
    
    command = (f"nice -n 19 ionice -c 3 ffmpeg -y {input_options} {filter_complex} "
               f"{map_options} {video_codec_options} {audio_codec_options} {shlex.quote(output_path)}")
    
    # Limpiar espacios extra
    command = ' '.join(command.split())
    logger.info(f"Comando FFmpeg construido: {command}")
    return command

def run_ffmpeg_process(command, progress_callback=None, duration=None):
    """Ejecuta un comando FFmpeg y parsea su progreso si se provee callback."""
    process = subprocess.Popen(shlex.split(command), stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8', errors='ignore')
    
    progress_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    
    for line in iter(process.stderr.readline, ''):
        if progress_callback and duration:
            match = progress_pattern.search(line)
            if match:
                h, m, s, _ = map(int, match.groups())
                time_processed = h * 3600 + m * 60 + s
                percentage = (time_processed / duration) * 100 if duration > 0 else 0
                progress_callback(min(100, percentage))
    
    process.wait()
    return process.returncode == 0