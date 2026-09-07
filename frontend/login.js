document.getElementById('login-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const error = document.getElementById('error');
    error.classList.add('hidden');
    try {
        const response = await fetch('/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ login: form.get('login'), password: form.get('password') }) });
        if (response.ok) { location.replace('/'); return; }
        const data = await response.json().catch(() => ({}));
        error.textContent = data.detail || `Ошибка входа (${response.status})`;
    } catch (requestError) {
        error.textContent = 'Не удалось связаться с сервером. Проверьте, что Docker запущен.';
    }
    error.classList.remove('hidden');
});
