# --- START OF FILE src/core/ffmpeg.py ---

import logging
import subprocess
import os
import json
from typing import List, Tuple, Dict, Optional

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    if not os.path.exists(file_path):
        logger.error(f"ffprobe no puede encontrar el archivo: {file_path}")
        return {}
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", file_path]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return {}

def build_ffmpeg_command(
    task: Dict, input_path: str, output_path: str,
    watermark_path: Optional[str] = None, replace_audio_path: Optional[str] = None,
    audio_thumb_path: Optional[str] = None, subs_path: Optional[str] = None
) -> Tuple[List[List[str]], str]:
    config, file_type = task.get('processing_config', {}), task.get('file_type')
    if config.get('extract_audio'): return _build_extract_audio_command(input_path, output_path)
    if config.get('gif_options'): return _build_gif_command(task, input_path, output_path)
    if file_type == 'audio': return _build_audio_command(task, input_path, output_path, audio_thumb_path)
    return _build_standard_video_command(task, input_path, output_path, watermark_path, replace_audio_path, subs_path)

def _build_audio_command(
    task: Dict, input_path: str, output_path: str, audio_thumb_path: Optional[str]
) -> Tuple[List[List[str]], str]:
    config, command = task.get('processing_config', {}), ["ffmpeg", "-y", "-hide_banner"]
    command.extend(["-i", input_path])
    if audio_thumb_path: command.extend(["-i", audio_thumb_path])
    command.extend(["-map", "0:a"])
    if audio_thumb_path:
        command.extend(["-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic"])
    command.extend(["-c:a", "copy"])
    if audio_tags := config.get('audio_tags'):
        for key, value in audio_tags.items(): command.extend(["-metadata", f"{key}={value}"])
    command.extend(["-progress", "pipe:2", output_path])
    return [command], output_path

def _build_standard_video_command(
    task: Dict, input_path: str, output_path: str, watermark_path: Optional[str],
    replace_audio_path: Optional[str], subs_path: Optional[str]
) -> Tuple[List[List[str]], str]:
    config, command = task.get('processing_config', {}), ["ffmpeg", "-y", "-hide_banner"]
    if trim_times := config.get('trim_times'):
        try:
            if '-' in trim_times: start, end = trim_times.split('-', 1); command.extend(["-ss", start.strip(), "-to", end.strip()])
            else: command.extend(["-to", trim_times.strip()])
        except Exception as e: logger.warning(f"Formato de trim inválido: {e}. Se ignorará.")
    
    command.extend(["-i", input_path])
    input_count = 1
    watermark_input_index, audio_input_index, subs_input_index = None, None, None
    if watermark_path: command.extend(["-i", watermark_path]); watermark_input_index = input_count; input_count += 1
    if replace_audio_path: command.extend(["-i", replace_audio_path]); audio_input_index = input_count; input_count += 1
    if subs_path: command.extend(["-i", subs_path]); subs_input_index = input_count

    filter_complex_parts, current_video_chain = [], "[0:v]"
    if transcode_res := config.get('transcode', {}).get('resolution'):
        filter_complex_parts.append(f"{current_video_chain}scale=-2:{transcode_res.replace('p', '')}[scaled_v]")
        current_video_chain = "[scaled_v]"
    
    if wm_conf := config.get('watermark'):
        next_chain = "[watermarked_v]"
        if wm_conf.get('type') == 'image' and watermark_path:
            pos_map = {'top_left': '10:10', 'top_right': 'main_w-overlay_w-10:10', 'bottom_left': '10:main_h-overlay_h-10', 'bottom_right': 'main_w-overlay_w-10:main_h-overlay_h-10'}
            overlay_pos = pos_map.get(wm_conf.get('position', 'br'), pos_map['bottom_right'])
            filter_complex_parts.append(f"{current_video_chain}[{watermark_input_index}:v]overlay={overlay_pos}{next_chain}")
            current_video_chain = next_chain
        elif wm_conf.get('type') == 'text':
            text = wm_conf.get('text','').replace("'", "’").replace(":", "∶")
            pos_map = {'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10', 'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'}
            text_pos = pos_map.get(wm_conf.get('position', 'br'), pos_map['bottom_right'])
            drawtext = f"drawtext=fontfile='assets/font.ttf':text='{text}':fontcolor=white@0.8:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{text_pos}"
            filter_complex_parts.append(f"{current_video_chain}{drawtext}{next_chain}")
            current_video_chain = next_chain

    if filter_complex_parts:
        command.extend(["-filter_complex", ";".join(filter_complex_parts), "-map", current_video_chain.replace('[','').replace(']','')])
    else: command.extend(["-map", "0:v?"])
    
    if replace_audio_path: command.extend(["-map", f"{audio_input_index}:a", "-shortest"])
    elif config.get('mute_audio'): command.append("-an")
    else: command.extend(["-map", "0:a?"])
    
    # [IMPLEMENTACIÓN] Lógica de mapeo de subtítulos.
    if config.get('remove_subtitles'): command.append("-sn")
    elif subs_path: command.extend(["-map", f"{subs_input_index}:s?"])
    else: command.extend(["-map", "0:s?"])
    
    if config.get('transcode') or replace_audio_path:
        command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "128k"])
    else: command.extend(["-c:v", "copy", "-c:a", "copy"])
    
    command.extend(["-c:s", "mov_text", "-progress", "pipe:2", output_path])
    return [command], output_path

def _build_extract_audio_command(input_path: str, output_path_base: str) -> Tuple[List[List[str]], str]:
    media_info, ext = get_media_info(input_path), ".m4a"
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    if audio_stream and (codec_name := audio_stream.get('codec_name')):
        ext_map = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}
        ext = ext_map.get(codec_name, '.m4a')
    final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
    command = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "copy", final_output_path]
    return [command], final_output_path

def _build_gif_command(task: Dict, input_path: str, output_path_base: str) -> Tuple[List[List[str]], str]:
    config = task.get('processing_config', {})
    gif_opts = config.get('gif_options', {})
    duration, fps = gif_opts.get('duration', 5.0), gif_opts.get('fps', 15)
    final_output_path = f"{os.path.splitext(output_path_base)[0]}.gif"
    command: List[str] = ["ffmpeg", "-y"]
    if trim_times := config.get('trim_times'):
        if '-' in trim_times: start, _ = trim_times.split('-', 1); command.extend(["-ss", start.strip()])
        else: command.extend(["-ss", trim_times.strip()])
    command.extend(["-t", str(duration), "-i", input_path])
    filter_complex = f"fps={fps},scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
    command.extend(["-filter_complex", filter_complex, "-progress", "pipe:2", final_output_path])
    return [command], final_output_path