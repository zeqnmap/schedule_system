const { createApp, ref, computed, onMounted, watch } = Vue;
createApp({ setup() {
    const groups = ref([]), teachers = ref([]), plans = ref([]), schedule = ref([]), allSchedule = ref([]), curatorHours = ref([]), terms = ref([]);
    const selectedGroupId = ref(null), selectedTermId = ref(null), selectedWeek = ref(1), isArchived = ref(false), isArchivedAll = ref(false), isGenerating = ref(false);
    const modalMode = ref(null), form = ref({}), selectedPlanId = ref(null), errorMessage = ref('');
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
    const currentWeekActualHours = computed(() => schedule.value.filter(e => e.status !== 'canceled').length);
    const showSaturday = computed(() => currentGroupHasSaturday.value || schedule.value.some(e => e.day_of_week === 6));
    const groupPlans = computed(() => plans.value.filter(p => Number(p.group_id) === Number(selectedGroupId.value) && (!selectedTermId.value || Number(p.term_id) === Number(selectedTermId.value))));
    const fetchData = async () => { const [gRes, tRes, pRes, cRes, termRes] = await Promise.all([fetch('/groups/'), fetch('/teachers/'), fetch('/course_plans/'), fetch('/curator-hours/'), fetch('/group-terms/')]); if (gRes.ok) { groups.value = await gRes.json(); if (groups.value.length && !selectedGroupId.value) selectedGroupId.value = groups.value[0].id; } if (tRes.ok) teachers.value = await tRes.json(); if (pRes.ok) plans.value = await pRes.json(); if (cRes.ok) curatorHours.value = await cRes.json(); if (termRes.ok) terms.value = await termRes.json(); if (!selectedTermId.value && currentGroupTerms.value.length) selectedTermId.value = currentGroupTerms.value[0].id; };
    const fetchSchedule = async () => { if (!selectedGroupId.value) return; const termParam = selectedTermId.value ? `&term_id=${selectedTermId.value}` : ''; const [res, allRes] = await Promise.all([fetch(`/schedule/?group_id=${selectedGroupId.value}&week_number=${selectedWeek.value}${termParam}`), fetch(`/schedule/?week_number=${selectedWeek.value}`)]); if (res.ok) schedule.value = await res.json(); if (allRes.ok) allSchedule.value = await allRes.json(); };
    const fetchArchiveStatus = async () => { if (!selectedGroupId.value) return; const [res, allRes] = await Promise.all([fetch(`/archived-weeks/status?group_id=${selectedGroupId.value}&week_number=${selectedWeek.value}`), fetch(`/archived-weeks/status-all?week_number=${selectedWeek.value}`)]); if (res.ok) isArchived.value = (await res.json()).is_archived; if (allRes.ok) isArchivedAll.value = (await allRes.json()).is_archived; };
    const changeWeeklyHoursPrompt = async () => { const input = prompt(`Введите норму часов в неделю для Группы ${currentGroupNumber.value}:`, currentGroupWeeklyHours.value); if (input === null) return; const parsed = parseInt(input, 10); if (!isNaN(parsed) && parsed >= 2) { const res = await fetch(`/groups/${selectedGroupId.value}/set-weekly-hours`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ weekly_hours: parsed }) }); if (res.ok) currentGroup.value.weekly_hours = (await res.json()).weekly_hours; } };
    const toggleSaturdayForGroup = async () => { const res = await fetch(`/groups/${selectedGroupId.value}/toggle-saturday`, { method: 'POST' }); if (res.ok) { currentGroup.value.has_saturday = (await res.json()).has_saturday; await fetchSchedule(); } };
    const toggleArchive = async () => { const res = await fetch('/archived-weeks/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_id: selectedGroupId.value, week_number: selectedWeek.value }) }); if (res.ok) { isArchived.value = (await res.json()).is_archived; await fetchArchiveStatus(); } };
    const toggleArchiveAll = async () => { const res = await fetch('/archived-weeks/toggle-all', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ week_number: selectedWeek.value }) }); if (res.ok) { const data = await res.json(); isArchivedAll.value = data.is_archived; await fetchArchiveStatus(); } };
    const selectTerm = termId => {
        const term = currentGroupTerms.value.find(item => Number(item.id) === Number(termId));
        if (!term) return;
        selectedTermId.value = term.id;
        selectedWeek.value = Number(term.start_week || 1);
        modalMode.value = null;
        errorMessage.value = '';
    };
    watch(selectedGroupId, () => { selectedTermId.value = currentGroupTerms.value[0]?.id || null; });
    watch(selectedTermId, () => { if (currentTermWeeks.value.length) selectedWeek.value = currentTermWeeks.value[0]; fetchSchedule(); fetchArchiveStatus(); });
    watch(selectedWeek, () => { fetchSchedule(); fetchArchiveStatus(); });
    onMounted(async () => { await fetchData(); await fetchSchedule(); await fetchArchiveStatus(); });
    const getDayName = dayNum => ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'][dayNum - 1];
    const getTeacherName = id => teachers.value.find(t => t.id === id)?.name || `Преп. ${id}`;
    const getTeacherNames = entry => [entry.teacher_id, entry.teacher2_id].filter(Boolean).map(getTeacherName).join(' / ');
    const getEntries = (day, slot) => schedule.value.filter(e => e.day_of_week === day && e.time_slot === slot);
    const hasActiveEntry = (day, slot) => getEntries(day, slot).some(e => e.status !== 'canceled');
    const curatorHourAt = (day, slot) => curatorHours.value.filter(item => (Number(item.group_id) === 0 || Number(item.group_id) === Number(selectedGroupId.value)) && item.day_of_week === day && slot >= item.time_slot && slot < item.time_slot + item.duration).sort((a, b) => Number(a.group_id || 0) - Number(b.group_id || 0))[0];
    const curatorTeacherName = hour => { const teacherId = hour?.teacher_id || (Number(hour?.group_id) === 0 ? currentGroup.value?.curator_teacher_id : null); return teacherId ? getTeacherName(teacherId) : 'Куратор не указан'; };
    const curatorRoomName = hour => hour?.room_name || (Number(hour?.group_id) === 0 ? currentGroup.value?.curator_room_name : null) || '—';
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
    const openCreateModal = (day, slot) => { if (isArchived.value || curatorHourAt(day, slot)) return; modalMode.value = 'create'; selectedPlanId.value = null; const teacher = teachers.value[0]; form.value = { week_number: selectedWeek.value, term_id: selectedTermId.value, day_of_week: day, time_slot: slot, teacher_id: teacher?.id || null, teacher2_id: null, group_id: Number(selectedGroupId.value), subject_name: '', status: 'planned', room_name: teacher?.room_name || 'Не назначен' }; };
    const saveEntry = async () => { const url = modalMode.value === 'edit' ? `/schedule/${form.value.id}` : '/schedule/'; const response = await fetch(url, { method: modalMode.value === 'edit' ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value) }); if (!response.ok) { const data = await response.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось сохранить занятие'; return; } await fetchSchedule(); modalMode.value = null; };
    const deleteEntry = async () => { if (!confirm('ВНИМАНИЕ! Это полностью сотрет урок из базы. Продолжить?')) return; await fetch(`/schedule/${form.value.id}`, { method: 'DELETE' }); await fetchSchedule(); modalMode.value = null; };
    const cancelEntry = async () => { await fetch(`/schedule/${form.value.id}/cancel`, { method: 'POST' }); await fetchSchedule(); modalMode.value = null; };
    const restoreEntry = async () => { await fetch(`/schedule/${form.value.id}/restore`, { method: 'POST' }); await fetchSchedule(); modalMode.value = null; };
    const generateScheduleAll = async () => { if (!confirm('Запустить глобальную генерацию на ВЕСЬ СЕМЕСТР для всех групп?\nЭто займет до 60 секунд.')) return; isGenerating.value = true; errorMessage.value = ''; try { let res = await fetch('/generate_schedule/', { method: 'POST' }); let data = await res.json(); if (!res.ok && data.detail && (data.detail.includes('НЕ ХВАТАЕТ') || data.detail.includes('ограничения несовместимы') || data.detail.includes('Математический тупик'))) { const action = confirm(data.detail + '\n\nНажмите OK, чтобы исправить автоматически безопасным режимом (без накладок), или Отмена для ручного исправления.'); if (action) { res = await fetch('/generate_schedule/?approve_adjustments=true', { method: 'POST' }); data = await res.json(); } } if (res.ok) { await fetchSchedule(); alert(data.message); } else errorMessage.value = data.detail || 'Неизвестная ошибка сервера'; } catch (e) { errorMessage.value = 'Ошибка связи (Timeout).'; } isGenerating.value = false; };
    const normalizeExportDate = value => {
        const iso = String(value || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (iso) return `${iso[1]}-${iso[2]}-${iso[3]}`;
        const ru = String(value || '').trim().match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
        return ru ? `${ru[3]}-${ru[2]}-${ru[1]}` : null;
    };
    const exportPdf = day => {
        const today = new Date().toISOString().slice(0, 10);
        const enteredDate = prompt('Введите дату: ДД.ММ.ГГГГ или ГГГГ-ММ-ДД', today);
        if (enteredDate === null) return;
        const selectedDate = normalizeExportDate(enteredDate);
        if (!selectedDate) { alert('Введите дату в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.'); return; }
        window.open(`/export/schedule.pdf?week_number=${selectedWeek.value}&day=${day}&schedule_date=${encodeURIComponent(selectedDate)}`, '_blank');
    };
    const exportTeachersPdf = day => {
        const today = new Date().toISOString().slice(0, 10);
        const enteredDate = prompt('Введите дату: ДД.ММ.ГГГГ или ГГГГ-ММ-ДД', today);
        if (enteredDate === null) return;
        const selectedDate = normalizeExportDate(enteredDate);
        if (!selectedDate) { alert('Введите дату в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.'); return; }
        window.open(`/export/teachers.pdf?week_number=${selectedWeek.value}&day=${day}&schedule_date=${encodeURIComponent(selectedDate)}`, '_blank');
    };
    return { groups, teachers, plans, schedule, allSchedule, curatorHours, terms, selectedGroupId, selectedTermId, selectedWeek, isArchived, isArchivedAll, isGenerating, modalMode, form, selectedPlanId, groupPlans, errorMessage, currentGroupTerms, currentTermWeeks, selectTerm, availableTeachers, availableTeachers2, availableRooms, getDayName, getTeacherName, getTeacherNames, getEntries, hasActiveEntry, curatorHourAt, curatorTeacherName, curatorRoomName, onPlanChange, updateRoomFromTeacher, openEditModal, openCreateModal, saveEntry, deleteEntry, cancelEntry, restoreEntry, toggleArchive, toggleArchiveAll, currentGroupNumber, currentGroupHasSaturday, currentGroupWeeklyHours, currentWeekActualHours, currentGroupSemesterWeeks, showSaturday, toggleSaturdayForGroup, changeWeeklyHoursPrompt, generateScheduleAll, exportPdf, exportTeachersPdf };
} }).component('searchable-select', SearchableSelect).mount('#app');
