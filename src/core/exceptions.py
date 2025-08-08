class BaseBotException(Exception):
    def __init__(self, message="Ocurrió un error inesperado en el bot."):
        self.message = message
        super().__init__(self.message)

class NetworkError(BaseBotException):
    def __init__(self, message="Error de red transitorio. La operación podría reintentarse."):
        super().__init__(message)

class DiskSpaceError(BaseBotException):
    def __init__(self, message="Espacio en disco insuficiente."):
        super().__init__(message)

class InvalidMediaError(BaseBotException):
    def __init__(self, message="El archivo proporcionado está corrupto o no es un formato de medios compatible."):
        super().__init__(message)

class FFmpegProcessingError(BaseBotException):
    def __init__(self, message="Error irrecuperable durante el procesamiento con FFmpeg.", log=""):
        self.log = log
        full_message = f"{message}\n\n--- Log de FFmpeg ---\n{log}"
        super().__init__(full_message)

class AuthenticationError(BaseBotException):
    def __init__(self, service_name: str, message="Fallo de autenticación."):
        self.service_name = service_name
        full_message = f"Error de autenticación con {service_name}: {message}"
        super().__init__(full_message)