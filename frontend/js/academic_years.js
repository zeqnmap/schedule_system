const { createApp, ref, onMounted } = Vue;
createApp({ setup() {
    const years = ref([]), form = ref({ name: '', start_date: '', end_date: '' }), editingId = ref(null), errorMessage = ref('');
    const load = async () => { const res = await fetch('/academic-years/'); if (res.ok) years.value = await res.json(); };
    const save = async () => {
        const payload = { ...form.value, is_active: true };
        const res = await fetch(editingId.value ? `/academic-years/${editingId.value}` : '/academic-years/', { method: editingId.value ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        if (!res.ok) { const data = await res.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось сохранить учебный год'; return; }
        const year = await res.json(); localStorage.setItem('edusync-academic-year-id', String(year.id)); reset(); await load();
    };
    const activate = async year => { const res = await fetch(`/academic-years/${year.id}/activate`, { method: 'POST' }); if (res.ok) { localStorage.setItem('edusync-academic-year-id', String(year.id)); await load(); } };
    const edit = year => { editingId.value = year.id; form.value = { name: year.name, start_date: year.start_date, end_date: year.end_date }; };
    const reset = () => { editingId.value = null; form.value = { name: '', start_date: '', end_date: '' }; };
    onMounted(load);
    return { years, form, editingId, errorMessage, save, activate, edit, reset };
} }).mount('#app');
