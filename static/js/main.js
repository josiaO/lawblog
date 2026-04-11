/* ── Theme Management ── */
(function() {
  const saved = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
})();

document.addEventListener('DOMContentLoaded', function() {

  /* Theme toggle */
  const themeBtn = document.getElementById('themeToggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme');
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    });
  }

  /* Cursor glow */
  const glow = document.getElementById('cursorGlow');
  if (glow && window.innerWidth > 768) {
    document.addEventListener('mousemove', e => {
      glow.style.left = e.clientX + 'px';
      glow.style.top = e.clientY + 'px';
      glow.style.opacity = '1';
    });
    document.addEventListener('mouseleave', () => { glow.style.opacity = '0'; });
  }

  /* Hamburger */
  const ham = document.getElementById('hamburger');
  const mobileMenu = document.getElementById('mobileMenu');
  if (ham && mobileMenu) {
    ham.addEventListener('click', () => {
      ham.classList.toggle('open');
      mobileMenu.classList.toggle('open');
    });
  }

  /* Nav scroll effect */
  const nav = document.getElementById('siteNav');
  if (nav) {
    window.addEventListener('scroll', () => {
      nav.style.borderBottomColor = window.scrollY > 20
        ? 'var(--border-accent)' : 'var(--border)';
    }, { passive: true });
  }

  /* Reading progress bar */
  const progressBar = document.querySelector('.reading-progress-bar');
  if (progressBar) {
    window.addEventListener('scroll', () => {
      const el = document.documentElement;
      const scrollTop = el.scrollTop || document.body.scrollTop;
      const height = el.scrollHeight - el.clientHeight;
      progressBar.style.width = (height > 0 ? (scrollTop / height) * 100 : 0) + '%';
    }, { passive: true });
  }

  /* Scroll reveal */
  const reveals = document.querySelectorAll('.reveal');
  if (reveals.length) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if (e.isIntersecting) { e.target.classList.add('visible'); io.unobserve(e.target); }
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
    reveals.forEach(el => io.observe(el));
  }

  /* Newsletter modal */
  const modal = document.getElementById('newsletterModal');
  const closeBtn = document.getElementById('modalClose');
  if (modal && closeBtn) {
    closeBtn.addEventListener('click', () => modal.classList.remove('open'));
    modal.addEventListener('click', e => { if (e.target === modal) modal.classList.remove('open'); });
    // Show modal after 30s for first visit
    if (!sessionStorage.getItem('newsletterShown')) {
      setTimeout(() => {
        modal.classList.add('open');
        sessionStorage.setItem('newsletterShown', '1');
      }, 30000);
    }
  }

  document.addEventListener('click', function (e) {
    const opener = e.target.closest('[data-open-newsletter]');
    if (!opener) return;
    e.preventDefault();
    const m = document.getElementById('newsletterModal');
    if (m) m.classList.add('open');
  });

  initNewsletterSubscribe();

  /* Flash message auto-dismiss */
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.5s'; }, 4000);
    setTimeout(() => el.remove(), 4500);
  });

});

function initNewsletterSubscribe() {
  var form = document.getElementById('newsletterSubscribeForm');
  if (!form) return;
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    submitNewsletterForm(form);
  });
}

function nlSleep(ms) {
  return new Promise(function (r) { setTimeout(r, ms); });
}

function nlWaitForGrecaptcha(timeoutMs) {
  var siteKey = (document.body.dataset.recaptchaKey || '').trim();
  if (!siteKey) return Promise.resolve(true);
  var deadline = Date.now() + (timeoutMs || 40000);
  return new Promise(function (resolve) {
    (function poll() {
      if (typeof grecaptcha !== 'undefined' && typeof grecaptcha.ready === 'function') {
        resolve(true);
        return;
      }
      if (Date.now() > deadline) {
        resolve(false);
        return;
      }
      setTimeout(poll, 120);
    })();
  });
}

async function nlRecaptchaToken(siteKey, maxAttempts) {
  var n = maxAttempts || 4;
  for (var i = 0; i < n; i++) {
    try {
      var t = await new Promise(function (resolve, reject) {
        grecaptcha.ready(function () {
          grecaptcha.execute(siteKey, { action: 'subscribe' }).then(resolve).catch(reject);
        });
      });
      if (t && String(t).trim()) return String(t).trim();
    } catch (err) { /* retry */ }
    if (i < n - 1) await nlSleep(400 + i * 450);
  }
  return '';
}

function nlFetchSubscribe(actionUrl, bodyString, csrf, timeoutMs) {
  var url = actionUrl || '/subscribe';
  var ms = timeoutMs || 40000;
  var headers = {
    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
    Accept: 'application/json',
    'X-CSRFToken': csrf,
    'X-Requested-With': 'XMLHttpRequest',
  };
  if (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) {
    return fetch(url, {
      method: 'POST',
      headers: headers,
      credentials: 'same-origin',
      body: bodyString,
      signal: AbortSignal.timeout(ms),
    });
  }
  var ctrl = new AbortController();
  var id = setTimeout(function () { ctrl.abort(); }, ms);
  return fetch(url, {
    method: 'POST',
    headers: headers,
    credentials: 'same-origin',
    body: bodyString,
    signal: ctrl.signal,
  }).finally(function () { clearTimeout(id); });
}

/**
 * Newsletter: always intercept submit — use CSRF + fields from the live form (not the head meta tag).
 */
async function submitNewsletterForm(form) {
  var emailInput = form.querySelector('[name="email"]');
  var nameInput = form.querySelector('[name="name"]');
  var tokInput = form.querySelector('[name="recaptcha_token"]');
  var csrfInput = form.querySelector('[name="csrf_token"]');
  var btn = form.querySelector('#subBtn');
  var result = document.getElementById('subResult');
  if (!emailInput || !btn || !csrfInput) return;

  var email = (emailInput.value || '').trim();
  var name = nameInput ? (nameInput.value || '').trim() : '';
  var csrf = (csrfInput.value || '').trim();
  var labelBusy = btn.dataset.busy || '…';
  var labelDefault = btn.dataset.label || btn.textContent.trim() || 'Subscribe';
  var siteKey = (document.body.dataset.recaptchaKey || '').trim();

  if (!email) {
    showResult(result, 'Please enter your email.', false);
    return;
  }

  btn.disabled = true;
  btn.textContent = labelBusy;

  var token = '';
  if (siteKey) {
    var loaded = await nlWaitForGrecaptcha(40000);
    if (!loaded) {
      showResult(
        result,
        'Security script did not load in time. Check your connection, or allow google.com / gstatic.com (Firefox: disable Strict tracking for this site if needed).',
        false
      );
      btn.disabled = false;
      btn.textContent = labelDefault;
      return;
    }
    token = await nlRecaptchaToken(siteKey, 4);
    if (!token) {
      showResult(
        result,
        'Could not complete the security check. Refresh the page, try again, or allow Google scripts for this site.',
        false
      );
      btn.disabled = false;
      btn.textContent = labelDefault;
      return;
    }
  }
  if (tokInput) tokInput.value = token;

  var body = new URLSearchParams();
  body.set('csrf_token', csrf);
  body.set('email', email);
  body.set('name', name);
  body.set('recaptcha_token', token);

  try {
    var res = await nlFetchSubscribe(form.action, body.toString(), csrf, 40000);
    var data = {};
    try {
      data = await res.json();
    } catch (parseErr) {
      showResult(
        result,
        res.status >= 500
          ? 'The server had a problem. Wait a moment and try again.'
          : 'Something went wrong. Please try again.',
        false
      );
      btn.disabled = false;
      btn.textContent = labelDefault;
      return;
    }
    var ok = !!data.ok;
    showResult(result, data.msg || (ok ? 'Thank you!' : 'Something went wrong.'), ok);
    if (ok) {
      emailInput.value = '';
      if (nameInput) nameInput.value = '';
      if (tokInput) tokInput.value = '';
    }
  } catch (e) {
    var aborted = e && (e.name === 'AbortError' || e.name === 'TimeoutError');
    showResult(
      result,
      aborted ? 'Request timed out. Check your connection and try again.' : 'Something went wrong. Please try again.',
      false
    );
  }
  btn.disabled = false;
  btn.textContent = labelDefault;
}

function showResult(el, msg, ok) {
  if (!el) return;
  el.textContent = msg;
  el.className = 'sub-result ' + (ok ? 'ok' : 'err');
  el.style.display = 'block';
}

/* Share buttons */
function sharePost(platform, url, title) {
  const encodedUrl = encodeURIComponent(url);
  const encodedTitle = encodeURIComponent(title);
  const links = {
    twitter: `https://twitter.com/intent/tweet?url=${encodedUrl}&text=${encodedTitle}`,
    facebook: `https://www.facebook.com/sharer/sharer.php?u=${encodedUrl}`,
    linkedin: `https://www.linkedin.com/sharing/share-offsite/?url=${encodedUrl}`,
    copy: null
  };
  if (platform === 'copy') {
    navigator.clipboard.writeText(url).then(() => {
      const btn = document.querySelector('.copy-link-btn');
      if (btn) {
        const done = btn.dataset.copyDone || '✓ Copied!';
        const label = btn.dataset.copyLabel || 'Copy link';
        const orig = '🔗 ' + label;
        btn.textContent = done;
        setTimeout(() => { btn.textContent = orig; }, 2200);
      }
    });
    return;
  }
  window.open(links[platform], '_blank', 'width=600,height=400');
}
