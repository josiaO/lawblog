# -*- coding: utf-8 -*-
"""
Intent-based admin help assistant (English + Kiswahili).
Keyword matching finds the right topic; optional AI (see app.py) can polish wording for the user.

This module does not access the database or build SQL — only in-memory intent scoring.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def _norm(text: str) -> str:
    if not text:
        return ''
    t = unicodedata.normalize('NFKC', text).lower()
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _score_message(message_norm: str, keywords: tuple[str, ...]) -> int:
    score = 0
    for raw in keywords:
        k = _norm(raw)
        if len(k) < 2:
            continue
        if k in message_norm:
            score += min(10, 2 + len(k) // 3)
    return score


def _intent(
    intent_id: str,
    keywords_en: tuple[str, ...],
    keywords_sw: tuple[str, ...],
    reply_en: str,
    reply_sw: str,
    links: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        'id': intent_id,
        'keywords_en': keywords_en,
        'keywords_sw': keywords_sw,
        'reply_en': reply_en.strip(),
        'reply_sw': reply_sw.strip(),
        'links': links,
    }


# ─── Intents: keep wording simple; links are Flask endpoint names ─────────────
ADMIN_HELP_INTENTS: list[dict[str, Any]] = [
    _intent(
        'overview',
        (
            'dashboard', 'home', 'admin', 'start', 'overview', 'where', 'begin',
            'panel', 'help me', 'how does',
        ),
        (
            'dashibodi', 'uanzishaji', 'msaada', 'anza', 'ni nini', 'jinsi',
            'paneli', 'msaada wa', 'je', 'nifanyeje',
        ),
        """Welcome to the admin area. Here is the simple map:

• **Dashboard** — numbers for posts, published posts, and subscribers, plus shortcuts.
• **Blog Posts** — write, edit, publish, or draft articles.
• **Subscribers** — people who joined your newsletter.
• **Comments** — reader comments on posts (you can remove one if needed).
• **Portfolio** — your public portfolio page: intro, CV PDF, work items, certificates.
• **Send Emails** — send one email to everyone on your newsletter list.
• **Site Settings** — site name, bio, social links, logo, banner, CV, and more.

Use the gold **Save** buttons after you change something. Open **View Site** to see the public pages.""",
        """Karibu kwenye eneo la msimamizi. Ramani rahisi:

• **Dashboard** — takwimu za makala, zilizochapishwa, na watumiaji wa barua, na njia za mkato.
• **Blog Posts** — kuandika, kuhariri, kuchapisha, au kuacha makala rasimu.
• **Subscribers** — waliojiunga na jarida lako.
• **Comments** — maoni ya wasomaji (unaweza kufuta moja moja).
• **Portfolio** — ukurasa wa umma: utangulizi, CV (PDF), kazi, vyeti.
• **Send Emails** — barua moja kwa wote waliojiunga na jarida.
• **Site Settings** — jina, wasifu, mitandao, nembo, bango, CV, n.k.

Bonyeza **Save** baada ya mabadiliko. **View Site** inaonyesha tovuti ya umma.""",
        ('admin_dashboard', 'admin_posts', 'admin_settings'),
    ),
    _intent(
        'write_post',
        (
            'write post', 'new post', 'create post', 'article', 'blog post', 'editor',
            'how to post', 'publish article', 'draft',
        ),
        (
            'andika', 'makala', 'post', 'chapisha', 'mpya', 'hariri', 'mwili', 'kichwa',
            'jinsi ya kuandika', 'rasimu',
        ),
        """**How to write a post**

1. Go to **Blog Posts** → **New Post** (or open an old one to edit).
2. **English tab** — title (required), optional excerpt (short preview on lists), then the big **visual editor** for the article body.
3. **Kiswahili tab** — same idea: title, excerpt (muhtasari), and Swahili body.
4. Right side: turn on **Published** when you want it live; **Featured** shows it more on the homepage.
5. **Cover image** — optional; good for social sharing.
6. **Tags** — type words separated by commas, or click the small tag buttons.
7. Click **Save Changes** or **Create Post** at the bottom of that card.

The editor works like a simple Word: headings, bold, lists, links, and **image** upload from the toolbar.""",
        """**Jinsi ya kuandika makala**

1. Nenda **Blog Posts** → **New Post** (au fungua iliyopo kuhariri).
2. **Kichupo cha Kiingereza** — kichwa (lazima), muhtasari wa hiari, kisha **mhariri** kwa mwili wa makala.
3. **Kichupo cha Kiswahili** — kichwa, muhtasari, na mwili wa Kiswahili.
4. Upande wa kulia: weka **Published** ikiwa unataka ionekane; **Featured** inaonyesha zaidi ukurasa wa mwanzo.
5. **Picha ya jalada** — hiari; nzuri kwa mitandao.
6. **Tags** — andika maneno kutenganishwa kwa koma, au bonyeza vibadges.
7. Bonyeza **Save** / **Create Post**.

Mhariri ni kama Word rahisi: vichwa, bold, orodha, viungo, na **picha** kutoka upau.""",
        ('admin_new_post', 'admin_posts'),
    ),
    _intent(
        'publish_featured',
        (
            'publish', 'unpublish', 'draft', 'live', 'visible', 'featured', 'homepage',
            'checkbox',
        ),
        (
            'chapisha', 'ionekane', 'rasimu', 'featured', 'mwanzo', 'tiki', 'published',
        ),
        """**Published** and **Featured** (on the post edit page, right column)

• **Published** — when checked, visitors can see the post on the public blog (if it has a title and body saved).
• When **Published** is off, the post stays a **draft** — only you see it in admin.
• **Featured** — extra highlight on the homepage for important posts (still needs **Published** on to be public).

Always click **Save Changes** after toggling these.""",
        """**Published** na **Featured** (ukurasa wa kuhariri makala, upande wa kulia)

• **Published** — ukiweka tiki, wageni waona makala kwenye blogu (ikiwa umekhifadhi).
• Bila **Published**, ni **rasimu** — wewe tu admin unaiona.
• **Featured** — makala inaangaziwa zaidi mwanzoni (bado inahitaji **Published**).

Bonyeza **Save Changes** baada ya kubadilisha.""",
        (),
    ),
    _intent(
        'quill_toolbar',
        (
            'toolbar', 'heading', 'bold', 'italic', 'list', 'link', 'image upload',
            'visual editor', 'format',
        ),
        (
            'upau', 'kichwa', 'picha', 'kiungo', 'orodha', 'mhariri', 'bold',
        ),
        """**Editor toolbar** (above the article text)

• **Heading menu** — pick Heading 1–3 or normal paragraph for structure.
• **B I U S** — bold, italic, underline, strikethrough.
• **Quote** and **code** — special blocks for quotes or code snippets.
• **Link** — select text, click link, paste URL.
• **Image** — click the image icon; your file uploads to the site and is inserted where the cursor is.
• **Numbered / bullet lists** — for steps or bullet points.
• **Clear formatting** — removes styles from selected text.

Tip: use headings so the **Article outline** box on the right stays useful.""",
        """**Upau wa zana** (juu ya maandishi)

• **Heading** — chagua kichwa 1–3 au aya ya kawaida.
• **B I U S** — bold, mlalo, msingi, kukatiza mstari.
• **Quote / code** — vizuizi maalum.
• **Link** — chagua maandishi, bandika anwani.
• **Picha** — ikoni ya picha; faili inapakiwa na kuwekwa mahali pako la kishale.
• **Orodha** — nambari au alama.
• **Safisha** — ondoa muundo ulioteuliwa.

Kidokezo: tumia vichwa ili **Article outline** iwe na maana.""",
        (),
    ),
    _intent(
        'ai_writing',
        (
            'ai', 'improve writing', 'grammar', 'tone', 'writing tools', 'highlight',
            'smart writing', 'fix grammar', 'writing assistant',
        ),
        (
            'ai', 'sarufi', 'uboreshi', 'sauti', 'zana za kuandika', 'chagua maandishi',
            'kunoa maandishi',
        ),
        """**Writing tools** (on the post editor, left side — open **Writing tools & AI**)

• **Improve writing** — small clarity fixes; your meaning stays the same.
• **Fix grammar** — spelling and grammar only. **Tip:** select the paragraph you want first so only that part changes.
• **Improve tone** — warmer, clearer style; your facts stay the same.

If a button shows an error or nothing happens, smart writing may not be switched on for your site — ask whoever looks after the website.

**Quick inserts** in the same place add a ready-made heading, quote, or example line. Those are not automatic writing; they just drop in a starter block you can edit.""",
        """**Zana za uandishi** (kwenye mhariri wa makala, upande wa kushoto — fungua **Writing tools & AI**)

• **Improve writing** — kunoa uelewa kidogo; maana yako inabaki.
• **Fix grammar** — tahajia na sarufi. **Kidokezo:** chagua aya unayotaka kwanza ili sehemu hiyo tu ibadilishwe.
• **Improve tone** — mtindo laini zaidi; ukweli wako unabaki.

Ikiwa kitufe kinakosa kufanya kazi, huenda kipengele hiki hakijawashwa — muulize anayesimamia tovuti.

**Quick inserts** hapo hapo huingiza kichwa, nukuu, au mstari wa mfano tayari — siyo uandishi wa kiotomatiki, unahariri mwenyewe.""",
        (),
    ),
    _intent(
        'excerpt_cover_tags',
        (
            'excerpt', 'preview', 'lead', 'cover image', 'tags', 'reading time',
            'outline',
        ),
        (
            'muhtasari', 'hakiki', 'jalada', 'cover', 'lebo', 'tags', 'muda wa kusoma',
            'muhtasari wa orodha',
        ),
        """**Excerpt** — short text shown on blog lists and previews; optional but nice for social snippets.

**Cover image** — optional picture for the post; upload on the right; roughly 1400×800 works well.

**Tags** — type words separated by commas (for example: law, youth). Quick-add buttons add common tags.

**Reading time** — estimated automatically from the **active** language tab (~200 words per minute).

**Article outline** — built from headings (H1–H3) in the **active** tab — helps you see structure.""",
        """**Muhtasari** — maandishi mafupi kwenye orodha ya blogu; hiari lakini muhimu kwa mitandao.

**Picha ya jalada** — hiari; pakia upande wa kulia; ~1400×800 ni nzuri.

**Tags** — maneno kutenganishwa na koma. Vibadges hujiongeza haraka.

**Muda wa kusoma** — hesabiwa kutoka **kichupo** unachotumia.

**Muhtasari wa vichwa** — kutoka vichwa H1–H3 kwenye kichupo kilichowazi.""",
        (),
    ),
    _intent(
        'markdown_legacy',
        (
            'markdown', 'old post', 'converted', 'weird format',
        ),
        (
            'umbizo la kale', 'ya kale', 'markdown',
        ),
        """Some older posts may open in a special old format; the editor turns them into normal rich text. After you **Save**, they stay as normal editor text. For new posts, just use the toolbar — no special codes needed.""",
        """Makala za zamani zinaweza kuwa katika umbizo la kale; mhariri hubadilisha kuwa maandishi ya kawaida. Baada ya **Save**, zinabaki hivyo. Makala mpya: tumia upau tu.""",
        (),
    ),
    _intent(
        'site_settings_identity',
        (
            'site settings', 'site name', 'identity', 'bio', 'tagline', 'email contact',
        ),
        (
            'mipangilio', 'jina', 'tovuti', 'wasifu', 'kichwa kidogo', 'barua pepe',
        ),
        """**Site Settings** → **Identity**

• **Site / Your Name** — shown as the site title in many places.
• **Email** — contact address visitors may use; also used for some system emails.
• **Tagline** — short line under the name (English and Kiswahili fields).
• **Bio** — longer about text (English and Kiswahili).

Click **Save All Settings** at the bottom when done.""",
        """**Site Settings** → **Utambulisho**

• **Jina la tovuti** — linaonekana kama kichwa.
• **Barua pepe** — mawasiliano; pia kwa baadhi ya mifumo.
• **Tagline** — mstari mfupi (Kiingereza na Kiswahili).
• **Bio** — maelezo marefu.

Bonyeza **Save All Settings** mwishoni.""",
        ('admin_settings',),
    ),
    _intent(
        'site_settings_hero_social',
        (
            'hero quote', 'quote', 'twitter', 'linkedin', 'instagram', 'social',
        ),
        (
            'nukuu', 'shujaa', 'twitter', 'linkedin', 'instagram', 'mitandao',
        ),
        """**Hero Quote** — the big inspirational lines on the homepage (English + Kiswahili).

**Social Links** — full URLs to your profiles. Leave blank if you do not use one.""",
        """**Hero Quote** — mistari mikubwa mwanzoni (EN + SW).

**Viungo vya mitandao** — URL kamili. Acha wazi ikiwa hutumii.""",
        ('admin_settings',),
    ),
    _intent(
        'site_settings_files',
        (
            'logo', 'avatar', 'banner', 'cv', 'upload image', 'pdf', 'media',
        ),
        (
            'nembo', 'logo', 'avatar', 'bango', 'banner', 'cv', 'pdf', 'pakia',
        ),
        """**Files & Media** in **Site Settings**

• **Homepage banner** — wide image behind the hero (~1920×720).
• **Logo** — small brand mark.
• **Profile photo / Avatar** — round photo, often used on About and similar.
• **CV / Resume** — PDF only; also manageable from **Portfolio** with the same stored file.

After choosing a file, scroll down and click **Save All Settings**. PDF names can be `.pdf` or `.PDF` — both work.""",
        """**Faili** katika **Site Settings**

• **Banner** — pana mwanzoni (~1920×720).
• **Logo** — nembo ndogo.
• **Avatar** — picha ya duara.
• **CV** — PDF; pia unaweza kuisimamia kwenye **Portfolio**.

Chagua faili kisha **Save All Settings**. `.pdf` au `.PDF` zote zinafanya kazi.""",
        ('admin_settings', 'admin_portfolio'),
    ),
    _intent(
        'brevo_email',
        (
            'newsletter', 'subscriber email', 'test email',
            'sender', 'welcome email', 'email list', 'mailing list',
        ),
        (
            'jarida', 'barua', 'tuma', 'mtumaji', 'orodha ya barua',
        ),
        """**Newsletter and email**

People who sign up on your site are listed under **Subscribers**. To email them all at once, use **Send Emails** in the sidebar.

Welcome messages and “new post” notices usually need your **sending address** to be verified once with your email provider — whoever set up the site normally handles that.

In **Site Settings**, you can use **Send test email** to see if mail reaches your own inbox.""",
        """**Jarida na barua pepe**

Waliojiunga kwenye tovuti wako orodha ya **Subscribers**. Kutuma barua kwa wote: **Send Emails** kwenye menyu ya kando.

Ujumbe wa karibu na arifa za makala mpya mara nyingi huhitaji **anwani ya kutuma** kuthibitishwa — mtu aliyeweka tovuti huifanya mara moja.

**Site Settings** — **Send test email** kuona kama barua inafika kwako.""",
        ('admin_settings', 'admin_send_emails', 'admin_subscribers'),
    ),
    _intent(
        'subscribers_page',
        (
            'subscribers', 'newsletter list', 'who subscribed',
        ),
        (
            'waliojiunga', 'orodha', 'subscribers',
        ),
        """**Subscribers** shows everyone who joined your newsletter on the site. To send them an email, use **Send Emails**. New posts may also trigger an automatic notice if that was set up for your site.""",
        """**Subscribers** inaonyesha waliojiunga na jarida. Kutuma barua: **Send Emails**. Makala mpya zinaweza kutuma arifa kiotomatiki ikiwa hilo limewashwa.""",
        ('admin_subscribers', 'admin_send_emails'),
    ),
    _intent(
        'broadcast',
        (
            'broadcast', 'announcement', 'email everyone', 'send to all',
        ),
        (
            'tuma barua', 'wat wote', 'broadcast', 'tangazo',
        ),
        """**Send Emails** — write a **subject** and your **message** (you can use bold text and links, like in an email). Press send. Everyone on the list receives it.

Sending can take a little while. If nothing arrives, ask whoever manages your site’s email setup to check that sending is allowed and the address is verified.""",
        """**Send Emails** — andika **mada** na **ujumbe** (unaweza tumia maandishi mazito na viungo). Bonyeza tuma. Kila aliyejiunga atapokea.

Kutuma kunaweza kuchukua muda. Ikiwa hakuna kitu kinachofika, muulize anayesimamia barua pepe ya tovuti.""",
        ('admin_send_emails',),
    ),
    _intent(
        'comments_admin',
        (
            'comment', 'comments', 'moderate', 'delete comment', 'spam',
        ),
        (
            'maoni', 'futa', 'comments',
        ),
        """**Comments** lists what readers wrote under your posts. You can **delete** a comment if it is spam or unsuitable.""",
        """**Comments** — maoni ya wasomaji chini ya makala. Unaweza **kufuta** maoni mabaya au taka.""",
        ('admin_comments',),
    ),
    _intent(
        'portfolio_help',
        (
            'portfolio', 'cv', 'cv upload', 'certificate', 'award', 'work item', 'achievements',
        ),
        (
            'portfolio', 'cv', 'cheti', 'tuzo', 'kazi', 'mafano', 'wasifu',
        ),
        """**Portfolio** (admin)

• **Review uploads** at the top shows your CV, how many work images, and certificate files.
• **Intro** — English and Kiswahili text at the top of the public page.
• **CV** — PDF only; choose file and click **Save intro & CV**. You can **Remove CV** from the summary card.
• **Work & projects** — rows with title, category, description, optional photo; **Save** each row; **Delete** removes one.
• **Certificates** — title, details, optional PDF or image attachment.

The public **Portfolio** page shows your CV strip, work cards, and certificates for visitors.""",
        """**Portfolio**

• **Review uploads** — muhtasari wa CV, picha za kazi, viambatanisho.
• **Utangulizi** — Kiingereza na Kiswahili.
• **CV** — PDF; chagua faili kisha **Save**. **Remove CV** inapatikana.
• **Kazi** — safu za maelezo na picha hiari.
• **Vyeti** — cheti au picha.

Ukurasa wa umma unaonyesha CV, kazi, na vyeti.""",
        ('admin_portfolio',),
    ),
    _intent(
        'uploads_general',
        (
            'upload', 'photo', 'picture', 'image', 'file', 'pdf', 'broken', 'missing',
            'disappeared', 'will not', 'cannot upload', 'too large', 'error',
        ),
        (
            'pakia', 'picha', 'faili', 'haionekani', 'haifanyi', 'kosa', 'kubwa',
        ),
        """**If a picture or PDF will not upload**

Check the file is an allowed type (images for photos, PDF for a CV) and not bigger than the site allows. Always press **Save** after you pick a file.

If files **used to work** but vanished after a site move or upgrade, that is handled outside this screen — ask whoever manages your website or hosting.

For day-to-day use, upload from **Site Settings** (logo, banner, avatar, CV) or inside a post (**image** button), or on **Portfolio**.""",
        """**Ikiwa picha au PDF haipakiki**

Hakikisha aina ya faili inaruhusiwa na sio kubwa mno. Bonyeza **Save** baada ya kuchagua.

Ikiwa faili **zilikuwepo** kisha zikapotea baada ya mabadiliko ya tovuti, jambo hilo linaangaliwa nje ya skrini hii — muulize msimamizi wa tovuti.

Pakia kutoka **Site Settings**, kwenye makala (kitufe cha **picha**), au **Portfolio**.""",
        ('admin_settings', 'admin_portfolio'),
    ),
    _intent(
        'login_logout',
        (
            'login', 'logout', 'password', 'cannot log in', 'forgot',
        ),
        (
            'ingia', 'toka', 'nenosiri', 'siwezi',
        ),
        """Use **Logout** in the sidebar to leave the admin area safely.

If you **forgot your password**, only someone who manages the website or hosting can reset it for you — this screen cannot email a new password by itself.""",
        """**Logout** iko sidebar kutoka kwa usalama.

Uki**sahau nenosiri**, ni mtu anayesimamia tovuti au hosting pekee anayeweza kulirekebisha — skrini hii haitumi nenosiri kwa barua pepe.""",
        ('admin_logout',),
    ),
    _intent(
        'posts_list',
        (
            'posts list', 'all posts', 'edit post', 'delete post', 'draft list',
        ),
        (
            'orodha ya makala', 'futa', 'hariri', 'makala zote',
        ),
        """**Blog Posts** shows every article. Open one to edit. Use **Preview** on the edit screen to see the public page (visitors only see it if the post is **Published**).""",
        """**Blog Posts** — makala zote. Fungua kuhariri. **Preview** inaonyesha ukurasa wa umma (wageni waona ikiwa **Published**).""",
        ('admin_posts',),
    ),
    _intent(
        'upload_image_post',
        (
            'upload image', 'insert image', 'picture in post', 'image in article',
        ),
        (
            'pakia picha', 'picha kwenye makala',
        ),
        """In the post editor toolbar, click the **image** icon. Choose a file from your computer; the site stores it and puts the picture in your article where the cursor is. Works the same on the English and Kiswahili tabs.""",
        """Katika mhariri, bonyeza ikoni ya **picha**. Chagua faili; inapakiwa na kuwekwa ndani ya makala.""",
        (),
    ),
]

FALLBACK_INTENT = _intent(
    'fallback',
    (),
    (),
    """I am not sure which topic you mean. Try one of these short questions:

• How do I write or publish a post?
• What do the AI writing buttons do?
• Where do I change site name, logo, and social links?
• How does Portfolio and CV upload work?
• How do the newsletter list and “email everyone” work?

Or use the **quick topics** chips below. You can also switch to **Kiswahili** and ask again.""",
    """Sielewi swali bila ya kina. Jaribu:

• Ninawezaje kuandika au kuchapisha makala?
• Vitufe vya AI vina maana gani?
• Nabadilisha jina, nembo, mitandao wapi?
• Portfolio na CV je?
• Jarida na kutuma barua kwa wote ni nini?

Tumia **mada** hapa chini au geuza lugha ya Kiswahili.""",
    ('admin_dashboard', 'admin_settings', 'admin_posts'),
)


def resolve_help_query(message: str, lang: str) -> tuple[str, str, tuple[str, ...]]:
    """
    Return (intent_id, reply_text, link_endpoint_names).
    lang: 'en' or 'sw'.
    """
    if lang not in ('en', 'sw'):
        lang = 'en'
    n = _norm(message)
    if not n:
        return (
            'empty',
            'Please type a question.' if lang == 'en' else 'Andika swali tafadhali.',
            (),
        )

    best: dict[str, Any] | None = None
    best_score = 0
    for intent in ADMIN_HELP_INTENTS:
        kws = tuple(intent['keywords_en']) + tuple(intent['keywords_sw'])
        s = _score_message(n, kws)
        if s > best_score:
            best_score = s
            best = intent

    if best is None or best_score < 2:
        best = FALLBACK_INTENT

    reply = best['reply_sw'] if lang == 'sw' else best['reply_en']
    links = tuple(best.get('links') or ())
    return (best['id'], reply, links)


# Short labels for suggested prompts (chips) in the UI
HELP_SUGGESTIONS: tuple[dict[str, str], ...] = (
    {
        'id': 's_write',
        'prompt_en': 'How do I write and publish a new post?',
        'prompt_sw': 'Ninawezaje kuandika na kuchapisha makala mpya?',
    },
    {
        'id': 's_ai',
        'prompt_en': 'What do Improve writing, Fix grammar, and Improve tone do?',
        'prompt_sw': 'Vitufe vya AI vya uandishi vina maana gani?',
    },
    {
        'id': 's_settings',
        'prompt_en': 'What does each part of Site Settings mean?',
        'prompt_sw': 'Site Settings — kila sehemu inamaanisha nini?',
    },
    {
        'id': 's_portfolio',
        'prompt_en': 'How do I upload my CV and certificates on Portfolio?',
        'prompt_sw': 'Ninawezaje kupakia CV na vyeti kwenye Portfolio?',
    },
    {
        'id': 's_newsletter',
        'prompt_en': 'How does the newsletter list and sending emails work?',
        'prompt_sw': 'Jarida na kutuma barua kwa wasomaji hufanyaje kazi?',
    },
    {
        'id': 's_sw',
        'prompt_en': 'Switch: explain the dashboard in Swahili.',
        'prompt_sw': 'Eleza dashboard kwa Kiswahili kilichomo ndani.',
    },
)
