document.addEventListener('DOMContentLoaded', function() {
  var themeBtn = document.getElementById('adminThemeToggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var current = document.documentElement.getAttribute('data-theme') || 'dark';
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try {
        localStorage.setItem('theme', next);
      } catch (e) {}
    });
  }

  // Flash auto-dismiss
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transition = 'opacity 0.5s';
    }, 4000);
    setTimeout(() => el.remove(), 4500);
  });

  // Sidebar toggle for mobile
  const sidebar = document.getElementById('adminSidebar');
  const toggle = document.getElementById('sidebarToggle');
  const overlay = document.getElementById('sidebarOverlay');
  const closeBtn = document.getElementById('sidebarClose');

  function toggleSidebar(state) {
    if (!sidebar) return;
    const force = state !== undefined ? state : !sidebar.classList.contains('open');
    sidebar.classList.toggle('open', force);
    if (overlay) overlay.classList.toggle('open', force);
    document.body.classList.toggle('sidebar-open', force);
  }

  if (toggle) toggle.addEventListener('click', (e) => { e.stopPropagation(); toggleSidebar(); });
  if (overlay) overlay.addEventListener('click', () => toggleSidebar(false));
  if (closeBtn) closeBtn.addEventListener('click', () => toggleSidebar(false));

  // Close sidebar when clicking outside on mobile
  document.addEventListener('click', function(e) {
    if (sidebar && sidebar.classList.contains('open')) {
      if (!sidebar.contains(e.target) && (!toggle || !toggle.contains(e.target))) {
        toggleSidebar(false);
      }
    }
  });

  // Close sidebar on escape key
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') toggleSidebar(false);
  });

  // File upload zones — drag and drop
  document.querySelectorAll('.file-upload-zone').forEach(zone => {
    const input = zone.querySelector('input[type="file"]');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.style.borderColor = 'var(--accent)'; });
    zone.addEventListener('dragleave', () => { zone.style.borderColor = ''; });
    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.style.borderColor = '';
      if (input && e.dataTransfer.files.length) {
        const dt = new DataTransfer();
        dt.items.add(e.dataTransfer.files[0]);
        input.files = dt.files;
        input.dispatchEvent(new Event('change'));
      }
    });
  });
});
