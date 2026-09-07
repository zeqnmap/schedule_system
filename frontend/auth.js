(async () => {
    const publicPage = location.pathname.endsWith('/login.html');
    const response = await fetch('/auth/me');
    if (!publicPage && response.status === 401) location.replace('/login.html');
    if (!response.ok) return;
    const user = await response.json();
    document.documentElement.dataset.role = user.is_admin ? 'admin' : 'scheduler';
    document.querySelectorAll('.admin-only').forEach(element => element.toggleAttribute('hidden', !user.is_admin));
})();
