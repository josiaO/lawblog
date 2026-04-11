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

  const subBtn = document.getElementById('subBtn');
  if (subBtn) {
    subBtn.addEventListener('click', function (e) {
      e.preventDefault();
      submitSubscribe();
    });
  }

  /* Flash message auto-dismiss */
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.5s'; }, 4000);
    setTimeout(() => el.remove(), 4500);
  });

});

function waitForGrecaptcha(timeoutMs) {
  var siteKey = (document.body.dataset.recaptchaKey || '').trim();
  if (!siteKey) return Promise.resolve(true);
  var deadline = Date.now() + timeoutMs;
  return new Promise(function (resolve) {
    (function poll() {
      if (typeof grecaptcha !== 'undefined') {
        resolve(true);
        return;
      }
      if (Date.now() > deadline) {
        resolve(false);
        return;
      }
      setTimeout(poll, 80);
    })();
  });
}

/* Newsletter subscribe — form POST so Flask-WTF reads csrf_token from request.form */
async function submitSubscribe() {
  const emailInput = document.getElementById('subEmail');
  const nameInput = document.getElementById('subName');
  const btn = document.getElementById('subBtn');
  const result = document.getElementById('subResult');
  if (!btn || !emailInput) return;

  const email = (emailInput.value || '').trim();
  const name = nameInput ? (nameInput.value || '').trim() : '';
  const labelBusy = btn.dataset.busy || '…';
  const labelDefault = btn.dataset.label || btn.textContent.trim() || 'Subscribe';

  if (!email) {
    showResult(result, 'Please enter your email.', false);
    return;
  }

  btn.disabled = true;
  btn.textContent = labelBusy;

  var token = '';
  var siteKey = (document.body.dataset.recaptchaKey || '').trim();
  if (siteKey) {
    var loaded = await waitForGrecaptcha(15000);
    if (!loaded) {
      if (result) {
        showResult(
          result,
          'Security check timed out. Check your connection, disable blockers for this site, and try again.',
          false
        );
      }
      btn.disabled = false;
      btn.textContent = labelDefault;
      return;
    }
    try {
      token = await new Promise(function (resolve, reject) {
        grecaptcha.ready(function () {
          grecaptcha.execute(siteKey, { action: 'subscribe' }).then(resolve).catch(reject);
        });
      });
    } catch (e) {
      token = '';
    }
  }

  var csrf = document.querySelector('meta[name="csrf-token"]');
  csrf = csrf ? csrf.getAttribute('content') || '' : '';
  var body = new URLSearchParams();
  body.set('csrf_token', csrf);
  body.set('email', email);
  body.set('name', name);
  body.set('recaptcha_token', token);

  try {
    var res = await fetch('/subscribe', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        Accept: 'application/json',
        'X-CSRFToken': csrf,
      },
      credentials: 'same-origin',
      body: body.toString(),
    });
    var data = {};
    try {
      data = await res.json();
    } catch (parseErr) {
      if (result) showResult(result, 'Something went wrong. Please try again.', false);
      btn.disabled = false;
      btn.textContent = labelDefault;
      return;
    }
    var ok = !!data.ok;
    if (result) {
      showResult(result, data.msg || (ok ? 'Thank you!' : 'Something went wrong.'), ok);
    }
    if (ok) {
      emailInput.value = '';
      if (nameInput) nameInput.value = '';
    }
  } catch (e) {
    if (result) showResult(result, 'Something went wrong. Please try again.', false);
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
