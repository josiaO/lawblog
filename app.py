import os
import io
import json
import re
import math
import time
import threading
import requests
from openai import OpenAI
import markdown2
import bleach
from datetime import datetime
from urllib.parse import quote, urlparse, urlparse
from slugify import slugify
from dotenv import load_dotenv
from flask import (Flask, render_template, redirect, url_for, request,
                   flash, jsonify, abort, session, send_from_directory,
                   has_request_context, current_app, Response, stream_with_context)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.routing import BuildError
from flask_caching import Cache
from flask_talisman import Talisman

from admin_help_assistant import resolve_help_query, HELP_SUGGESTIONS
from PIL import Image, UnidentifiedImageError

load_dotenv()

app = Flask(__name__, instance_path=os.path.join(os.path.dirname(__file__), 'instance'))

_DEV_SECRET_PLACEHOLDER = 'dev-secret-key-change-in-prod'


def _is_production_deployment():
    """True when the app is treated as live (must not use dev defaults)."""
    return (
        os.environ.get('FLASK_ENV', '').lower() == 'production'
        or os.environ.get('ENV', '').lower() == 'production'
        or os.environ.get('RAILWAY_ENVIRONMENT', '').lower() == 'production'
    )


_secret_key = (os.environ.get('SECRET_KEY') or '').strip()
if _is_production_deployment():
    if not _secret_key:
        raise RuntimeError(
            'SECRET_KEY must be set to a long random value in production '
            '(detected FLASK_ENV=production, ENV=production, or RAILWAY_ENVIRONMENT=production).'
        )
    if _secret_key == _DEV_SECRET_PLACEHOLDER:
        raise RuntimeError('SECRET_KEY must not use the development placeholder in production.')
    if len(_secret_key) < 32:
        raise RuntimeError('SECRET_KEY should be at least 32 characters in production.')
else:
    if not _secret_key:
        _secret_key = _DEV_SECRET_PLACEHOLDER
app.config['SECRET_KEY'] = _secret_key

app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))

# Database
database_url = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
if database_url.startswith('sqlite'):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'check_same_thread': False},
    }
else:
    try:
        _pool_size = int(os.environ.get('SQLALCHEMY_POOL_SIZE') or os.environ.get('DB_POOL_SIZE', 10))
        _max_overflow = int(os.environ.get('SQLALCHEMY_MAX_OVERFLOW') or os.environ.get('DB_POOL_MAX_OVERFLOW', 20))
        _pool_recycle = int(os.environ.get('SQLALCHEMY_POOL_RECYCLE') or os.environ.get('DB_POOL_RECYCLE', 3600))
    except ValueError:
        _pool_size, _max_overflow, _pool_recycle = 10, 20, 3600
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': max(1, _pool_size),
        'max_overflow': max(0, _max_overflow),
        'pool_recycle': max(300, _pool_recycle),
        'pool_pre_ping': True,
    }

# Upload folder
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'

cache = Cache(config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})
cache.init_app(app)

talisman = Talisman()
if _is_production_deployment():
    talisman.init_app(
        app,
        content_security_policy={
            'default-src': ["'self'"],
            'script-src': ["'self'", "'unsafe-inline'", "'unsafe-eval'", "https://www.google.com", "https://www.gstatic.com", "https://cdnjs.cloudflare.com"],
            'style-src': ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://cdnjs.cloudflare.com"],
            'font-src': ["'self'", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com", "data:"],
            'img-src': ["'self'", "data:", "https://res.cloudinary.com", "https://*"],
            'frame-src': ["'self'", "https://www.google.com"],
            'connect-src': ["'self'", "https://*"],
        },
        force_https=True,
        strict_transport_security=True,
        session_cookie_secure=True
    )
else:
    talisman.init_app(app, force_https=False, content_security_policy=None)

# Performance: add cache-control headers to static files
@app.after_request
def add_header(response):
    # If the response is for a static file, cache it for 1 year
    if request.path.startswith('/static/') and response.status_code == 200:
        response.headers['Cache-Control'] = 'public, max-age=31536000'
    return response

# Custom error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('public/404.html', lang=session.get('lang', 'en'), settings=get_settings()), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('public/500.html', lang=session.get('lang', 'en'), settings=get_settings()), 500


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept:
        return jsonify({
            'ok': False,
            'msg': 'Security check failed. Refresh the page and try again.',
        }), 400
    flash('Your session expired or the form was resent. Please try again.', 'error')
    return redirect(request.referrer or url_for('index')), 400


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


def _bleach_max_input_chars():
    try:
        return max(50_000, int(os.environ.get('BLEACH_MAX_INPUT_CHARS', '2000000')))
    except ValueError:
        return 2_000_000


def _sanitize_rich_html(html: str) -> str:
    """Bleach-clean HTML with a size cap to avoid pathological CPU use on huge documents."""
    if not html or not str(html).strip():
        return ''
    h = str(html)
    cap = _bleach_max_input_chars()
    if len(h) > cap:
        log = current_app.logger if has_request_context() else app.logger
        log.warning('Sanitizing truncated HTML from %d to %d chars (BLEACH_MAX_INPUT_CHARS)', len(h), cap)
        h = h[:cap]
    return bleach.clean(
        h, tags=BLEACH_TAGS, attributes=BLEACH_ATTRS,
        protocols=BLEACH_PROTOCOLS, strip=True,
    )


def _openai_http_timeout(default: float = 90.0) -> float:
    """Bounded HTTP timeout for OpenAI-compatible clients (Groq, OpenRouter, etc.)."""
    raw = (os.environ.get('AI_HTTP_TIMEOUT') or '').strip()
    if not raw:
        return default
    try:
        return max(5.0, min(float(raw), 600.0))
    except ValueError:
        return default


def _openai_http_timeout_help() -> float:
    raw = (os.environ.get('AI_HTTP_TIMEOUT_HELP') or '').strip()
    if raw:
        try:
            return max(5.0, min(float(raw), 600.0))
        except ValueError:
            pass
    return _openai_http_timeout(60.0)


def _openai_http_timeout_editor() -> float:
    raw = (os.environ.get('AI_HTTP_TIMEOUT_EDITOR') or '').strip()
    if raw:
        try:
            return max(5.0, min(float(raw), 600.0))
        except ValueError:
            pass
    return _openai_http_timeout(120.0)


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

    __table_args__ = (
        db.Index('ix_post_published_created_at', 'published', 'created_at'),
        db.Index('ix_post_published_featured_created', 'published', 'featured', 'created_at'),
    )

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
            return _sanitize_rich_html(body)
        html = markdown2.markdown(
            body, extras=['fenced-code-blocks', 'tables', 'strike', 'footnotes', 'task_list']
        )
        return _sanitize_rich_html(html)

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


class PortfolioItem(db.Model):
    """Work / project cards on the public portfolio page."""

    id = db.Column(db.Integer, primary_key=True)
    sort_order = db.Column(db.Integer, default=0, index=True)
    icon = db.Column(db.String(32), default='📌')
    category_en = db.Column(db.String(200), default='')
    category_sw = db.Column(db.String(200), default='')
    title_en = db.Column(db.String(300), default='')
    title_sw = db.Column(db.String(300), default='')
    desc_en = db.Column(db.Text, default='')
    desc_sw = db.Column(db.Text, default='')
    year = db.Column(db.String(20), default='')
    image = db.Column(db.String(500))


class PortfolioCredential(db.Model):
    """Certificates, awards, achievements — optional PDF or image attachment."""

    id = db.Column(db.Integer, primary_key=True)
    sort_order = db.Column(db.Integer, default=0, index=True)
    icon = db.Column(db.String(32), default='🏅')
    title_en = db.Column(db.String(300), default='')
    title_sw = db.Column(db.String(300), default='')
    detail_en = db.Column(db.Text, default='')
    detail_sw = db.Column(db.Text, default='')
    year = db.Column(db.String(20), default='')
    attachment = db.Column(db.String(500))


DEFAULT_PORTFOLIO_INTRO_EN = (
    'A collection of academic papers, advocacy work, and published pieces that reflect a commitment '
    'to justice, youth empowerment, and the law.'
)
DEFAULT_PORTFOLIO_INTRO_SW = (
    'Mkusanyiko wa kazi za kitaaluma, ulinzi, na machapisho yanayoonyesha kujitolea kwa haki na vijana.'
)

DEFAULT_PORTFOLIO_ITEMS = [
    {
        'icon': '📜', 'category_en': 'Academic Research', 'category_sw': 'Utafiti wa Kitaaluma',
        'title_en': 'Constitutional Law & Youth Rights', 'title_sw': 'Sheria ya Katiba & Haki za Vijana',
        'desc_en': (
            'Exploring the intersection of constitutional protections and the legal status of youth '
            'in modern democratic systems.'
        ),
        'desc_sw': (
            'Kuchunguza jinsi ulinzi wa katiba unavyokutana na hali ya kisheria ya vijana katika '
            'mifumo ya kidemokrasia ya kisasa.'
        ),
        'year': '2024',
    },
    {
        'icon': '✍', 'category_en': 'Published Essay', 'category_sw': 'Makala Iliyochapishwa',
        'title_en': 'The Pen and the Gavel', 'title_sw': 'Kalamu na Nyundo',
        'desc_en': (
            'A meditation on how writing shapes legal thought and how legal thought shapes our world. '
            'Published in the student law review.'
        ),
        'desc_sw': (
            'Tafakari juu ya jinsi uandishi unavyounda mawazo ya kisheria na jinsi mawazo hayo '
            'yanavyounda ulimwengu wetu.'
        ),
        'year': '2024',
    },
    {
        'icon': '🏛', 'category_en': 'Moot Court', 'category_sw': 'Mahakama ya Mazoezi',
        'title_en': 'Regional Moot Court Competition', 'title_sw': 'Mashindano ya Kanda ya Mahakama ya Mazoezi',
        'desc_en': (
            'Represented the law faculty in regional competition. Argued a landmark case on digital '
            'privacy rights for minors.'
        ),
        'desc_sw': (
            'Kuwakilisha chuo cha sheria katika mashindano ya kanda. Kujadili kesi muhimu kuhusu '
            'faragha ya kidijitali kwa walio chini ya miaka.'
        ),
        'year': '2023',
    },
    {
        'icon': '✊', 'category_en': 'Advocacy', 'category_sw': 'Ulinzi',
        'title_en': 'Youth Legal Aid Initiative', 'title_sw': 'Mpango wa Msaada wa Kisheria kwa Vijana',
        'desc_en': (
            'Co-founded a campus initiative providing basic legal information to underprivileged youth '
            'in the community.'
        ),
        'desc_sw': (
            'Kuunda pamoja mpango wa chuo unaotoa taarifa za msingi za kisheria kwa vijana walio '
            'katika hali ngumu.'
        ),
        'year': '2023',
    },
    {
        'icon': '🎤', 'category_en': 'Speaking', 'category_sw': 'Hotuba',
        'title_en': 'TEDx Campus Talk', 'title_sw': 'Hotuba ya TEDx Chuo',
        'desc_en': (
            'Delivered a talk on "Why Young People Should Care About the Law" to an audience of 300+ students.'
        ),
        'desc_sw': (
            'Kutoa hotuba kuhusu "Kwa nini vijana wapaswa kujali sheria" kwa wasikilizaji zaidi ya 300.'
        ),
        'year': '2023',
    },
    {
        'icon': '📰', 'category_en': 'Op-Ed', 'category_sw': 'Maoni',
        'title_en': 'National Youth Justice Op-Ed', 'title_sw': 'Maoni ya Kitaifa kuhusu Haki kwa Vijana',
        'desc_en': (
            'Published opinion piece arguing for reform of juvenile justice systems in the national '
            'student newspaper.'
        ),
        'desc_sw': (
            'Makala ya maoni iliyochapishwa inayopendekeza mabadiliko katika mifumo ya haki kwa vijana '
            'kwenye gazeti la wanafunzi.'
        ),
        'year': '2022',
    },
]

DEFAULT_AWARDS_ITEMS = [
    {
        'icon': '🏆',
        'title_en': 'Academic excellence award',
        'title_sw': 'Tuzo ya ufanisi wa masomo',
        'detail_en': 'Faculty recognition for outstanding performance in legal studies.',
        'detail_sw': 'Utambuzi wa chuo kwa ufanisi bora katika masomo ya sheria.',
        'year': '2024',
    },
    {
        'icon': '📜',
        'title_en': 'Certificate in Human Rights Law',
        'title_sw': 'Cheti cha Sheria ya Haki za Binadamu',
        'detail_en': 'Completed intensive programme on international human rights frameworks.',
        'detail_sw': 'Kumaliza mafunzo ya kina kuhusu mifumo ya kimataifa ya haki za binadamu.',
        'year': '2023',
    },
    {
        'icon': '⭐',
        'title_en': 'Community service honour',
        'title_sw': 'Heshima ya huduma kwa jamii',
        'detail_en': 'Awarded for volunteer legal literacy work with local youth groups.',
        'detail_sw': 'Kutolewa kwa kazi ya kujitolea ya uelewa wa kisheria na vikundi vya vijana.',
        'year': '2023',
    },
]


def _legacy_portfolio_json_list(setting_key):
    raw = SiteSettings.get(setting_key, '')
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _row_to_portfolio_item(row, sort_order):
    return PortfolioItem(
        sort_order=sort_order,
        icon=(row.get('icon') or '📌')[:32],
        category_en=(row.get('category_en') or '')[:200],
        category_sw=(row.get('category_sw') or '')[:200],
        title_en=(row.get('title_en') or '')[:300],
        title_sw=(row.get('title_sw') or '')[:300],
        desc_en=row.get('desc_en') or '',
        desc_sw=row.get('desc_sw') or '',
        year=(row.get('year') or '')[:20],
    )


def _row_to_portfolio_credential(row, sort_order):
    return PortfolioCredential(
        sort_order=sort_order,
        icon=(row.get('icon') or '🏅')[:32],
        title_en=(row.get('title_en') or '')[:300],
        title_sw=(row.get('title_sw') or '')[:300],
        detail_en=row.get('detail_en') or '',
        detail_sw=row.get('detail_sw') or '',
        year=(row.get('year') or '')[:20],
    )


def _ensure_portfolio_seeded():
    """One-time import from legacy JSON or built-in samples into SQL rows."""
    if SiteSettings.get('portfolio_db_seeded_v2') == '1':
        return
    if PortfolioItem.query.first():
        SiteSettings.set('portfolio_db_seeded_v2', '1')
        return

    legacy_items = _legacy_portfolio_json_list('portfolio_items_json')
    items_src = legacy_items if legacy_items else DEFAULT_PORTFOLIO_ITEMS
    for i, row in enumerate(items_src):
        if isinstance(row, dict):
            db.session.add(_row_to_portfolio_item(row, i))

    legacy_creds = _legacy_portfolio_json_list('portfolio_awards_json')
    creds_src = legacy_creds if legacy_creds else DEFAULT_AWARDS_ITEMS
    for i, row in enumerate(creds_src):
        if isinstance(row, dict):
            db.session.add(_row_to_portfolio_credential(row, i))

    db.session.commit()
    SiteSettings.set('portfolio_db_seeded_v2', '1')


def _portfolio_intro_value(key, fallback):
    row = SiteSettings.query.filter_by(key=key).first()
    if row is None:
        return fallback
    return row.value if row.value is not None else fallback


def get_portfolio_page_data():
    """Data for the public portfolio page (intro, ORM items, ORM credentials)."""
    _ensure_portfolio_seeded()
    items = PortfolioItem.query.order_by(
        PortfolioItem.sort_order.asc(), PortfolioItem.id.asc(),
    ).all()
    awards = PortfolioCredential.query.order_by(
        PortfolioCredential.sort_order.asc(), PortfolioCredential.id.asc(),
    ).all()
    # Use work_items (not "items") so Jinja portfolio.work_items works — dict.items is the .items() method.
    return {
        'intro_en': _portfolio_intro_value('portfolio_intro_en', DEFAULT_PORTFOLIO_INTRO_EN),
        'intro_sw': _portfolio_intro_value('portfolio_intro_sw', DEFAULT_PORTFOLIO_INTRO_SW),
        'work_items': items,
        'awards': awards,
    }


def attachment_kind(path):
    """'image' | 'pdf' | 'file' | 'none' for public portfolio templates."""
    if not path or not str(path).strip():
        return 'none'
    p = str(path).lower().split('?')[0]
    if p.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
        return 'image'
    if p.endswith('.pdf'):
        return 'pdf'
    return 'file'


def _render_announcement_email(subject, inner_html):
    settings = get_settings()
    site_name = settings.get('name') or 'Our site'
    base = get_public_base_url()
    site_url = base or ''
    contact = (settings.get('email') or '').strip()
    unsub = f'mailto:{contact}?subject={quote("Unsubscribe from newsletter")}' if contact else ''
    return render_template(
        'email/announcement.html',
        subject_line=subject,
        header_title=site_name,
        body_html=inner_html,
        footer_note='You are receiving this because you subscribed to our newsletter.',
        site_url=site_url,
        visit_label='Visit the site',
        unsubscribe_mailto=unsub,
        unsubscribe_label='Unsubscribe',
    )


def schedule_subscriber_broadcast(subject, inner_html_sanitized):
    """Email all active subscribers in a background thread (Brevo transactional)."""
    app_obj = current_app._get_current_object()

    def worker():
        with app_obj.app_context():
            if not brevo_transactional_ready():
                app.logger.error('Broadcast skipped: Brevo transactional not configured.')
                return
            subs = Subscriber.query.filter_by(active=True).all()
            if not subs:
                app.logger.info('Broadcast skipped: no active subscribers.')
                return
            html = _render_announcement_email(subject, inner_html_sanitized)
            plain = _html_to_text_fallback(inner_html_sanitized)
            subj = subject[:998]
            ok, fail = 0, 0
            for sub in subs:
                if brevo_send_transactional(sub.email, sub.name or '', subj, html, text_content=plain):
                    ok += 1
                else:
                    fail += 1
                time.sleep(0.12)
            app.logger.info(
                'Subscriber broadcast finished: sent=%s failed=%s subject=%r',
                ok, fail, subj[:80],
            )

    threading.Thread(target=worker, daemon=True).start()


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


def _read_upload_bytes(file):
    """Read the whole upload into memory (PIL + werkzeug FileStorage is unreliable under gunicorn)."""
    try:
        file.seek(0)
    except (OSError, AttributeError):
        pass
    raw = file.read()
    if not raw:
        raise ValueError('The uploaded file was empty.')
    return raw


def _pil_open_image_bytes(raw):
    """Open image from bytes; return None if Pillow cannot decode (corrupt or not a raster image)."""
    bio = io.BytesIO(raw)
    try:
        img = Image.open(bio)
        img.load()
        return img
    except (OSError, UnidentifiedImageError, ValueError):
        return None


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
    ext_l = ext.lower()

    if cloudinary_configured():
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
            raw = _read_upload_bytes(file)
            img = _pil_open_image_bytes(raw)
            if img is not None:
                img.thumbnail(resize)
                buf = io.BytesIO()
                save_kw = {'quality': 90}
                if ext_l == '.png':
                    img.save(buf, format='PNG')
                else:
                    if img.mode in ('RGBA', 'P') and ext_l != '.webp':
                        img = img.convert('RGB')
                    fmt = 'WEBP' if ext_l == '.webp' else 'JPEG'
                    img.save(buf, format=fmt, **save_kw)
                buf.seek(0)
                return _cloudinary_upload_stream(buf, folder, public_id=unique_base, resource_type='image')
            app.logger.warning(
                'Pillow could not decode %r (%d bytes); uploading raw bytes to Cloudinary.',
                filename,
                len(raw),
            )
            return _cloudinary_upload_stream(
                io.BytesIO(raw),
                folder,
                public_id=unique_base,
                resource_type='image',
            )

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

    out_name = f"{unique_base}{ext}"
    if subfolder:
        dest_dir = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
        rel = f"uploads/{subfolder}/{out_name}"
    else:
        dest_dir = app.config['UPLOAD_FOLDER']
        rel = f"uploads/{out_name}"

    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, out_name)

    if resize and ext_l in ('.jpg', '.jpeg', '.png', '.webp'):
        raw = _read_upload_bytes(file)
        img = _pil_open_image_bytes(raw)
        if img is None:
            raise ValueError(
                'Could not read that image. It may be corrupt or not a valid PNG/JPEG/WebP. '
                'Try re-saving it from your photo editor or use another file.'
            )
        img.thumbnail(resize)
        if ext_l == '.png':
            img.save(path, format='PNG')
        elif ext_l == '.webp':
            img.save(path, format='WEBP', quality=90)
        else:
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.save(path, quality=90, format='JPEG')
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


def _brevo_sender_domain_likely_unverifiable(sender_email):
    """Hosting default domains cannot be added as verified senders in Brevo."""
    if not sender_email or '@' not in sender_email:
        return True
    domain = sender_email.split('@', 1)[1].lower().strip()
    if domain.endswith('.railway.app') or domain.endswith('.up.railway.app'):
        return True
    if 'herokuapp.com' in domain:
        return True
    if domain in ('localhost', 'example.com', 'test', 'invalid'):
        return True
    return False


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


def _html_to_text_fallback(html_content):
    """Minimal plain-text sibling for transactional email (deliverability)."""
    if not html_content:
        return ''
    t = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', html_content)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:8000] if len(t) > 8000 else t


def brevo_send_transactional(to_email, to_name, subject, html_content, text_content=None):
    """Send one transactional email via Brevo REST API."""
    if not brevo_transactional_ready():
        return False
    api_key = os.environ.get('BREVO_API_KEY', '').strip()
    sender_email = os.environ.get('BREVO_SENDER_EMAIL', '').strip()
    settings = get_settings()
    sender_name = (os.environ.get('BREVO_SENDER_NAME') or '').strip() or (
        settings.get('name') or 'Newsletter'
    )
    reply_email = (os.environ.get('BREVO_REPLY_TO_EMAIL') or settings.get('email') or '').strip()
    if _brevo_sender_domain_likely_unverifiable(sender_email):
        app.logger.warning(
            'BREVO_SENDER_EMAIL uses %s — Brevo cannot verify default hosting domains. '
            'Use noreply@yourdomain.com after domain verification in Brevo.',
            sender_email.split('@', 1)[-1],
        )
    url = 'https://api.brevo.com/v3/smtp/email'
    headers = {'api-key': api_key, 'Content-Type': 'application/json'}
    display = (to_name or '').strip() or to_email.split('@')[0]
    plain = text_content if text_content is not None else _html_to_text_fallback(html_content)
    payload = {
        'sender': {'name': sender_name, 'email': sender_email},
        'to': [{'email': to_email.strip(), 'name': display[:120]}],
        'subject': subject[:998],
        'htmlContent': html_content,
    }
    if plain:
        payload['textContent'] = plain
    if reply_email:
        payload['replyTo'] = {'email': reply_email, 'name': sender_name[:80]}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException:
        app.logger.exception('Brevo transactional request failed for %s', to_email)
        return False
    ok = resp.status_code in (200, 201, 202, 204)
    if ok:
        try:
            data = resp.json()
            mid = data.get('messageId')
            app.logger.info('Brevo accepted email to=%s messageId=%s', to_email, mid)
        except (ValueError, TypeError):
            app.logger.info('Brevo accepted email to=%s (status %s)', to_email, resp.status_code)
        return True
    err_body = (resp.text or '')[:800]
    try:
        data = resp.json()
        if isinstance(data, dict):
            msg = data.get('message') or data.get('error') or data
            err_body = str(msg)[:800]
    except (ValueError, TypeError):
        pass
    app.logger.error(
        'Brevo rejected email to=%s HTTP %s: %s — check BREVO_SENDER_EMAIL is verified in Brevo '
        '(Senders & IP / domain authentication) and API key has permission to send emails.',
        to_email,
        resp.status_code,
        err_body,
    )
    return False


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
        app.logger.warning(
            'Welcome email not sent to %s: set BREVO_SENDER_EMAIL to a verified sender in Brevo '
            '(SMTP & API → Senders). List signup still works without it.',
            to_email,
        )
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
    try:
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
    except Exception:
        app.logger.exception('Welcome email template render failed for %s', to_email)
        return
    welcome_plain = f'{greeting}\n\n{body_text}\n\n{home_url}'
    if not brevo_send_transactional(to_email, name, subject_line, html, text_content=welcome_plain):
        app.logger.error(
            'Welcome email was not delivered to %s; see Brevo error above. '
            'In Brevo: Transactional → Statistics / Logs, or verify API key can send emails.',
            to_email,
        )


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


@app.template_filter('attachment_kind')
def attachment_kind_filter(path):
    return attachment_kind(path)


def _cloudinary_raw_inline_url(url: str) -> str:
    """Prefer inline display in browser (iframe / new tab) instead of forced download."""
    if not url or '/raw/upload/' not in url:
        return url
    if '/fl_inline/' in url:
        return url
    if '/fl_attachment/' in url:
        return url.replace('/fl_attachment/', '/fl_inline/', 1)
    return url.replace('/raw/upload/', '/raw/upload/fl_inline/', 1)


def _cloudinary_raw_attachment_url(url: str) -> str:
    """Explicit attachment delivery for Download button."""
    if not url or '/raw/upload/' not in url:
        return url
    if '/fl_attachment/' in url:
        return url
    if '/fl_inline/' in url:
        return url.replace('/fl_inline/', '/fl_attachment/', 1)
    return url.replace('/raw/upload/', '/raw/upload/fl_attachment/', 1)


def _cv_should_proxy_cloudinary_pdf(url: str) -> bool:
    """
    True when CV is delivered from Cloudinary — proxy through our app so visitors whose browsers
    cannot reach res.cloudinary.com still get the file from our domain.
    Admin-controlled URL only; host must be res.cloudinary.com with a standard /upload/ delivery path.
    """
    try:
        p = urlparse(url)
    except (TypeError, ValueError):
        return False
    if (p.scheme or '').lower() not in ('http', 'https'):
        return False
    if (p.hostname or '').lower() != 'res.cloudinary.com':
        return False
    path = (p.path or '').lower()
    # Raw PDFs use /raw/upload/; misconfigured uploads might use /image/upload/…pdf
    if '/raw/upload/' in path or '/image/upload/' in path:
        return True
    return False


def _portfolio_cv_urls(cv_stored: str):
    """
    (preview_iframe_url, download_href_url) for the CV PDF.
    Cloudinary raw PDFs default to attachment; local static PDFs need explicit inline for reliable iframe embed.
    """
    if not cv_stored or not str(cv_stored).strip():
        return None, None
    s = str(cv_stored).strip()
    if s.startswith('https://') or s.startswith('http://'):
        if _cv_should_proxy_cloudinary_pdf(s):
            try:
                return url_for('public_cv_pdf'), url_for('public_cv_pdf', download=1)
            except BuildError:
                pass
        return _cloudinary_raw_inline_url(s), _cloudinary_raw_attachment_url(s)
    path = s.lstrip('/')
    if path.startswith('static/'):
        path = path[7:]
    if not path.startswith('uploads/'):
        try:
            u = url_for('static', filename=path)
        except BuildError:
            u = '/static/' + path
        return u, u
    inner = path[len('uploads/') :]
    if '..' in inner or inner.startswith(('/', '\\')):
        try:
            u = url_for('static', filename=path)
        except BuildError:
            u = '/static/' + path
        return u, u
    if not inner.lower().endswith('.pdf'):
        dl = url_for('uploaded_file', filename=inner)
        return dl, dl
    try:
        inline = url_for('uploaded_file_inline', filename=inner)
        dl = url_for('uploaded_file', filename=inner)
        return inline, dl
    except BuildError:
        u = '/static/' + path
        return u, u


def _next_portfolio_item_sort():
    m = db.session.query(db.func.max(PortfolioItem.sort_order)).scalar()
    return (m if m is not None else -1) + 1


def _next_credential_sort():
    m = db.session.query(db.func.max(PortfolioCredential.sort_order)).scalar()
    return (m if m is not None else -1) + 1


def _save_portfolio_item_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError('Image type not allowed. Use PNG, JPG, WebP, or GIF.')
    return save_upload(file_storage, 'portfolio', (1400, 900))


def _save_credential_attachment(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    fn = (file_storage.filename or '').lower()
    if fn.endswith('.pdf'):
        return save_upload(file_storage, 'credentials')
    if allowed_file(file_storage.filename):
        return save_upload(file_storage, 'credentials', (1200, 1200))
    raise ValueError('Upload a PDF or image (PNG, JPG, WebP, GIF).')


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
    for (tags_blob,) in db.session.query(Post.tags).filter(Post.published.is_(True)).all():
        if tags_blob:
            for piece in str(tags_blob).split(','):
                t = piece.strip()
                if t:
                    all_tags.add(t)
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
    lang = get_lang()
    portfolio_data = get_portfolio_page_data()
    cv_inline, cv_download = _portfolio_cv_urls(SiteSettings.get('cv', '') or '')
    return render_template(
        'public/portfolio.html',
        portfolio=portfolio_data,
        lang=lang,
        cv_inline_url=cv_inline,
        cv_download_url=cv_download,
    )


# Browser-like UA: some CDNs return 403/empty for non-browser clients on delivery URLs.
_CV_PROXY_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)


@app.route('/portfolio/cv-file')
def public_cv_pdf():
    """
    Stream the site CV PDF from Cloudinary through this origin.
    Visitors who cannot reach res.cloudinary.com (blocked DNS/firewall) can still view/download
    the résumé because only the app server fetches Cloudinary.

    Fetches the exact URL stored in settings (no fl_inline/fl_attachment rewriting). Those
    URL edits often break raw delivery and caused 4xx from Cloudinary surfaced as 502 here.
    Inline vs download is controlled only by this response's Content-Disposition.
    """
    cv = (SiteSettings.get('cv') or '').strip()
    if not cv.startswith(('http://', 'https://')):
        abort(404)
    if not _cv_should_proxy_cloudinary_pdf(cv):
        abort(404)
    arg_dl = request.args.get('download')
    want_dl = str(arg_dl).lower() in ('1', 'true', 'yes')
    fetch_url = cv
    try:
        upstream = requests.get(
            fetch_url,
            timeout=90,
            stream=True,
            allow_redirects=True,
            headers={
                'User-Agent': _CV_PROXY_UA,
                'Accept': 'application/pdf,*/*;q=0.8',
            },
        )
    except requests.RequestException:
        app.logger.exception('CV proxy: request to Cloudinary failed')
        abort(502)
    if upstream.status_code != 200:
        upstream.close()
        app.logger.warning(
            'CV proxy: upstream HTTP %s for %s',
            upstream.status_code,
            fetch_url[:160],
        )
        abort(502)

    disposition = 'attachment' if want_dl else 'inline'

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(
        stream_with_context(generate()),
        mimetype='application/pdf',
        headers={
            'Content-Disposition': f'{disposition}; filename="cv.pdf"',
            'Cache-Control': 'public, max-age=120',
        },
    )


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


@app.route('/uploads/inline/<path:filename>')
def uploaded_file_inline(filename):
    """
    Serve PDF with Content-Disposition: inline so browsers embed it instead of downloading
    on every iframe navigation (fixes portfolio CV preview + reload loops).
    """
    if '..' in filename or filename.startswith(('/', '\\')):
        abort(404)
    if not filename.lower().endswith('.pdf'):
        abort(404)
    folder = os.path.normpath(app.config['UPLOAD_FOLDER'])
    target = os.path.normpath(os.path.join(folder, filename))
    if not target.startswith(folder + os.sep) and target != folder:
        abort(404)
    if not os.path.isfile(target):
        abort(404)
    return send_from_directory(
        app.config['UPLOAD_FOLDER'],
        filename,
        mimetype='application/pdf',
        as_attachment=False,
        max_age=3600,
    )


# Help assistant: human-readable labels for suggested links (en / sw).
HELP_ASSISTANT_NAV_LABELS = {
    'admin_dashboard': {'en': 'Open Dashboard', 'sw': 'Fungua Dashboard'},
    'admin_posts': {'en': 'Blog Posts', 'sw': 'Makala'},
    'admin_new_post': {'en': 'New Post', 'sw': 'Makala mpya'},
    'admin_settings': {'en': 'Site Settings', 'sw': 'Mipangilio ya tovuti'},
    'admin_portfolio': {'en': 'Portfolio', 'sw': 'Portfolio'},
    'admin_subscribers': {'en': 'Subscribers', 'sw': 'Waliojiunga'},
    'admin_broadcast': {'en': 'Email subscribers', 'sw': 'Tuma barua'},
    'admin_comments': {'en': 'Comments', 'sw': 'Maoni'},
    'admin_logout': {'en': 'Logout', 'sw': 'Toka'},
}


@app.route('/admin/help-assistant/config', methods=['GET'])
@login_required
def admin_help_assistant_config():
    chips = []
    for s in HELP_SUGGESTIONS:
        chips.append({
            'id': s['id'],
            'label_en': s['prompt_en'][:72] + ('…' if len(s['prompt_en']) > 72 else ''),
            'label_sw': s['prompt_sw'][:72] + ('…' if len(s['prompt_sw']) > 72 else ''),
            'prompt_en': s['prompt_en'],
            'prompt_sw': s['prompt_sw'],
        })
    ai_on = _help_assistant_ai_keys_configured()
    w_en = (
        'Hi — I explain how this admin panel works. Ask in simple English or Kiswahili. '
        'I do not change settings for you; I only give steps and tips. '
        'Try the shortcuts below or type your own question.'
    )
    w_sw = (
        'Hujambo — naeleza jinsi paneli hii inavyofanya kazi. Uliza kwa Kiingereza au Kiswahili kwa lugha rahisi. '
        'Sibadilishi mipangilio badala yako; natoa hatua tu. '
        'Jaribu vitufe hapa chini au andika swali lako.'
    )
    if ai_on:
        w_en += ' When your site has smart help enabled, my answers are gently polished for clarity — the facts stay the same.'
        w_sw += ' Tovuti yako ikiwa na msaada mahiri, majibu yanaweza lainishwa kidogo — ukweli hubaki ule ule.'
    return jsonify({
        'ok': True,
        'welcome_en': w_en,
        'welcome_sw': w_sw,
        'ai_available': ai_on,
        'chips': chips,
    })


@app.route('/admin/help-assistant/chat', methods=['POST'])
@login_required
def admin_help_assistant_chat():
    data = request.get_json(silent=True) or {}
    msg = (data.get('message') or '').strip()
    lang = (data.get('lang') or 'en').lower()
    if lang not in ('en', 'sw'):
        lang = 'en'
    intent_id, reply, endpoints = resolve_help_query(msg, lang)
    links = []
    for ep in endpoints:
        try:
            url = url_for(ep)
        except BuildError:
            continue
        lab = HELP_ASSISTANT_NAV_LABELS.get(ep, {})
        label = lab.get(lang) or lab.get('en') or ep.replace('admin_', '').replace('_', ' ').title()
        links.append({'url': url, 'label': label})

    source = 'faq'
    final_reply = reply
    if intent_id != 'empty':
        expanded, _ai_err = help_assistant_ai_expand(msg, lang, reply, intent_id)
        if expanded:
            final_reply = expanded
            source = 'ai'

    return jsonify({
        'ok': True,
        'intent': intent_id,
        'reply': final_reply,
        'links': links,
        'source': source,
    })


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
                        rel = save_upload(f, subfolder, resize)
                        SiteSettings.set(field, rel)
                    except ValueError as e:
                        flash(str(e), 'error')

        # CV upload (case-insensitive .pdf; must pass allowed_file)
        cv_upload = request.files.get('cv')
        if cv_upload and cv_upload.filename:
            fn = (cv_upload.filename or '').strip()
            if not allowed_file(fn) or not fn.lower().endswith('.pdf'):
                flash('CV must be a PDF file.', 'error')
            else:
                try:
                    rel = save_upload(cv_upload, 'documents')
                    SiteSettings.set('cv', rel)
                except ValueError as e:
                    flash(str(e), 'error')

        flash('Settings saved!', 'success')
        return redirect(url_for('admin_settings'))
    return render_template('admin/settings.html')


@app.route('/admin/diagnostics/brevo-test-email', methods=['POST'])
@login_required
def admin_brevo_test_email():
    """Send a one-off transactional email to the logged-in admin (debug Brevo)."""
    if not brevo_transactional_ready():
        flash(
            'Brevo transactional email is not configured. Set BREVO_API_KEY and '
            'BREVO_SENDER_EMAIL (verified sender on your own domain).',
            'error',
        )
        return redirect(url_for('admin_settings'))
    subj = 'Test email from your law blog'
    html = '<p>If you received this, Brevo transactional sending works.</p><p>You can delete this message.</p>'
    ok = brevo_send_transactional(
        current_user.email,
        '',
        subj,
        html,
        text_content='If you received this, Brevo transactional sending works.',
    )
    if ok:
        flash(
            f'Test email sent to {current_user.email}. Check inbox and spam. '
            'If nothing arrives, read the server log for the exact Brevo API error.',
            'success',
        )
    else:
        flash(
            'Brevo rejected the test send. Check hosting logs for lines starting with '
            '"Brevo rejected email". Fix sender verification or API key permissions.',
            'error',
        )
    return redirect(url_for('admin_settings'))


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
                    cover = save_upload(f, 'covers', (1400, 800))
                except ValueError as e:
                    flash(str(e), 'error')

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
                    post.cover_image = save_upload(f, 'covers', (1400, 800))
                except ValueError as e:
                    flash(str(e), 'error')

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


@app.route('/admin/broadcast', methods=['GET', 'POST'])
@login_required
def admin_broadcast():
    count = Subscriber.query.filter_by(active=True).count()
    if request.method == 'POST':
        subject = (request.form.get('subject') or '').strip()
        body = request.form.get('body_html') or ''
        if not subject:
            flash('Subject is required.', 'error')
            return redirect(url_for('admin_broadcast'))
        if not body.strip():
            flash('Message body is required.', 'error')
            return redirect(url_for('admin_broadcast'))
        if not brevo_transactional_ready():
            flash('Set BREVO_API_KEY and BREVO_SENDER_EMAIL before sending subscriber email.', 'error')
            return redirect(url_for('admin_broadcast'))
        try:
            inner = bleach.clean(
                body,
                tags=BLEACH_TAGS,
                attributes=BLEACH_ATTRS,
                protocols=BLEACH_PROTOCOLS,
                strip=True,
            )
        except Exception:
            flash('Could not sanitize HTML body.', 'error')
            return redirect(url_for('admin_broadcast'))
        schedule_subscriber_broadcast(subject, inner)
        flash(
            f'Broadcast queued for {count} active subscriber(s). Sending runs in the background; '
            'check server logs for per-recipient errors.',
            'success',
        )
        return redirect(url_for('admin_broadcast'))
    return render_template('admin/broadcast.html', count=count)


@app.route('/admin/portfolio', methods=['GET', 'POST'])
@login_required
def admin_portfolio():
    _ensure_portfolio_seeded()
    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()

        if action == 'save_intro':
            SiteSettings.set('portfolio_intro_en', request.form.get('portfolio_intro_en', ''))
            SiteSettings.set('portfolio_intro_sw', request.form.get('portfolio_intro_sw', ''))
            cvf = request.files.get('cv')
            if cvf and cvf.filename:
                fn = (cvf.filename or '').strip()
                if not allowed_file(fn) or not fn.lower().endswith('.pdf'):
                    flash('CV must be a PDF file.', 'error')
                    return redirect(url_for('admin_portfolio'))
                try:
                    rel = save_upload(cvf, 'documents')
                    SiteSettings.set('cv', rel)
                    flash('Intro saved and CV file uploaded.', 'success')
                except ValueError as e:
                    flash(str(e), 'error')
                    return redirect(url_for('admin_portfolio'))
            else:
                flash('Intro text saved.', 'success')
            return redirect(url_for('admin_portfolio'))

        if action == 'remove_cv':
            SiteSettings.set('cv', '')
            flash('CV removed from the site (you can upload a new PDF anytime).', 'success')
            return redirect(url_for('admin_portfolio'))

        if action == 'add_item':
            it = PortfolioItem(
                sort_order=_next_portfolio_item_sort(),
                icon=(request.form.get('icon') or '📌')[:32],
                category_en=(request.form.get('category_en') or '')[:200],
                category_sw=(request.form.get('category_sw') or '')[:200],
                title_en=(request.form.get('title_en') or '')[:300],
                title_sw=(request.form.get('title_sw') or '')[:300],
                desc_en=request.form.get('desc_en') or '',
                desc_sw=request.form.get('desc_sw') or '',
                year=(request.form.get('year') or '')[:20],
            )
            db.session.add(it)
            db.session.flush()
            if request.files.get('image') and request.files['image'].filename:
                try:
                    it.image = _save_portfolio_item_image(request.files['image'])
                except ValueError as e:
                    flash(str(e), 'error')
                    db.session.rollback()
                    return redirect(url_for('admin_portfolio'))
            db.session.commit()
            flash('Work item added.', 'success')
            return redirect(url_for('admin_portfolio'))

        if action == 'update_item':
            pid = request.form.get('item_id', type=int)
            if not pid:
                flash('Invalid work item.', 'error')
                return redirect(url_for('admin_portfolio'))
            it = PortfolioItem.query.get_or_404(pid)
            it.icon = (request.form.get('icon') or '📌')[:32]
            it.category_en = (request.form.get('category_en') or '')[:200]
            it.category_sw = (request.form.get('category_sw') or '')[:200]
            it.title_en = (request.form.get('title_en') or '')[:300]
            it.title_sw = (request.form.get('title_sw') or '')[:300]
            it.desc_en = request.form.get('desc_en') or ''
            it.desc_sw = request.form.get('desc_sw') or ''
            it.year = (request.form.get('year') or '')[:20]
            so = request.form.get('sort_order', type=int)
            if so is not None:
                it.sort_order = so
            if request.files.get('image') and request.files['image'].filename:
                try:
                    it.image = _save_portfolio_item_image(request.files['image'])
                except ValueError as e:
                    flash(str(e), 'error')
                    return redirect(url_for('admin_portfolio'))
            db.session.commit()
            flash('Work item updated.', 'success')
            return redirect(url_for('admin_portfolio'))

        if action == 'add_credential':
            cr = PortfolioCredential(
                sort_order=_next_credential_sort(),
                icon=(request.form.get('icon') or '🏅')[:32],
                title_en=(request.form.get('title_en') or '')[:300],
                title_sw=(request.form.get('title_sw') or '')[:300],
                detail_en=request.form.get('detail_en') or '',
                detail_sw=request.form.get('detail_sw') or '',
                year=(request.form.get('year') or '')[:20],
            )
            db.session.add(cr)
            db.session.flush()
            if request.files.get('attachment') and request.files['attachment'].filename:
                try:
                    cr.attachment = _save_credential_attachment(request.files['attachment'])
                except ValueError as e:
                    flash(str(e), 'error')
                    db.session.rollback()
                    return redirect(url_for('admin_portfolio'))
            db.session.commit()
            flash('Certificate / award added.', 'success')
            return redirect(url_for('admin_portfolio'))

        if action == 'update_credential':
            cid = request.form.get('credential_id', type=int)
            if not cid:
                flash('Invalid entry.', 'error')
                return redirect(url_for('admin_portfolio'))
            cr = PortfolioCredential.query.get_or_404(cid)
            cr.icon = (request.form.get('icon') or '🏅')[:32]
            cr.title_en = (request.form.get('title_en') or '')[:300]
            cr.title_sw = (request.form.get('title_sw') or '')[:300]
            cr.detail_en = request.form.get('detail_en') or ''
            cr.detail_sw = request.form.get('detail_sw') or ''
            cr.year = (request.form.get('year') or '')[:20]
            so = request.form.get('sort_order', type=int)
            if so is not None:
                cr.sort_order = so
            if request.files.get('attachment') and request.files['attachment'].filename:
                try:
                    cr.attachment = _save_credential_attachment(request.files['attachment'])
                except ValueError as e:
                    flash(str(e), 'error')
                    return redirect(url_for('admin_portfolio'))
            db.session.commit()
            flash('Certificate / award updated.', 'success')
            return redirect(url_for('admin_portfolio'))

        flash('Unknown action.', 'error')
        return redirect(url_for('admin_portfolio'))

    pdata = get_portfolio_page_data()
    items = PortfolioItem.query.order_by(
        PortfolioItem.sort_order.asc(), PortfolioItem.id.asc(),
    ).all()
    credentials = PortfolioCredential.query.order_by(
        PortfolioCredential.sort_order.asc(), PortfolioCredential.id.asc(),
    ).all()
    return render_template(
        'admin/portfolio.html',
        intro_en=pdata['intro_en'],
        intro_sw=pdata['intro_sw'],
        items=items,
        credentials=credentials,
        cv_path=SiteSettings.get('cv', ''),
    )


@app.route('/admin/portfolio/item/<int:item_id>/delete', methods=['POST'])
@login_required
def admin_portfolio_item_delete(item_id):
    it = PortfolioItem.query.get_or_404(item_id)
    db.session.delete(it)
    db.session.commit()
    flash('Work item removed.', 'success')
    return redirect(url_for('admin_portfolio'))


@app.route('/admin/portfolio/credential/<int:cred_id>/delete', methods=['POST'])
@login_required
def admin_portfolio_credential_delete(cred_id):
    cr = PortfolioCredential.query.get_or_404(cred_id)
    db.session.delete(cr)
    db.session.commit()
    flash('Certificate / award removed.', 'success')
    return redirect(url_for('admin_portfolio'))


@app.route('/admin/upload-image', methods=['POST'])
@login_required
def admin_upload_image():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    if f and allowed_file(f.filename):
        try:
            rel = save_upload(f, 'blog')
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        if rel.startswith('http://') or rel.startswith('https://'):
            url = rel
        else:
            url = url_for('static', filename=rel)
        return jsonify({'url': url})
    return jsonify({'error': 'Invalid file'}), 400


AI_WRITING_ACTIONS = {
    'improve': (
        'Light-touch clarity pass: fix unclear phrases and small redundancies only. '
        'Keep the author’s voice, meaning, and legal precision. Do not add facts, arguments, or new sections.'
    ),
    'grammar': (
        'Fix spelling, grammar, and punctuation only. Do not rephrase for style, “sound better”, or shorten. '
        'Keep the same wording unless a correction strictly requires a tiny change (e.g. subject–verb agreement).'
    ),
    'tone': (
        'Adjust tone to be warmer, clearer, and more confident for a general audience. '
        'Keep facts, citations, and structure; do not remove or reorder major blocks.'
    ),
}

# Lower temperature = less wholesale rewriting (especially grammar).
AI_ACTION_TEMPERATURE = {
    'grammar': 0.08,
    'improve': 0.18,
    'tone': 0.32,
}

AI_EDITOR_SYSTEM = (
    'You are a careful copy editor. Reply with ONLY an HTML fragment (no markdown, no code fences, '
    'no <!DOCTYPE> or <html> wrapper). Use tags such as: p, br, strong, em, u, s, h1, h2, h3, ul, ol, li, '
    'a, img, blockquote, pre, code. For external links use target="_blank" rel="noopener noreferrer". '
    'Preserve img src and a href exactly when they are unchanged. '
    'Keep the same block order and structure as the input: do not replace the piece with a new outline, '
    'summary, or heavily condensed version unless the input is already that short.'
)


def _ai_instruction_for(action, scope):
    """Scope: 'full' = entire body, 'selection' = highlighted excerpt only."""
    base = AI_WRITING_ACTIONS[action]
    if scope == 'selection':
        return (
            'The HTML below is ONLY a highlighted excerpt from a larger article (not the full post). '
            'Return ONLY the revised excerpt as an HTML fragment of similar length and structure—'
            'do not invent surrounding sections or rewrite as if it were the whole article. '
        ) + base
    return (
        'The HTML below is the full article body. Edit conservatively: same order of blocks, same headings '
        'where possible, same lists and media. Do not merge or split paragraphs except to fix clear grammar. '
        'Do not summarize, shorten, or replace the article with a new draft. '
    ) + base


def _normalize_ai_html_output(content):
    if not content or not str(content).strip():
        return None
    content = str(content).strip()
    if content.startswith('```'):
        content = re.sub(r'^```(?:html|HTML)?\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content).strip()
    return content if content else None


def _ai_max_input_chars():
    try:
        return max(20_000, int(os.environ.get('AI_MAX_INPUT_CHARS', 120000)))
    except ValueError:
        return 120000


# OpenAI-SDK-compatible providers: Groq + OpenRouter (preset IDs below).
AI_CHAT_MODELS = {
    'groq': {
        'balanced': 'llama-3.3-70b-versatile',
        'fast': 'llama-3.1-8b-instant',
        'long_context': 'mixtral-8x7b-32768',
        'google': 'gemma2-9b-it',
    },
    'openrouter': {
        'auto_free': 'openrouter/free',
        'smart_free': 'qwen/qwen3.6-plus:free',
        'fast_free': 'stepfun/step-3.5-flash:free',
        'nvidia_free': 'nvidia/nemotron-3-super-120b-a12b:free',
        'vision_free': 'google/lyria-3-pro-preview',
    },
}


def _groq_chat_model_id():
    direct = (os.environ.get('GROQ_MODEL') or '').strip()
    if direct:
        return direct
    preset = (os.environ.get('GROQ_MODEL_PRESET') or 'balanced').strip().lower()
    return AI_CHAT_MODELS['groq'].get(preset) or AI_CHAT_MODELS['groq']['balanced']


def _openrouter_chat_model_id():
    direct = (os.environ.get('OPENROUTER_MODEL') or '').strip()
    if direct:
        return direct
    preset = (os.environ.get('OPENROUTER_MODEL_PRESET') or 'auto_free').strip().lower()
    return AI_CHAT_MODELS['openrouter'].get(preset) or AI_CHAT_MODELS['openrouter']['auto_free']


def _help_assistant_ai_keys_configured():
    return bool(
        (os.environ.get('GROQ_API_KEY') or '').strip()
        or (os.environ.get('OPENROUTER_API_KEY') or '').strip()
    )


HELP_ASSISTANT_AI_SYSTEM = (
    'You are a friendly guide for a non-technical person using a blog admin panel.\n\n'
    'You receive the user\'s question and a REFERENCE answer that is correct for this admin panel.\n\n'
    'Rules:\n'
    '- Write your entire reply in the language specified (English OR Kiswahili only).\n'
    '- Stay faithful to the REFERENCE. You may shorten, clarify, or use friendly bullets, but do not '
    'invent menus, buttons, or steps that are not implied by the REFERENCE.\n'
    '- Do not mention: developers, programming, configuration files, secrets, databases, source code, '
    'hosting brands, third-party APIs, product names for AI providers, or technical diagnostics.\n'
    '- If the REFERENCE says to ask whoever runs the website, say that in very simple words.\n'
    '- You may use **bold** only for short labels that match the interface (e.g. **Save**, **Site Settings**).\n'
    '- No code fences. No HTML tags. Plain text with line breaks.'
)


def _openai_sdk_text_chat(client, model, system: str, user: str, temperature=0.2, max_tokens=900):
    """Plain-text chat completion; return (text, error)."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        msg = str(e)
        if hasattr(e, 'message') and e.message:
            msg = str(e.message)
        return None, msg[:650]
    try:
        raw = (resp.choices[0].message.content or '') if resp.choices else ''
    except (IndexError, AttributeError, TypeError):
        return None, 'Unexpected chat completion response.'
    out = _normalize_ai_html_output(raw)
    if not out:
        return None, 'Empty response.'
    return out.strip(), None


def help_assistant_ai_expand(user_question: str, lang: str, reference: str, intent_id: str):
    """
    Use Groq then OpenRouter to rewrite the reference in simpler language.
    Returns (expanded_text, None) on success, or (None, error) on failure.
    Skips when no keys or intent is empty.
    """
    if intent_id == 'empty':
        return None, None
    if not _help_assistant_ai_keys_configured():
        return None, None
    ref = reference.strip()
    if len(ref) > 8000:
        ref = ref[:8000] + '\n…'
    lang_name = 'Kiswahili' if lang == 'sw' else 'English'
    user_block = (
        f'Reply language (entire answer must be in this language): {lang_name}\n\n'
        f'User question:\n{user_question}\n\n'
        f'REFERENCE (stay accurate):\n{ref}'
    )
    groq_key = (os.environ.get('GROQ_API_KEY') or '').strip()
    or_key = (os.environ.get('OPENROUTER_API_KEY') or '').strip()
    failures = []

    if groq_key:
        client = OpenAI(
            base_url='https://api.groq.com/openai/v1',
            api_key=groq_key,
            timeout=_openai_http_timeout_help(),
        )
        out, err = _openai_sdk_text_chat(
            client, _groq_chat_model_id(), HELP_ASSISTANT_AI_SYSTEM, user_block, 0.2, 900,
        )
        if out:
            app.logger.info('Help assistant: AI expansion used Groq (%s)', _groq_chat_model_id())
            return out, None
        if err:
            failures.append(err)
            app.logger.warning('Help assistant: Groq expansion failed — %s', err[:180])

    if or_key:
        model = _openrouter_chat_model_id()
        base = (os.environ.get('OPENROUTER_API_BASE') or 'https://openrouter.ai/api/v1').rstrip('/')
        headers = {}
        referer = (
            os.environ.get('OPENROUTER_HTTP_REFERER')
            or os.environ.get('PUBLIC_BASE_URL')
            or os.environ.get('SITE_URL')
            or ''
        ).strip()
        if referer:
            headers['HTTP-Referer'] = referer
        app_title = (os.environ.get('OPENROUTER_APP_TITLE') or '').strip()
        if app_title:
            headers['X-Title'] = app_title
        kw = {'base_url': base, 'api_key': or_key, 'timeout': _openai_http_timeout_help()}
        if headers:
            kw['default_headers'] = headers
        client = OpenAI(**kw)
        out, err = _openai_sdk_text_chat(client, model, HELP_ASSISTANT_AI_SYSTEM, user_block, 0.2, 900)
        if out:
            app.logger.info('Help assistant: AI expansion used OpenRouter (%s)', model)
            return out, None
        if err:
            failures.append(err)

    return None, (failures[0] if failures else 'AI unavailable')


def _openai_sdk_rewrite_html(client, model, html_fragment, instruction, temperature=0.25):
    """Run chat completion; return (html_fragment_out, error_message)."""
    user_content = f'{instruction}\n\nHTML:\n{html_fragment}'
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': AI_EDITOR_SYSTEM},
                {'role': 'user', 'content': user_content},
            ],
            temperature=temperature,
            max_tokens=8192,
        )
    except Exception as e:
        msg = str(e)
        if hasattr(e, 'message') and e.message:
            msg = str(e.message)
        return None, msg[:650]
    try:
        raw = (resp.choices[0].message.content or '') if resp.choices else ''
    except (IndexError, AttributeError, TypeError):
        return None, 'Unexpected chat completion response.'
    out = _normalize_ai_html_output(raw)
    if not out:
        return None, 'AI returned empty content.'
    return out, None


def _groq_rewrite_editor_html(html_fragment, instruction, temperature=0.25):
    api_key = (os.environ.get('GROQ_API_KEY') or '').strip()
    if not api_key:
        return None, None
    model = _groq_chat_model_id()
    client = OpenAI(
        base_url='https://api.groq.com/openai/v1',
        api_key=api_key,
        timeout=_openai_http_timeout_editor(),
    )
    return _openai_sdk_rewrite_html(client, model, html_fragment, instruction, temperature)


def _openrouter_rewrite_editor_html(html_fragment, instruction, temperature=0.25):
    api_key = (os.environ.get('OPENROUTER_API_KEY') or '').strip()
    if not api_key:
        return None, None
    model = _openrouter_chat_model_id()
    base = (os.environ.get('OPENROUTER_API_BASE') or 'https://openrouter.ai/api/v1').rstrip('/')
    headers = {}
    referer = (
        os.environ.get('OPENROUTER_HTTP_REFERER')
        or os.environ.get('PUBLIC_BASE_URL')
        or os.environ.get('SITE_URL')
        or ''
    ).strip()
    if referer:
        headers['HTTP-Referer'] = referer
    app_title = (os.environ.get('OPENROUTER_APP_TITLE') or '').strip()
    if app_title:
        headers['X-Title'] = app_title
    kw = {'base_url': base, 'api_key': api_key, 'timeout': _openai_http_timeout_editor()}
    if headers:
        kw['default_headers'] = headers
    client = OpenAI(**kw)
    return _openai_sdk_rewrite_html(client, model, html_fragment, instruction, temperature)


def rewrite_editor_html(html_fragment, instruction, temperature=0.25):
    """Groq first, then OpenRouter. Skips providers with no key; on failure, tries the next."""
    groq_key = (os.environ.get('GROQ_API_KEY') or '').strip()
    or_key = (os.environ.get('OPENROUTER_API_KEY') or '').strip()
    failures = []

    if groq_key:
        out, err = _groq_rewrite_editor_html(html_fragment, instruction, temperature)
        if out:
            app.logger.info('AI writing: used Groq (%s)', _groq_chat_model_id())
            return out, None
        if err:
            failures.append(f'Groq: {err}')
            app.logger.warning('AI writing: Groq failed, trying OpenRouter — %s', err[:200])

    if or_key:
        out, err = _openrouter_rewrite_editor_html(html_fragment, instruction, temperature)
        if out:
            app.logger.info('AI writing: used OpenRouter (%s)', _openrouter_chat_model_id())
            return out, None
        if err:
            failures.append(f'OpenRouter: {err}')

    if not (groq_key or or_key):
        return None, (
            'No AI API keys configured. Set GROQ_API_KEY and/or OPENROUTER_API_KEY.'
        )
    detail = ' '.join(failures) if failures else 'Every provider returned empty output.'
    return None, f'All configured AI providers failed. {detail}'


@app.route('/admin/ai/writing', methods=['POST'])
@login_required
def admin_ai_writing():
    data = request.get_json(silent=True) or {}
    content = data.get('content') or ''
    action = (data.get('action') or '').strip().lower()
    scope = (data.get('scope') or 'full').strip().lower()
    if scope not in ('full', 'selection'):
        scope = 'full'
    if action not in AI_WRITING_ACTIONS:
        return jsonify({'ok': False, 'error': 'Invalid action.'}), 400
    max_in = _ai_max_input_chars()
    if len(content) > max_in:
        return jsonify({
            'ok': False,
            'error': f'Content too large for AI (max {max_in} characters). Shorten the draft or raise AI_MAX_INPUT_CHARS.',
        }), 400
    instruction = _ai_instruction_for(action, scope)
    temp = AI_ACTION_TEMPERATURE.get(action, 0.25)
    result, err = rewrite_editor_html(content, instruction, temperature=temp)
    if err:
        return jsonify({'ok': False, 'error': err}), 503
    return jsonify({'ok': True, 'result': result})


@app.route('/admin/comments')
@login_required
def admin_comments():
    rows = (
        Comment.query.options(joinedload(Comment.post))
        .order_by(Comment.created_at.desc())
        .all()
    )
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
