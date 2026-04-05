/**
 * Admin help assistant — intent FAQ (EN/SW). Requires #adminHelpAssistantRoot in DOM.
 */
(function () {
  var root = document.getElementById('adminHelpAssistantRoot');
  if (!root) return;

  var CONFIG_URL = root.getAttribute('data-config-url') || '';
  var CHAT_URL = root.getAttribute('data-chat-url') || '';
  var panel = document.getElementById('adminHelpAssistantPanel');
  var fab = document.getElementById('adminHelpFab');
  var closeBtn = document.getElementById('adminHelpClose');
  var messagesEl = document.getElementById('adminHelpMessages');
  var chipsEl = document.getElementById('adminHelpChips');
  var form = document.getElementById('adminHelpForm');
  var input = document.getElementById('adminHelpInput');
  var btnEn = document.getElementById('adminHelpLangEn');
  var btnSw = document.getElementById('adminHelpLangSw');
  var sidebarBtn = document.getElementById('adminHelpFromSidebar');

  var LANG_KEY = 'lawblog_admin_help_lang';
  var lang = localStorage.getItem(LANG_KEY) || 'en';
  if (lang !== 'sw') lang = 'en';

  var configLoaded = false;
  var welcomeEn = '';
  var welcomeSw = '';
  var chips = [];

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function csrfHeader() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') || '' : '';
  }

  function formatReply(text) {
    var paras = (text || '').trim().split(/\n\n+/);
    return paras
      .map(function (p) {
        var h = esc(p).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        return '<div class="admin-help-para">' + h.replace(/\n/g, '<br/>') + '</div>';
      })
      .join('');
  }

  function applyChromeLang() {
    document.querySelectorAll('[data-i18n-en][data-i18n-sw]').forEach(function (el) {
      el.textContent = lang === 'sw' ? el.getAttribute('data-i18n-sw') : el.getAttribute('data-i18n-en');
    });
    if (input) {
      input.placeholder =
        lang === 'sw' ? 'Andika swali lako hapa… (Kiswahili au Kiingereza)' : 'Type your question… (English or Kiswahili)';
    }
    if (btnEn) btnEn.classList.toggle('active', lang === 'en');
    if (btnSw) btnSw.classList.toggle('active', lang === 'sw');
    if (fab && panel && !panel.hasAttribute('hidden')) {
      fab.setAttribute(
        'aria-label',
        lang === 'sw' ? 'Funga msaidizi' : 'Close help assistant'
      );
    }
  }

  function setOpen(on) {
    if (!panel || !fab) return;
    if (on) {
      panel.removeAttribute('hidden');
    } else {
      panel.setAttribute('hidden', '');
    }
    fab.setAttribute('aria-expanded', on ? 'true' : 'false');
    fab.setAttribute(
      'aria-label',
      on
        ? lang === 'sw'
          ? 'Funga msaidizi'
          : 'Close help assistant'
        : lang === 'sw'
          ? 'Fungua msaidizi'
          : 'Open help assistant'
    );
    if (on) {
      input && input.focus();
      if (!configLoaded) loadConfig();
    }
  }

  function toggle() {
    setOpen(panel.hidden);
  }

  function appendBubble(role, html, links) {
    var wrap = document.createElement('div');
    wrap.className = 'admin-help-bubble admin-help-bubble--' + role;
    var inner = document.createElement('div');
    inner.className = 'admin-help-bubble-inner';
    inner.innerHTML = html;
    wrap.appendChild(inner);
    if (links && links.length) {
      var nav = document.createElement('div');
      nav.className = 'admin-help-links';
      links.forEach(function (L) {
        var a = document.createElement('a');
        a.href = L.url;
        a.className = 'admin-help-link';
        a.textContent = L.label;
        nav.appendChild(a);
      });
      wrap.appendChild(nav);
    }
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function renderChips() {
    if (!chipsEl) return;
    chipsEl.innerHTML = '';
    chips.forEach(function (c) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'admin-help-chip';
      b.textContent = lang === 'sw' ? c.label_sw : c.label_en;
      b.addEventListener('click', function () {
        var q = lang === 'sw' ? c.prompt_sw : c.prompt_en;
        input.value = q;
        sendMessage(q);
      });
      chipsEl.appendChild(b);
    });
  }

  function loadConfig() {
    fetch(CONFIG_URL, { credentials: 'same-origin' })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        configLoaded = true;
        if (!data.ok) return;
        welcomeEn = data.welcome_en || '';
        welcomeSw = data.welcome_sw || '';
        chips = data.chips || [];
        messagesEl.innerHTML = '';
        appendBubble(
          'bot',
          formatReply(lang === 'sw' ? welcomeSw : welcomeEn),
          null
        );
        renderChips();
      })
      .catch(function () {
        configLoaded = true;
        appendBubble('bot', formatReply('Could not load help topics. Try again later.'), null);
      });
  }

  function sendMessage(text) {
    var q = (text != null ? text : input.value).trim();
    if (!q) return;
    appendBubble('user', esc(q).replace(/\n/g, '<br/>'), null);
    input.value = '';
    appendBubble('bot', '<span class="admin-help-thinking">…</span>', null);
    var thinking = messagesEl.lastChild;

    fetch(CHAT_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRFToken': csrfHeader(),
      },
      body: JSON.stringify({ message: q, lang: lang }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        thinking.remove();
        if (!data.ok) {
          appendBubble('bot', esc(data.error || 'Something went wrong.'), null);
          return;
        }
        appendBubble('bot', formatReply(data.reply || ''), data.links || []);
        if (data.source === 'ai') {
          var bubbles = messagesEl.querySelectorAll('.admin-help-bubble--bot .admin-help-bubble-inner');
          var lastInner = bubbles[bubbles.length - 1];
          if (lastInner) {
            var badge = document.createElement('div');
            badge.className = 'admin-help-ai-badge';
            badge.textContent =
              lang === 'sw'
                ? '✨ Jibu limeimarishwa kwa msaada mahiri (ukweli ule ule).'
                : '✨ Smart help polished this answer (same facts).';
            lastInner.appendChild(badge);
          }
        }
      })
      .catch(function () {
        thinking.remove();
        appendBubble('bot', esc('Network error. Check your connection.'), null);
      });
  }

  fab.addEventListener('click', function (e) {
    e.stopPropagation();
    toggle();
  });
  if (closeBtn) {
    closeBtn.addEventListener('click', function () {
      setOpen(false);
    });
  }
  if (sidebarBtn) {
    sidebarBtn.addEventListener('click', function () {
      toggle();
    });
  }

  btnEn.addEventListener('click', function () {
    lang = 'en';
    localStorage.setItem(LANG_KEY, lang);
    applyChromeLang();
    renderChips();
  });
  btnSw.addEventListener('click', function () {
    lang = 'sw';
    localStorage.setItem(LANG_KEY, lang);
    applyChromeLang();
    renderChips();
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    sendMessage();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && panel && !panel.hidden) setOpen(false);
  });

  applyChromeLang();
  setOpen(false);
})();
