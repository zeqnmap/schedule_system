const { createApp, ref, onMounted } = Vue;
createApp({ setup() {
    const users = ref([]), error = ref(''), form = ref({ login: '', password: '' });
    const load = async () => { const res = await fetch('/users/'); if (res.ok) users.value = await res.json(); else if (res.status === 403) location.replace('/'); };
    const createUser = async () => { error.value = ''; const res = await fetch('/users/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value) }); if (res.ok) { form.value = { login: '', password: '' }; await load(); } else error.value = (await res.json()).detail || 'Не удалось создать пользователя'; };
    const removeUser = async id => { if (!confirm('Удалить пользователя?')) return; await fetch(`/users/${id}`, { method: 'DELETE' }); load(); };
    const logout = async () => { await fetch('/auth/logout', { method: 'POST' }); location.replace('/login.html'); };
    onMounted(load);
    return { users, error, form, createUser, removeUser, logout };
} }).mount('#app');
