import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def _fix_db_url(url):
    """Neon/Heroku 'postgres://' → 'postgresql://' (SQLAlchemy 2.x uyumu)."""
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'kya-dev-secret-CHANGE-IN-PROD')

    # Vercel/Neon/Supabase'de DATABASE_URL env var kullanılır, yoksa local SQLite
    _db_url = os.environ.get('DATABASE_URL',
                              f"sqlite:///{os.path.join(BASE_DIR, 'database', 'kya.db')}")
    SQLALCHEMY_DATABASE_URI = _fix_db_url(_db_url)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # Dosya yükleme
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024   # 8MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

    # Admin
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'kya2024admin!')

    # Cloudinary (isteğe bağlı — Vercel'de dosya yüklemek için)
    # Değer: "cloudinary://API_KEY:API_SECRET@CLOUD_NAME"
    CLOUDINARY_URL = os.environ.get('CLOUDINARY_URL', '')

    # WTF CSRF (admin form koruması)
    WTF_CSRF_TIME_LIMIT = 7200  # 2 saat

