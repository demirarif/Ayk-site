"""
Vercel serverless entry point.
Vercel bu dosyayı lambda olarak çalıştırır.
"""
import sys
import os
import traceback

# Proje kökünü Python yoluna ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app import app  # Flask WSGI uygulaması
except Exception as e:
    # Import hatasını yutma — Vercel loglarında görünsün
    traceback.print_exc()
    # Minimum hata yanıt veren basit WSGI app döndür
    def app(environ, start_response):
        err = f'Import hatası: {e}\n\n{traceback.format_exc()}'
        start_response('500 Internal Server Error',
                       [('Content-Type', 'text/plain; charset=utf-8')])
        return [err.encode('utf-8')]

# Vercel, modülde `app` tanımlıysa WSGI olarak otomatik sarar.
