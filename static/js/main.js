/**
 * COUNSEL & CRAFT — Main Interactions
 */

/* ── Theme Management ── */
(function() {
  const saved = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
})();

document.addEventListener('DOMContentLoaded', function() {
  const body = document.body;
  const html = document.documentElement;

  /* ── Theme Toggle ── */
  const themeBtn = document.getElementById('themeToggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const current = html.getAttribute('data-theme');
      const next = current === 'dark' ? 'light' : 'dark';
      html.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    });
  }

  /* ── Cursor Glow ── */
  const glow = document.getElementById('cursorGlow');
  if (glow && window.innerWidth > 768) {
    document.addEventListener('mousemove', e => {
      requestAnimationFrame(() => {
        glow.style.left = e.clientX + 'px';
        glow.style.top = e.clientY + 'px';
        glow.style.opacity = '1';
      });
    }, { passive: true });
    document.addEventListener('mouseleave', () => { glow.style.opacity = '0'; });
  }

  /* ── Navigation Scroll Effect ── */
  const nav = document.getElementById('siteNav');
  if (nav) {
    window.addEventListener('scroll', () => {
      if (window.scrollY > 50) {
        nav.classList.add('scrolled');
      } else {
        nav.classList.remove('scrolled');
      }
    }, { passive: true });
  }

  /* ── Mobile Menu ── */
  const ham = document.getElementById('hamburger');
  const mobileMenu = document.getElementById('mobileMenu');
  if (ham && mobileMenu) {
    const setMenuState = (isOpen) => {
      ham.classList.toggle('open', isOpen);
      mobileMenu.classList.toggle('open', isOpen);
      body.classList.toggle('no-scroll', isOpen);
      ham.setAttribute('aria-expanded', String(isOpen));
      mobileMenu.setAttribute('aria-hidden', String(!isOpen));
    };

    ham.addEventListener('click', () => {
      const isOpen = !mobileMenu.classList.contains('open');
      setMenuState(isOpen);
    });

    // Close mobile menu when a link is selected
    mobileMenu.addEventListener('click', (e) => {
      if (e.target.closest('a')) {
        setMenuState(false);
      }
    });

    // Close on Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && mobileMenu.classList.contains('open')) {
        setMenuState(false);
      }
    });

    // Ensure menu closes when switching to desktop width
    window.addEventListener('resize', () => {
      if (window.innerWidth > 1024 && mobileMenu.classList.contains('open')) {
        setMenuState(false);
      }
    }, { passive: true });
  }

  /* ── Scroll Reveal ── */
  const reveals = document.querySelectorAll('.reveal');
  if (reveals.length) {
    const observerOptions = {
      threshold: 0.15,
      rootMargin: '0px 0px -50px 0px'
    };
    const revealObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          revealObserver.unobserve(entry.target);
        }
      });
    }, observerOptions);
    reveals.forEach(el => revealObserver.observe(el));
  }

  /* ── Newsletter Logic ── */
  initNewsletter();

  /* ── Flash Messages ── */
  const flashes = document.querySelectorAll('.flash');
  flashes.forEach(el => {
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(-10px)';
      el.style.transition = 'all 0.5s ease';
      setTimeout(() => el.remove(), 500);
    }, 5000);
  });
});

/**
 * Newsletter Subscription Module
 */
function initNewsletter() {
  const modal = document.getElementById('newsletterModal');
  const closeBtn = document.getElementById('modalClose');
  const form = document.getElementById('newsletterSubscribeForm');
  const resultDiv = document.getElementById('subResult');
  const subBtn = document.getElementById('subBtn');

  if (!modal) return;

  let autoShowTimer = null;

  function openModal() {
    // Clear auto-show timer if user opens manually
    if (autoShowTimer) {
      clearTimeout(autoShowTimer);
      autoShowTimer = null;
    }
    
    modal.classList.add('open');
    document.body.classList.add('no-scroll');
    sessionStorage.setItem('nl_shown', '1');

    if (resultDiv) {
      resultDiv.style.display = 'none';
      resultDiv.className = 'sub-result';
    }
  }

  function closeModal() {
    modal.classList.remove('open');
    document.body.classList.remove('no-scroll');
  }

  // Event Listeners for Opening
  document.addEventListener('click', e => {
    // We use a broader check to ensure all data-open-newsletter targets work
    const target = e.target.closest('[data-open-newsletter]');
    if (target) {
      e.preventDefault();
      openModal();
    }
  });

  if (closeBtn) closeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    closeModal();
  });

  // Close on backdrop click
  modal.addEventListener('click', e => { 
    if (e.target === modal) closeModal(); 
  });

  // Close on Escape
  document.addEventListener('keydown', e => { 
    if (e.key === 'Escape' && modal.classList.contains('open')) {
      closeModal();
    }
  });

  // Auto-show after 40s (only if not already shown)
  if (!sessionStorage.getItem('nl_shown')) {
    autoShowTimer = setTimeout(() => {
      if (!modal.classList.contains('open')) {
        openModal();
      }
    }, 40000);
  }

  // Form Submission
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      await handleSubscription(form, subBtn, resultDiv);
    });
  }
}

async function handleSubscription(form, btn, result) {
  const emailInput = form.querySelector('input[type="email"]');
  const nameInput = form.querySelector('input[name="name"]');
  const csrfInput = form.querySelector('input[name="csrf_token"]');
  const tokInput = document.getElementById('subRecaptchaToken');
  
  const siteKey = (document.body.dataset.recaptchaKey || '').trim();
  const labelOrig = btn.textContent;
  const labelBusy = btn.dataset.busy || '…';

  if (!emailInput.value) return;

  // UI Stalling
  btn.disabled = true;
  btn.textContent = labelBusy;
  if (result) result.style.display = 'none';

  try {
    let token = '';
    
    // reCAPTCHA Handling
    if (siteKey && typeof grecaptcha !== 'undefined') {
      try {
        token = await new Promise((resolve, reject) => {
          grecaptcha.ready(() => {
            grecaptcha.execute(siteKey, { action: 'subscribe' })
              .then(resolve)
              .catch(reject);
          });
        });
      } catch (err) {
        console.warn('reCAPTCHA failed:', err);
        // We continue anyway, backend will decide if it is mandatory
      }
    }

    if (tokInput) tokInput.value = token;

    // Build Request
    const formData = new FormData(form);
    const response = await fetch(form.action, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrfInput.value
      },
      body: formData
    });

    const data = await response.json();
    
    if (data.ok) {
      showSubscriptionResult(result, data.msg || 'Success!', true);
      form.reset();
      // Optional: Close modal after success
      setTimeout(() => {
        const modal = document.getElementById('newsletterModal');
        if (modal && modal.classList.contains('open')) {
          modal.classList.remove('open');
        }
      }, 3000);
    } else {
      showSubscriptionResult(result, data.msg || 'Error occurred.', false);
    }
  } catch (err) {
    console.error('Subscription error:', err);
    showSubscriptionResult(result, 'Connection error. Please try again.', false);
  } finally {
    btn.disabled = false;
    btn.textContent = labelOrig;
  }
}

function showSubscriptionResult(el, msg, isOk) {
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
  el.className = `sub-result ${isOk ? 'ok' : 'err'}`;
}

/**
 * Social Sharing
 */
function sharePost(platform, url, title) {
  const meta = {
    twitter: `https://twitter.com/intent/tweet?text=${encodeURIComponent(title)}&url=${encodeURIComponent(url)}`,
    facebook: `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`,
    linkedin: `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}`
  };

  if (platform === 'copy') {
    navigator.clipboard.writeText(url).then(() => {
      const btn = document.querySelector('.copy-link-btn');
      if (btn) {
        const orig = btn.innerHTML;
        btn.innerHTML = btn.dataset.copyDone || '✓ Copied';
        setTimeout(() => { btn.innerHTML = orig; }, 2000);
      }
    });
    return;
  }

  if (meta[platform]) {
    window.open(meta[platform], '_blank', 'width=600,height=450');
  }
}
