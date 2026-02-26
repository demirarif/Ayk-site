import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def _fix_db_url(url):
    """Neon/Heroku 'postgres://' → 'postgresql://' (SQLAlchemy 2.x uyumu)."""
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url

_raw_db_url = os.environ.get(
    'DATABASE_URL',
    f"sqlite:///{os.path.join(BASE_DIR, 'database', 'kya.db')}"
)
_db_url = _fix_db_url(_raw_db_url)
_is_postgres = _db_url.startswith('postgresql')

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'kya-dev-secret-CHANGE-IN-PROD')
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Vercel serverless → her istek yeni bağlantı açar, pool gerekmez
    # SQLite local → varsayılan pool yeterli
    if _is_postgres:
        from sqlalchemy.pool import NullPool
        SQLALCHEMY_ENGINE_OPTIONS = {
            'poolclass': NullPool,
            'connect_args': {'sslmode': 'require'}
            if 'sslmode' not in _db_url else {},
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}

    # Dosya yükleme
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024   # 8MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

    # Admin
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'kya2024admin!')

    # Cloudinary (isteğe bağlı — Vercel'de dosya yüklemek için)
    CLOUDINARY_URL = os.environ.get('CLOUDINARY_URL', '')

    # WTF CSRF
    WTF_CSRF_TIME_LIMIT = 7200

