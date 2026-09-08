const { createApp, ref, computed, onMounted } = Vue;
createApp({ setup() {
    const subjects = ref([]), groups = ref([]), rules = ref([]), error = ref(''), scope = ref('course');
    const form = ref({ subject_name: '', course: 1, group_id: null, weekly_hours: 3, lesson_mode: 'auto' });
    const courses = computed(() => [...new Set(groups.value.map(group => group.course))].sort((a, b) => a - b));
    const request = async (url, options = {}) => { const response = await fetch(url, options); if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || 'Операция не выполнена'); } return response.json(); };
    const load = async () => { error.value = ''; try { [subjects.value, groups.value, rules.value] = await Promise.all([request('/subjects/'), request('/groups/'), request('/algorithm-rules/')]); if (courses.value.length && !courses.value.includes(form.value.course)) form.value.course = courses.value[0]; if (groups.value.length && !form.value.group_id) form.value.group_id = groups.value[0].id; } catch (err) { error.value = err.message; } };
    const createRule = async () => { error.value = ''; const payload = { ...form.value, group_id: scope.value === 'group' ? form.value.group_id : null, course: scope.value === 'course' ? form.value.course : null }; try { await request('/algorithm-rules/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); await load(); } catch (err) { error.value = err.message; } };
    const removeRule = async rule => { if (!confirm(`Удалить правило для «${rule.subject_name}»?`)) return; try { await request(`/algorithm-rules/${rule.id}`, { method: 'DELETE' }); await load(); } catch (err) { error.value = err.message; } };
    const scopeLabel = rule => rule.group_id ? `Группа ${groups.value.find(group => group.id === rule.group_id)?.number || rule.group_id}` : `${rule.course} курс`;
    const modeLabel = mode => ({ auto: 'Авто: пары и уроки', lessons: 'Только уроки', pairs: 'Только пары', pair_and_lesson: '1 пара + 1 урок' })[mode] || mode;
    onMounted(load); return { subjects, groups, rules, error, scope, form, courses, load, createRule, removeRule, scopeLabel, modeLabel };
} }).mount('#app');
