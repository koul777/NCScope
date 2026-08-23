"""Vercel ASGI entrypoint for the NCScope FastAPI application."""

from app.main import app

__all__ = ["app"]
