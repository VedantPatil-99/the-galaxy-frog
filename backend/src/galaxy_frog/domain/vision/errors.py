"""Provider-independent vision and OCR failure contracts."""


class VisionProviderError(RuntimeError):
    """Safe failure raised when a vision or OCR provider fails or is unavailable."""


class OCRExtractionError(VisionProviderError):
    """Safe failure raised when OCR extraction fails on a frame."""
