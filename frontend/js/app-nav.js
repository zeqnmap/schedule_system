(() => {
    const nav = document.querySelector('nav');
    if (!nav) return;

    const current = location.pathname.split('/').pop() || 'index.html';
    const pages = [
        ['groups_subjects.html', 'Справочники', 'admin'],
        ['teachers.html', 'Преподаватели', 'admin'],
        ['admin.html', 'Учебные планы', 'admin'],
        ['progress.html', 'Часы', 'admin'],
        ['algorithm_settings.html', 'Алгоритм', 'admin'],
        ['users.html', 'Доступы', 'admin'],
        ['index.html', 'Расписание', 'all'],
    ];
    const render = (user = null) => {
        const visible = pages.filter(([, , role]) => role === 'all' || user?.is_admin);
        const links = visible.map(([href, label]) => `<a href="/html/${href}" class="app-nav-link ${href === current ? 'is-active' : ''} ${href === 'index.html' ? 'is-schedule' : ''}">${label}</a>`).join('');
        const userBlock = user ? `<div class="app-user"><span>${user.login}</span><button type="button" title="Выйти" aria-label="Выйти" data-logout>↗</button></div>` : '';
        nav.innerHTML = `<div class="app-nav mx-auto flex max-w-[1800px] items-center justify-between px-6 lg:px-10"><a class="app-brand" href="/html/index.html" aria-label="EduSync"><span class="app-brand-mark">E</span><span class="app-brand-name">EduSync<small>2026</small></span></a><div class="app-nav-links">${links}${userBlock}</div></div>`;
        nav.querySelector('[data-logout]')?.addEventListener('click', async () => { await fetch('/auth/logout', { method: 'POST' }); location.replace('/login.html'); });
    };

    document.body.classList.add('app-with-nav');
    render();
    fetch('/auth/me').then(response => response.ok ? response.json() : null).then(render).catch(() => render());
})();
