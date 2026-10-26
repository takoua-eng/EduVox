"""Services IA du module quiz (génération et correction avec Gemini)."""

from .gemini import ErreurIA, gemini_configure

__all__ = ["ErreurIA", "gemini_configure"]
