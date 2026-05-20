"""
Speech-to-Text Module wrapper using faster-whisper.
"""

class WhisperSTT:
    def __init__(self, model_name: str, language: str):
        self.model_name = model_name
        self.language = language
        # Placeholder initialization
        logger_name = "voice_satellite.stt"
        import logging
        logging.getLogger(logger_name).info(f"Initialized WhisperSTT with model={model_name}, lang={language}")
