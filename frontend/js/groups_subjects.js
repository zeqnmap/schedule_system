const { createApp, ref, computed, onMounted } = Vue;
createApp({ setup() {
    const groups = ref([]), subjects = ref([]), teachers = ref([]), rooms = ref([]);
    const newGroup = ref({ number: null, course: 1, has_saturday: false, semester_weeks: 20, curator_teacher_id: null, curator_room_name: '' });
    const editingGroupId = ref(null), newSubject = ref({ name: '' }), errorMessage = ref('');
    const groupTerms = ref([]), termForm = ref({ start_date: '', end_date: '' }), newTermForm = ref({ start_date: '', end_date: '' });
    const editingTermId = ref(null), editingTermName = ref('');
    let errorTimer;
    const showError = message => { errorMessage.value = message; clearTimeout(errorTimer); errorTimer = setTimeout(() => { errorMessage.value = ''; }, 6000); };
    const fetchData = async () => { try { const [gRes, sRes, tRes, rRes] = await Promise.all([fetch('/groups/'), fetch('/subjects/'), fetch('/teachers/'), fetch('/rooms/')]); if (gRes.ok) groups.value = await gRes.json(); if (sRes.ok) subjects.value = await sRes.json(); if (tRes.ok) teachers.value = await tRes.json(); if (rRes.ok) rooms.value = await rRes.json(); } catch (e) { errorMessage.value = 'Не удалось подключиться к серверу API.'; } };
    onMounted(fetchData);
    const saveGroup = async () => { errorMessage.value = ''; try { const url = editingGroupId.value ? `/groups/${editingGroupId.value}` : '/groups/'; const res = await fetch(url, { method: editingGroupId.value ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(newGroup.value) }); if (res.ok) { resetGroupForm(); await fetchData(); } else showError((await res.json()).detail || 'Ошибка сохранения'); } catch (e) { showError('Ошибка сервера'); } };
    const loadTerms = async () => { if (!editingGroupId.value) return; const res = await fetch(`/group-terms/?group_id=${editingGroupId.value}`); if (res.ok) groupTerms.value = await res.json(); };
    const editGroup = async g => { editingGroupId.value = g.id; newGroup.value = { number: g.number, course: g.course || 1, has_saturday: Boolean(g.has_saturday), semester_weeks: g.semester_weeks || 20, curator_teacher_id: g.curator_teacher_id || null, curator_room_name: g.curator_room_name || '' }; await loadTerms(); };
    const resetGroupForm = () => { editingGroupId.value = null; groupTerms.value = []; termForm.value = { start_date: '', end_date: '' }; newTermForm.value = { start_date: '', end_date: '' }; editingTermId.value = null; newGroup.value = { number: null, course: 1, has_saturday: false, semester_weeks: 20, curator_teacher_id: null, curator_room_name: '' }; };
    const nextTermNumber = computed(() => groupTerms.value.length + 1);
    const formatDate = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString('ru-RU') : '—';
    const dateAfterWeeks = (startDate, weeks) => {
        if (!startDate) return '';
        const date = new Date(`${startDate}T00:00:00`);
        date.setDate(date.getDate() + Math.max(1, Number(weeks) || 1) * 7 - 1);
        return date.toISOString().slice(0, 10);
    };
    // The legacy field is the source of truth for the first semester length.
    // The preview changes immediately; the server persists it with the group.
    const termWeeks = term => Number(term.term_number) === 1 && !term.is_locked
        ? Math.max(1, Number(newGroup.value.semester_weeks) || 1)
        : Number(term.weeks || 1);
    const termEnd = term => Number(term.term_number) === 1 && !term.is_locked
        ? dateAfterWeeks(term.start_date, termWeeks(term))
        : term.end_date;
    const calculatedTermEnd = computed(() => { const term = groupTerms.value.find(item => item.id === editingTermId.value); return term ? formatDate(dateAfterWeeks(termForm.value.start_date, termWeeks(term))) : ''; });
    const addTerm = async () => { if (!newTermForm.value.start_date || !newTermForm.value.end_date) return showError('Укажите даты начала и окончания семестра'); const res = await fetch('/group-terms/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_id: editingGroupId.value, term_number: nextTermNumber.value, name: `${nextTermNumber.value} семестр`, ...newTermForm.value }) }); if (!res.ok) return showError((await res.json()).detail || 'Не удалось добавить семестр'); newTermForm.value = { start_date: '', end_date: '' }; await loadTerms(); };
    const editTerm = term => { editingTermId.value = term.id; editingTermName.value = term.name; termForm.value = { start_date: term.start_date, end_date: term.end_date }; };
    const cancelTermEdit = () => { editingTermId.value = null; editingTermName.value = ''; termForm.value = { start_date: '', end_date: '' }; };
    const saveTermDates = async () => { const current = groupTerms.value.find(term => term.id === editingTermId.value); if (!current) return; const res = await fetch(`/group-terms/${current.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_id: current.group_id, term_number: current.term_number, name: current.name, ...termForm.value }) }); if (!res.ok) return showError((await res.json()).detail || 'Не удалось сохранить даты'); cancelTermEdit(); await loadTerms(); };
    const lockTerm = async term => { if (!confirm(`Завершить ${term.name}? Генерация для него будет навсегда отключена, но ручное редактирование уроков останется.`)) return; const res = await fetch(`/group-terms/${term.id}/lock`, { method: 'POST' }); if (res.ok) await loadTerms(); else showError((await res.json()).detail || 'Не удалось завершить семестр'); };
    const deleteGroup = async id => { if (!confirm('Точно удалить?')) return; await fetch(`/groups/${id}`, { method: 'DELETE' }); fetchData(); };
    const toggleSaturday = async groupId => { await fetch(`/groups/${groupId}/toggle-saturday`, { method: 'POST' }); fetchData(); };
    const addSubject = async () => { const trimmedName = newSubject.value.name.trim(); if (!trimmedName) return; await fetch('/subjects/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: trimmedName }) }); newSubject.value.name = ''; fetchData(); };
    const availableCuratorTeachers = computed(() => teachers.value.filter(t => !groups.value.some(g => g.id !== editingGroupId.value && Number(g.curator_teacher_id) === Number(t.id))));
    const availableCuratorRooms = computed(() => rooms.value.filter(r => !groups.value.some(g => g.id !== editingGroupId.value && (g.curator_room_name || '').trim() === r.name)));
    const deleteSubject = async id => { if (!confirm('Удалить предмет?')) return; await fetch(`/subjects/${id}`, { method: 'DELETE' }); fetchData(); };
    return { groups, subjects, teachers, rooms, newGroup, editingGroupId, newSubject, errorMessage, groupTerms, termForm, newTermForm, editingTermId, editingTermName, nextTermNumber, calculatedTermEnd, formatDate, termWeeks, termEnd, addTerm, editTerm, cancelTermEdit, saveTermDates, lockTerm, saveGroup, editGroup, resetGroupForm, deleteGroup, toggleSaturday, addSubject, deleteSubject, availableCuratorTeachers, availableCuratorRooms };
} }).mount('#app');
