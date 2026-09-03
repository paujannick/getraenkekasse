// Getränkekasse v3 – kleines Framework: Toasts, Theme, Palette, HTMX-Hooks.
(function () {
    'use strict';

    // ================= Theme =================
    const THEME_KEY = 'gk-theme';
    function applyTheme(t) {
        if (t === 'dark' || t === 'light') {
            document.documentElement.setAttribute('data-theme', t);
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
    }
    const saved = localStorage.getItem(THEME_KEY);
    applyTheme(saved || 'system');
    window.gkSetTheme = function (t) {
        if (t === 'system') localStorage.removeItem(THEME_KEY);
        else localStorage.setItem(THEME_KEY, t);
        applyTheme(t);
    };

    // ================= Toasts =================
    function ensureContainer() {
        let c = document.querySelector('.toast-container');
        if (!c) { c = document.createElement('div'); c.className = 'toast-container'; document.body.appendChild(c); }
        return c;
    }
    window.gkToast = function (msg, opts = {}) {
        const el = document.createElement('div');
        el.className = 'toast ' + (opts.level || '');
        el.innerHTML = '<span class="msg"></span>';
        el.querySelector('.msg').textContent = msg;
        if (opts.action) {
            const a = document.createElement('span');
            a.className = 'undo';
            a.textContent = opts.action.label || 'Rückgängig';
            a.onclick = () => { opts.action.onClick && opts.action.onClick(); el.remove(); };
            el.appendChild(a);
        }
        ensureContainer().appendChild(el);
        const t = opts.duration || 4000;
        setTimeout(() => { el.style.opacity = 0; el.style.transform = 'translateY(6px)'; setTimeout(() => el.remove(), 250); }, t);
    };

    // htmx signalisiert Toasts über Response-Header "HX-Trigger": {"showToast":{"msg":"..."}}
    document.body.addEventListener('showToast', (e) => {
        const d = e.detail;
        window.gkToast(d.msg || 'OK', { level: d.level, action: d.action, duration: d.duration });
    });

    // ================= Command Palette =================
    let paletteData = null;
    async function loadPalette() {
        if (paletteData) return paletteData;
        try {
            const r = await fetch('/api/palette');
            paletteData = await r.json();
        } catch (_) { paletteData = { items: [] }; }
        return paletteData;
    }
    function renderPalette(query) {
        const list = document.querySelector('#palette-list');
        list.innerHTML = '';
        const q = query.trim().toLowerCase();
        const items = paletteData.items || [];
        const filtered = q
            ? items.filter(it => (it.title + ' ' + (it.subtitle || '')).toLowerCase().includes(q))
            : items;
        filtered.slice(0, 30).forEach((it, idx) => {
            const li = document.createElement('li');
            if (idx === 0) li.classList.add('active');
            li.dataset.href = it.href || '';
            li.innerHTML = `<span>${it.title}</span>${it.subtitle ? '<small class="text-muted">' + it.subtitle + '</small>' : ''}<span class="kind">${it.kind}</span>`;
            li.onclick = () => go(li);
            list.appendChild(li);
        });
    }
    function go(li) {
        const href = li.dataset.href;
        if (href) window.location.href = href;
        closePalette();
    }
    function openPalette() {
        loadPalette().then(() => {
            document.getElementById('palette-backdrop').setAttribute('open', '');
            const input = document.getElementById('palette-input');
            input.value = ''; renderPalette('');
            setTimeout(() => input.focus(), 20);
        });
    }
    function closePalette() { document.getElementById('palette-backdrop')?.removeAttribute('open'); }
    window.gkOpenPalette = openPalette;
    window.gkClosePalette = closePalette;

    document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault(); openPalette();
        } else if (e.key === 'Escape') {
            closePalette();
        } else if (document.getElementById('palette-backdrop')?.hasAttribute('open')) {
            const list = document.getElementById('palette-list');
            const active = list.querySelector('li.active');
            if (e.key === 'ArrowDown' && active?.nextElementSibling) {
                e.preventDefault();
                active.classList.remove('active'); active.nextElementSibling.classList.add('active');
                active.nextElementSibling.scrollIntoView({ block: 'nearest' });
            } else if (e.key === 'ArrowUp' && active?.previousElementSibling) {
                e.preventDefault();
                active.classList.remove('active'); active.previousElementSibling.classList.add('active');
                active.previousElementSibling.scrollIntoView({ block: 'nearest' });
            } else if (e.key === 'Enter' && active) {
                e.preventDefault(); go(active);
            }
        }
    });

    // ================= Sidebar Toggle (Mobile) =================
    document.addEventListener('click', (e) => {
        if (e.target.closest('.sidebar-toggle')) {
            document.querySelector('.sidebar')?.classList.toggle('open');
        }
        if (e.target.classList.contains('palette-backdrop')) closePalette();
    });

    // Init input listener when DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        const inp = document.getElementById('palette-input');
        if (inp) inp.addEventListener('input', (e) => renderPalette(e.target.value));
    });
})();
