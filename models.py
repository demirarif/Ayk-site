from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """Admin kullanıcıları"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class SiteSetting(db.Model):
    """Site geneli anahtar-değer ayarları (logo, iletişim, vb.)"""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    label = db.Column(db.String(200), nullable=True)   # Admin paneli etiketi
    type = db.Column(db.String(30), default='text')    # text, textarea, image, email, phone

    @classmethod
    def get(cls, key, default=None):
        s = cls.query.filter_by(key=key).first()
        return s.value if s else default

    @classmethod
    def set(cls, key, value):
        s = cls.query.filter_by(key=key).first()
        if s:
            s.value = value
        else:
            s = cls(key=key, value=value)
            db.session.add(s)
        db.session.commit()

    def __repr__(self):
        return f'<SiteSetting {self.key}>'


class HeroSection(db.Model):
    """Her sayfanın hero (üst banner) içeriği"""
    id = db.Column(db.Integer, primary_key=True)
    page = db.Column(db.String(50), unique=True, nullable=False)   # index, hakkimizda, vb.
    title = db.Column(db.String(200), nullable=True)
    subtitle = db.Column(db.String(300), nullable=True)
    image_url = db.Column(db.String(500), nullable=True)           # /static/uploads/... veya URL

    def __repr__(self):
        return f'<Hero {self.page}>'


class TeamMember(db.Model):
    """Ekip üyeleri"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(150), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    linkedin_url = db.Column(db.String(300), nullable=True)
    photo_url = db.Column(db.String(500), nullable=True)
    order_index = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<TeamMember {self.name}>'


class PracticeArea(db.Model):
    """Çalışma alanları"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    icon = db.Column(db.String(80), default='fas fa-gavel')   # Font Awesome sınıfı
    image_url = db.Column(db.String(500), nullable=True)       # Alan görseli
    order_index = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f'<PracticeArea {self.title}>'


class ContactMessage(db.Model):
    """İletişim formu mesajları"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(300), nullable=True)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<ContactMessage {self.name} {self.email}>'


class Article(db.Model):
    """Makaleler"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    slug = db.Column(db.String(300), unique=True, nullable=False)
    summary = db.Column(db.Text, nullable=True)
    content = db.Column(db.Text, nullable=True)
    author = db.Column(db.String(150), nullable=True)
    cover_url = db.Column(db.String(500), nullable=True)
    is_published = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    published_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<Article {self.title}>'
