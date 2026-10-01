const { createApp, ref, computed, onMounted, watch } = Vue;
createApp({ setup() {
    const groups = ref([]), subjects = ref([]), teachers = ref([]), rooms = ref([]), plans = ref([]), terms = ref([]);
    const editingPlanId = ref(null);
    const selectedGroupId = ref(null), selectedTermId = ref(null), filterGroupSearch = ref('');
    const emptyForm = () => ({ group_id: null, term_id: null, subject_name: '', total_hours: null, max_weekly_hours: 4, teacher_id: null, teacher2_id: null, teacher_hours: null, teacher2_hours: null, room_name: null, room2_name: null });
    const form = ref(emptyForm());
    const errorMessage = ref('');
    const fetchData = async () => { try {
        const yearId = localStorage.getItem('edusync-academic-year-id'); const yearParam = yearId ? `?academic_year_id=${yearId}&limit=1000` : '?limit=1000';
        const [gRes, sRes, tRes, rRes, pRes, termRes] = await Promise.all([fetch('/groups/'), fetch('/subjects/'), fetch('/teachers/'), fetch('/rooms/'), fetch(`/course_plans/${yearParam}`), fetch(`/group-terms/${yearParam}`)]);
        if (gRes.ok) groups.value = await gRes.json(); if (sRes.ok) subjects.value = await sRes.json(); if (tRes.ok) teachers.value = await tRes.json(); if (rRes.ok) rooms.value = await rRes.json(); if (pRes.ok) plans.value = await pRes.json(); if (termRes.ok) terms.value = await termRes.json();
    } catch (e) { errorMessage.value = 'Ошибка подключения к серверу API'; } };
    onMounted(fetchData);
    const getGroupNumber = id => groups.value.find(g => g.id === id)?.number || id;
    const getTeacherName = id => teachers.value.find(t => t.id === id)?.name || id;
    const termsForForm = Vue.computed(() => terms.value.filter(term => Number(term.group_id) === Number(form.value.group_id)));
    const availableFilterTerms = computed(() => selectedGroupId.value
        ? terms.value.filter(term => Number(term.group_id) === Number(selectedGroupId.value))
        : []);
    const filteredGroups = computed(() => {
        const query = filterGroupSearch.value.trim().toLocaleLowerCase();
        return query ? groups.value.filter(group => String(group.number).toLocaleLowerCase().includes(query)) : groups.value;
    });
    const filteredPlans = computed(() => plans.value.filter(plan =>
        (!selectedGroupId.value || Number(plan.group_id) === Number(selectedGroupId.value)) &&
        (!selectedTermId.value || Number(plan.term_id) === Number(selectedTermId.value))
    ));
    watch(selectedGroupId, () => { selectedTermId.value = null; });
    const getTermName = id => terms.value.find(term => Number(term.id) === Number(id))?.name || 'Семестр не выбран';
    const formatDate = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString('ru-RU') : '—';
    const savePlan = async () => { errorMessage.value = ''; try {
        const url = editingPlanId.value ? `/course_plans/${editingPlanId.value}` : '/course_plans/';
        const res = await fetch(url, { method: editingPlanId.value ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value) });
        if (res.ok) { resetForm(); await fetchData(); } else errorMessage.value = (await res.json()).detail || 'Ошибка сохранения плана';
    } catch (e) { errorMessage.value = 'Ошибка связи с сервером'; } };
    const editPlan = p => { editingPlanId.value = p.id; form.value = { ...p }; };
    const resetForm = () => { editingPlanId.value = null; form.value = emptyForm(); };
    const deletePlan = async id => { if (!confirm('Точно удалить этот план?')) return; await fetch(`/course_plans/${id}`, { method: 'DELETE' }); fetchData(); };
    return { groups, subjects, teachers, rooms, plans, terms, form, editingPlanId, errorMessage, selectedGroupId, selectedTermId, filterGroupSearch, filteredGroups, availableFilterTerms, filteredPlans, termsForForm, getTermName, formatDate, savePlan, editPlan, resetForm, deletePlan, getGroupNumber, getTeacherName };
} }).component('searchable-select', SearchableSelect).mount('#app');
