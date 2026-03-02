import os
import re
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Vercel filesystem salt okunur — SQLite mutlaka /tmp altında olmalı
ON_VERCEL = os.environ.get('VERCEL', '') == '1'
_SQLITE_PATH = '/tmp/kya.db' if ON_VERCEL else os.path.join(BASE_DIR, 'database', 'kya.db')


def _build_db_url(raw_url):
    """
    Verilen DATABASE_URL'i SQLAlchemy + psycopg2 için hazırlar.
    - postgres:// → postgresql+psycopg2://
    - sslmode=require URL içinde kalır (psycopg2 libpq aracılığıyla işler)
    - pgbouncer parametresi temizlenir
    """
    if not raw_url or raw_url.startswith('sqlite'):
        return raw_url, {}

    # postgres:// → postgresql://
    url = raw_url.replace('postgres://', 'postgresql://', 1)

    # psycopg2 sürücüsünü zorla
    if url.startswith('postgresql://') and '+' not in url.split('://')[0]:
        url = url.replace('postgresql://', 'postgresql+psycopg2://', 1)

    # pgbouncer parametresi psycopg2'yi bozar → çıkar
    url = re.sub(r'[?&]pgbouncer=[^&]*', '', url).rstrip('?').rstrip('&')

    # psycopg2, sslmode'u URL'de okur — connect_args gerekmez
    return url, {}


# ─────────────────────────────────────────────────────────
# Neon Vercel entegrasyonu şu değişkenleri otomatik ekler:
#   POSTGRES_URL            → pgbouncer (havuzlu)
#   DATABASE_URL_UNPOOLED   → direkt bağlantı (serverless için ideal)
#   POSTGRES_URL_NO_SSL     → SSL'siz
# Manuel olarak eklenen DATABASE_URL varsa öncelikli kullanılır.
# ─────────────────────────────────────────────────────────
def _pick_db_url():
    for key in ('DATABASE_URL', 'DATABASE_URL_UNPOOLED', 'POSTGRES_URL', 'POSTGRES_URL_NO_SSL'):
        val = (os.environ.get(key) or '').strip()
        if val and val.startswith(('postgres', 'sqlite')):
            print(f'[config] DB URL kaynağı: {key}')
            return val
    print('[config] DB URL bulunamadı, SQLite kullanılıyor.')
    return f'sqlite:///{_SQLITE_PATH}'


_raw_db_url = _pick_db_url()
_db_url, _connect_args = _build_db_url(_raw_db_url)
_is_postgres = 'postgresql' in _db_url


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'kya-dev-secret-CHANGE-IN-PROD')
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    if _is_postgres:
        # Vercel serverless: Lambda container ısındıktan sonra bağlantıyı yeniden kullan.
        # NullPool her request'te yeni TCP bağlantısı açtığı için +300-500ms ekliyordu.
        # pool_size=1: tek bağlantı yeterli (serverless tek iş parçacığı).
        # pool_pre_ping: stale bağlantıları tespit edip yeniden bağlanır.
        # pool_recycle=300: 5 dakikada bir bağlantıyı yenile (Neon idle timeout önlemi).
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_size': 1,
            'max_overflow': 2,
            'pool_timeout': 10,
            'pool_recycle': 300,
            'pool_pre_ping': True,
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {}

    # Dosya yükleme
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

    # Admin
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'arifdemir')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'qwer1234')

    # CSRF
    WTF_CSRF_TIME_LIMIT = 7200

