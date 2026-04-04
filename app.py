import os
import io
import json
import re
import math
import threading
import requests
import markdown2
import bleach
from datetime import datetime
from urllib.parse import quote
from slugify import slugify
from dotenv import load_dotenv
from flask import (Flask, render_template, redirect, url_for, request,
                   flash, jsonify, abort, session, send_from_directory,
                   has_request_context)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image

load_dotenv()

app = Flask(__name__, instance_path=os.path.join(os.path.dirname(__file__), 'instance'))
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))

# Database
database_url = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Upload folder
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'

# ─── Rich text (Quill) / Markdown ─────────────────────────────────────────────

BLEACH_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'code',
    'ul', 'ol', 'li', 'strong', 'em', 'a', 'img', 'br', 'hr', 'table',
    'thead', 'tbody', 'tr', 'th', 'td', 'figure', 'figcaption', 'mark',
    'del', 'ins', 'sup', 'sub', 'details', 'summary', 'span', 's', 'u',
]
BLEACH_ATTRS = {
    'a': ['href', 'title', 'target', 'rel', 'class'],
    'img': ['src', 'alt', 'title', 'class', 'width', 'height'],
    'p': ['class'], 'span': ['class'], 'blockquote': ['class'],
    'h1': ['class'], 'h2': ['class'], 'h3': ['class'], 'h4': ['class'],
    'ol': ['class'], 'ul': ['class'], 'li': ['class'], 'pre': ['class'], 'code': ['class'],
}
# Bleach 6+: allowed URL schemes for href, src, etc.
BLEACH_PROTOCOLS = frozenset({'http', 'https', 'mailto', 'tel', 'data'})


def _strip_html_tags(text):
    if not text:
        return ''
    return re.sub(r'<[^>]+>', ' ', str(text))


def _rich_body_has_visible_content(html):
    """True if stored HTML is more than an empty Quill placeholder (e.g. <p><br></p>)."""
    if not html or not str(html).strip():
        return False
    h = str(html)
    if re.search(r'<img\s[^>]*\bsrc\s*=', h, re.I):
        return True
    if re.search(r'<iframe\b', h, re.I):
        return True
    if re.search(r'<video\b', h, re.I):
        return True
    plain = _strip_html_tags(h).replace('\u00a0', ' ')
    plain = re.sub(r'\s+', ' ', plain).strip()
    return bool(plain)


def _empty_quill_to_blank(html):
    """Store '' instead of <p><br></p> when the secondary-language editor was not used."""
    if not html or not str(html).strip():
        return ''
    if _rich_body_has_visible_content(html):
        return html
    return ''


# ─── Models ───────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.filter_by(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = value
        else:
            row = cls(key=key, value=value)
            db.session.add(row)
        db.session.commit()


class Post(db.Model):
    """Blog post. Columns title_fr / excerpt_fr / body_fr store Kiswahili (legacy names)."""

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    title_en = db.Column(db.String(300), nullable=False)
    title_fr = db.Column(db.String(300))  # Kiswahili
    excerpt_en = db.Column(db.Text)
    excerpt_fr = db.Column(db.Text)
    body_en = db.Column(db.Text, nullable=False)
    body_fr = db.Column(db.Text)
    cover_image = db.Column(db.String(300))
    tags = db.Column(db.String(300))
    published = db.Column(db.Boolean, default=False)
    featured = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def body_looks_like_html(body):
        """Detect Quill/rich HTML vs legacy Markdown (avoid sending HTML through markdown2)."""
        if not body or not str(body).strip():
            return False
        s = str(body).lstrip()
        if not s.startswith('<'):
            return False
        b = str(body)
        # Opening tags (Quill) or any closing tag => treat as HTML
        if '</' in b:
            return True
        opens = (
            '<p', '<h1', '<h2', '<h3', '<h4', '<h5', '<h6', '<ul', '<ol', '<li',
            '<blockquote', '<div', '<pre', '<span', '<img', '<a ', '<br',
            '<table', '<strong', '<em', '<u', '<code', '<hr',
        )
        return any(x in b for x in opens)

    def _raw_body(self, lang='en'):
        """Body for public display. Kiswahili falls back to English when SW HTML is empty placeholder."""
        if lang == 'en':
            return self.body_en or ''
        sw = self.body_fr
        if sw and str(sw).strip() and _rich_body_has_visible_content(sw):
            return sw
        return self.body_en or ''

    def reading_time(self, lang='en'):
        body = self._raw_body(lang)
        plain = _strip_html_tags(body) if self.body_looks_like_html(body) else body
        words = len(re.findall(r'\w+', plain))
        minutes = max(1, math.ceil(words / 200))
        return minutes

    def body_for_editor(self, lang='en'):
        """HTML for Quill; legacy Markdown is converted once for editing."""
        raw = self.body_en if lang == 'en' else (self.body_fr or '')
        if not raw or not str(raw).strip():
            return ''
        if self.body_looks_like_html(raw):
            return raw
        html = markdown2.markdown(
            raw, extras=['fenced-code-blocks', 'tables', 'strike', 'footnotes', 'task_list']
        )
        return html

    def rendered_body(self, lang='en'):
        body = self._raw_body(lang)
        if self.body_looks_like_html(body):
            return bleach.clean(
                body, tags=BLEACH_TAGS, attributes=BLEACH_ATTRS,
                protocols=BLEACH_PROTOCOLS, strip=True,
            )
        html = markdown2.markdown(
            body, extras=['fenced-code-blocks', 'tables', 'strike', 'footnotes', 'task_list']
        )
        return bleach.clean(
            html, tags=BLEACH_TAGS, attributes=BLEACH_ATTRS,
            protocols=BLEACH_PROTOCOLS, strip=True,
        )

    def tag_list(self):
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(',') if t.strip()]


class Subscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120))
    subscribed_at = db.Column(db.DateTime, default=datetime.utcnow)
    active = db.Column(db.Boolean, default=True)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False, index=True)
    author_name = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved = db.Column(db.Boolean, default=True)
    post = db.relationship('Post', backref=db.backref('comments', lazy='dynamic'))


# ─── Helpers ──────────────────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def cloudinary_configured():
    return bool(
        os.environ.get('CLOUDINARY_URL')
        or (
            os.environ.get('CLOUDINARY_CLOUD_NAME')
            and os.environ.get('CLOUDINARY_API_KEY')
            and os.environ.get('CLOUDINARY_API_SECRET')
        )
    )


def _cloudinary_upload_stream(stream, folder, public_id=None, resource_type='auto', fmt=None):
    import cloudinary
    import cloudinary.uploader

    if os.environ.get('CLOUDINARY_URL'):
        cloudinary.config()
    else:
        cloudinary.config(
            cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
            api_key=os.environ.get('CLOUDINARY_API_KEY'),
            api_secret=os.environ.get('CLOUDINARY_API_SECRET'),
        )
    opts = {'folder': f"lawblog/{folder}".strip('/'), 'resource_type': resource_type}
    if public_id:
        opts['public_id'] = public_id
    if fmt:
        opts['format'] = fmt
    result = cloudinary.uploader.upload(stream, **opts)
    return result['secure_url']


def save_upload(file, subfolder='', resize=None):
    """Save to local static/uploads or Cloudinary when configured (recommended on Railway)."""
    filename = secure_filename(file.filename or '')
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    name, ext = os.path.splitext(filename)
    safe_name = slugify(name) if name else 'file'
    unique_base = f"{ts}_{safe_name}"

    try:
        file.seek(0)
    except (OSError, AttributeError):
        pass

    folder = subfolder or 'misc'

    if cloudinary_configured():
        ext_l = ext.lower()
        if ext_l == '.pdf':
            try:
                file.seek(0)
            except (OSError, AttributeError):
                pass
            return _cloudinary_upload_stream(
                file.stream if hasattr(file, 'stream') else file,
                folder,
                public_id=unique_base,
                resource_type='raw',
            )

        if resize and ext_l in ('.jpg', '.jpeg', '.png', '.webp'):
            try:
                file.seek(0)
            except (OSError, AttributeError):
                pass
            img = Image.open(file)
            img.thumbnail(resize)
            buf = io.BytesIO()
            save_kw = {'quality': 90}
            if ext_l == '.png':
                img.save(buf, format='PNG')
            else:
                img.save(buf, format='JPEG', **save_kw)
            buf.seek(0)
            return _cloudinary_upload_stream(buf, folder, public_id=unique_base, resource_type='image')

        try:
            file.seek(0)
        except (OSError, AttributeError):
            pass
        return _cloudinary_upload_stream(
            file.stream if hasattr(file, 'stream') else file,
            folder,
            public_id=unique_base,
            resource_type='image',
        )

    filename = f"{unique_base}{ext}"
    if subfolder:
        dest_dir = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
        rel = f"uploads/{subfolder}/{filename}"
    else:
        dest_dir = app.config['UPLOAD_FOLDER']
        rel = f"uploads/{filename}"

    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, filename)

    if resize and ext.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
        try:
            file.seek(0)
        except (OSError, AttributeError):
            pass
        img = Image.open(file)
        img.thumbnail(resize)
        img.save(path, quality=90)
    else:
        try:
            file.seek(0)
        except (OSError, AttributeError):
            pass
        file.save(path)

    return rel


def verify_recaptcha(token):
    secret = os.environ.get('RECAPTCHA_SECRET_KEY', '')
    if not secret or secret == 'your-recaptcha-secret-key':
        return True  # skip in dev
    resp = requests.post('https://www.google.com/recaptcha/api/siteverify',
                         data={'secret': secret, 'response': token}, timeout=5)
    data = resp.json()
    return data.get('success') and data.get('score', 0) >= 0.5


def add_to_brevo(email, name=''):
    api_key = os.environ.get('BREVO_API_KEY', '')
    list_id_raw = os.environ.get('BREVO_LIST_ID', '')
    if not api_key or api_key == 'your-brevo-api-key':
        return True  # skip in dev
    try:
        list_id = int(str(list_id_raw).strip())
    except (TypeError, ValueError):
        app.logger.warning('BREVO_LIST_ID missing or invalid; contact not synced to Brevo list.')
        return False
    url = 'https://api.brevo.com/v3/contacts'
    headers = {'api-key': api_key, 'Content-Type': 'application/json'}
    payload = {
        'email': email,
        'attributes': {'FIRSTNAME': name},
        'listIds': [list_id],
        'updateEnabled': True
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    return resp.status_code in (200, 201, 204)


def brevo_transactional_ready():
    api_key = (os.environ.get('BREVO_API_KEY') or '').strip()
    if not api_key or api_key == 'your-brevo-api-key':
        return False
    return bool((os.environ.get('BREVO_SENDER_EMAIL') or '').strip())


def get_public_base_url():
    """Canonical site URL for emails and absolute asset links. Set PUBLIC_BASE_URL in production."""
    for key in ('PUBLIC_BASE_URL', 'SITE_URL'):
        v = (os.environ.get(key) or '').strip().rstrip('/')
        if v:
            return v
    if has_request_context():
        return request.host_url.rstrip('/')
    return ''


def absolute_public_static_url(stored_path):
    """Turn DB upload path (or absolute URL) into a full URL for HTML emails."""
    if not stored_path or not str(stored_path).strip():
        return ''
    s = str(stored_path).strip()
    if s.startswith('http://') or s.startswith('https://'):
        return s
    base = get_public_base_url()
    if not base:
        return ''
    path = s.lstrip('/')
    if path.startswith('static/'):
        path = path[len('static/'):]
    return f'{base}/static/{path}'


def brevo_send_transactional(to_email, to_name, subject, html_content):
    """Send one transactional email via Brevo REST API."""
    if not brevo_transactional_ready():
        return False
    api_key = os.environ.get('BREVO_API_KEY', '').strip()
    sender_email = os.environ.get('BREVO_SENDER_EMAIL', '').strip()
    settings = get_settings()
    sender_name = (os.environ.get('BREVO_SENDER_NAME') or '').strip() or (
        settings.get('name') or 'Newsletter'
    )
    url = 'https://api.brevo.com/v3/smtp/email'
    headers = {'api-key': api_key, 'Content-Type': 'application/json'}
    display = (to_name or '').strip() or to_email.split('@')[0]
    payload = {
        'sender': {'name': sender_name, 'email': sender_email},
        'to': [{'email': to_email.strip(), 'name': display[:120]}],
        'subject': subject[:998],
        'htmlContent': html_content,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException:
        app.logger.exception('Brevo transactional request failed for %s', to_email)
        return False
    if resp.status_code not in (200, 201, 202, 204):
        app.logger.warning(
            'Brevo transactional %s for %s: %s %s',
            resp.status_code, to_email, resp.reason, (resp.text or '')[:300],
        )
        return False
    return True


def _plain_excerpt_for_email(post, max_len=400):
    ex = (post.excerpt_en or '').strip()
    if ex:
        t = bleach.clean(ex, tags=[], strip=True)
        t = re.sub(r'\s+', ' ', t).strip()
    else:
        body = post.body_en or ''
        if post.body_looks_like_html(body):
            plain = _strip_html_tags(body)
        else:
            plain = body
        t = re.sub(r'\s+', ' ', (plain or '').strip()).strip()
    if len(t) <= max_len:
        return t
    cut = t[:max_len].rsplit(' ', 1)[0]
    return (cut or t[:max_len]) + '…'


def send_newsletter_welcome_email(to_email, name='', returning=False):
    if not brevo_transactional_ready():
        return
    settings = get_settings()
    site_name = settings.get('name') or 'Our blog'
    home_url = get_public_base_url() or '#'
    contact = (settings.get('email') or '').strip()
    hero_url = absolute_public_static_url(settings.get('banner_image') or '')
    if returning:
        header_title = 'Welcome back'
        subject_line = f"You're resubscribed - {site_name}"
        greeting = f"Hi {name.split()[0] if name else 'there'}, good to see you again."
        body_text = (
            f"You're back on the list at {site_name}. "
            "We'll email you when a new article is published."
        )
    else:
        header_title = "You're subscribed"
        subject_line = f'Welcome to {site_name}'
        greeting = f"Hi {name.split()[0] if name else 'there'}, thank you for subscribing."
        body_text = (
            f"You'll get occasional updates from {site_name} when new writing goes live. "
            'No spam - just the work.'
        )
    unsub_mailto = ''
    if contact:
        unsub_mailto = f'mailto:{contact}?subject={quote("Unsubscribe from newsletter")}'
    html = render_template(
        'email/welcome_subscribe.html',
        subject_line=subject_line,
        header_title=header_title,
        hero_url=hero_url,
        greeting=greeting,
        body_text=body_text,
        home_url=home_url,
        cta_label='Visit the site',
        footer_note='You received this because you subscribed to our newsletter.',
        contact_email=contact,
        unsubscribe_mailto=unsub_mailto,
        unsubscribe_label='Unsubscribe',
        view_site_label='View in browser',
    )
    brevo_send_transactional(to_email, name, subject_line, html)


def _render_new_post_email(post):
    settings = get_settings()
    site_name = settings.get('name') or 'Our blog'
    author = (settings.get('name') or 'The author').strip()
    base = get_public_base_url()
    post_url = f'{base}/blog/{post.slug}' if base else f'/blog/{post.slug}'
    cover_url = absolute_public_static_url(post.cover_image or '')
    excerpt_text = _plain_excerpt_for_email(post)
    meta_line = f'By {author} • {post.reading_time("en")} min read'
    contact = (settings.get('email') or '').strip()
    unsub_mailto = f'mailto:{contact}?subject={quote("Unsubscribe from newsletter")}' if contact else ''
    return render_template(
        'email/new_post_notify.html',
        subject_line=f'New on {site_name}: {post.title_en}',
        header_title='New blog post 🚀',
        cover_url=cover_url,
        post_title=post.title_en,
        meta_line=meta_line,
        excerpt_text=excerpt_text,
        post_url=post_url,
        cta_label='Read full article',
        tags=post.tag_list(),
        footer_note='You received this email because you subscribed to our blog.',
        view_article_label='View in browser',
        contact_email=contact,
        unsubscribe_mailto=unsub_mailto,
        unsubscribe_label='Unsubscribe',
    )


def schedule_new_post_notifications(post_id):
    """Email active subscribers after a post becomes published (background thread)."""
    flag = (os.environ.get('BREVO_NOTIFY_NEW_POST') or '1').strip().lower()
    if flag in ('0', 'false', 'no', 'off'):
        return
    if not brevo_transactional_ready():
        app.logger.info('New post emails skipped: set BREVO_API_KEY and BREVO_SENDER_EMAIL.')
        return

    def worker():
        with app.app_context():
            if not get_public_base_url():
                app.logger.warning(
                    'New post emails skipped: set PUBLIC_BASE_URL (or SITE_URL) so links work in email.'
                )
                return
            post = Post.query.get(post_id)
            if not post or not post.published:
                return
            subs = Subscriber.query.filter_by(active=True).all()
            if not subs:
                return
            html = _render_new_post_email(post)
            subject = f'New on {get_settings().get("name") or "Blog"}: {post.title_en}'[:998]
            for sub in subs:
                brevo_send_transactional(sub.email, sub.name or '', subject, html)

    threading.Thread(target=worker, daemon=True).start()


def get_lang():
    l = session.get('lang', 'en')
    if l == 'fr':
        session['lang'] = 'sw'
        l = 'sw'
    return l if l in ('en', 'sw') else 'en'


def get_settings():
    return {
        'name': SiteSettings.get('name', 'Counsel & Craft'),
        'tagline_en': SiteSettings.get('tagline_en', 'Law · Writing · Youth'),
        'tagline_fr': SiteSettings.get('tagline_fr', 'Sheria · Uandishi · Vijana'),
        'bio_en': SiteSettings.get('bio_en', 'A law student, writer, and voice for the youth.'),
        'bio_fr': SiteSettings.get('bio_fr', 'Mwanafunzi wa sheria, mwandishi, na sauti ya vijana.'),
        'email': SiteSettings.get('email', ''),
        'twitter': SiteSettings.get('twitter', ''),
        'linkedin': SiteSettings.get('linkedin', ''),
        'instagram': SiteSettings.get('instagram', ''),
        'logo': SiteSettings.get('logo', ''),
        'avatar': SiteSettings.get('avatar', ''),
        'banner_image': SiteSettings.get('banner_image', ''),
        'cv': SiteSettings.get('cv', ''),
        'hero_quote_en': SiteSettings.get('hero_quote_en', 'Justice is the constant will to render to every man his due.'),
        'hero_quote_fr': SiteSettings.get('hero_quote_fr', 'Haki ni nia thabiti ya kumpa kila mtu kilicho chake.'),
        'recaptcha_site_key': os.environ.get('RECAPTCHA_SITE_KEY', ''),
    }


@app.context_processor
def inject_globals():
    lang = get_lang()
    return dict(lang=lang, settings=get_settings(), now=datetime.utcnow())


@app.template_filter('asset')
def asset_filter(path):
    """Convert stored upload path to proper URL, handles edge cases."""
    if not path:
        return ''
    path = path.strip().lstrip('/')
    if path.startswith('https://') or path.startswith('http://'):
        return path
    if path.startswith('static/'):
        path = path[7:]
    from flask import url_for as _uf
    try:
        return _uf('static', filename=path)
    except Exception:
        return '/static/' + path


# ─── Language toggle ──────────────────────────────────────────────────────────

@app.route('/set-lang/<lang>')
def set_lang(lang):
    if lang in ('en', 'sw'):
        session['lang'] = lang
    return redirect(request.referrer or url_for('index'))


# ─── Public routes ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    lang = get_lang()
    featured = Post.query.filter_by(published=True, featured=True).order_by(Post.created_at.desc()).limit(3).all()
    recent = Post.query.filter_by(published=True).order_by(Post.created_at.desc()).limit(6).all()
    return render_template('public/index.html', featured=featured, recent=recent, lang=lang)


@app.route('/about')
def about():
    return render_template('public/about.html')


@app.route('/blog')
def blog():
    lang = get_lang()
    page = request.args.get('page', 1, type=int)
    tag = request.args.get('tag', '')
    q = Post.query.filter_by(published=True)
    if tag:
        q = q.filter(Post.tags.contains(tag))
    posts = q.order_by(Post.created_at.desc()).paginate(page=page, per_page=9, error_out=False)
    all_tags = set()
    for p in Post.query.filter_by(published=True).all():
        all_tags.update(p.tag_list())
    return render_template('public/blog.html', posts=posts, all_tags=sorted(all_tags), active_tag=tag, lang=lang)


@app.route('/blog/<slug>')
def post(slug):
    lang = get_lang()
    p = Post.query.filter_by(slug=slug, published=True).first_or_404()
    related = Post.query.filter(Post.published==True, Post.id!=p.id).order_by(Post.created_at.desc()).limit(3).all()
    comments = p.comments.filter_by(approved=True).order_by(Comment.created_at.asc()).all()
    return render_template('public/post.html', post=p, related=related, comments=comments, lang=lang)


@app.route('/portfolio')
def portfolio():
    return render_template('public/portfolio.html')


@app.route('/subscribe', methods=['POST'])
def subscribe():
    data = request.get_json()
    email = (data.get('email') or '').strip()
    name = (data.get('name') or '').strip()
    token = data.get('recaptcha_token', '')

    if not email or '@' not in email:
        return jsonify({'ok': False, 'msg': 'Invalid email address.'})

    if not verify_recaptcha(token):
        return jsonify({'ok': False, 'msg': 'reCAPTCHA failed. Please try again.'})

    existing = Subscriber.query.filter_by(email=email).first()
    if existing:
        if not existing.active:
            existing.active = True
            db.session.commit()
            add_to_brevo(email, name)
            send_newsletter_welcome_email(email, name, returning=True)
            return jsonify({'ok': True, 'msg': 'Welcome back! You\'re resubscribed.'})
        return jsonify({'ok': False, 'msg': 'You\'re already subscribed!'})

    sub = Subscriber(email=email, name=name)
    db.session.add(sub)
    db.session.commit()
    add_to_brevo(email, name)
    send_newsletter_welcome_email(email, name, returning=False)
    return jsonify({'ok': True, 'msg': 'You\'re in! Thank you for subscribing.'})


@app.route('/blog/<slug>/comment', methods=['POST'])
def post_comment(slug):
    p = Post.query.filter_by(slug=slug, published=True).first_or_404()
    data = request.get_json(silent=True) or {}
    name = (data.get('author_name') or '').strip()[:120]
    body = (data.get('body') or '').strip()
    token = data.get('recaptcha_token', '')

    if not name or len(name) < 2:
        return jsonify({'ok': False, 'msg': 'Please enter your name.'}), 400
    if not body or len(body) < 3:
        return jsonify({'ok': False, 'msg': 'Please write a comment.'}), 400
    if len(body) > 4000:
        return jsonify({'ok': False, 'msg': 'Comment is too long.'}), 400

    if not verify_recaptcha(token):
        return jsonify({'ok': False, 'msg': 'reCAPTCHA failed. Please try again.'}), 400

    clean_body = bleach.clean(body, tags=[], strip=True)
    c = Comment(post_id=p.id, author_name=name, body=clean_body, approved=True)
    db.session.add(c)
    db.session.commit()
    return jsonify({
        'ok': True,
        'msg': 'Thank you! Your comment is published.',
        'comment': {
            'author_name': c.author_name,
            'body': c.body,
            'created_at': c.created_at.strftime('%b %d, %Y'),
        },
    })


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ─── Admin routes ─────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('admin_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('admin/login.html')


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for('admin_login'))


@app.route('/admin')
@login_required
def admin_dashboard():
    total_posts = Post.query.count()
    published_posts = Post.query.filter_by(published=True).count()
    subscribers = Subscriber.query.filter_by(active=True).count()
    recent_posts = Post.query.order_by(Post.created_at.desc()).limit(5).all()
    recent_subs = Subscriber.query.order_by(Subscriber.subscribed_at.desc()).limit(5).all()
    return render_template('admin/dashboard.html',
                           total_posts=total_posts, published_posts=published_posts,
                           subscribers=subscribers, recent_posts=recent_posts,
                           recent_subs=recent_subs)


@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    if request.method == 'POST':
        fields = ['name','tagline_en','tagline_fr','bio_en','bio_fr','email',
                  'twitter','linkedin','instagram','hero_quote_en','hero_quote_fr']
        for f in fields:
            val = request.form.get(f, '')
            SiteSettings.set(f, val)

        # Handle file uploads
        for field, subfolder, resize in [
            ('logo', 'branding', (400, 400)),
            ('avatar', 'branding', (800, 800)),
            ('banner_image', 'branding', (1920, 720)),
        ]:
            if field in request.files and request.files[field].filename:
                f = request.files[field]
                if allowed_file(f.filename):
                    try:
                        f.seek(0)
                    except (OSError, AttributeError):
                        pass
                    rel = save_upload(f, subfolder, resize)
                    SiteSettings.set(field, rel)

        # CV upload
        if 'cv' in request.files and request.files['cv'].filename:
            f = request.files['cv']
            if f.filename.endswith('.pdf'):
                try:
                    f.seek(0)
                except (OSError, AttributeError):
                    pass
                rel = save_upload(f, 'documents')
                SiteSettings.set('cv', rel)

        flash('Settings saved!', 'success')
        return redirect(url_for('admin_settings'))
    return render_template('admin/settings.html')


@app.route('/admin/posts')
@login_required
def admin_posts():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('admin/posts.html', posts=posts)


@app.route('/admin/posts/new', methods=['GET', 'POST'])
@login_required
def admin_new_post():
    if request.method == 'POST':
        title_en = request.form.get('title_en', '').strip()
        if not title_en:
            flash('English title is required.', 'error')
            return redirect(request.url)

        base_slug = slugify(title_en)
        slug = base_slug
        counter = 1
        while Post.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1

        cover = None
        if 'cover_image' in request.files and request.files['cover_image'].filename:
            f = request.files['cover_image']
            if allowed_file(f.filename):
                try:
                    f.seek(0)
                except (OSError, AttributeError):
                    pass
                cover = save_upload(f, 'covers', (1400, 800))

        post = Post(
            slug=slug,
            title_en=title_en,
            title_fr=request.form.get('title_fr', ''),
            excerpt_en=request.form.get('excerpt_en', ''),
            excerpt_fr=request.form.get('excerpt_fr', ''),
            body_en=request.form.get('body_en', ''),
            body_fr=_empty_quill_to_blank(request.form.get('body_fr', '')),
            tags=request.form.get('tags', ''),
            cover_image=cover,
            published='published' in request.form,
            featured='featured' in request.form,
        )
        db.session.add(post)
        db.session.commit()
        if post.published:
            schedule_new_post_notifications(post.id)
        flash('Post created!', 'success')
        return redirect(url_for('admin_posts'))
    return render_template('admin/post_edit.html', post=None)


@app.route('/admin/posts/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if request.method == 'POST':
        was_published = post.published
        post.title_en = request.form.get('title_en', '').strip()
        post.title_fr = request.form.get('title_fr', '')
        post.excerpt_en = request.form.get('excerpt_en', '')
        post.excerpt_fr = request.form.get('excerpt_fr', '')
        post.body_en = request.form.get('body_en', '')
        post.body_fr = _empty_quill_to_blank(request.form.get('body_fr', ''))
        post.tags = request.form.get('tags', '')
        post.published = 'published' in request.form
        post.featured = 'featured' in request.form
        post.updated_at = datetime.utcnow()

        if 'cover_image' in request.files and request.files['cover_image'].filename:
            f = request.files['cover_image']
            if allowed_file(f.filename):
                try:
                    f.seek(0)
                except (OSError, AttributeError):
                    pass
                post.cover_image = save_upload(f, 'covers', (1400, 800))

        db.session.commit()
        if post.published and not was_published:
            schedule_new_post_notifications(post.id)
        flash('Post updated!', 'success')
        return redirect(url_for('admin_posts'))
    return render_template('admin/post_edit.html', post=post)


@app.route('/admin/posts/<int:post_id>/delete', methods=['POST'])
@login_required
def admin_delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    Comment.query.filter_by(post_id=post.id).delete()
    db.session.delete(post)
    db.session.commit()
    flash('Post deleted.', 'success')
    return redirect(url_for('admin_posts'))


@app.route('/admin/posts/<int:post_id>/toggle', methods=['POST'])
@login_required
def admin_toggle_post(post_id):
    post = Post.query.get_or_404(post_id)
    was_published = post.published
    post.published = not post.published
    db.session.commit()
    if post.published and not was_published:
        schedule_new_post_notifications(post.id)
    return jsonify({'published': post.published})


@app.route('/admin/subscribers')
@login_required
def admin_subscribers():
    subs = Subscriber.query.order_by(Subscriber.subscribed_at.desc()).all()
    return render_template('admin/subscribers.html', subs=subs)


@app.route('/admin/upload-image', methods=['POST'])
@login_required
def admin_upload_image():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    if f and allowed_file(f.filename):
        try:
            f.seek(0)
        except (OSError, AttributeError):
            pass
        rel = save_upload(f, 'blog')
        if rel.startswith('http://') or rel.startswith('https://'):
            url = rel
        else:
            url = url_for('static', filename=rel)
        return jsonify({'url': url})
    return jsonify({'error': 'Invalid file'}), 400


AI_WRITING_ACTIONS = {
    'improve': (
        'Improve clarity, flow, and readability. Keep the meaning. Preserve headings, lists, links, and images.'
    ),
    'grammar': (
        'Fix grammar, spelling, and punctuation only. Do not change tone except where incorrect grammar forces it.'
    ),
    'tone': (
        'Make the tone warmer, clearer, and more confident for a general audience. Keep facts and structure.'
    ),
}


def openrouter_rewrite_editor_html(html_fragment, instruction):
    """Rewrite HTML via OpenRouter (OpenAI-compatible chat completions API)."""
    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return None, 'Add OPENROUTER_API_KEY to your environment to use AI writing tools.'
    model = os.environ.get('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
    base = os.environ.get('OPENROUTER_API_BASE', 'https://openrouter.ai/api/v1').rstrip('/')
    url = f'{base}/chat/completions'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    referer = os.environ.get('OPENROUTER_HTTP_REFERER', '').strip()
    if referer:
        headers['HTTP-Referer'] = referer
    app_title = os.environ.get('OPENROUTER_APP_TITLE', '').strip()
    if app_title:
        headers['X-Title'] = app_title
    system = (
        'You are a professional editor. Reply with ONLY an HTML fragment (no markdown, no code fences, '
        'no <!DOCTYPE> or <html> wrapper). Use tags such as: p, br, strong, em, u, s, h1, h2, h3, ul, ol, li, '
        'a, img, blockquote, pre, code. For external links use target="_blank" rel="noopener noreferrer". '
        'Preserve img src and a href when possible.'
    )
    try:
        resp = requests.post(
            url,
            headers=headers,
            json={
                'model': model,
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': f'{instruction}\n\nHTML:\n{html_fragment}'},
                ],
                'temperature': 0.35,
                'max_tokens': 8192,
            },
            timeout=120,
        )
    except requests.RequestException:
        return None, 'Could not reach OpenRouter. Try again later.'
    if resp.status_code != 200:
        try:
            data = resp.json()
            err_obj = data.get('error')
            if isinstance(err_obj, dict):
                err = err_obj.get('message', str(err_obj))[:500]
            elif isinstance(err_obj, str):
                err = err_obj[:500]
            else:
                err = resp.text[:500] or 'OpenRouter request failed'
        except Exception:
            err = resp.text[:500] or 'OpenRouter request failed'
        return None, err
    try:
        payload = resp.json()
        choices = payload.get('choices') or []
        content = (choices[0].get('message') or {}).get('content') or ''
    except (IndexError, KeyError, TypeError):
        return None, 'Unexpected AI response.'
    content = content.strip()
    if content.startswith('```'):
        content = re.sub(r'^```(?:html|HTML)?\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content).strip()
    if not content:
        return None, 'AI returned empty content.'
    return content, None


@app.route('/admin/ai/writing', methods=['POST'])
@login_required
def admin_ai_writing():
    data = request.get_json(silent=True) or {}
    content = data.get('content') or ''
    action = (data.get('action') or '').strip().lower()
    if action not in AI_WRITING_ACTIONS:
        return jsonify({'ok': False, 'error': 'Invalid action.'}), 400
    if len(content) > 200_000:
        return jsonify({'ok': False, 'error': 'Content too large.'}), 400
    result, err = openrouter_rewrite_editor_html(content, AI_WRITING_ACTIONS[action])
    if err:
        return jsonify({'ok': False, 'error': err}), 503
    return jsonify({'ok': True, 'result': result})


@app.route('/admin/comments')
@login_required
def admin_comments():
    rows = (Comment.query.join(Post).order_by(Comment.created_at.desc()).all())
    return render_template('admin/comments.html', comments=rows)


@app.route('/admin/comments/<int:comment_id>/delete', methods=['POST'])
@login_required
def admin_delete_comment(comment_id):
    c = Comment.query.get_or_404(comment_id)
    db.session.delete(c)
    db.session.commit()
    flash('Comment removed.', 'success')
    return redirect(url_for('admin_comments'))


# ─── Init ─────────────────────────────────────────────────────────────────────

def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.first():
            admin_email = os.environ.get('ADMIN_EMAIL', 'admin@example.com')
            admin_password = os.environ.get('ADMIN_PASSWORD', 'changeme123')
            user = User(email=admin_email)
            user.set_password(admin_password)
            db.session.add(user)
            db.session.commit()
            print(f"✅ Admin created: {admin_email}")


if __name__ == '__main__':
    init_db()
    app.run(debug=os.environ.get('FLASK_ENV') != 'production')
