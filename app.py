import os
import re
import base64
import requests as http_client
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, redirect, url_for,
                   request, flash, abort, jsonify, send_from_directory)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename

from config import Config
from models import db, User, SiteSetting, HeroSection, TeamMember, PracticeArea, Article

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


@app.route('/favicon.ico')
def favicon():
    """Return favicon if present; avoid 404 noise in logs."""
    try:
        return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico')
    except Exception:
        return '', 204


@app.route('/static/uploads/logo.png')
def legacy_logo_png():
    """Geride kalan .png logo referansları için .svg placeholder döndür."""
    try:
        return send_from_directory(os.path.join(app.root_path, 'static', 'uploads'), 'logo.svg')
    except Exception:
        return '', 204


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


def site_settings():
    """Tüm ayarları dict olarak döndürür (template context)."""
    return {s.key: s.value for s in SiteSetting.query.all()}


@app.context_processor
def inject_settings():
    """Tüm şablonlarda settings değişkeni hazır bulunsun."""
    return {'settings': site_settings()}


# ─────────────────────────────────────────
# PUBLIC ROTALAR
# ─────────────────────────────────────────

@app.route('/')
def index():
    hero = HeroSection.query.filter_by(page='index').first()
    areas = PracticeArea.query.filter_by(is_active=True).order_by(PracticeArea.order_index).limit(6).all()
    articles = Article.query.filter_by(is_published=True).order_by(Article.published_at.desc()).limit(3).all()
    settings = site_settings()
    return render_template('index.html', hero=hero, areas=areas, articles=articles, settings=settings)


@app.route('/hakkimizda')
def hakkimizda():
    hero = HeroSection.query.filter_by(page='hakkimizda').first()
    settings = site_settings()
    return render_template('hakkimizda.html', hero=hero, settings=settings)


@app.route('/ekibimiz')
def ekibimiz():
    hero = HeroSection.query.filter_by(page='ekibimiz').first()
    team = TeamMember.query.filter_by(is_active=True).order_by(TeamMember.order_index).all()
    settings = site_settings()
    return render_template('ekibimiz.html', hero=hero, team=team, settings=settings)


@app.route('/faaliyet')
def faaliyet():
    hero = HeroSection.query.filter_by(page='faaliyet').first()
    areas = PracticeArea.query.filter_by(is_active=True).order_by(PracticeArea.order_index).all()
    settings = site_settings()
    return render_template('faaliyet.html', hero=hero, areas=areas, settings=settings)


@app.route('/makaleler')
def makaleler():
    hero = HeroSection.query.filter_by(page='makaleler').first()
    articles = Article.query.filter_by(is_published=True).order_by(Article.published_at.desc()).all()
    settings = site_settings()
    return render_template('makaleler.html', hero=hero, articles=articles, settings=settings)


@app.route('/makaleler/<slug>')
def makale_detay(slug):
    article = Article.query.filter_by(slug=slug, is_published=True).first_or_404()
    settings = site_settings()
    return render_template('makale_detay.html', article=article, settings=settings)


@app.route('/iletisim')
def iletisim():
    hero = HeroSection.query.filter_by(page='iletisim').first()
    settings = site_settings()
    return render_template('iletisim.html', hero=hero, settings=settings)


@app.route('/iletisim/gonder', methods=['POST'])
def iletisim_gonder():
    """İletişim formu (basit loglama, ileride e-posta entegre edilir)."""
    name = request.form.get('name', '')
    email = request.form.get('email', '')
    message = request.form.get('message', '')
    app.logger.info(f"İletişim formu: {name} <{email}>: {message[:100]}")
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
            login_user(user, remember=True)
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
    titles = request.form.getlist('title[]')
    descs  = request.form.getlist('desc[]')
    icons  = request.form.getlist('icon[]')
    actives = request.form.getlist('active[]')   # aktif checkbox indeksleri
    for i, title in enumerate(titles):
        if title.strip():
            area = PracticeArea(
                title=title.strip(),
                description=descs[i] if i < len(descs) else '',
                icon=icons[i] if i < len(icons) else 'fas fa-gavel',
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
    setting_defs = [
        ('contact_address', 'Adres', 'textarea'),
        ('contact_phone', 'Telefon', 'text'),
        ('contact_email', 'E-posta', 'email'),
        ('contact_hours', 'Çalışma Saatleri', 'text'),
        ('about_short', 'Kısa Tanıtım (Alt Başlık)', 'textarea'),
        ('logo_url', 'Logo URL (Açık Zemin - Renkli)', 'text'),
        ('logo_white_url', 'Logo URL (Koyu Zemin - Beyaz)', 'text'),
        ('footer_text', 'Footer Metin', 'textarea'),
        ('google_maps_embed', 'Google Maps Embed URL', 'text'),
        ('home_practice_title', 'Ana Sayfa - Çalışma Alanları Başlık', 'text'),
        ('home_practice_subtitle', 'Ana Sayfa - Çalışma Alanları Alt Başlık', 'text'),
        ('home_articles_title', 'Ana Sayfa - Makaleler Başlık', 'text'),
        ('home_articles_subtitle', 'Ana Sayfa - Makaleler Alt Başlık', 'text'),
        ('about_values_title', 'Hakkımızda - Değerlerimiz Başlık', 'text'),
        ('about_values_subtitle', 'Hakkımızda - Değerlerimiz Alt Başlık', 'text'),
        ('team_section_title', 'Ekibimiz - Bölüm Başlığı', 'text'),
        ('team_section_subtitle', 'Ekibimiz - Bölüm Alt Başlığı', 'text'),
        ('areas_section_title', 'Çalışma Alanları - Bölüm Başlığı', 'text'),
        ('areas_section_subtitle', 'Çalışma Alanları - Bölüm Alt Başlığı', 'text'),
        ('articles_section_title', 'Makaleler - Bölüm Başlığı', 'text'),
        ('articles_section_subtitle', 'Makaleler - Bölüm Alt Başlığı', 'text'),
        ('contact_section_title', 'İletişim Bölümü Başlık', 'text'),
        ('contact_section_subtitle', 'İletişim Bölümü Alt Başlık', 'text'),
    ]
    if request.method == 'POST':
        for key, _, _ in setting_defs:
            value = request.form.get(key, '')
            SiteSetting.set(key, value)
        # Logo dosya yükleme
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
                
        flash('Ayarlar kaydedildi.', 'success')
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


# ─────────────────────────────────────────
# Uploads serve
# ─────────────────────────────────────────
@app.route('/static/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/admin/logo-sifirla', methods=['POST'])
@login_required
def admin_logo_reset():
    """DB'deki logo URL'lerini yazısız.png ile zorla güncelle."""
    _LOGO = 'https://raw.githubusercontent.com/demirarif/KYA-Hukuk/main/static/uploads/logo-yaz%C4%B1s%C4%B1z.png'
    SiteSetting.set('logo_url', _LOGO)
    SiteSetting.set('logo_white_url', _LOGO)
    flash('Logolar varsayılana sıfırlandı.', 'success')
    return redirect(url_for('admin_settings'))


# ─────────────────────────────────────────
# DB başlatma & seed
# ─────────────────────────────────────────
def init_db():
    with app.app_context():
        db.create_all()
        admin_username = app.config['ADMIN_USERNAME']
        admin_password = app.config['ADMIN_PASSWORD']

        # Admin kullanıcı (mevcut 'admin' hesabını yeni kullanıcı adına taşır ve şifreyi günceller)
        admin = User.query.filter_by(username=admin_username).first()
        legacy_admin = User.query.filter_by(username='admin').first()
        if not admin:
            if legacy_admin:
                legacy_admin.username = admin_username
                legacy_admin.set_password(admin_password)
                admin = legacy_admin
            else:
                admin = User(username=admin_username)
                admin.set_password(admin_password)
                db.session.add(admin)
            db.session.commit()
            print(f"✓ Admin kullanıcı hazır: {admin_username}")

        # Varsayılan ayarlar
        defaults = {
            'contact_address': 'Balgat Mahallesi, Ziyabey Caddesi No: 14/8, Çankaya / ANKARA',
            'contact_phone': '+90 312 123 45 67',
            'contact_email': 'info@kyahukukdanismanlik.com',
            'contact_hours': 'Pazartesi - Cuma: 09:00 - 18:00',
            'about_short': 'Ulusal ve uluslararası hukuki danışmanlık & avukatlık hizmetleri.',
            'footer_text': '© 2026 KYA Hukuk ve Danışmanlık. Tüm hakları saklıdır.',
            'logo_url': 'https://raw.githubusercontent.com/demirarif/KYA-Hukuk/main/static/uploads/logo-yaz%C4%B1s%C4%B1z.png',
            'logo_white_url': 'https://raw.githubusercontent.com/demirarif/KYA-Hukuk/main/static/uploads/logo-yaz%C4%B1s%C4%B1z.png',
            'google_maps_embed': 'https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3059.424507449123!2d32.8322003!3d39.9443787!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x14d34f0a4309eec5%3A0x77936d1cd6fe2fde!2sKYA%20HUKUK%20ve%20DANI%C5%9FMANLIK!5e0!3m2!1str!2str!4v1700000000000!5m2!1str!2str',
            'home_practice_title': 'Çalışma Alanlarımız',
            'home_practice_subtitle': 'Başlıca uzmanlık alanlarımızı keşfedin.',
            'home_articles_title': 'Son Makaleler',
            'home_articles_subtitle': 'Güncel hukuki içerikler ve makaleler.',
            'contact_section_title': 'İletişim',
            'contact_section_subtitle': 'Sorularınız ve hukuki danışmanlık talepleriniz için bize ulaşın.',
        }
        for key, value in defaults.items():
            if not SiteSetting.query.filter_by(key=key).first():
                db.session.add(SiteSetting(key=key, value=value))

        # Logo: yazısız.png kullanıyorsa zaten doğru, değilse güncelle
        _LOGO_URL = 'https://raw.githubusercontent.com/demirarif/KYA-Hukuk/main/static/uploads/logo-yaz%C4%B1s%C4%B1z.png'
        for _key in ('logo_url', 'logo_white_url'):
            _s = SiteSetting.query.filter_by(key=_key).first()
            if _s and 'yaz' not in str(_s.value or ''):
                _s.value = _LOGO_URL
                db.session.add(_s)

        # Harita embed: eski q= parametre URL'sini embed URL'siyle değiştir
        _MAP = 'https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3059.424507449123!2d32.8322003!3d39.9443787!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x14d34f0a4309eec5%3A0x77936d1cd6fe2fde!2sKYA%20HUKUK%20ve%20DANI%C5%9FMANLIK!5e0!3m2!1str!2str!4v1700000000000!5m2!1str!2str'
        map_setting = SiteSetting.query.filter_by(key='google_maps_embed').first()
        if map_setting and ('maps?q=' in str(map_setting.value or '') or not map_setting.value):
            map_setting.value = _MAP
            db.session.add(map_setting)

        # Hero bölümleri
        hero_defaults = [
            ('index',      'KELEŞTEMUR | YİĞİT | ALTAY', 'HUKUK VE DANIŞMANLIK',
             'https://images.unsplash.com/photo-1589829545856-d10d557cf95f?w=1600&q=80'),
            ('hakkimizda', 'Hakkımızda', 'Hukukun Üstünlüğü ve Adalet İçin Buradayız',
             'https://images.unsplash.com/photo-1505664194779-8beaceb93744?w=1600&q=80'),
            ('ekibimiz',   'Avukat Kadromuz', 'Uzman ve Deneyimli Hukukçularımız',
             'https://images.unsplash.com/photo-1521791136064-7986c2920216?w=1600&q=80'),
            ('faaliyet',   'Çalışma Alanları', 'Başlıca Uzmanlık Alanlarımız',
             'https://images.unsplash.com/photo-1589994965851-a8f479c573a9?w=1600&q=80'),
            ('makaleler',  'Makaleler', 'Hukuki Bilgi Köşesi',
             'https://images.unsplash.com/photo-1456324504439-367cee3b3c32?w=1600&q=80'),
            ('iletisim',   'İletişim', 'Bize Ulaşın',
             'https://images.unsplash.com/photo-1497366412874-3415097a27e7?w=1600&q=80'),
        ]
        for page, title, subtitle, image_url in hero_defaults:
            if not HeroSection.query.filter_by(page=page).first():
                db.session.add(HeroSection(page=page, title=title, subtitle=subtitle, image_url=image_url))

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
