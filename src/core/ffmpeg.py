# --- START OF FILE src/core/ffmpeg.py (CORREGIDO Y REESTRUCTURADO) ---

import logging
import subprocess
import os
import json
from typing import List, Tuple, Dict, Optional
from src.core.exceptions import FfmpegError

logger = logging.getLogger(__name__)

class FfmpegProcessor:
    def __init__(self, input_path: str, output_dir: str, config: Dict, media_info: Dict):
        """
        Inicializa el procesador de FFmpeg con todos los datos necesarios para una tarea.
        """
        self.input_path = input_path
        self.output_dir = output_dir
        self.config = config
        self.media_info = media_info
        self.task = {"processing_config": config, "file_type": media_info.get('file_type', 'video')}

    def run(self) -> Optional[str]:
        """
        Punto de entrada principal para ejecutar el procesamiento FFmpeg.
        Determina si se necesita alguna operación y, si es así, la ejecuta.
        Devuelve la ruta al archivo procesado o None si no se hizo nada.
        """
        # Si no hay ninguna configuración de procesamiento, no hacemos nada.
        if not self.config:
            return None

        # TODO: Añadir lógica para descargar recursos externos (marcas de agua, audios, etc.)
        # Por ahora, asumimos que las rutas se pasarán en un futuro.
        watermark_path = None
        replace_audio_path = None
        audio_thumb_path = None
        subs_path = None

        output_base_name = os.path.splitext(os.path.basename(self.input_path))[0]
        output_path_base = os.path.join(self.output_dir, output_base_name)

        commands, final_output_path = self._build_ffmpeg_command(output_path_base, watermark_path, replace_audio_path, audio_thumb_path, subs_path)

        if not commands:
            logger.info("No se generó ningún comando de FFmpeg basado en la configuración.")
            return None

        for command in commands:
            self._execute_ffmpeg_command(command)

        if os.path.exists(final_output_path):
            return final_output_path
        
        raise FfmpegError(f"El comando FFmpeg finalizó pero el archivo de salida '{final_output_path}' no fue creado.")

    def _execute_ffmpeg_command(self, command: List[str]):
        """Ejecuta un comando FFmpeg y maneja la salida."""
        logger.info(f"Ejecutando comando FFmpeg: {' '.join(command)}")
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, text=True)
            for line in process.stdout:
                logger.debug(f"[FFMPEG] {line.strip()}")
            
            process.wait()

            if process.returncode != 0:
                raise FfmpegError(f"FFmpeg falló con el código de salida {process.returncode}.")
        except FileNotFoundError:
            raise FfmpegError("El ejecutable 'ffmpeg' no fue encontrado. Asegúrate de que esté instalado y en el PATH del sistema.")
        except Exception as e:
            raise FfmpegError(f"Error inesperado al ejecutar FFmpeg: {e}")

    def _build_ffmpeg_command(
        self, output_path_base: str,
        watermark_path: Optional[str] = None, replace_audio_path: Optional[str] = None,
        audio_thumb_path: Optional[str] = None, subs_path: Optional[str] = None
    ) -> Tuple[List[List[str]], str]:
        if self.config.get('extract_audio'): return self._build_extract_audio_command(output_path_base)
        if self.config.get('gif_options'): return self._build_gif_command(output_path_base)
        if self.task.get('file_type') == 'audio': return self._build_audio_command(output_path_base, audio_thumb_path)
        return self._build_standard_video_command(output_path_base, watermark_path, replace_audio_path, subs_path)

    def _build_audio_command(self, output_path_base: str, audio_thumb_path: Optional[str]) -> Tuple[List[List[str]], str]:
        final_output_path = f"{output_path_base}.m4a"
        command = ["ffmpeg", "-y", "-hide_banner", "-i", self.input_path]
        if audio_thumb_path: command.extend(["-i", audio_thumb_path])
        
        command.extend(["-map", "0:a"])
        if audio_thumb_path:
            command.extend(["-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic"])
        
        command.extend(["-c:a", "copy"])
        if audio_tags := self.config.get('audio_tags'):
            for key, value in audio_tags.items(): command.extend(["-metadata", f"{key}={value}"])
        
        command.extend(["-progress", "pipe:2", final_output_path])
        return [command], final_output_path

    def _build_standard_video_command(
        self, output_path_base: str, watermark_path: Optional[str],
        replace_audio_path: Optional[str], subs_path: Optional[str]
    ) -> Tuple[List[List[str]], str]:
        final_output_path = f"{output_path_base}.mkv"
        command = ["ffmpeg", "-y", "-hide_banner"]
        if trim_times := self.config.get('trim_times'):
            try:
                if '-' in trim_times: start, end = trim_times.split('-', 1); command.extend(["-ss", start.strip(), "-to", end.strip()])
                else: command.extend(["-to", trim_times.strip()])
            except Exception as e: logger.warning(f"Formato de trim inválido: {e}. Se ignorará.")
        
        command.extend(["-i", self.input_path])
        input_count = 1
        watermark_input_index, audio_input_index, subs_input_index = None, None, None
        if watermark_path: command.extend(["-i", watermark_path]); watermark_input_index = input_count; input_count += 1
        if replace_audio_path: command.extend(["-i", replace_audio_path]); audio_input_index = input_count; input_count += 1
        if subs_path: command.extend(["-i", subs_path]); subs_input_index = input_count

        filter_complex_parts = []
        current_video_chain = "[0:v]"
        
        if transcode_res := self.config.get('transcode', {}).get('resolution'):
            next_chain = "[scaled_v]"
            filter_complex_parts.append(f"{current_video_chain}scale=-2:{transcode_res.replace('p', '')}{next_chain}")
            current_video_chain = next_chain
        
        if wm_conf := self.config.get('watermark'):
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
            command.extend(["-filter_complex", ";".join(filter_complex_parts)])
            command.extend(["-map", current_video_chain])
        else:
            command.extend(["-map", "0:v?"])
        
        if replace_audio_path: command.extend(["-map", f"{audio_input_index}:a?", "-shortest"])
        elif self.config.get('mute_audio'): command.append("-an")
        else: command.extend(["-map", "0:a?"])
        
        if self.config.get('remove_subtitles'): command.append("-sn")
        elif subs_path: command.extend(["-map", f"{subs_input_index}:s?"])
        else: command.extend(["-map", "0:s?"])
        
        if self.config.get('transcode') or replace_audio_path:
            command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "128k"])
        else: command.extend(["-c:v", "copy", "-c:a", "copy"])
        
        command.extend(["-c:s", "mov_text", "-progress", "pipe:2", final_output_path])
        return [command], final_output_path

    def _build_extract_audio_command(self, output_path_base: str) -> Tuple[List[List[str]], str]:
        ext = ".m4a"
        audio_stream = next((s for s in self.media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
        if audio_stream and (codec_name := audio_stream.get('codec_name')):
            ext_map = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}
            ext = ext_map.get(codec_name, '.m4a')
        final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
        command = ["ffmpeg", "-y", "-i", self.input_path, "-vn", "-c:a", "copy", final_output_path]
        return [command], final_output_path

    def _build_gif_command(self, output_path_base: str) -> Tuple[List[List[str]], str]:
        gif_opts = self.config.get('gif_options', {})
        duration, fps = gif_opts.get('duration', 5.0), gif_opts.get('fps', 15)
        final_output_path = f"{os.path.splitext(output_path_base)[0]}.gif"
        command: List[str] = ["ffmpeg", "-y"]
        if trim_times := self.config.get('trim_times'):
            if '-' in trim_times: start, _ = trim_times.split('-', 1); command.extend(["-ss", start.strip()])
            else: command.extend(["-ss", trim_times.strip()])
        command.extend(["-t", str(duration), "-i", self.input_path])
        filter_complex = f"fps={fps},scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
        command.extend(["-filter_complex", filter_complex, "-progress", "pipe:2", final_output_path])
        return [command], final_output_path

    # Esta función ahora es un método estático. Puede ser llamada desde fuera sin crear una instancia.
    @staticmethod
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
            
    # Función estática para unir videos, ya que es una operación especial.
    @staticmethod
    def join_videos(file_list: List[str], output_dir: str, output_name: str) -> str:
        list_file_path = os.path.join(output_dir, "join_list.txt")
        with open(list_file_path, 'w') as f:
            for file_path in file_list:
                f.write(f"file '{os.path.abspath(file_path)}'\n")

        final_output_path = os.path.join(output_dir, f"{output_name}.mkv")
        command = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file_path, "-c", "copy", final_output_path
        ]
        
        logger.info(f"Ejecutando comando de unión FFmpeg: {' '.join(command)}")
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            os.remove(list_file_path)
            if os.path.exists(final_output_path):
                return final_output_path
            raise FfmpegError("El comando de unión finalizó pero el archivo de salida no fue creado.")
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg falló al unir videos. Error: {e.stderr}")
            raise FfmpegError(f"FFmpeg falló al unir videos: {e.stderr}")
        except Exception as e:
            raise FfmpegError(f"Error inesperado al unir videos: {e}")

# --- END OF FILE src/core/ffmpeg.py ---