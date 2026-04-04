import os
import json
import re
import math
import requests
import markdown2
import bleach
from datetime import datetime
from slugify import slugify
from dotenv import load_dotenv
from flask import (Flask, render_template, redirect, url_for, request,
                   flash, jsonify, abort, session, send_from_directory)
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
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    title_en = db.Column(db.String(300), nullable=False)
    title_fr = db.Column(db.String(300))
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

    def reading_time(self, lang='en'):
        body = self.body_en if lang == 'en' else (self.body_fr or self.body_en)
        words = len(re.findall(r'\w+', body))
        minutes = max(1, math.ceil(words / 200))
        return minutes

    def rendered_body(self, lang='en'):
        body = self.body_en if lang == 'en' else (self.body_fr or self.body_en)
        allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + [
            'p','h1','h2','h3','h4','h5','h6','blockquote','pre','code',
            'ul','ol','li','strong','em','a','img','br','hr','table',
            'thead','tbody','tr','th','td','figure','figcaption','mark',
            'del','ins','sup','sub','details','summary'
        ]
        allowed_attrs = {'a': ['href','title','target','rel'], 'img': ['src','alt','title','class']}
        html = markdown2.markdown(body, extras=['fenced-code-blocks','tables','strike','footnotes','task_list'])
        return bleach.clean(html, tags=allowed_tags, attributes=allowed_attrs)

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


# ─── Helpers ──────────────────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file, subfolder='', resize=None):
    filename = secure_filename(file.filename)
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    name, ext = os.path.splitext(filename)
    safe_name = slugify(name) if name else 'file'
    filename = f"{ts}_{safe_name}{ext}"

    if subfolder:
        dest_dir = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
        rel = f"uploads/{subfolder}/{filename}"
    else:
        dest_dir = app.config['UPLOAD_FOLDER']
        rel = f"uploads/{filename}"

    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, filename)

    if resize and ext.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
        img = Image.open(file)
        img.thumbnail(resize)
        img.save(path, quality=90)
    else:
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
    list_id = os.environ.get('BREVO_LIST_ID', '')
    if not api_key or api_key == 'your-brevo-api-key':
        return True  # skip in dev
    url = 'https://api.brevo.com/v3/contacts'
    headers = {'api-key': api_key, 'Content-Type': 'application/json'}
    payload = {
        'email': email,
        'attributes': {'FIRSTNAME': name},
        'listIds': [int(list_id)],
        'updateEnabled': True
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    return resp.status_code in (200, 201, 204)


def get_lang():
    return session.get('lang', 'en')


def get_settings():
    return {
        'name': SiteSettings.get('name', 'Counsel & Craft'),
        'tagline_en': SiteSettings.get('tagline_en', 'Law · Writing · Youth'),
        'tagline_fr': SiteSettings.get('tagline_fr', 'Droit · Écriture · Jeunesse'),
        'bio_en': SiteSettings.get('bio_en', 'A law student, writer, and voice for the youth.'),
        'bio_fr': SiteSettings.get('bio_fr', 'Étudiant en droit, écrivain et voix de la jeunesse.'),
        'email': SiteSettings.get('email', ''),
        'twitter': SiteSettings.get('twitter', ''),
        'linkedin': SiteSettings.get('linkedin', ''),
        'instagram': SiteSettings.get('instagram', ''),
        'logo': SiteSettings.get('logo', ''),
        'avatar': SiteSettings.get('avatar', ''),
        'cv': SiteSettings.get('cv', ''),
        'hero_quote_en': SiteSettings.get('hero_quote_en', 'Justice is the constant will to render to every man his due.'),
        'hero_quote_fr': SiteSettings.get('hero_quote_fr', 'La justice est la volonté constante de rendre à chacun ce qui lui est dû.'),
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
    if lang in ('en', 'fr'):
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
    return render_template('public/post.html', post=p, related=related, lang=lang)


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
            return jsonify({'ok': True, 'msg': 'Welcome back! You\'re resubscribed.'})
        return jsonify({'ok': False, 'msg': 'You\'re already subscribed!'})

    sub = Subscriber(email=email, name=name)
    db.session.add(sub)
    db.session.commit()
    add_to_brevo(email, name)
    return jsonify({'ok': True, 'msg': 'You\'re in! Thank you for subscribing.'})


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
        ]:
            if field in request.files and request.files[field].filename:
                f = request.files[field]
                if allowed_file(f.filename):
                    rel = save_upload(f, subfolder, resize)
                    SiteSettings.set(field, rel)

        # CV upload
        if 'cv' in request.files and request.files['cv'].filename:
            f = request.files['cv']
            if f.filename.endswith('.pdf'):
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
                cover = save_upload(f, 'covers', (1400, 800))

        post = Post(
            slug=slug,
            title_en=title_en,
            title_fr=request.form.get('title_fr', ''),
            excerpt_en=request.form.get('excerpt_en', ''),
            excerpt_fr=request.form.get('excerpt_fr', ''),
            body_en=request.form.get('body_en', ''),
            body_fr=request.form.get('body_fr', ''),
            tags=request.form.get('tags', ''),
            cover_image=cover,
            published='published' in request.form,
            featured='featured' in request.form,
        )
        db.session.add(post)
        db.session.commit()
        flash('Post created!', 'success')
        return redirect(url_for('admin_posts'))
    return render_template('admin/post_edit.html', post=None)


@app.route('/admin/posts/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if request.method == 'POST':
        post.title_en = request.form.get('title_en', '').strip()
        post.title_fr = request.form.get('title_fr', '')
        post.excerpt_en = request.form.get('excerpt_en', '')
        post.excerpt_fr = request.form.get('excerpt_fr', '')
        post.body_en = request.form.get('body_en', '')
        post.body_fr = request.form.get('body_fr', '')
        post.tags = request.form.get('tags', '')
        post.published = 'published' in request.form
        post.featured = 'featured' in request.form
        post.updated_at = datetime.utcnow()

        if 'cover_image' in request.files and request.files['cover_image'].filename:
            f = request.files['cover_image']
            if allowed_file(f.filename):
                post.cover_image = save_upload(f, 'covers', (1400, 800))

        db.session.commit()
        flash('Post updated!', 'success')
        return redirect(url_for('admin_posts'))
    return render_template('admin/post_edit.html', post=post)


@app.route('/admin/posts/<int:post_id>/delete', methods=['POST'])
@login_required
def admin_delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    db.session.delete(post)
    db.session.commit()
    flash('Post deleted.', 'success')
    return redirect(url_for('admin_posts'))


@app.route('/admin/posts/<int:post_id>/toggle', methods=['POST'])
@login_required
def admin_toggle_post(post_id):
    post = Post.query.get_or_404(post_id)
    post.published = not post.published
    db.session.commit()
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
        rel = save_upload(f, 'blog')
        url = url_for('static', filename=rel)
        return jsonify({'url': url})
    return jsonify({'error': 'Invalid file'}), 400


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
