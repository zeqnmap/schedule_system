const { createApp, ref, computed, onMounted, watch } = Vue;
createApp({ setup() {
    const groups = ref([]), teachers = ref([]), rooms = ref([]), plans = ref([]), schedule = ref([]), allSchedule = ref([]), curatorHours = ref([]), curatorOverrides = ref([]), daysOff = ref([]), groupBreakDays = ref([]), terms = ref([]), academicYears = ref([]);
    const selectedAcademicYearId = ref(Number(localStorage.getItem('edusync-academic-year-id')) || null);
    const selectedGroupId = ref(null), selectedTermId = ref(null), selectedWeek = ref(1), selectedDate = ref(''), isArchived = ref(false), isArchivedAll = ref(false), isGenerating = ref(false);
    const modalMode = ref(null), form = ref({}), selectedPlanId = ref(null), activeCardId = ref(null), curatorEdit = ref(null), generationScope = ref(null), errorMessage = ref('');
    let scheduleRequestId = 0;
    let archiveRequestId = 0;
    const currentGroup = computed(() => groups.value.find(gr => Number(gr.id) === Number(selectedGroupId.value)));
    const currentGroupNumber = computed(() => currentGroup.value?.number || '');
    const currentGroupHasSaturday = computed(() => Boolean(currentGroup.value?.has_saturday));
    const currentGroupWeeklyHours = computed(() => currentGroup.value?.weekly_hours || 30);
    const currentGroupSemesterWeeks = computed(() => currentGroup.value?.semester_weeks || 20);
    const currentGroupTerms = computed(() => terms.value
        .filter(term => Number(term.group_id) === Number(selectedGroupId.value))
        .sort((a, b) => Number(a.term_number || 0) - Number(b.term_number || 0)));
    const currentTerm = computed(() => currentGroupTerms.value.find(term => Number(term.id) === Number(selectedTermId.value)));
    const currentTermWeeks = computed(() => { const term = currentTerm.value; return term ? Array.from({ length: term.weeks }, (_, index) => term.start_week + index) : Array.from({ length: currentGroupSemesterWeeks.value }, (_, index) => index + 1); });
    const currentAcademicYear = computed(() => academicYears.value.find(year => Number(year.id) === Number(selectedAcademicYearId.value)));
    const localDate = value => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
    const weekForDate = value => {
        const year = currentAcademicYear.value;
        if (!year || !value) return 1;
        const start = new Date(`${year.start_date}T00:00:00`);
        const monday = new Date(start);
        monday.setDate(start.getDate() - ((start.getDay() + 6) % 7));
        return Math.max(1, Math.floor((new Date(`${value}T00:00:00`) - monday) / 604800000) + 1);
    };
    const dateForWeek = week => {
        const year = currentAcademicYear.value;
        if (!year) return '';
        const start = new Date(`${year.start_date}T00:00:00`), monday = new Date(start);
        monday.setDate(start.getDate() - ((start.getDay() + 6) % 7) + (Number(week) - 1) * 7);
        return localDate(monday);
    };
    const calendarDateForDay = day => {
        const year = currentAcademicYear.value;
        if (!year) return null;
        const start = new Date(`${year.start_date}T00:00:00`);
        const monday = new Date(start);
        monday.setDate(start.getDate() - ((start.getDay() + 6) % 7) + (Number(selectedWeek.value) - 1) * 7 + day - 1);
        return monday;
    };
    const formatDayDate = day => {
        const value = calendarDateForDay(day);
        return value ? value.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) : '';
    };
    const isBeforeTermStart = day => {
        const value = calendarDateForDay(day), term = currentTerm.value;
        return Boolean(value && term && value < new Date(`${term.start_date}T00:00:00`));
    };
    const currentWeekActualHours = computed(() => schedule.value.filter(e => e.status !== 'canceled').length);
    const showSaturday = computed(() => currentGroupHasSaturday.value || schedule.value.some(e => e.day_of_week === 6));
    const groupPlans = computed(() => plans.value.filter(p => Number(p.group_id) === Number(selectedGroupId.value) && (!selectedTermId.value || Number(p.term_id) === Number(selectedTermId.value))));
    const loadCuratorOverrides = async () => { if (!selectedGroupId.value) { curatorOverrides.value = []; return; } const response = await fetch(`/group-curator-hour-overrides/?group_id=${selectedGroupId.value}`); curatorOverrides.value = response.ok ? await response.json() : []; };
    const loadCalendarBlocks = async () => {
        if (!selectedAcademicYearId.value || !selectedGroupId.value) { daysOff.value = []; groupBreakDays.value = []; return; }
        const [daysResponse, breaksResponse] = await Promise.all([
            fetch(`/academic-days-off/?academic_year_id=${selectedAcademicYearId.value}`),
            fetch(`/group-break-days/?group_id=${selectedGroupId.value}&academic_year_id=${selectedAcademicYearId.value}`),
        ]);
        daysOff.value = daysResponse.ok ? await daysResponse.json() : [];
        groupBreakDays.value = breaksResponse.ok ? await breaksResponse.json() : [];
    };
    const fetchData = async () => { const yRes = await fetch('/academic-years/'); if (yRes.ok) { academicYears.value = await yRes.json(); if (!academicYears.value.some(y => Number(y.id) === Number(selectedAcademicYearId.value))) selectedAcademicYearId.value = academicYears.value.find(y => y.is_active)?.id || academicYears.value[0]?.id || null; } const yearParam = selectedAcademicYearId.value ? `?academic_year_id=${selectedAcademicYearId.value}` : ''; const [gRes, tRes, rRes, pRes, cRes, termRes] = await Promise.all([fetch('/groups/'), fetch('/teachers/'), fetch('/rooms/'), fetch('/course_plans/'), fetch('/curator-hours/'), fetch(`/group-terms/${yearParam}`)]); if (gRes.ok) { groups.value = await gRes.json(); if (groups.value.length && !selectedGroupId.value) selectedGroupId.value = groups.value[0].id; } if (tRes.ok) teachers.value = await tRes.json(); if (rRes.ok) rooms.value = await rRes.json(); if (pRes.ok) plans.value = await pRes.json(); if (cRes.ok) curatorHours.value = await cRes.json(); if (termRes.ok) terms.value = await termRes.json(); selectedTermId.value = currentGroupTerms.value[0]?.id || null; await Promise.all([loadCuratorOverrides(), loadCalendarBlocks()]); };
    const selectAcademicYear = async id => { if (!id || Number(id) === Number(selectedAcademicYearId.value)) return; const res = await fetch(`/academic-years/${id}/activate`, { method: 'POST' }); if (!res.ok) return; selectedAcademicYearId.value = Number(id); localStorage.setItem('edusync-academic-year-id', String(id)); selectedTermId.value = null; await fetchData(); await fetchSchedule(); await fetchArchiveStatus(); };
    const fetchSchedule = async () => {
        const groupId = Number(selectedGroupId.value), termId = Number(selectedTermId.value), week = Number(selectedWeek.value);
        const requestId = ++scheduleRequestId;
        if (!groupId || !termId) { schedule.value = []; allSchedule.value = []; return; }
        const [res, allRes] = await Promise.all([
            fetch(`/schedule/?group_id=${groupId}&week_number=${week}&term_id=${termId}`),
            fetch(`/schedule/?week_number=${week}`),
        ]);
        const [termSchedule, weekSchedule] = await Promise.all([
            res.ok ? res.json() : [],
            allRes.ok ? allRes.json() : [],
        ]);
        // Пользователь мог уже выбрать другой семестр или дату, пока шёл запрос.
        if (requestId !== scheduleRequestId || groupId !== Number(selectedGroupId.value) || termId !== Number(selectedTermId.value) || week !== Number(selectedWeek.value)) return;
        schedule.value = termSchedule;
        allSchedule.value = weekSchedule;
    };
    const fetchArchiveStatus = async () => {
        const groupId = Number(selectedGroupId.value), week = Number(selectedWeek.value);
        const requestId = ++archiveRequestId;
        if (!groupId) { isArchived.value = false; isArchivedAll.value = false; return; }
        const [res, allRes] = await Promise.all([
            fetch(`/archived-weeks/status?group_id=${groupId}&week_number=${week}`),
            fetch(`/archived-weeks/status-all?week_number=${week}`),
        ]);
        const [groupStatus, allStatus] = await Promise.all([
            res.ok ? res.json() : null,
            allRes.ok ? allRes.json() : null,
        ]);
        if (requestId !== archiveRequestId || groupId !== Number(selectedGroupId.value) || week !== Number(selectedWeek.value)) return;
        isArchived.value = Boolean(groupStatus?.is_archived);
        isArchivedAll.value = Boolean(allStatus?.is_archived);
    };
    const changeWeeklyHoursPrompt = async () => { const input = prompt(`Введите норму часов в неделю для Группы ${currentGroupNumber.value}:`, currentGroupWeeklyHours.value); if (input === null) return; const parsed = parseInt(input, 10); if (!isNaN(parsed) && parsed >= 2) { const res = await fetch(`/groups/${selectedGroupId.value}/set-weekly-hours`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ weekly_hours: parsed }) }); if (res.ok) currentGroup.value.weekly_hours = (await res.json()).weekly_hours; } };
    const toggleSaturdayForGroup = async () => { const res = await fetch(`/groups/${selectedGroupId.value}/toggle-saturday`, { method: 'POST' }); if (res.ok) { currentGroup.value.has_saturday = (await res.json()).has_saturday; await fetchSchedule(); } };
    const toggleArchive = async () => { const res = await fetch('/archived-weeks/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_id: selectedGroupId.value, week_number: selectedWeek.value }) }); if (res.ok) { isArchived.value = (await res.json()).is_archived; await fetchArchiveStatus(); } };
    const toggleArchiveAll = async () => { const res = await fetch('/archived-weeks/toggle-all', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ week_number: selectedWeek.value }) }); if (res.ok) { const data = await res.json(); isArchivedAll.value = data.is_archived; await fetchArchiveStatus(); } };
    const selectTerm = termId => {
        const term = currentGroupTerms.value.find(item => Number(item.id) === Number(termId));
        if (!term) return;
        selectedTermId.value = term.id;
        selectedWeek.value = Number(term.start_week || 1);
        selectedDate.value = term.start_date;
        modalMode.value = null;
        errorMessage.value = '';
    };
    watch(selectedGroupId, async () => { selectedTermId.value = currentGroupTerms.value[0]?.id || null; await Promise.all([loadCuratorOverrides(), loadCalendarBlocks()]); });
    watch(selectedTermId, () => { if (currentTermWeeks.value.length) { selectedWeek.value = currentTermWeeks.value[0]; selectedDate.value = currentTerm.value?.start_date || dateForWeek(selectedWeek.value); } fetchSchedule(); fetchArchiveStatus(); });
    watch(selectedDate, value => { if (!value) return; const week = weekForDate(value); if (currentTermWeeks.value.includes(week)) selectedWeek.value = week; });
    watch(selectedWeek, () => { fetchSchedule(); fetchArchiveStatus(); });
    onMounted(async () => { await fetchData(); await fetchSchedule(); await fetchArchiveStatus(); });
    const getDayName = dayNum => ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'][dayNum - 1];
    const getTeacherName = id => teachers.value.find(t => t.id === id)?.name || `Преп. ${id}`;
    const getTeacherNames = entry => [entry.teacher_id, entry.teacher2_id].filter(Boolean).map(getTeacherName).join(' / ');
    const getEntries = (day, slot) => schedule.value.filter(e => e.day_of_week === day && e.time_slot === slot);
    const hasActiveEntry = (day, slot) => getEntries(day, slot).some(e => e.status !== 'canceled');
    const curatorHourForDate = (day, slot) => {
        const hour = curatorHours.value.filter(item => item.day_of_week === day && slot >= item.time_slot && slot < item.time_slot + item.duration)[0];
        if (!hour) return null;
        const scheduleDate = localDate(calendarDateForDay(day));
        if (daysOff.value.some(item => item.day_date === scheduleDate) || groupBreakDays.value.some(item => item.day_date === scheduleDate)) return null;
        const override = curatorOverrides.value.find(item => Number(item.curator_hour_id) === Number(hour.id) && item.schedule_date === scheduleDate);
        return {
            ...hour,
            source_id: hour.id,
            schedule_date: scheduleDate,
            is_hidden: Boolean(override?.is_hidden),
            teacher_id: override?.teacher_id ?? hour.teacher_id ?? currentGroup.value?.curator_teacher_id ?? null,
            room_name: override?.room_name ?? hour.room_name ?? currentGroup.value?.curator_room_name ?? '',
        };
    };
    const curatorHourAt = (day, slot) => { const hour = curatorHourForDate(day, slot); return hour?.is_hidden ? null : hour; };
    const hiddenCuratorHourAt = (day, slot) => { const hour = curatorHourForDate(day, slot); return hour?.is_hidden ? hour : null; };
    const curatorTeacherName = hour => hour?.teacher_id ? getTeacherName(hour.teacher_id) : 'Куратор не указан';
    const curatorRoomName = hour => hour?.room_name || '—';
    const curatorCardId = hour => `curator-${hour.source_id}-${hour.schedule_date}`;
    const openCuratorEdit = hour => { if (isArchived.value) return; curatorEdit.value = { curator_hour_id: hour.source_id, schedule_date: hour.schedule_date, hour_type: hour.hour_type, teacher_id: hour.teacher_id || null, room_name: hour.room_name || '' }; activeCardId.value = null; };
    const saveCuratorEdit = async () => {
        if (!curatorEdit.value) return;
        const response = await fetch(`/group-curator-hour-overrides/${selectedGroupId.value}/${curatorEdit.value.curator_hour_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ schedule_date: curatorEdit.value.schedule_date, teacher_id: curatorEdit.value.teacher_id || null, room_name: curatorEdit.value.room_name || null, is_hidden: false }) });
        if (!response.ok) { const data = await response.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось сохранить изменения'; return; }
        curatorEdit.value = null; await loadCuratorOverrides();
    };
    const removeCuratorHourFromGroup = async hour => {
        if (!confirm(`Удалить «${hour.hour_type === 'information' ? 'Информационный' : 'Кураторский'} час» только из расписания группы ${currentGroupNumber.value}?`)) return;
        const response = await fetch(`/group-curator-hour-overrides/${selectedGroupId.value}/${hour.source_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ schedule_date: hour.schedule_date, is_hidden: true }) });
        if (!response.ok) { const data = await response.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось удалить час'; return; }
        activeCardId.value = null; await loadCuratorOverrides();
    };
    const restoreCuratorHourForGroup = async hour => {
        const response = await fetch(`/group-curator-hour-overrides/${selectedGroupId.value}/${hour.source_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ schedule_date: hour.schedule_date, is_hidden: false }) });
        if (!response.ok) { const data = await response.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось вернуть час'; return; }
        await loadCuratorOverrides();
    };
    const updateRoomFromTeacher = () => { const teacher = teachers.value.find(t => t.id === form.value.teacher_id); if (teacher) form.value.room_name = teacher.room_name || ''; };
    const teacherWorksOnDay = teacher => String(teacher?.working_days || '1,2,3,4,5').split(',').map(Number).includes(Number(form.value.day_of_week)) && teacher.is_active !== false && !teacher.on_vacation && !teacher.is_sick && !String(teacher?.vacation_weeks || '').split(',').map(Number).includes(Number(selectedWeek.value));
    const sameSlotEntries = computed(() => allSchedule.value.filter(entry => entry.status !== 'canceled' && entry.day_of_week === Number(form.value.day_of_week) && entry.time_slot === Number(form.value.time_slot) && entry.id !== form.value.id));
    const isPhysicalEducation = subject => /физ|спорт|здоров/i.test(subject || '');
    const teacherBusy = teacherId => sameSlotEntries.value.some(entry => [entry.teacher_id, entry.teacher2_id].includes(teacherId));
    const roomBusy = roomName => sameSlotEntries.value.some(entry => entry.room_name === roomName);
    const availableTeachers = computed(() => teachers.value.filter(teacher => teacherWorksOnDay(teacher) && !teacherBusy(teacher.id)));
    const availableTeachers2 = computed(() => availableTeachers.value.filter(teacher => teacher.id !== Number(form.value.teacher_id)));
    const availableRooms = computed(() => {
        const rooms = teachers.value.filter(teacher => {
            if (!teacherWorksOnDay(teacher) || !teacher.room_name) return false;
            if (!roomBusy(teacher.room_name)) return true;
            const peCount = sameSlotEntries.value.filter(entry => entry.room_name === teacher.room_name && isPhysicalEducation(entry.subject_name)).length;
            return isPhysicalEducation(form.value.subject_name) && peCount < 2;
        }).map(teacher => [teacher.room_name, { name: teacher.room_name }]);
        return [...new Map(rooms).values()].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    });
    const onPlanChange = () => { const plan = plans.value.find(p => p.id === selectedPlanId.value); if (plan) { form.value.subject_name = plan.subject_name; form.value.teacher_id = plan.teacher_id; form.value.teacher2_id = plan.teacher2_id; updateRoomFromTeacher(); } };
    const openEditModal = entry => { if (isArchived.value) return; modalMode.value = 'edit'; form.value = { ...entry }; const matchedPlan = plans.value.find(p => p.group_id === entry.group_id && p.term_id === entry.term_id && p.subject_name === entry.subject_name); selectedPlanId.value = matchedPlan?.id || null; };
    const toggleCardActions = entryId => { if (!isArchived.value) activeCardId.value = activeCardId.value === entryId ? null : entryId; };
    const openCreateModal = (day, slot) => { if (isArchived.value || curatorHourAt(day, slot)) return; modalMode.value = 'create'; selectedPlanId.value = null; const teacher = teachers.value[0]; form.value = { week_number: selectedWeek.value, term_id: selectedTermId.value, day_of_week: day, time_slot: slot, teacher_id: teacher?.id || null, teacher2_id: null, group_id: Number(selectedGroupId.value), subject_name: '', status: 'planned', room_name: teacher?.room_name || 'Не назначен' }; };
    const saveEntry = async () => { const url = modalMode.value === 'edit' ? `/schedule/${form.value.id}` : '/schedule/'; const response = await fetch(url, { method: modalMode.value === 'edit' ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value) }); if (!response.ok) { const data = await response.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось сохранить занятие'; return; } await fetchSchedule(); modalMode.value = null; };
    const deleteEntry = async () => { if (!confirm('ВНИМАНИЕ! Это полностью сотрет урок из базы. Продолжить?')) return; await fetch(`/schedule/${form.value.id}`, { method: 'DELETE' }); await fetchSchedule(); modalMode.value = null; };
    const cancelEntry = async () => { await fetch(`/schedule/${form.value.id}/cancel`, { method: 'POST' }); await fetchSchedule(); modalMode.value = null; };
    const restoreEntry = async () => { await fetch(`/schedule/${form.value.id}/restore`, { method: 'POST' }); await fetchSchedule(); modalMode.value = null; };
    const deleteCardEntry = async entry => { if (!confirm('Удалить занятие без возможности восстановления?')) return; const response = await fetch(`/schedule/${entry.id}`, { method: 'DELETE' }); if (response.ok) { activeCardId.value = null; await fetchSchedule(); } };
    const cancelCardEntry = async entry => { const response = await fetch(`/schedule/${entry.id}/cancel`, { method: 'POST' }); if (response.ok) { activeCardId.value = null; await fetchSchedule(); } };
    const restoreCardEntry = async entry => { const response = await fetch(`/schedule/${entry.id}/restore`, { method: 'POST' }); if (response.ok) { activeCardId.value = null; await fetchSchedule(); } };
    const generationLabel = scope => ({ 1: '1-й семестр', 2: '2-й семестр', all: 'все открытые семестры' })[scope] || 'все открытые семестры';
    const openGenerationDialog = () => { generationScope.value = 'all'; };
    const generateScheduleAll = async () => {
        const scope = generationScope.value || 'all';
        if (!confirm(`Сгенерировать расписание: ${generationLabel(scope)}?\nИзменения затронут только выбранные открытые семестры.`)) return;
        isGenerating.value = true; errorMessage.value = ''; generationScope.value = null;
        const termQuery = scope === 'all' ? '' : `&term_number=${scope}`;
        try {
            let res = await fetch(`/generate_schedule/?approve_adjustments=false${termQuery}`, { method: 'POST' }); let data = await res.json();
            if (!res.ok && data.detail && (data.detail.includes('НЕ ХВАТАЕТ') || data.detail.includes('ограничения несовместимы') || data.detail.includes('Математический тупик'))) {
                const action = confirm(data.detail + '\n\nНажмите OK, чтобы исправить автоматически безопасным режимом (без накладок), или Отмена для ручного исправления.');
                if (action) { res = await fetch(`/generate_schedule/?approve_adjustments=true${termQuery}`, { method: 'POST' }); data = await res.json(); }
            }
            if (res.ok) { await fetchSchedule(); alert(data.message); } else errorMessage.value = data.detail || 'Неизвестная ошибка сервера';
        } catch (e) { errorMessage.value = 'Ошибка связи (Timeout).'; }
        isGenerating.value = false;
    };
    const exportPdf = day => {
        const scheduleDate = localDate(calendarDateForDay(day));
        window.open(`/export/schedule.pdf?week_number=${selectedWeek.value}&day=${day}&schedule_date=${encodeURIComponent(scheduleDate)}&academic_year_id=${selectedAcademicYearId.value}`, '_blank');
    };
    const exportTeachersPdf = day => {
        const scheduleDate = localDate(calendarDateForDay(day));
        window.open(`/export/teachers.pdf?week_number=${selectedWeek.value}&day=${day}&schedule_date=${encodeURIComponent(scheduleDate)}&academic_year_id=${selectedAcademicYearId.value}`, '_blank');
    };
    return { groups, teachers, rooms, plans, schedule, allSchedule, curatorHours, curatorOverrides, daysOff, groupBreakDays, terms, academicYears, selectedAcademicYearId, selectAcademicYear, selectedGroupId, selectedTermId, selectedWeek, selectedDate, isArchived, isArchivedAll, isGenerating, modalMode, form, selectedPlanId, activeCardId, curatorEdit, generationScope, groupPlans, errorMessage, currentGroupTerms, currentTermWeeks, currentTerm, selectTerm, availableTeachers, availableTeachers2, availableRooms, getDayName, getTeacherName, getTeacherNames, getEntries, hasActiveEntry, curatorHourAt, hiddenCuratorHourAt, curatorTeacherName, curatorRoomName, curatorCardId, openCuratorEdit, saveCuratorEdit, removeCuratorHourFromGroup, restoreCuratorHourForGroup, onPlanChange, updateRoomFromTeacher, openCreateModal, openEditModal, toggleCardActions, deleteCardEntry, cancelCardEntry, restoreCardEntry, saveEntry, deleteEntry, cancelEntry, restoreEntry, toggleArchive, toggleArchiveAll, currentGroupNumber, currentGroupHasSaturday, currentGroupWeeklyHours, currentWeekActualHours, currentGroupSemesterWeeks, showSaturday, toggleSaturdayForGroup, changeWeeklyHoursPrompt, generationLabel, openGenerationDialog, generateScheduleAll, exportPdf, exportTeachersPdf, formatDayDate, isBeforeTermStart };
} }).component('searchable-select', SearchableSelect).mount('#app');
