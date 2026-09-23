const { createApp, ref, computed, onMounted, watch } = Vue;

createApp({ setup() {
    const groups = ref([]), teachers = ref([]), terms = ref([]), selectedGroupId = ref(null), selectedTeacherId = ref(null), mode = ref('group');
    const calendarOpen = ref(false), startDate = ref(''), endDate = ref(''), calendarMonth = ref(''), errorMessage = ref('');
    const localDate = value => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
    const groupTerms = computed(() => terms.value.filter(term => Number(term.group_id) === Number(selectedGroupId.value)).sort((a, b) => String(a.start_date).localeCompare(String(b.start_date))));
    const selectedTeacher = computed(() => teachers.value.find(teacher => Number(teacher.id) === Number(selectedTeacherId.value)));
    const teacherTerms = computed(() => terms.value.filter(term => teachers.value.length && groups.value.some(group => Number(group.id) === Number(term.group_id))));
    const currentRange = computed(() => {
        const source = mode.value === 'teacher' ? teacherTerms.value : groupTerms.value;
        if (!source.length) return null;
        return { start: source.reduce((min, item) => item.start_date < min ? item.start_date : min, source[0].start_date), end: source.reduce((max, item) => item.end_date > max ? item.end_date : max, source[0].end_date) };
    });
    const calendarMonthDate = computed(() => new Date(`${calendarMonth.value || currentRange.value?.start || '2026-09-01'}T00:00:00`));
    const monthTitle = computed(() => calendarMonthDate.value.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' }));
    const calendarDays = computed(() => {
        if (!currentRange.value) return [];
        const month = calendarMonthDate.value;
        const first = new Date(month.getFullYear(), month.getMonth(), 1);
        const offset = (first.getDay() + 6) % 7;
        const count = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
        return Array.from({ length: offset + count }, (_, index) => {
            if (index < offset) return null;
            const value = localDate(new Date(month.getFullYear(), month.getMonth(), index - offset + 1));
            return { value, number: Number(value.slice(-2)), disabled: value < currentRange.value.start || value > currentRange.value.end, selected: value === startDate.value || value === endDate.value, between: Boolean(startDate.value && endDate.value && value > startDate.value && value < endDate.value) };
        });
    });
    const canPreviousMonth = computed(() => currentRange.value && calendarMonth.value > currentRange.value.start.slice(0, 7));
    const canNextMonth = computed(() => currentRange.value && calendarMonth.value < currentRange.value.end.slice(0, 7));
    const formatDate = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString('ru-RU') : 'не выбрано';
    const load = async () => {
        const yearId = localStorage.getItem('edusync-academic-year-id');
        const [groupsResponse, teachersResponse, termsResponse] = await Promise.all([
            fetch('/groups/'), fetch('/teachers/'), fetch(`/group-terms/${yearId ? `?academic_year_id=${yearId}` : ''}`),
        ]);
        if (groupsResponse.ok) groups.value = await groupsResponse.json();
        if (teachersResponse.ok) teachers.value = await teachersResponse.json();
        if (termsResponse.ok) terms.value = await termsResponse.json();
        if (!selectedGroupId.value && groups.value.length) selectedGroupId.value = groups.value[0].id;
        if (!selectedTeacherId.value && teachers.value.length) selectedTeacherId.value = teachers.value[0].id;
    };
    const resetPeriod = () => {
        if (!currentRange.value) return;
        startDate.value = currentRange.value.start;
        endDate.value = currentRange.value.end;
        calendarMonth.value = currentRange.value.start.slice(0, 7);
    };
    const openCalendar = () => { errorMessage.value = ''; resetPeriod(); calendarOpen.value = true; };
    const closeCalendar = () => { calendarOpen.value = false; };
    const previousMonth = () => { if (!canPreviousMonth.value) return; const month = calendarMonthDate.value; calendarMonth.value = localDate(new Date(month.getFullYear(), month.getMonth() - 1, 1)).slice(0, 7); };
    const nextMonth = () => { if (!canNextMonth.value) return; const month = calendarMonthDate.value; calendarMonth.value = localDate(new Date(month.getFullYear(), month.getMonth() + 1, 1)).slice(0, 7); };
    const selectDate = day => {
        if (!day || day.disabled) return;
        if (!startDate.value || endDate.value) { startDate.value = day.value; endDate.value = ''; return; }
        if (day.value < startDate.value) { endDate.value = startDate.value; startDate.value = day.value; } else endDate.value = day.value;
    };
    const downloadStatement = () => {
        if (!(mode.value === 'group' ? selectedGroupId.value : selectedTeacherId.value) || !startDate.value || !endDate.value) return;
        if (!endDate.value) { errorMessage.value = 'Выберите дату окончания периода.'; return; }
        const endpoint = mode.value === 'group' ? `/export/hours-statement.pdf?group_id=${selectedGroupId.value}` : `/export/teacher-hours-statement.pdf?teacher_id=${selectedTeacherId.value}`;
        window.open(`${endpoint}&start_date=${startDate.value}&end_date=${endDate.value}`, '_blank');
        calendarOpen.value = false;
    };
    watch([selectedGroupId, selectedTeacherId, mode], resetPeriod);
    onMounted(load);
    return { mode, groups, teachers, selectedGroupId, selectedTeacherId, selectedTeacher, groupTerms, currentRange, calendarOpen, startDate, endDate, calendarDays, monthTitle, canPreviousMonth, canNextMonth, errorMessage, formatDate, openCalendar, closeCalendar, previousMonth, nextMonth, selectDate, downloadStatement };
} }).mount('#app');
