# ⚖ Counsel & Craft — Personal Site & Blog

A dynamic, bilingual (English / French) personal website built with Flask for a law student, writer, and youth advocate. Features a full blog with rich markdown editor, newsletter via Brevo, reCAPTCHA protection, dark/light theme toggle, and Railway-ready deployment.

---

## ✨ Features

### Public Site
- **Homepage** — hero with avatar, quote badge, featured & recent posts
- **Blog** — paginated posts, tag filtering, reading time estimates
- **Post reader** — beautiful reading experience, progress bar, share buttons, newsletter nudge
- **About** — bio, social links, CV download
- **Portfolio** — showcase academic/professional work
- **Dark / Light theme toggle** — persists via localStorage
- **EN / FR language toggle** — full bilingual content support
- **Newsletter** — modal with Brevo integration + reCAPTCHA v3

### Admin Panel (`/admin`)
- **Dashboard** — stats overview (posts, subscribers, recent activity)
- **Blog editor** — EasyMDE rich markdown editor with:
  - Bold, italic, strikethrough, headings, quotes, lists, links, images
  - Code blocks, tables, horizontal rules, fullscreen, side-by-side preview
  - In-editor image upload
  - Markdown cheatsheet reference
  - Reading time estimator
  - Bilingual content (EN + FR tabs)
  - Cover image upload with preview
  - Tag chips for quick tag adding
  - Autosave to localStorage
- **Post management** — toggle publish/draft, feature posts, delete
- **Site settings** — name, bio, tagline, quotes (EN+FR), social links, logo, avatar, CV upload
- **Subscribers** — list with email, name, date

---

## 🚀 Quick Start (Local)

```bash
# Clone and enter directory
cd lawblog

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Edit .env with your credentials

# Run (auto-creates DB and admin user)
python app.py
```

Visit `http://localhost:5000` — admin at `/admin/login`

Default admin: `admin@example.com` / `changeme123` (set in `.env`)

---

## ⚙️ Environment Variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask secret key (use a long random string) |
| `DATABASE_URL` | Database URL (SQLite default, PostgreSQL for Railway) |
| `ADMIN_EMAIL` | Admin login email |
| `ADMIN_PASSWORD` | Admin login password |
| `BREVO_API_KEY` | Your Brevo (Sendinblue) API key |
| `BREVO_LIST_ID` | Brevo contact list ID for newsletter |
| `BREVO_SENDER_EMAIL` | From email for Brevo |
| `RECAPTCHA_SITE_KEY` | Google reCAPTCHA v3 site key |
| `RECAPTCHA_SECRET_KEY` | Google reCAPTCHA v3 secret key |

---

## 📬 Brevo Newsletter Setup

1. Create an account at [brevo.com](https://brevo.com)
2. Go to **Contacts → Lists** → create a new list → copy the List ID
3. Go to **Settings → API Keys** → create an API key
4. Add both to `.env`

Subscribers are automatically added to your Brevo list when they sign up on the site.

---

## 🔐 Google reCAPTCHA v3 Setup

1. Go to [google.com/recaptcha](https://www.google.com/recaptcha/admin)
2. Register a new site — choose **reCAPTCHA v3**
3. Add your domain (and `localhost` for development)
4. Copy the **Site Key** and **Secret Key** to `.env`

reCAPTCHA is skipped automatically if keys aren't set (dev mode).

---

## 🚂 Deploy to Railway

1. Push code to GitHub
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add a **PostgreSQL** plugin in Railway (auto-sets `DATABASE_URL`)
4. In Railway → **Variables**, add all environment variables from `.env`
5. Railway auto-detects `railway.toml` and deploys

The database is initialized automatically on first deploy.

---

## 📁 Project Structure

```
lawblog/
├── app.py                    # Main Flask app
├── requirements.txt
├── Procfile                  # Gunicorn entry
├── railway.toml              # Railway deploy config
├── .env.example              # Environment template
├── static/
│   ├── css/
│   │   ├── main.css          # Public site styles
│   │   └── admin.css         # Admin panel styles
│   ├── js/
│   │   ├── main.js           # Public JS (theme, subscribe, etc.)
│   │   └── admin.js          # Admin JS
│   └── uploads/              # User-uploaded files (gitignored)
└── templates/
    ├── public/
    │   ├── base.html         # Public base layout
    │   ├── index.html        # Homepage
    │   ├── blog.html         # Blog listing
    │   ├── post.html         # Blog post reader
    │   ├── about.html        # About page
    │   └── portfolio.html    # Portfolio page
    └── admin/
        ├── base.html         # Admin base layout
        ├── login.html        # Login page
        ├── dashboard.html    # Admin dashboard
        ├── posts.html        # Post list
        ├── post_edit.html    # Post editor (EasyMDE)
        ├── settings.html     # Site settings
        └── subscribers.html  # Subscriber list
```

---

## 🎨 Design Notes

- **Aesthetic**: Editorial Noir — Playfair Display serif + DM Sans body
- **Colors**: Dark gold (`#d4af5f`) accent on dark navy background
- **Fonts**: Google Fonts (Playfair Display, DM Sans, DM Mono)
- **Animations**: Scroll reveal, cursor glow, ring spin, fade-up
- **Fully responsive** — mobile menu, stacked layouts

---

## 🔧 Customization

- Add/edit portfolio items: edit `templates/public/portfolio.html`
- Change hero quote: Admin → Settings → Hero Quote
- Add new languages: extend `get_lang()` in `app.py` and add translations to templates
- Change color scheme: edit CSS variables in `static/css/main.css`
