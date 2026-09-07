const { createApp, ref, computed, onMounted } = Vue;

createApp({
    setup() {
        const users = ref([]), error = ref(''), currentUserId = ref(null);
        const form = ref({ login: '', password: '' });
        const activeCount = computed(() => users.value.filter(user => user.is_active).length);
        const adminCount = computed(() => users.value.filter(user => user.is_admin).length);
        const request = async (url, options = {}) => {
            const response = await fetch(url, options);
            if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || 'Операция не выполнена'); }
            return response.json();
        };
        const load = async () => { error.value = ''; try { users.value = await request('/users/'); } catch (err) { error.value = err.message; } };
        const update = async (user, payload) => { error.value = ''; try { Object.assign(user, await request(`/users/${user.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })); } catch (err) { error.value = err.message; } };
        const createUser = async () => { error.value = ''; try { await request('/users/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value) }); form.value = { login: '', password: '' }; await load(); } catch (err) { error.value = err.message; } };
        const toggleActive = user => update(user, { is_active: !user.is_active });
        const resetPassword = async user => { const password = prompt(`Новый пароль для ${user.login} (минимум 8 символов):`); if (password !== null) await update(user, { password }); };
        const removeUser = async user => { if (!confirm(`Удалить аккаунт «${user.login}»?`)) return; try { await request(`/users/${user.id}`, { method: 'DELETE' }); await load(); } catch (err) { error.value = err.message; } };
        const logout = async () => { await fetch('/auth/logout', { method: 'POST' }); location.replace('/login.html'); };
        onMounted(async () => { const response = await fetch('/auth/me'); if (response.ok) currentUserId.value = (await response.json()).id; await load(); });
        return { users, error, form, currentUserId, activeCount, adminCount, load, createUser, toggleActive, resetPassword, removeUser, logout };
    }
}).mount('#app');
