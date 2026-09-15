const { createApp, ref, onMounted } = Vue;
createApp({ setup() {
    const groups = ref([]), subjects = ref([]), teachers = ref([]), plans = ref([]), terms = ref([]);
    const editingPlanId = ref(null);
    const form = ref({ group_id: null, term_id: null, subject_name: '', total_hours: null, max_weekly_hours: 4, teacher_id: null, teacher2_id: null });
    const errorMessage = ref('');
    const fetchData = async () => { try {
        const yearId = localStorage.getItem('edusync-academic-year-id'); const yearParam = yearId ? `?academic_year_id=${yearId}` : '';
        const [gRes, sRes, tRes, pRes, termRes] = await Promise.all([fetch('/groups/'), fetch('/subjects/'), fetch('/teachers/'), fetch(`/course_plans/${yearParam}`), fetch(`/group-terms/${yearParam}`)]);
        if (gRes.ok) groups.value = await gRes.json(); if (sRes.ok) subjects.value = await sRes.json(); if (tRes.ok) teachers.value = await tRes.json(); if (pRes.ok) plans.value = await pRes.json(); if (termRes.ok) terms.value = await termRes.json();
    } catch (e) { errorMessage.value = 'Ошибка подключения к серверу API'; } };
    onMounted(fetchData);
    const getGroupNumber = id => groups.value.find(g => g.id === id)?.number || id;
    const getTeacherName = id => teachers.value.find(t => t.id === id)?.name || id;
    const termsForForm = Vue.computed(() => terms.value.filter(term => Number(term.group_id) === Number(form.value.group_id)));
    const getTermName = id => terms.value.find(term => Number(term.id) === Number(id))?.name || 'Семестр не выбран';
    const formatDate = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString('ru-RU') : '—';
    const savePlan = async () => { errorMessage.value = ''; try {
        const url = editingPlanId.value ? `/course_plans/${editingPlanId.value}` : '/course_plans/';
        const res = await fetch(url, { method: editingPlanId.value ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value) });
        if (res.ok) { resetForm(); await fetchData(); } else errorMessage.value = (await res.json()).detail || 'Ошибка сохранения плана';
    } catch (e) { errorMessage.value = 'Ошибка связи с сервером'; } };
    const editPlan = p => { editingPlanId.value = p.id; form.value = { ...p }; };
    const resetForm = () => { editingPlanId.value = null; form.value = { group_id: null, term_id: null, subject_name: '', total_hours: null, max_weekly_hours: 4, teacher_id: null, teacher2_id: null }; };
    const deletePlan = async id => { if (!confirm('Точно удалить этот план?')) return; await fetch(`/course_plans/${id}`, { method: 'DELETE' }); fetchData(); };
    return { groups, subjects, teachers, plans, form, editingPlanId, errorMessage, termsForForm, getTermName, formatDate, savePlan, editPlan, resetForm, deletePlan, getGroupNumber, getTeacherName };
} }).component('searchable-select', SearchableSelect).mount('#app');
