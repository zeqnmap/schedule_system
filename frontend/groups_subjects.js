const { createApp, ref, onMounted } = Vue;
createApp({ setup() {
    const groups = ref([]), subjects = ref([]);
    const newGroup = ref({ number: null, course: 1, has_saturday: false, semester_weeks: 20 });
    const editingGroupId = ref(null), newSubject = ref({ name: '' }), errorMessage = ref('');
    const fetchData = async () => { try { const [gRes, sRes] = await Promise.all([fetch('http://localhost:8000/groups/'), fetch('http://localhost:8000/subjects/')]); if (gRes.ok) groups.value = await gRes.json(); if (sRes.ok) subjects.value = await sRes.json(); } catch (e) { errorMessage.value = 'Не удалось подключиться к серверу API.'; } };
    onMounted(fetchData);
    const saveGroup = async () => { errorMessage.value = ''; try { const url = editingGroupId.value ? `http://localhost:8000/groups/${editingGroupId.value}` : 'http://localhost:8000/groups/'; const res = await fetch(url, { method: editingGroupId.value ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(newGroup.value) }); if (res.ok) { resetGroupForm(); await fetchData(); } else errorMessage.value = (await res.json()).detail || 'Ошибка сохранения'; } catch (e) { errorMessage.value = 'Ошибка сервера'; } };
    const editGroup = g => { editingGroupId.value = g.id; newGroup.value = { number: g.number, course: g.course || 1, has_saturday: Boolean(g.has_saturday), semester_weeks: g.semester_weeks || 20 }; };
    const resetGroupForm = () => { editingGroupId.value = null; newGroup.value = { number: null, course: 1, has_saturday: false, semester_weeks: 20 }; };
    const deleteGroup = async id => { if (!confirm('Точно удалить?')) return; await fetch(`http://localhost:8000/groups/${id}`, { method: 'DELETE' }); fetchData(); };
    const toggleSaturday = async groupId => { await fetch(`http://localhost:8000/groups/${groupId}/toggle-saturday`, { method: 'POST' }); fetchData(); };
    const addSubject = async () => { const trimmedName = newSubject.value.name.trim(); if (!trimmedName) return; await fetch('http://localhost:8000/subjects/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: trimmedName }) }); newSubject.value.name = ''; fetchData(); };
    const deleteSubject = async id => { if (!confirm('Удалить предмет?')) return; await fetch(`http://localhost:8000/subjects/${id}`, { method: 'DELETE' }); fetchData(); };
    return { groups, subjects, newGroup, editingGroupId, newSubject, errorMessage, saveGroup, editGroup, resetGroupForm, deleteGroup, toggleSaturday, addSubject, deleteSubject };
} }).mount('#app');
