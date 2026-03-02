import os
import re
import time
import base64
import requests as http_client
from datetime import datetime

from flask import (Flask, render_template, redirect, url_for,
                   request, flash, abort, jsonify, send_from_directory, session)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename

from config import Config
from models import db, User, SiteSetting, HeroSection, TeamMember, PracticeArea, Article, ContactMessage

# ─────────────────────────────────────────
# App kurulum
# ─────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)

login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'
login_manager.login_message = 'Bu sayfayı görmek için giriş yapmanız gerekiyor.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ─────────────────────────────────────────
# Admin subdomain yönlendirmesi
# admin.kyahukukdanismanlik.site → /admin/*
# ─────────────────────────────────────────
@app.before_request
def admin_subdomain_redirect():
    host = request.headers.get('Host', '').lower().split(':')[0]
    # admin.* alt alan adını tamamen kapatıp ana domaine yönlendir
    if host.startswith('admin.'):
        target = f"https://www.kyahukukdanismanlik.site{request.full_path.rstrip('?')}"
        return redirect(target, code=301)


# ─────────────────────────────────────────
# GitHub görsel depolama yardımcıları
# ─────────────────────────────────────────
_GH_OWNER  = 'demirarif'
_GH_REPO   = 'KYA-Hukuk'
_GH_BRANCH = 'main'
_GH_API    = f'https://api.github.com/repos/{_GH_OWNER}/{_GH_REPO}/contents'


def _gh_headers():
    token = os.environ.get('GITHUB_TOKEN', '')
    return {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
    }


def _upload_to_github(filename, file_bytes):
    """Dosyayı repo'ya kaydeder, raw URL döndürür."""
    path = f'static/uploads/{filename}'
    api_url = f'{_GH_API}/{path}'
    headers = _gh_headers()

    # Mevcut dosyayı kontrol et (güncelleme için sha gerekli)
    existing = http_client.get(api_url, headers=headers, timeout=10)
    sha = existing.json().get('sha') if existing.status_code == 200 else None

    payload = {
        'message': f'upload: {filename}',
        'content': base64.b64encode(file_bytes).decode(),
        'branch': _GH_BRANCH,
    }
    if sha:
        payload['sha'] = sha

    resp = http_client.put(api_url, json=payload, headers=headers, timeout=30)
    if resp.status_code in (200, 201):
        return f'https://raw.githubusercontent.com/{_GH_OWNER}/{_GH_REPO}/{_GH_BRANCH}/{path}'
    app.logger.error(f'GitHub yükleme hatası {resp.status_code}: {resp.text[:200]}')
    return None


def _delete_from_github(url):
    """raw.githubusercontent.com URL'si verilen dosyayı repo'dan siler."""
    if not url or 'raw.githubusercontent.com' not in url:
        return
    if not os.environ.get('GITHUB_TOKEN'):
        return
    try:
        # URL: https://raw.githubusercontent.com/owner/repo/branch/path
        after = url.split('raw.githubusercontent.com/', 1)[-1]
        parts = after.split('/', 3)   # owner / repo / branch / path
        if len(parts) < 4:
            return
        _, _, branch, path = parts
        api_url = f'{_GH_API}/{path}'
        headers = _gh_headers()
        existing = http_client.get(api_url, headers=headers, timeout=10)
        if existing.status_code != 200:
            return
        sha = existing.json().get('sha')
        http_client.delete(
            api_url,
            json={'message': f'delete: {path}', 'sha': sha, 'branch': branch},
            headers=headers,
            timeout=15,
        )
    except Exception as e:
        app.logger.warning(f'GitHub silme hatası: {e}')


# ─────────────────────────────────────────
# Yardımcı fonksiyonlar
# ─────────────────────────────────────────
def allowed_file(filename):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS'])


def slugify(text):
    text = text.lower().strip()
    replacements = {
        'ş': 's', 'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ö': 'o', 'ç': 'c',
        'Ş': 's', 'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ö': 'o', 'Ç': 'c'
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text.strip('-')


@app.route('/static/uploads/logo.png')
def legacy_logo_png():
    """Geride kalan .png logo referansları için .svg placeholder döndür."""
    try:
        return send_from_directory(os.path.join(app.root_path, 'Assets'), 'logo-color.png')
    except Exception:
        return '', 204


@app.route('/Assets/<path:filename>')
def assets_file(filename):
    """Assets klasörünü doğrudan servis et (logo/hero)."""
    try:
        return send_from_directory(os.path.join(app.root_path, 'Assets'), filename)
    except Exception:
        return '', 404


def save_upload(file):
    """
    Dosyayı kaydeder ve URL döndürür.
    - GITHUB_TOKEN varsa → GitHub repo'ya yükler (kalıcı, tavsiye edilen)
    - Yoksa → yerel static/uploads/ (geliştirme ortamı)
    """
    if not file or file.filename == '':
        return None
    if not allowed_file(file.filename):
        flash('Geçersiz dosya formatı.', 'error')
        return None

    filename = secure_filename(file.filename)
    base, ext = os.path.splitext(filename)
    filename = f"{base}_{int(datetime.utcnow().timestamp())}{ext}"
    file_bytes = file.read()

    # GitHub depolama (production)
    if os.environ.get('GITHUB_TOKEN'):
        url = _upload_to_github(filename, file_bytes)
        if not url:
            flash('Görsel GitHub\'a yüklenemedi. GITHUB_TOKEN yetkilerini kontrol edin.', 'error')
        return url

    # Vercel'de token yoksa uyarı ver (salt okunur dosya sistemi)
    if os.environ.get('VERCEL') == '1':
        flash('Vercel ortamında görsel yüklemek için GITHUB_TOKEN gereklidir!', 'error')
        return None

    # Yerel geliştirme
    upload_dir = app.config['UPLOAD_FOLDER']
    try:
        os.makedirs(upload_dir, exist_ok=True)
        filepath = os.path.join(upload_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(file_bytes)
        return f'/static/uploads/{filename}'
    except OSError as e:
        app.logger.error(f'Dosya kayıt hatası: {e}')
        flash('Yerel dosya kayıt hatası.', 'error')
        return None


_settings_cache: dict = {}
_settings_cache_ts: float = 0.0
_SETTINGS_TTL = 60  # saniye — admin kaydettiğinde cache sıfırlanır


def site_settings() -> dict:
    """Tüm ayarları dict olarak döndürür; 60 sn TTL önbellek kullanır."""
    global _settings_cache, _settings_cache_ts
    if time.time() - _settings_cache_ts < _SETTINGS_TTL and _settings_cache:
        return _settings_cache
    try:
        _settings_cache = {s.key: s.value for s in SiteSetting.query.all()}
        _settings_cache_ts = time.time()
    except Exception:
        # DB geçici erişilemez — mevcut cache veya boş dict döndür
        if not _settings_cache:
            _settings_cache = {}
    return _settings_cache


def _invalidate_settings_cache():
    """Admin bir ayar değiştirdiğinde cache'i hemen sıfırla."""
    global _settings_cache_ts
    _settings_cache_ts = 0.0


@app.context_processor
def inject_settings():
    """Tüm şablonlarda settings ve unread_messages_count hazır bulunsun."""
    try:
        ctx = {'settings': site_settings()}
    except Exception:
        ctx = {'settings': {}}
    ctx['unread_messages_count'] = 0
    # Admin sayfalarında okunmamış mesaj sayısını ekle (sidebar badge için)
    if request.endpoint and request.endpoint.startswith('admin_') and current_user.is_authenticated:
        try:
            ctx['unread_messages_count'] = ContactMessage.query.filter_by(is_read=False).count()
        except Exception:
            ctx['unread_messages_count'] = 0
    return ctx


# ─────────────────────────────────────────
# PUBLIC ROTALAR
# ─────────────────────────────────────────

@app.route('/')
def index():
    hero = HeroSection.query.filter_by(page='index').first()
    areas = PracticeArea.query.filter_by(is_active=True).order_by(PracticeArea.order_index).limit(6).all()
    articles = Article.query.filter_by(is_published=True).order_by(Article.published_at.desc()).limit(3).all()
    return render_template('index.html', hero=hero, areas=areas, articles=articles)


@app.route('/hakkimizda')
def hakkimizda():
    hero = HeroSection.query.filter_by(page='hakkimizda').first()
    return render_template('hakkimizda.html', hero=hero)


@app.route('/ekibimiz')
def ekibimiz():
    hero = HeroSection.query.filter_by(page='ekibimiz').first()
    team = TeamMember.query.filter_by(is_active=True).order_by(TeamMember.order_index).all()
    return render_template('ekibimiz.html', hero=hero, team=team)


@app.route('/faaliyet')
def faaliyet():
    hero = HeroSection.query.filter_by(page='faaliyet').first()
    areas = PracticeArea.query.filter_by(is_active=True).order_by(PracticeArea.order_index).all()
    return render_template('faaliyet.html', hero=hero, areas=areas)


@app.route('/makaleler')
def makaleler():
    hero = HeroSection.query.filter_by(page='makaleler').first()
    articles = Article.query.filter_by(is_published=True).order_by(Article.published_at.desc()).all()
    return render_template('makaleler.html', hero=hero, articles=articles)


@app.route('/makaleler/<slug>')
def makale_detay(slug):
    article = Article.query.filter_by(slug=slug, is_published=True).first_or_404()
    return render_template('makale_detay.html', article=article)


@app.route('/iletisim')
def iletisim():
    hero = HeroSection.query.filter_by(page='iletisim').first()
    return render_template('iletisim.html', hero=hero)


@app.route('/iletisim/gonder', methods=['POST'])
def iletisim_gonder():
    """İletişim formu — doğrulama, DB kaydı."""
    name    = request.form.get('name', '').strip()
    email   = request.form.get('email', '').strip()
    subject = request.form.get('subject', '').strip()
    message = request.form.get('message', '').strip()

    # Sunucu tarafı doğrulama
    errors = []
    if len(name) < 2:
        errors.append('Ad Soyad en az 2 karakter olmalı.')
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        errors.append('Geçerli bir e-posta adresi girin.')
    if len(message) < 10:
        errors.append('Mesajınız en az 10 karakter olmalı.')
    if errors:
        for err in errors:
            flash(err, 'error')
        return redirect(url_for('iletisim'))

    msg = ContactMessage(
        name=name,
        email=email,
        subject=subject or None,
        message=message,
        ip_address=request.remote_addr,
    )
    db.session.add(msg)
    db.session.commit()
    app.logger.info(f"İletişim formu kaydedildi: {name} <{email}>")
    flash('Mesajınız alındı. En kısa sürede dönüş yapacağız.', 'success')
    return redirect(url_for('iletisim'))


# ─────────────────────────────────────────
# ADMIN ROTALAR
# ─────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=False)
            session.permanent = True   # 3 saatlik PERMANENT_SESSION_LIFETIME tetiklenir
            next_page = request.args.get('next')
            return redirect(next_page or url_for('admin_dashboard'))
        flash('Kullanıcı adı veya şifre yanlış.', 'error')
    return render_template('admin/login.html')


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for('admin_login'))


@app.route('/admin')
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    stats = {
        'team': TeamMember.query.filter_by(is_active=True).count(),
        'articles': Article.query.filter_by(is_published=True).count(),
        'drafts': Article.query.filter_by(is_published=False).count(),
        'areas': PracticeArea.query.filter_by(is_active=True).count(),
        'total_messages': ContactMessage.query.count(),
        'unread_messages': ContactMessage.query.filter_by(is_read=False).count(),
    }
    recent_articles = Article.query.order_by(Article.created_at.desc()).limit(5).all()
    return render_template('admin/dashboard.html', stats=stats, recent_articles=recent_articles)


# ── Ekip ──────────────────────────────────

@app.route('/admin/ekip')
@login_required
def admin_team():
    team = TeamMember.query.order_by(TeamMember.order_index).all()
    return render_template('admin/team.html', team=team)


@app.route('/admin/ekip/yeni', methods=['GET', 'POST'])
@login_required
def admin_team_new():
    if request.method == 'POST':
        photo_url = save_upload(request.files.get('photo'))
        member = TeamMember(
            name=request.form['name'],
            role=request.form.get('role', ''),
            bio=request.form.get('bio', ''),
            linkedin_url=request.form.get('linkedin_url', ''),
            photo_url=photo_url or request.form.get('photo_url_ext', ''),
            order_index=int(request.form.get('order_index', 0)),
            is_active='is_active' in request.form,
        )
        db.session.add(member)
        db.session.commit()
        flash(f'"{member.name}" eklendi.', 'success')
        return redirect(url_for('admin_team'))
    return render_template('admin/team_form.html', member=None)


@app.route('/admin/ekip/<int:mid>/duzenle', methods=['GET', 'POST'])
@login_required
def admin_team_edit(mid):
    member = db.session.get(TeamMember, mid) or abort(404)
    if request.method == 'POST':
        new_photo = save_upload(request.files.get('photo'))
        member.name = request.form['name']
        member.role = request.form.get('role', '')
        member.bio = request.form.get('bio', '')
        member.linkedin_url = request.form.get('linkedin_url', '')
        if new_photo:
            _delete_from_github(member.photo_url)  # eski görseli sil
            member.photo_url = new_photo
        elif request.form.get('photo_url_ext'):
            _delete_from_github(member.photo_url)
            member.photo_url = request.form['photo_url_ext']
        member.order_index = int(request.form.get('order_index', 0))
        member.is_active = 'is_active' in request.form
        db.session.commit()
        flash(f'"{member.name}" güncellendi.', 'success')
        return redirect(url_for('admin_team'))
    return render_template('admin/team_form.html', member=member)


@app.route('/admin/ekip/<int:mid>/sil', methods=['POST'])
@login_required
def admin_team_delete(mid):
    member = db.session.get(TeamMember, mid) or abort(404)
    _delete_from_github(member.photo_url)  # görseli repo'dan sil
    db.session.delete(member)
    db.session.commit()
    flash(f'"{member.name}" silindi.', 'info')
    return redirect(url_for('admin_team'))


# ── Makaleler ─────────────────────────────

@app.route('/admin/makaleler')
@login_required
def admin_articles():
    articles = Article.query.order_by(Article.created_at.desc()).all()
    return render_template('admin/articles.html', articles=articles)


@app.route('/admin/makaleler/yeni', methods=['GET', 'POST'])
@login_required
def admin_article_new():
    if request.method == 'POST':
        title = request.form['title']
        custom_slug = request.form.get('slug', '').strip()
        slug = slugify(custom_slug) if custom_slug else slugify(title)
        # Slug benzersizliği
        base_slug, counter = slug, 1
        while Article.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1
        cover_url = save_upload(request.files.get('cover'))
        is_published = 'is_published' in request.form
        article = Article(
            title=title,
            slug=slug,
            summary=request.form.get('summary', ''),
            content=request.form.get('content', ''),
            author=request.form.get('author', ''),
            cover_url=cover_url or request.form.get('cover_url_ext', ''),
            is_published=is_published,
            published_at=datetime.utcnow() if is_published else None,
        )
        db.session.add(article)
        db.session.commit()
        flash(f'"{article.title}" eklendi.', 'success')
        return redirect(url_for('admin_articles'))
    return render_template('admin/article_form.html', article=None)


@app.route('/admin/makaleler/<int:aid>/duzenle', methods=['GET', 'POST'])
@login_required
def admin_article_edit(aid):
    article = db.session.get(Article, aid) or abort(404)
    if request.method == 'POST':
        new_cover = save_upload(request.files.get('cover'))
        article.title = request.form['title']
        # Slug güncelleme (doluysa kullan, boşsa mevcut koru)
        custom_slug = request.form.get('slug', '').strip()
        if custom_slug and custom_slug != article.slug:
            new_slug = slugify(custom_slug)
            if not Article.query.filter(Article.id != article.id, Article.slug == new_slug).first():
                article.slug = new_slug
        article.summary = request.form.get('summary', '')
        article.content = request.form.get('content', '')
        article.author = request.form.get('author', '')
        if new_cover:
            _delete_from_github(article.cover_url)  # eski kapağı sil
            article.cover_url = new_cover
        elif request.form.get('cover_url_ext'):
            _delete_from_github(article.cover_url)
            article.cover_url = request.form['cover_url_ext']
        was_published = article.is_published
        article.is_published = 'is_published' in request.form
        if article.is_published and not was_published:
            article.published_at = datetime.utcnow()
        db.session.commit()
        flash(f'"{article.title}" güncellendi.', 'success')
        return redirect(url_for('admin_articles'))
    return render_template('admin/article_form.html', article=article)


@app.route('/admin/makaleler/<int:aid>/sil', methods=['POST'])
@login_required
def admin_article_delete(aid):
    article = db.session.get(Article, aid) or abort(404)
    _delete_from_github(article.cover_url)  # kapak görselini repo'dan sil
    db.session.delete(article)
    db.session.commit()
    flash(f'"{article.title}" silindi.', 'info')
    return redirect(url_for('admin_articles'))


# ── Çalışma Alanları ──────────────────────

@app.route('/admin/alanlar')
@login_required
def admin_areas():
    areas = PracticeArea.query.order_by(PracticeArea.order_index).all()
    return render_template('admin/practice_areas.html', areas=areas)


@app.route('/admin/alanlar/kaydet', methods=['POST'])
@login_required
def admin_areas_save():
    """Tüm alanları toplu kaydet/güncelle"""
    PracticeArea.query.delete()
    titles      = request.form.getlist('title[]')
    descs       = request.form.getlist('desc[]')
    icons       = request.form.getlist('icon[]')
    image_urls  = request.form.getlist('image_url[]')   # mevcut/dış URL
    image_files = request.files.getlist('image_file[]')  # yeni yükleme
    actives     = request.form.getlist('active[]')
    for i, title in enumerate(titles):
        if not title.strip():
            continue
        # Görsel: yeni dosya yüklendiyse kullan, yoksa mevcut URL'yi koru
        uploaded = save_upload(image_files[i] if i < len(image_files) else None)
        area_image = uploaded or (image_urls[i].strip() if i < len(image_urls) else '')
        area = PracticeArea(
            title=title.strip(),
            description=descs[i] if i < len(descs) else '',
            icon=icons[i] if i < len(icons) else 'fas fa-gavel',
            image_url=area_image or None,
            order_index=i,
            is_active=(str(i) in actives),
        )
        db.session.add(area)
    db.session.commit()
    flash('Çalışma alanları güncellendi.', 'success')
    return redirect(url_for('admin_areas'))


# ── Sayfa Hero Görselleri ─────────────────

@app.route('/admin/hero')
@login_required
def admin_hero():
    heroes = {h.page: h for h in HeroSection.query.all()}
    pages = [
        ('index', 'Anasayfa'),
        ('hakkimizda', 'Hakkımızda'),
        ('ekibimiz', 'Ekibimiz'),
        ('faaliyet', 'Çalışma Alanları'),
        ('makaleler', 'Makaleler'),
        ('iletisim', 'İletişim'),
    ]
    return render_template('admin/hero.html', heroes=heroes, pages=pages)


@app.route('/admin/hero/kaydet', methods=['POST'])
@login_required
def admin_hero_save():
    page = request.form.get('page')
    hero = HeroSection.query.filter_by(page=page).first() or HeroSection(page=page)
    hero.title = request.form.get('title', '')
    hero.subtitle = request.form.get('subtitle', '')
    new_img = save_upload(request.files.get('image'))
    if new_img:
        hero.image_url = new_img
    elif request.form.get('image_url_ext'):
        hero.image_url = request.form['image_url_ext']
    if not hero.id:
        db.session.add(hero)
    db.session.commit()
    flash(f'Hero güncellendi.', 'success')
    return redirect(url_for('admin_hero'))


# ── İletişim & Ayarlar ────────────────────

@app.route('/admin/ayarlar', methods=['GET', 'POST'])
@login_required
def admin_settings():
    # Desteklenen tüm ayar anahtarları (beyaz liste)
    setting_defs = [
        ('contact_address', 'Adres', 'textarea'),
        ('contact_phone', 'Telefon', 'text'),
        ('contact_email', 'E-posta', 'email'),
        ('contact_hours', 'Çalışma Saatleri', 'text'),
        ('google_maps_embed', 'Google Maps Embed URL', 'text'),
        ('whatsapp_number', 'WhatsApp Numarası (ülke koduyla, başında + olmadan, örn: 905312345678)', 'text'),
        ('about_short', 'Kısa Tanıtım (Alt Başlık)', 'textarea'),
        ('logo_url', 'Logo URL (Açık Zemin - Renkli)', 'text'),
        ('logo_white_url', 'Logo URL (Koyu Zemin - Beyaz)', 'text'),
        ('footer_text', 'Footer Metin', 'textarea'),
        ('seo_desc_index', 'SEO Açıklaması — Anasayfa', 'textarea'),
        ('seo_desc_hakkimizda', 'SEO Açıklaması — Hakkımızda', 'textarea'),
        ('seo_desc_ekibimiz', 'SEO Açıklaması — Ekibimiz', 'textarea'),
        ('seo_desc_faaliyet', 'SEO Açıklaması — Çalışma Alanları', 'textarea'),
        ('seo_desc_makaleler', 'SEO Açıklaması — Makaleler', 'textarea'),
        ('seo_desc_iletisim', 'SEO Açıklaması — İletişim', 'textarea'),
    ]
    if request.method == 'POST':
        # Her form yalnızca kendi alanını gönderir;
        # sadece POST'ta gelen key'leri kaydet (diğerlerini silme)
        for key, _, _ in setting_defs:
            if key in request.form:
                SiteSetting.set(key, request.form.get(key, ''))
        # Logo dosya yükleme (logo kartı gönderildiğinde)
        logo_file = request.files.get('logo_file')
        if logo_file and logo_file.filename:
            url = save_upload(logo_file)
            if url:
                SiteSetting.set('logo_url', url)
        logo_white_file = request.files.get('logo_white_file')
        if logo_white_file and logo_white_file.filename:
            url = save_upload(logo_white_file)
            if url:
                SiteSetting.set('logo_white_url', url)
        flash('Kaydedildi.', 'success')
        _invalidate_settings_cache()
        return redirect(url_for('admin_settings'))
    settings = {s.key: s.value for s in SiteSetting.query.all()}
    return render_template('admin/settings.html', settings=settings, setting_defs=setting_defs)


# ── Şifre Değiştir ────────────────────────

@app.route('/admin/sifre', methods=['GET', 'POST'])
@login_required
def admin_change_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new1 = request.form.get('new_password', '')
        new2 = request.form.get('new_password2', '')
        if not current_user.check_password(current):
            flash('Mevcut şifre yanlış.', 'error')
        elif new1 != new2:
            flash('Yeni şifreler eşleşmiyor.', 'error')
        elif len(new1) < 8:
            flash('Şifre en az 8 karakter olmalı.', 'error')
        else:
            current_user.set_password(new1)
            db.session.commit()
            flash('Şifre güncellendi.', 'success')
        return redirect(url_for('admin_change_password'))
    return render_template('admin/change_password.html')


# ── İletişim Mesajları ─────────────────────

@app.route('/admin/mesajlar')
@login_required
def admin_messages():
    msgs = ContactMessage.query.order_by(ContactMessage.created_at.desc()).all()
    unread_count = ContactMessage.query.filter_by(is_read=False).count()
    return render_template('admin/messages.html', msgs=msgs, unread_count=unread_count)


@app.route('/admin/mesajlar/<int:mid>/oku', methods=['POST'])
@login_required
def admin_message_read(mid):
    msg = db.session.get(ContactMessage, mid) or abort(404)
    msg.is_read = True
    db.session.commit()
    return redirect(url_for('admin_messages'))


@app.route('/admin/mesajlar/<int:mid>/sil', methods=['POST'])
@login_required
def admin_message_delete(mid):
    msg = db.session.get(ContactMessage, mid) or abort(404)
    db.session.delete(msg)
    db.session.commit()
    flash('Mesaj silindi.', 'info')
    return redirect(url_for('admin_messages'))


# ─────────────────────────────────────────
# Uploads serve + Favicon
# ─────────────────────────────────────────
@app.route('/static/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/favicon.ico')
@app.route('/favicon.svg')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        'favicon.svg',
        mimetype='image/svg+xml',
    )


@app.route('/admin/logo-sifirla', methods=['POST'])
@login_required
def admin_logo_reset():
    """DB'deki logo URL'lerini varsayılan değerlerle zorla güncelle."""
    _LOGO = '/Assets/logo-color.webp'
    _LOGO_WHITE = '/Assets/logo-disi.webp'
    SiteSetting.set('logo_url', _LOGO)
    SiteSetting.set('logo_white_url', _LOGO_WHITE)
    _invalidate_settings_cache()
    flash('Logolar varsayılana sıfırlandı.', 'success')
    return redirect(url_for('admin_settings'))


# ─────────────────────────────────────────
# DB başlatma & seed
# ─────────────────────────────────────────
def init_db():
    with app.app_context():
        db.create_all()

        # ── İzin verilen kullanıcıları SADECE env var’dan oku — koda kimlik bilgisi yazılmaz ──
        _env_users = {}
        for _prefix in ('ADMIN', 'USER2', 'USER3', 'USER4'):
            _u = (app.config.get(f'{_prefix}_USERNAME') or '').strip()
            _p = (app.config.get(f'{_prefix}_PASSWORD') or '').strip()
            if _u and _p:
                _env_users[_u] = _p

        # DB’de env’de tanımlı olmayan kullanıcıları sil
        # (Birisi doğrustan DB’ye kullanıcı ekleyemez — her deploy/cold-start temizlenir)
        for _usr in User.query.all():
            if _usr.username not in _env_users:
                db.session.delete(_usr)
        db.session.flush()

        # Env'de tanımlı her kullanıcıyı oluştur veya etkinleştir
        # NOT: Zaten varsa şifreye dokunma — dashboard'dan değiştirilebilir
        for _uname, _upass in _env_users.items():
            _usr = User.query.filter_by(username=_uname).first()
            if not _usr:
                _usr = User(username=_uname, is_active=True)
                _usr.set_password(_upass)
                db.session.add(_usr)
                print(f'✓ Yeni kullanıcı oluşturuldu: {_uname}')
            else:
                _usr.is_active = True  # devre dışıysa yeniden etkinleştir
        db.session.commit()
        print(f'✓ Kullanıcılar senkronize: {list(_env_users.keys())}')

        # ── Varsayılan ayarlar (tek sorguda toplu kontrol) ──────────────────
        defaults = {
            'contact_address': 'Balgat Mahallesi, Ziyabey Caddesi No: 14/8, Çankaya / ANKARA',
            'contact_phone': '+90 312 123 45 67',
            'contact_email': 'info@kyahukukdanismanlik.com',
            'contact_hours': 'Pazartesi - Cuma: 09:00 - 18:00',
            'about_short': 'Ulusal ve uluslararası hukuki danışmanlık & avukatlık hizmetleri.',
            'footer_text': '© 2026 KYA Hukuk ve Danışmanlık. Tüm hakları saklıdır.',
            'logo_url': '/Assets/logo-color.webp',
            'logo_white_url': '/Assets/logo-disi.webp',
            'google_maps_embed': 'https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3059.424507449123!2d32.8322003!3d39.9443787!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x14d34f0a4309eec5%3A0x77936d1cd6fe2fde!2sKYA%20HUKUK%20ve%20DANI%C5%9FMANLIK!5e0!3m2!1str!2str!4v1700000000000!5m2!1str!2str',
            'whatsapp_number': '',
            'home_practice_title': 'Çalışma Alanlarımız',
            'home_practice_subtitle': 'Başlıca uzmanlık alanlarımızı keşfedin.',
            'home_articles_title': 'Son Makaleler',
            'home_articles_subtitle': 'Güncel hukuki içerikler ve makaleler.',
            'about_values_title': 'Değerlerimiz',
            'about_values_subtitle': '',
            'team_section_title': 'Avukat Kadromuz',
            'team_section_subtitle': '',
            'areas_section_title': 'Çalışma Alanları',
            'areas_section_subtitle': '',
            'articles_section_title': 'Güncel Makaleler',
            'articles_section_subtitle': '',
            'contact_section_title': 'İletişim',
            'contact_section_subtitle': 'Sorularınız ve hukuki danışmanlık talepleriniz için bize ulaşın.',
            # Sayfa bazlı SEO meta açıklamaları
            'seo_desc_index':      'KYA Hukuk ve Danışmanlık — Ankara\'da ticaret, ceza, idare ve özel hukuk alanlarında uzman avukatlık hizmetleri.',
            'seo_desc_hakkimizda': 'Keleştemur | Yiğit | Altay Hukuk ve Danışmanlık Ofisi hakkında bilgi alın. 2020\'den bu yana Ankara\'da güvenilir hukuki danışmanlık.',
            'seo_desc_ekibimiz':   'KYA Hukuk avukat kadrosu: Av. Mehmet Emre Yiğit, Av. Tevfik Keleştemur, Av. Direnç Onat Altay ve diğer uzman hukukçularımız.',
            'seo_desc_faaliyet':   'KYA Hukuk çalışma alanları: Ticaret Hukuku, Ceza Hukuku, İdare Hukuku, Sözleşmeler, Fikri Mülkiyet ve daha fazlası.',
            'seo_desc_makaleler':  'KYA Hukuk güncel hukuki makaleler, içtihat değerlendirmeleri ve hukuki bilgi yazıları.',
            'seo_desc_iletisim':   'KYA Hukuk ve Danışmanlık ile iletişime geçin. Ankara Balgat ofisimiz, telefon ve e-posta bilgilerimiz.',
        }
        # Bir sorguda mevcut tüm key'leri çek; döngü içinde tek tek sorgu yok
        existing_settings = {s.key: s for s in SiteSetting.query.all()}
        for key, value in defaults.items():
            if key not in existing_settings:
                db.session.add(SiteSetting(key=key, value=value))

        # ── Kolon migrasyonu: PracticeArea.image_url (mevcut tablo varsa ALTER TABLE) ──
        from sqlalchemy import text as _text, inspect as _inspect
        try:
            inspector = _inspect(db.engine)
            cols = [c['name'] for c in inspector.get_columns('practice_area')]
            if 'image_url' not in cols:
                with db.engine.connect() as _conn:
                    _conn.execute(_text('ALTER TABLE practice_area ADD COLUMN image_url VARCHAR(500)'))
                    _conn.commit()
                print('✓ practice_area.image_url kolonu eklendi.')
        except Exception as _migr_err:
            print(f'[migration] practice_area.image_url: {_migr_err}')

        # PNG → WebP migration (aynı existing_settings dict'ten yararlan)
        _MIGRATIONS = {
            'logo_url':       ('/Assets/logo-color.png',  '/Assets/logo-color.webp'),
            'logo_white_url': ('/Assets/logo-disi.png',   '/Assets/logo-disi.webp'),
        }
        for key, (old_val, new_val) in _MIGRATIONS.items():
            row = existing_settings.get(key)
            if row and row.value == old_val:
                row.value = new_val

        # Harita embed: eski q= parametre URL'sini embed URL'siyle değiştir
        _MAP = 'https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3059.424507449123!2d32.8322003!3d39.9443787!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x14d34f0a4309eec5%3A0x77936d1cd6fe2fde!2sKYA%20HUKUK%20ve%20DANI%C5%9EMANLIK!5e0!3m2!1str!2str!4v1700000000000!5m2!1str!2str'
        map_row = existing_settings.get('google_maps_embed')
        if map_row and ('maps?q=' in str(map_row.value or '') or not map_row.value):
            map_row.value = _MAP

        # ── Hero bölümleri (tek sorguda toplu kontrol) ───────────────────────
        hero_defaults = [
            ('index', 'KELEŞTEMUR | YİĞİT | ALTAY', 'HUKUK VE DANIŞMANLIK', '/Assets/Atakule3.webp'),
            ('hakkimizda', 'Hakkımızda', 'Hukukun Üstünlüğü ve Adalet İçin Buradayız',
             'https://images.unsplash.com/photo-1505664194779-8beaceb93744?w=1600&q=80'),
            ('ekibimiz', 'Avukat Kadromuz', 'Uzman ve Deneyimli Hukukçularımız',
             'https://images.unsplash.com/photo-1521791136064-7986c2920216?w=1600&q=80'),
            ('faaliyet', 'Çalışma Alanları', 'Başlıca Uzmanlık Alanlarımız',
             'https://images.unsplash.com/photo-1589994965851-a8f479c573a9?w=1600&q=80'),
            ('makaleler', 'Makaleler', 'Hukuki Bilgi Köşesi',
             'https://images.unsplash.com/photo-1456324504439-367cee3b3c32?w=1600&q=80'),
            ('iletisim', 'İletişim', 'Bize Ulaşın',
             'https://images.unsplash.com/photo-1497366412874-3415097a27e7?w=1600&q=80'),
        ]
        existing_heroes = {h.page: h for h in HeroSection.query.all()}
        for page, title, subtitle, image_url in hero_defaults:
            if page not in existing_heroes:
                db.session.add(HeroSection(page=page, title=title, subtitle=subtitle, image_url=image_url))

        # Hero WebP migration
        idx_hero = existing_heroes.get('index')
        if idx_hero and idx_hero.image_url in ('/static/uploads/Atakule3.png', '/static/uploads/Atakule3.webp'):
            idx_hero.image_url = '/Assets/Atakule3.webp'

        # Ekip üyeleri
        if TeamMember.query.count() == 0:
            members = [
                TeamMember(
                    name='Av. Mehmet Emre Yiğit', role='Kurucu Ortak', order_index=0,
                    bio='Başkent Üniversitesi Hukuk Fakültesi mezunudur. Ticaret Hukuku ve Şirketler Hukuku alanlarında uzmanlaşmıştır.',
                    linkedin_url='https://www.linkedin.com/in/mehmetemreyigit/',
                    photo_url='https://images.unsplash.com/photo-1560250097-0b93528c311a?w=400&q=80',
                ),
                TeamMember(
                    name='Av. Tevfik Keleştemur', role='Kurucu Ortak', order_index=1,
                    bio='Ceza Hukuku ve İdare Hukuku alanlarında derinlemesine tecrübeye sahiptir.',
                    linkedin_url='https://www.linkedin.com/in/tevfik-kele%C5%9Ftemur-06ab04104/',
                    photo_url='https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=400&q=80',
                ),
                TeamMember(
                    name='Av. Direnç Onat Altay', role='Kurucu Ortak', order_index=2,
                    bio='Özel Hukuk, Sözleşmeler Hukuku ve Fikri Mülkiyet Hukuku alanlarında çalışmaktadır.',
                    linkedin_url='https://www.linkedin.com/in/diren%C3%A7-onat-altay-30b2491b3/',
                    photo_url='https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=400&q=80',
                ),
            ]
            db.session.add_all(members)

        # Çalışma alanları
        if PracticeArea.query.count() == 0:
            areas = [
                PracticeArea(title='Ticaret ve Şirketler Hukuku', icon='fas fa-briefcase', order_index=0,
                    description='Şirket kuruluşları, birleşme ve devralmalar, ticari sözleşmeler ve kurumsal yönetim danışmanlığı.'),
                PracticeArea(title='Ceza Hukuku', icon='fas fa-gavel', order_index=1,
                    description='Soruşturma ve kovuşturma aşamalarında şüpheli, sanık veya mağdur vekilliği, ağır ceza davaları.'),
                PracticeArea(title='İdare Hukuku', icon='fas fa-landmark', order_index=2,
                    description='İdari işlemlerin iptali, tam yargı davaları, kamulaştırma ve devlet ihaleleri süreçleri.'),
                PracticeArea(title='Özel Hukuk', icon='fas fa-users', order_index=3,
                    description='Kişiler hukuku, aile hukuku, miras hukuku ve medeni hukuktan doğan her türlü uyuşmazlığın çözümü.'),
                PracticeArea(title='Sözleşmeler Hukuku', icon='fas fa-file-signature', order_index=4,
                    description='Sözleşme hazırlama, inceleme, müzakere süreçleri ve sözleşmeden doğan itilafların giderilmesi.'),
                PracticeArea(title='Fikri Mülkiyet Hukuku', icon='fas fa-lightbulb', order_index=5,
                    description='Marka, patent, tasarım tescili, telif haklarının korunması ve haksız rekabet davaları.'),
            ]
            db.session.add_all(areas)

        # Örnek makale
        if Article.query.count() == 0:
            articles = [
                Article(
                    title='Vergi Anlaşmazlığı ve Vergi Uyuşmazlığı Kavramları',
                    slug='vergi-anlasmazligi-ve-vergi-uyusmazligi-kavramlari',
                    summary='Vergi hukuku kapsamında karşılaşılan temel kavramlar ve süreçler hakkında bilgilendirme.',
                    content='<p>Vergi anlaşmazlığı, mükellef ile vergi idaresi arasında ortaya çıkan görüş ayrılıklarını ifade eder...</p>',
                    author='KYA Hukuk',
                    is_published=True,
                    published_at=datetime(2024, 6, 1),
                ),
                Article(
                    title='Proforma Dolandırıcılığı',
                    slug='proforma-dolandiricilik',
                    summary='Uluslararası ticarette sıkça rastlanan proforma fatura dolandırıcılığı ve hukuki korunma yolları.',
                    content='<p>Proforma dolandırıcılığı, sahte proforma fatura kullanılarak gerçekleştirilen ticari sahtekarlık türüdür...</p>',
                    author='KYA Hukuk',
                    is_published=True,
                    published_at=datetime(2024, 9, 15),
                ),
            ]
            db.session.add_all(articles)

        db.session.commit()
        print("✓ Veritabanı ve varsayılan veriler hazır.")


# ─────────────────────────────────────────
# Sağlık kontrolü ve DB kurulum endpoint
# ─────────────────────────────────────────

@app.route('/health')
def health():
    """Vercel loglarında hata ayıklamak için durum bilgisi."""
    import sqlalchemy
    info = {
        'status': 'ok',
        'db_url': app.config['SQLALCHEMY_DATABASE_URI'][:40] + '...',
        'on_vercel': os.environ.get('VERCEL', 'no'),
    }
    try:
        with app.app_context():
            db.session.execute(sqlalchemy.text('SELECT 1'))
        info['db'] = 'connected'
    except Exception as e:
        info['db'] = f'ERROR: {e}'
    return jsonify(info)


@app.route('/setup')
def setup():
    """
    Tarayıcıdan DB kurulumunu tetikle.
    Kullanım: https://kyahukukdanismanlik.site/setup?key=<SETUP_KEY>
    SETUP_KEY env var'ı Vercel'de tanımlanmış olmalı.
    """
    key = request.args.get('key', '')
    expected = os.environ.get('SETUP_KEY', '')
    if not expected or key != expected:
        return jsonify({'error': 'Geçersiz anahtar'}), 403
    try:
        init_db()
        return jsonify({'status': 'ok', 'message': '✓ Veritabanı kuruldu ve veriler yüklendi.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ─────────────────────────────────────────
# Uygulama başlangıcında DB kur
# (Vercel serverless dahil her ortamda çalışır)
# ─────────────────────────────────────────
try:
    init_db()
except Exception as _init_err:
    print(f'[UYARI] init_db otomatik kurulum başarısız: {_init_err}')
    print('[BİLGİ] /setup?key=<SETUP_KEY> adresini ziyaret ederek manuel kurulum yapabilirsiniz.')

# ─────────────────────────────────────────
# Entry point (lokal geliştirme)
# ─────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True, port=5000)
