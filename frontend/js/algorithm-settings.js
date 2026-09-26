const { createApp, ref, computed, onMounted } = Vue;
createApp({ setup() {
    const subjects = ref([]), groups = ref([]), teachers = ref([]), rooms = ref([]), rules = ref([]), curatorHours = ref([]), years = ref([]), daysOff = ref([]), groupBreaks = ref([]), error = ref(''), notice = ref(''), curatorNotice = ref(''), curatorError = ref(''), scope = ref('course');
    const calendarMonth = ref('');
    const breakGroupId = ref(null), breakCalendarMonth = ref(''), groupTerms = ref([]), breakRangeStart = ref('');
    const curatorCount = ref(1);
    const curatorForms = ref([{ day_of_week: 1, time_slot: 1, duration: 1 }, { day_of_week: 2, time_slot: 1, duration: 1 }]);
    const curatorAssignments = ref({});
    const days = ['', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const form = ref({ subject_name: '', course: 1, group_id: null, term_number: null, weekly_hours: 3, lesson_mode: 'auto' });
    const courses = computed(() => [...new Set(groups.value.map(group => group.course))].sort((a, b) => a - b));
    const activeYear = computed(() => years.value.find(year => year.is_active) || null);
    const localDate = value => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
    const selectedMonth = computed(() => new Date(`${calendarMonth.value || activeYear.value?.start_date?.slice(0, 7) || '2026-09'}-01T00:00:00`));
    const calendarTitle = computed(() => selectedMonth.value.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' }));
    const calendarDays = computed(() => { const month = selectedMonth.value, first = new Date(month.getFullYear(), month.getMonth(), 1), offset = (first.getDay() + 6) % 7, count = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate(); return Array.from({ length: offset + count }, (_, index) => { if (index < offset) return null; const value = localDate(new Date(month.getFullYear(), month.getMonth(), index - offset + 1)); return { value, number: Number(value.slice(-2)), sunday: new Date(`${value}T00:00:00`).getDay() === 0, outside: !activeYear.value || value < activeYear.value.start_date || value > activeYear.value.end_date, off: daysOff.value.some(item => item.day_date === value) }; }); });
    const breakGroup = computed(() => groups.value.find(group => Number(group.id) === Number(breakGroupId.value)) || null);
    const breakTerms = computed(() => termsForBreak.value);
    const termsForBreak = computed(() => activeYear.value && breakGroupId.value ? groupTerms.value.filter(term => Number(term.group_id) === Number(breakGroupId.value) && Number(term.academic_year_id) === Number(activeYear.value.id)) : []);
    const breakSelectedMonth = computed(() => new Date(`${breakCalendarMonth.value || breakTerms.value[0]?.start_date?.slice(0, 7) || activeYear.value?.start_date?.slice(0, 7) || '2026-09'}-01T00:00:00`));
    const breakCalendarTitle = computed(() => breakSelectedMonth.value.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' }));
    const breakCalendarDays = computed(() => {
        const month = breakSelectedMonth.value, first = new Date(month.getFullYear(), month.getMonth(), 1), offset = (first.getDay() + 6) % 7, count = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
        return Array.from({ length: offset + count }, (_, index) => {
            if (index < offset) return null;
            const date = new Date(month.getFullYear(), month.getMonth(), index - offset + 1), value = localDate(date);
            const sunday = date.getDay() === 0, saturday = date.getDay() === 6;
            const inTerm = breakTerms.value.some(term => value >= term.start_date && value <= term.end_date);
            const commonOff = daysOff.value.some(item => item.day_date === value);
            const selected = groupBreaks.value.some(item => item.day_date === value);
            const noSaturdayClasses = !breakGroup.value?.has_saturday && saturday;
            return { value, number: date.getDate(), sunday, saturday, commonOff, selected, rangeStart: breakRangeStart.value === value, outside: !inTerm || sunday || noSaturdayClasses || commonOff };
        });
    });
    const request = async (url, options = {}) => { const response = await fetch(url, options); if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || 'Операция не выполнена'); } return response.json(); };
    const loadGroupBreaks = async () => {
        groupBreaks.value = [];
        if (!activeYear.value || !breakGroupId.value) return;
        groupBreaks.value = await request(`/group-break-days/?group_id=${breakGroupId.value}&academic_year_id=${activeYear.value.id}`);
    };
    const selectBreakGroup = async () => {
        breakRangeStart.value = '';
        breakCalendarMonth.value = breakTerms.value[0]?.start_date?.slice(0, 7) || activeYear.value?.start_date?.slice(0, 7) || '';
        try { await loadGroupBreaks(); } catch (err) { error.value = err.message; }
    };
    const load = async () => { error.value = ''; const results = await Promise.allSettled([request('/subjects/'), request('/groups/'), request('/teachers/'), request('/rooms/'), request('/algorithm-rules/'), request('/curator-hours/'), request('/academic-years/'), request('/group-terms/')]); const [subjectResult, groupResult, teacherResult, roomResult, ruleResult, curatorResult, yearResult, termResult] = results; subjects.value = subjectResult.status === 'fulfilled' ? subjectResult.value : []; groups.value = groupResult.status === 'fulfilled' ? groupResult.value : []; teachers.value = teacherResult.status === 'fulfilled' ? teacherResult.value : []; rooms.value = roomResult.status === 'fulfilled' ? roomResult.value : []; rules.value = ruleResult.status === 'fulfilled' ? ruleResult.value : []; curatorHours.value = curatorResult.status === 'fulfilled' ? curatorResult.value : []; years.value = yearResult.status === 'fulfilled' ? yearResult.value : []; groupTerms.value = termResult.status === 'fulfilled' ? termResult.value : []; if (activeYear.value) { const dayResult = await request(`/academic-days-off/?academic_year_id=${activeYear.value.id}`); daysOff.value = dayResult; if (!calendarMonth.value) calendarMonth.value = activeYear.value.start_date.slice(0, 7); } const failed = results.find(result => result.status === 'rejected'); if (failed) error.value = failed.reason.message; const globalHours = curatorHours.value.filter(hour => !Number(hour.group_id)); curatorCount.value = Math.min(2, Math.max(1, globalHours.length || 1)); if (globalHours.length) curatorForms.value = [0, 1].map(index => ({ day_of_week: globalHours[index]?.day_of_week || 1, time_slot: globalHours[index]?.time_slot || 1, duration: globalHours[index]?.duration || 1, hour_type: globalHours[index]?.hour_type || 'curator' })); const next = {}; groups.value.forEach(group => { const items = curatorHours.value.filter(hour => Number(hour.group_id) === Number(group.id)); next[group.id] = [0, 1].map(index => ({ teacher_id: items[index]?.teacher_id || null, room_name: items[index]?.room_name || '' })); }); curatorAssignments.value = next; if (courses.value.length && !courses.value.includes(form.value.course)) form.value.course = courses.value[0]; if (groups.value.length && !form.value.group_id) form.value.group_id = groups.value[0].id; if (groups.value.length && !breakGroupId.value) breakGroupId.value = groups.value[0].id; await selectBreakGroup(); };
    const toggleDayOff = async day => { if (!day || day.sunday || day.outside) return; notice.value = ''; const item = daysOff.value.find(value => value.day_date === day.value); if (!item && !confirm(`Точно сделать выходной на ${day.value}?`)) return; try { const response = await fetch(item ? `/academic-days-off/${item.id}` : '/academic-days-off/', item ? { method: 'DELETE' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ academic_year_id: activeYear.value.id, day_date: day.value, title: 'Общий выходной' }) }); if (!response.ok) throw new Error((await response.json()).detail); notice.value = item ? 'Дата снова доступна для расписания.' : `Дата стала общим выходным. Удалено занятий: ${response.headers.get('X-Removed-Schedule-Entries') || 0}.`; const result = await request(`/academic-days-off/?academic_year_id=${activeYear.value.id}`); daysOff.value = result; } catch (err) { error.value = err.message; } };
    const previousMonth = () => { const value = selectedMonth.value; calendarMonth.value = localDate(new Date(value.getFullYear(), value.getMonth() - 1, 1)).slice(0, 7); };
    const nextMonth = () => { const value = selectedMonth.value; calendarMonth.value = localDate(new Date(value.getFullYear(), value.getMonth() + 1, 1)).slice(0, 7); };
    const previousBreakMonth = () => { const value = breakSelectedMonth.value; breakCalendarMonth.value = localDate(new Date(value.getFullYear(), value.getMonth() - 1, 1)).slice(0, 7); };
    const nextBreakMonth = () => { const value = breakSelectedMonth.value; breakCalendarMonth.value = localDate(new Date(value.getFullYear(), value.getMonth() + 1, 1)).slice(0, 7); };
    const formatBreakDate = value => new Date(`${value}T00:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
    const toggleGroupBreak = async day => {
        if (!day || day.outside) return;
        const item = groupBreaks.value.find(value => value.day_date === day.value);
        if (item && !breakRangeStart.value) {
            notice.value = '';
            try {
                const response = await fetch(`/group-break-days/${item.id}`, { method: 'DELETE' });
                if (!response.ok) throw new Error((await response.json()).detail || 'Операция не выполнена');
                notice.value = 'Дата снова доступна для этой группы.';
                await loadGroupBreaks();
            } catch (err) { error.value = err.message; }
            return;
        }
        if (!breakRangeStart.value) {
            breakRangeStart.value = day.value;
            notice.value = `Начало периода: ${formatBreakDate(day.value)}. Теперь выберите последнюю дату.`;
            return;
        }
        const [start_date, end_date] = [breakRangeStart.value, day.value].sort();
        if (!confirm(`Сделать перерыв для группы ${breakGroup.value?.number || ''} с ${formatBreakDate(start_date)} по ${formatBreakDate(end_date)}?`)) return;
        notice.value = '';
        try {
            const response = await fetch('/group-break-days/range/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ academic_year_id: activeYear.value.id, group_id: breakGroupId.value, start_date, end_date, title: 'Перерыв группы' }) });
            if (!response.ok) throw new Error((await response.json()).detail || 'Операция не выполнена');
            notice.value = `Перерыв сохранён на ${response.headers.get('X-Added-Group-Break-Days') || 0} учебных дн. Удалено занятий группы: ${response.headers.get('X-Removed-Schedule-Entries') || 0}.`;
            breakRangeStart.value = '';
            await loadGroupBreaks();
        } catch (err) { error.value = err.message; }
    };
    const createRule = async () => { error.value = ''; const payload = { ...form.value, group_id: scope.value === 'group' ? form.value.group_id : null, course: scope.value === 'course' ? form.value.course : null }; try { await request('/algorithm-rules/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); await load(); } catch (err) { error.value = err.message; } };
    const removeRule = async rule => { if (!confirm(`Удалить правило для «${rule.subject_name}»?`)) return; try { await request(`/algorithm-rules/${rule.id}`, { method: 'DELETE' }); await load(); } catch (err) { error.value = err.message; } };
    const createCuratorHours = async () => { curatorNotice.value = ''; curatorError.value = ''; try { const global = curatorHours.value.filter(item => !Number(item.group_id)); for (const item of global) await request(`/curator-hours/${item.id}`, { method: 'DELETE' }); for (const curator of curatorForms.value.slice(0, curatorCount.value)) await request('/curator-hours/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...curator, group_id: 0, teacher_id: null, room_name: null, hour_type: curator.hour_type || 'curator' }) }); await load(); curatorNotice.value = `Сохранено общих часов: ${curatorCount.value}.`; } catch (err) { curatorError.value = err.message; } };
    const createCuratorHour = async () => { try { await request('/curator-hours/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(curatorForm.value) }); await load(); } catch (err) { error.value = err.message; } };
    const removeCuratorHour = async item => { try { await request(`/curator-hours/${item.id}`, { method: 'DELETE' }); await load(); } catch (err) { error.value = err.message; } };
    const scopeLabel = rule => rule.group_id ? `Группа ${groups.value.find(group => group.id === rule.group_id)?.number || rule.group_id}` : `${rule.course} курс`;
    const termLabel = rule => rule.term_number ? `${rule.term_number} семестр` : 'Все семестры';
    const modeLabel = mode => ({ auto: 'Авто: пары и уроки', lessons: 'Только уроки', pairs: 'Только пары', pair_and_lesson: '1 пара + 1 урок' })[mode] || mode;
    const availableCuratorRooms = (groupId, index = 0) => {
        const currentTime = curatorForms.value[index] || curatorForms.value[0];
        const overlaps = (first, second) => first && second
            && Number(first.day_of_week) === Number(second.day_of_week)
            && Number(first.time_slot) < Number(second.time_slot) + Number(second.duration || 1)
            && Number(second.time_slot) < Number(first.time_slot) + Number(first.duration || 1);
        const selectedElsewhere = new Set(groups.value.flatMap(group => (curatorAssignments.value[group.id] || [])
            .map((item, itemIndex) => ({ item, itemIndex }))
            .filter(({ itemIndex }) => group.id !== groupId || itemIndex !== index)
            .filter(({ itemIndex }) => overlaps(currentTime, curatorForms.value[itemIndex]))
            .map(({ item }) => item?.room_name)).filter(Boolean));
        const current = curatorAssignments.value[groupId]?.[index]?.room_name;
        return rooms.value.filter(room => room.name === current || !selectedElsewhere.has(room.name));
    };
    onMounted(load); return { subjects, groups, teachers, rooms, rules, curatorHours, curatorAssignments, days, curatorCount, curatorForms, curatorForm: curatorForms.value[0], error, notice, curatorNotice, curatorError, scope, form, courses, calendarMonth, calendarTitle, calendarDays, breakGroupId, breakGroup, groupBreaks, breakRangeStart, breakCalendarMonth, breakCalendarTitle, breakCalendarDays, selectBreakGroup, toggleGroupBreak, previousBreakMonth, nextBreakMonth, formatBreakDate, load, createRule, removeRule, createCuratorHours, createCuratorHour, removeCuratorHour, toggleDayOff, previousMonth, nextMonth, scopeLabel, termLabel, modeLabel, availableCuratorRooms };
} }).mount('#app');
