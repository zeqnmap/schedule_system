const { createApp, ref, computed, onMounted, watch } = Vue;
document.body.classList.add('academic-years-page');
createApp({ setup() {
    const years = ref([]), groups = ref([]), terms = ref([]), selectedGroupId = ref(null), form = ref({ name: '', start_date: '', end_date: '' }), termForm = ref({ start_date: '', weeks: 20 }), editingId = ref(null), editingTermId = ref(null), errorMessage = ref('');
    const activeYearId = computed(() => years.value.find(year => year.is_active)?.id || null);
    const groupTerms = computed(() => terms.value.filter(term => Number(term.group_id) === Number(selectedGroupId.value)).sort((a, b) => a.term_number - b.term_number));
    const loadTerms = async () => { if (!activeYearId.value) return; const res = await fetch(`/group-terms/?academic_year_id=${activeYearId.value}`); if (res.ok) terms.value = await res.json(); };
    const load = async () => { const [yearRes, groupRes] = await Promise.all([fetch('/academic-years/'), fetch('/groups/')]); if (yearRes.ok) years.value = await yearRes.json(); if (groupRes.ok) { groups.value = await groupRes.json(); if (!selectedGroupId.value) selectedGroupId.value = groups.value[0]?.id || null; } await loadTerms(); };
    const save = async () => {
        const payload = { ...form.value, is_active: true };
        const res = await fetch(editingId.value ? `/academic-years/${editingId.value}` : '/academic-years/', { method: editingId.value ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        if (!res.ok) { const data = await res.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось сохранить учебный год'; return; }
        const year = await res.json(); localStorage.setItem('edusync-academic-year-id', String(year.id)); reset(); await load();
    };
    const activate = async year => { const res = await fetch(`/academic-years/${year.id}/activate`, { method: 'POST' }); if (res.ok) { localStorage.setItem('edusync-academic-year-id', String(year.id)); await load(); } };
    const endDate = computed(() => { if (!termForm.value.start_date) return ''; const date = new Date(`${termForm.value.start_date}T00:00:00`); date.setDate(date.getDate() + Math.max(1, Number(termForm.value.weeks) || 1) * 7 - 1); return date.toISOString().slice(0, 10); });
    const addTerm = async () => { const number = groupTerms.value.length + 1; if (!selectedGroupId.value || !termForm.value.start_date || !activeYearId.value) return; const res = await fetch('/group-terms/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_id: selectedGroupId.value, academic_year_id: activeYearId.value, term_number: number, name: `${number} семестр`, start_date: termForm.value.start_date, end_date: endDate.value, weeks: Number(termForm.value.weeks) }) }); if (!res.ok) { errorMessage.value = (await res.json()).detail || 'Не удалось добавить семестр'; return; } termForm.value = { start_date: '', weeks: 20 }; await loadTerms(); };
    const lockTerm = async term => { if (!confirm(`Завершить ${term.name}?`)) return; const res = await fetch(`/group-terms/${term.id}/lock`, { method: 'POST' }); if (res.ok) await loadTerms(); };
    const editTerm = term => { termForm.value = { start_date: term.start_date, weeks: term.weeks }; editingTermId.value = term.id; };
    const saveTerm = async () => { const term = groupTerms.value.find(item => item.id === editingTermId.value); if (!term) return; const res = await fetch(`/group-terms/${term.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_id: term.group_id, academic_year_id: term.academic_year_id, term_number: term.term_number, name: term.name, start_date: termForm.value.start_date, end_date: endDate.value, weeks: Number(termForm.value.weeks) }) }); if (res.ok) { editingTermId.value = null; termForm.value = { start_date: '', weeks: 20 }; await loadTerms(); } };
    const deleteTerm = async term => { if (!confirm(`Удалить ${term.name}?`)) return; const res = await fetch(`/group-terms/${term.id}`, { method: 'DELETE' }); if (res.ok) await loadTerms(); };
    const edit = year => { editingId.value = year.id; form.value = { name: year.name, start_date: year.start_date, end_date: year.end_date }; };
    const reset = () => { editingId.value = null; form.value = { name: '', start_date: '', end_date: '' }; };
    watch(selectedGroupId, () => { termForm.value = { start_date: '', weeks: 20 }; });
    onMounted(load);
    return { years, groups, terms, selectedGroupId, groupTerms, termForm, endDate, form, editingId, editingTermId, errorMessage, save, activate, edit, reset, addTerm, lockTerm, editTerm, saveTerm, deleteTerm };
} }).mount('#app');
