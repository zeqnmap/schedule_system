(async () => {
    const publicPage = location.pathname.endsWith('/login.html');
    const response = await fetch('/auth/me');
    if (!publicPage && response.status === 401) location.replace('/login.html');
})();
