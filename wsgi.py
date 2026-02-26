"""
WSGI giriş noktası — Gunicorn için kullanılır.
Kullanım: gunicorn -w 4 -b 0.0.0.0:5000 wsgi:application
"""
from app import app, init_db

# Uygulama başlangıcında DB ve seed verilerini hazırla
init_db()

application = app

if __name__ == '__main__':
    application.run()
