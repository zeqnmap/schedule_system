const { createApp, ref, onMounted } = Vue;
createApp({ setup() {
    const groups = ref([]), subjects = ref([]), teachers = ref([]), plans = ref([]);
    const editingPlanId = ref(null);
    const form = ref({ group_id: null, subject_name: '', total_hours: null, max_weekly_hours: 4, teacher_id: null, teacher2_id: null });
    const errorMessage = ref('');
    const fetchData = async () => { try {
        const [gRes, sRes, tRes, pRes] = await Promise.all([fetch('/groups/'), fetch('/subjects/'), fetch('/teachers/'), fetch('/course_plans/')]);
        if (gRes.ok) groups.value = await gRes.json(); if (sRes.ok) subjects.value = await sRes.json(); if (tRes.ok) teachers.value = await tRes.json(); if (pRes.ok) plans.value = await pRes.json();
    } catch (e) { errorMessage.value = 'Ошибка подключения к серверу API'; } };
    onMounted(fetchData);
    const getGroupNumber = id => groups.value.find(g => g.id === id)?.number || id;
    const getTeacherName = id => teachers.value.find(t => t.id === id)?.name || id;
    const savePlan = async () => { errorMessage.value = ''; try {
        const url = editingPlanId.value ? `/course_plans/${editingPlanId.value}` : '/course_plans/';
        const res = await fetch(url, { method: editingPlanId.value ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value) });
        if (res.ok) { resetForm(); await fetchData(); } else errorMessage.value = (await res.json()).detail || 'Ошибка сохранения плана';
    } catch (e) { errorMessage.value = 'Ошибка связи с сервером'; } };
    const editPlan = p => { editingPlanId.value = p.id; form.value = { ...p }; };
    const resetForm = () => { editingPlanId.value = null; form.value = { group_id: null, subject_name: '', total_hours: null, max_weekly_hours: 4, teacher_id: null, teacher2_id: null }; };
    const deletePlan = async id => { if (!confirm('Точно удалить этот план?')) return; await fetch(`/course_plans/${id}`, { method: 'DELETE' }); fetchData(); };
    return { groups, subjects, teachers, plans, form, editingPlanId, errorMessage, savePlan, editPlan, resetForm, deletePlan, getGroupNumber, getTeacherName };
} }).component('searchable-select', SearchableSelect).mount('#app');
