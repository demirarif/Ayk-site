"""
Vercel serverless entry point.
Vercel bu dosyayı lambda olarak çalıştırır.
"""
import sys
import os

# Proje kökünü Python yoluna ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # Flask WSGI uygulaması

# Vercel WSGI handler
handler = app
