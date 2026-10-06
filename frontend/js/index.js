const { createApp, ref, computed, onMounted, watch } = Vue;
createApp({ setup() {
    const groups = ref([]), teachers = ref([]), rooms = ref([]), plans = ref([]), schedule = ref([]), allSchedule = ref([]), curatorHours = ref([]), curatorOverrides = ref([]), daysOff = ref([]), groupBreakDays = ref([]), terms = ref([]), academicYears = ref([]);
    const selectedAcademicYearId = ref(Number(localStorage.getItem('edusync-academic-year-id')) || null);
    const selectedGroupId = ref(null), selectedTermId = ref(null), selectedWeek = ref(1), selectedDate = ref(''), isArchived = ref(false), isArchivedAll = ref(false), isGenerating = ref(false);
    const generationProgress = ref(0), generationCompleted = ref(0), generationTotal = ref(0);
    watch(isGenerating, active => document.body.classList.toggle('generation-active', active));
    const modalMode = ref(null), form = ref({}), selectedPlanId = ref(null), activeCardId = ref(null), curatorEdit = ref(null), generationScope = ref(null), generationSettings = ref([]), scheduleAction = ref(null), scheduleActionMonth = ref(''), errorMessage = ref('');
    const slotOptions = ref({ plans: [], teachers: [], rooms: [] }), slotOptionsLoading = ref(false);
    let slotOptionsRequestId = 0;
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
    const groupPlans = computed(() => plans.value.filter(p => Number(p.group_id) === Number(form.value.group_id) && Number(p.term_id) === Number(form.value.term_id) && slotOptions.value.plans.includes(p.id)));
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
    const fetchData = async () => { const yRes = await fetch('/academic-years/'); if (yRes.ok) { academicYears.value = await yRes.json(); if (!academicYears.value.some(y => Number(y.id) === Number(selectedAcademicYearId.value))) selectedAcademicYearId.value = academicYears.value.find(y => y.is_active)?.id || academicYears.value[0]?.id || null; } const yearParam = selectedAcademicYearId.value ? `?academic_year_id=${selectedAcademicYearId.value}` : ''; const planParam = selectedAcademicYearId.value ? `?academic_year_id=${selectedAcademicYearId.value}&limit=1000` : '?limit=1000'; const [gRes, tRes, rRes, pRes, cRes, termRes] = await Promise.all([fetch('/groups/'), fetch('/teachers/'), fetch('/rooms/'), fetch(`/course_plans/${planParam}`), fetch('/curator-hours/'), fetch(`/group-terms/${yearParam}`)]); if (gRes.ok) { groups.value = await gRes.json(); if (groups.value.length && !selectedGroupId.value) selectedGroupId.value = groups.value[0].id; } if (tRes.ok) teachers.value = await tRes.json(); if (rRes.ok) rooms.value = await rRes.json(); if (pRes.ok) plans.value = await pRes.json(); if (cRes.ok) curatorHours.value = await cRes.json(); if (termRes.ok) terms.value = await termRes.json(); selectedTermId.value = currentGroupTerms.value[0]?.id || null; await Promise.all([loadCuratorOverrides(), loadCalendarBlocks()]); };
    const selectAcademicYear = async id => { if (!id || Number(id) === Number(selectedAcademicYearId.value)) return; const res = await fetch(`/academic-years/${id}/activate`, { method: 'POST' }); if (!res.ok) return; selectedAcademicYearId.value = Number(id); localStorage.setItem('edusync-academic-year-id', String(id)); selectedTermId.value = null; await fetchData(); await fetchSchedule(); await fetchArchiveStatus(); };
    const fetchSchedule = async () => {
        const groupId = Number(selectedGroupId.value), termId = Number(selectedTermId.value), week = Number(selectedWeek.value);
        const requestId = ++scheduleRequestId;
        if (!groupId || !termId) { schedule.value = []; allSchedule.value = []; return; }
        const [res, allRes] = await Promise.all([
            fetch(`/schedule/?group_id=${groupId}&week_number=${week}&term_id=${termId}`),
            fetch(`/schedule/?week_number=${week}&academic_year_id=${selectedAcademicYearId.value || ''}`),
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
            fetch(`/archived-weeks/status?group_id=${groupId}&week_number=${week}&academic_year_id=${selectedAcademicYearId.value}`),
            fetch(`/archived-weeks/status-all?week_number=${week}&academic_year_id=${selectedAcademicYearId.value}`),
        ]);
        const [groupStatus, allStatus] = await Promise.all([
            res.ok ? res.json() : null,
            allRes.ok ? allRes.json() : null,
        ]);
        if (requestId !== archiveRequestId || groupId !== Number(selectedGroupId.value) || week !== Number(selectedWeek.value)) return;
        isArchived.value = Boolean(groupStatus?.is_archived);
        isArchivedAll.value = Boolean(allStatus?.is_archived);
    };
    const changeWeeklyHoursPrompt = async () => {
        const input = prompt(`Введите норму уроков в неделю для группы ${currentGroupNumber.value} (1–54):`, currentGroupWeeklyHours.value);
        if (input === null) return;
        const parsed = Number(input);
        if (!Number.isInteger(parsed) || parsed < 1 || parsed > 54) { errorMessage.value = 'Укажите целое число от 1 до 54.'; return; }
        const res = await fetch(`/groups/${selectedGroupId.value}/set-weekly-hours`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ weekly_hours: parsed }) });
        if (res.ok) currentGroup.value.weekly_hours = (await res.json()).weekly_hours;
    };
    const toggleSaturdayForGroup = async () => { const res = await fetch(`/groups/${selectedGroupId.value}/toggle-saturday`, { method: 'POST' }); if (res.ok) { currentGroup.value.has_saturday = (await res.json()).has_saturday; await fetchSchedule(); } };
    const toggleArchive = async () => { const res = await fetch('/archived-weeks/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group_id: selectedGroupId.value, week_number: selectedWeek.value, academic_year_id: selectedAcademicYearId.value }) }); if (res.ok) { isArchived.value = (await res.json()).is_archived; await fetchArchiveStatus(); } };
    const toggleArchiveAll = async () => { const res = await fetch('/archived-weeks/toggle-all', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ week_number: selectedWeek.value, academic_year_id: selectedAcademicYearId.value }) }); if (res.ok) { const data = await res.json(); isArchivedAll.value = data.is_archived; await fetchArchiveStatus(); } };
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
    const getRoomNames = entry => [...new Set([entry.room_name, entry.room2_name].filter(Boolean))].join(' / ') || '—';
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
    const removeCuratorHourForAllGroups = async hour => {
        const title = hour.hour_type === 'information' ? 'Информационный' : 'Кураторский';
        if (!confirm(`Удалить «${title} час» ${hour.schedule_date} у всех групп? Это не затронет другие даты.`)) return;
        const response = await fetch(`/curator-hours/${hour.source_id}/hide-for-all?schedule_date=${encodeURIComponent(hour.schedule_date)}&academic_year_id=${selectedAcademicYearId.value}`, { method: 'POST' });
        if (!response.ok) { const data = await response.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось удалить час у всех групп'; return; }
        activeCardId.value = null; await loadCuratorOverrides();
    };
    const restoreCuratorHourForGroup = async hour => {
        const response = await fetch(`/group-curator-hour-overrides/${selectedGroupId.value}/${hour.source_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ schedule_date: hour.schedule_date, is_hidden: false }) });
        if (!response.ok) { const data = await response.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось вернуть час'; return; }
        await loadCuratorOverrides();
    };
    const updateRoomFromTeacher = () => { const teacher = teachers.value.find(t => Number(t.id) === Number(form.value.teacher_id)); if (teacher && availableRooms.value.some(room => room.name === teacher.room_name)) form.value.room_name = teacher.room_name; else if (!availableRooms.value.some(room => room.name === form.value.room_name)) form.value.room_name = availableRooms.value[0]?.name || ''; if (form.value.room2_name === form.value.room_name) form.value.room2_name = null; };
    const teacherWorksOnDay = teacher => String(teacher?.working_days || '1,2,3,4,5').split(',').map(Number).includes(Number(form.value.day_of_week)) && teacher.is_active !== false && !teacher.on_vacation && !teacher.is_sick && !String(teacher?.vacation_weeks || '').split(',').map(Number).includes(Number(selectedWeek.value));
    const sameSlotEntries = computed(() => allSchedule.value.filter(entry => entry.status !== 'canceled' && entry.day_of_week === Number(form.value.day_of_week) && entry.time_slot === Number(form.value.time_slot) && entry.id !== form.value.id));
    const availableTimeSlots = computed(() => {
        const day = Number(form.value.day_of_week);
        if (!day) return [];
        return Array.from({ length: 12 }, (_, index) => index + 1).filter(slot => {
            if (modalMode.value === 'edit' && Number(form.value.time_slot) === slot) return true;
            return !hasActiveEntry(day, slot) && !curatorHourAt(day, slot);
        });
    });
    const teacherBusy = teacherId => sameSlotEntries.value.some(entry => [entry.teacher_id, entry.teacher2_id].includes(teacherId));
    const roomBusy = roomName => sameSlotEntries.value.some(entry => [entry.room_name, entry.room2_name].includes(roomName));
    const availableTeachers = computed(() => teachers.value.filter(teacher => slotOptions.value.teachers.includes(teacher.id) && teacherWorksOnDay(teacher) && !teacherBusy(teacher.id)));
    const availableTeachers2 = computed(() => availableTeachers.value.filter(teacher => teacher.id !== Number(form.value.teacher_id)));
    const availableRooms = computed(() => [...rooms.value, { name: 'Без кабинета' }].filter(room => slotOptions.value.rooms.includes(room.name) && (room.name === 'Без кабинета' || !roomBusy(room.name))).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })));
    const availableRooms2 = computed(() => availableRooms.value.filter(room => room.name !== form.value.room_name && room.name !== 'Без кабинета'));
    const loadSlotOptions = async () => {
        const requestId = ++slotOptionsRequestId;
        slotOptions.value = { plans: [], teachers: [], rooms: [] };
        slotOptionsLoading.value = false;
        if (!modalMode.value || !form.value.group_id || !form.value.term_id || !form.value.day_of_week || !form.value.time_slot) return;
        slotOptionsLoading.value = true;
        const params = new URLSearchParams({ group_id: form.value.group_id, term_id: form.value.term_id, week_number: form.value.week_number, day_of_week: form.value.day_of_week, time_slot: form.value.time_slot });
        if (modalMode.value === 'edit') params.set('exclude_entry_id', form.value.id);
        try {
            const response = await fetch(`/schedule/available-options?${params}`);
            if (!response.ok) throw new Error('Не удалось проверить доступность ресурсов');
            const options = await response.json();
            if (requestId !== slotOptionsRequestId) return;
            slotOptions.value = options;
            if (!groupPlans.value.some(plan => Number(plan.id) === Number(selectedPlanId.value))) { selectedPlanId.value = null; form.value.subject_name = ''; }
            if (!availableTeachers.value.some(teacher => Number(teacher.id) === Number(form.value.teacher_id))) form.value.teacher_id = null;
            if (!availableTeachers2.value.some(teacher => Number(teacher.id) === Number(form.value.teacher2_id))) form.value.teacher2_id = null;
            if (!availableRooms.value.some(room => room.name === form.value.room_name)) form.value.room_name = '';
            if (!availableRooms2.value.some(room => room.name === form.value.room2_name)) form.value.room2_name = null;
        } catch (error) {
            if (requestId === slotOptionsRequestId) errorMessage.value = error.message;
        } finally {
            if (requestId === slotOptionsRequestId) slotOptionsLoading.value = false;
        }
    };
    const syncAvailableResources = () => {
        if (!modalMode.value || modalMode.value === 'edit') return;
        if (!availableTimeSlots.value.includes(Number(form.value.time_slot))) form.value.time_slot = availableTimeSlots.value[0] || null;
        if (!availableTeachers.value.some(teacher => Number(teacher.id) === Number(form.value.teacher_id))) form.value.teacher_id = null;
        if (!availableTeachers2.value.some(teacher => Number(teacher.id) === Number(form.value.teacher2_id))) form.value.teacher2_id = null;
        if (!availableRooms.value.some(room => room.name === form.value.room_name)) form.value.room_name = '';
        if (!availableRooms2.value.some(room => room.name === form.value.room2_name)) form.value.room2_name = null;
    };
    watch(() => [form.value.day_of_week, form.value.time_slot], () => { syncAvailableResources(); loadSlotOptions(); });
    watch(() => form.value.room_name, () => { if (form.value.room2_name === form.value.room_name) form.value.room2_name = null; });
    const onPlanChange = () => { const plan = groupPlans.value.find(p => Number(p.id) === Number(selectedPlanId.value)); if (plan) { form.value.subject_name = plan.subject_name; form.value.teacher_id = availableTeachers.value.some(t => t.id === plan.teacher_id) ? plan.teacher_id : null; form.value.teacher2_id = availableTeachers2.value.some(t => t.id === plan.teacher2_id) ? plan.teacher2_id : null; form.value.room_name = availableRooms.value.some(room => room.name === plan.room_name) ? plan.room_name : ''; form.value.room2_name = availableRooms2.value.some(room => room.name === plan.room2_name) ? plan.room2_name : null; if (!form.value.room_name) updateRoomFromTeacher(); } };
    const openEditModal = entry => { if (isArchived.value) return; modalMode.value = 'edit'; form.value = { ...entry }; const matchedPlan = plans.value.find(p => p.group_id === entry.group_id && p.term_id === entry.term_id && p.subject_name === entry.subject_name); selectedPlanId.value = matchedPlan?.id || null; loadSlotOptions(); };
    const toggleCardActions = entryId => { if (!isArchived.value) activeCardId.value = activeCardId.value === entryId ? null : entryId; };
    const openCreateModal = (day, slot) => { if (isArchived.value || curatorHourAt(day, slot) || hasActiveEntry(day, slot)) return; modalMode.value = 'create'; selectedPlanId.value = null; form.value = { week_number: selectedWeek.value, term_id: selectedTermId.value, day_of_week: day, time_slot: slot, teacher_id: null, teacher2_id: null, group_id: Number(selectedGroupId.value), subject_name: '', status: 'planned', room_name: '', room2_name: null }; loadSlotOptions(); };
    const saveEntry = async () => { if (slotOptionsLoading.value || !groupPlans.value.some(plan => Number(plan.id) === Number(selectedPlanId.value)) || !availableTeachers.value.some(teacher => Number(teacher.id) === Number(form.value.teacher_id)) || !availableRooms.value.some(room => room.name === form.value.room_name)) { errorMessage.value = 'Выберите доступные предмет, преподавателя и кабинет'; return; } const url = modalMode.value === 'edit' ? `/schedule/${form.value.id}` : '/schedule/'; const response = await fetch(url, { method: modalMode.value === 'edit' ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value) }); if (!response.ok) { const data = await response.json().catch(() => ({})); errorMessage.value = data.detail || 'Не удалось сохранить занятие'; return; } await fetchSchedule(); modalMode.value = null; };
    const deleteEntry = async () => { if (!confirm('ВНИМАНИЕ! Это полностью сотрет урок из базы. Продолжить?')) return; await fetch(`/schedule/${form.value.id}`, { method: 'DELETE' }); await fetchSchedule(); modalMode.value = null; };
    const cancelEntry = async () => { await fetch(`/schedule/${form.value.id}/cancel`, { method: 'POST' }); await fetchSchedule(); modalMode.value = null; };
    const restoreEntry = async () => { await fetch(`/schedule/${form.value.id}/restore`, { method: 'POST' }); await fetchSchedule(); modalMode.value = null; };
    const deleteCardEntry = async entry => { if (!confirm('Удалить занятие без возможности восстановления?')) return; const response = await fetch(`/schedule/${entry.id}`, { method: 'DELETE' }); if (response.ok) { activeCardId.value = null; await fetchSchedule(); } };
    const cancelCardEntry = async entry => { const response = await fetch(`/schedule/${entry.id}/cancel`, { method: 'POST' }); if (response.ok) { activeCardId.value = null; await fetchSchedule(); } };
    const restoreCardEntry = async entry => { const response = await fetch(`/schedule/${entry.id}/restore`, { method: 'POST' }); if (response.ok) { activeCardId.value = null; await fetchSchedule(); } };
    const generationLabel = scope => ({ 1: '1-й семестр', 2: '2-й семестр', all: 'все открытые семестры' })[scope] || 'все открытые семестры';
    const openGenerationDialog = () => {
        generationSettings.value = groups.value
            .map(group => ({ group_id: group.id, number: group.number, weekly_hours: group.weekly_hours ?? 30, has_saturday: Boolean(group.has_saturday) }))
            .sort((a, b) => Number(a.number) - Number(b.number));
        errorMessage.value = '';
        generationScope.value = 'all';
    };
    const generateScheduleAll = async () => {
        const scope = generationScope.value || 'all';
        if (generationSettings.value.some(group => !Number.isInteger(Number(group.weekly_hours)) || Number(group.weekly_hours) < 1 || Number(group.weekly_hours) > 54)) {
            errorMessage.value = 'Норма каждой группы должна быть от 1 до 54 часов.';
            return;
        }
        if (!confirm(`Сгенерировать расписание: ${generationLabel(scope)}?\nИзменения затронут только выбранные открытые семестры.`)) return;
        isGenerating.value = true; errorMessage.value = '';
        generationProgress.value = 0; generationCompleted.value = 0; generationTotal.value = 0;
        const termQuery = scope === 'all' ? '' : `?term_number=${scope}`;
        try {
            const settingsRes = await fetch('/groups/schedule-settings/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(generationSettings.value.map(group => ({ group_id: group.group_id, weekly_hours: Number(group.weekly_hours), has_saturday: group.has_saturday }))),
            });
            const savedSettings = await settingsRes.json();
            if (!settingsRes.ok) throw new Error(typeof savedSettings.detail === 'string' ? savedSettings.detail : 'Не удалось сохранить настройки групп');
            for (const saved of savedSettings) {
                const group = groups.value.find(item => item.id === saved.id);
                if (group) { group.weekly_hours = saved.weekly_hours; group.has_saturday = saved.has_saturday; }
            }
            const res = await fetch(`/generate_schedule/stream${termQuery}`, { method: 'POST' });
            if (!res.ok) {
                const failure = await res.json().catch(() => ({}));
                throw new Error(failure.detail || 'Не удалось запустить генерацию');
            }
            if (!res.body) throw new Error('Браузер не поддерживает получение прогресса');
            const reader = res.body.getReader(), decoder = new TextDecoder();
            let buffer = '', result = null;
            const handleLine = line => {
                if (!line.trim()) return;
                const update = JSON.parse(line);
                if (update.type === 'progress') {
                    generationCompleted.value = update.completed;
                    generationTotal.value = update.total;
                    generationProgress.value = update.total ? Math.min(99, Math.round(update.completed / update.total * 100)) : 0;
                } else if (update.type === 'result') result = update;
            };
            while (true) {
                const { value, done } = await reader.read();
                buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                lines.forEach(handleLine);
                if (done) break;
            }
            if (buffer) handleLine(buffer);
            if (!result) throw new Error('Соединение прервалось до завершения генерации');
            if (!result.ok) throw new Error(result.message);
            generationProgress.value = 100;
            generationScope.value = null;
            await fetchSchedule();
            alert(result.message);
        } catch (e) { errorMessage.value = e.message || 'Ошибка связи с сервером.'; }
        finally { isGenerating.value = false; }
    };
    const exportPdf = day => {
        const scheduleDate = localDate(calendarDateForDay(day));
        window.open(`/export/schedule.pdf?week_number=${selectedWeek.value}&day=${day}&schedule_date=${encodeURIComponent(scheduleDate)}&academic_year_id=${selectedAcademicYearId.value}`, '_blank');
    };
    const exportTeachersPdf = day => {
        const scheduleDate = localDate(calendarDateForDay(day));
        window.open(`/export/teachers.pdf?week_number=${selectedWeek.value}&day=${day}&schedule_date=${encodeURIComponent(scheduleDate)}&academic_year_id=${selectedAcademicYearId.value}`, '_blank');
    };
    const exportWeekPdf = audience => {
        const endpoint = audience === 'student' ? '/export/schedule.pdf' : '/export/teachers.pdf';
        window.open(`${endpoint}?week_number=${selectedWeek.value}&academic_year_id=${selectedAcademicYearId.value}`, '_blank');
    };
    const scheduleActionMonthDate = computed(() => new Date(`${scheduleActionMonth.value || currentAcademicYear.value?.start_date?.slice(0, 7) || '2026-09'}-01T00:00:00`));
    const scheduleActionMonthTitle = computed(() => scheduleActionMonthDate.value.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' }));
    const scheduleActionCalendarDays = computed(() => {
        const month = scheduleActionMonthDate.value, first = new Date(month.getFullYear(), month.getMonth(), 1);
        const offset = (first.getDay() + 6) % 7, count = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate(), year = currentAcademicYear.value;
        return Array.from({ length: offset + count }, (_, index) => {
            if (index < offset) return null;
            const value = localDate(new Date(month.getFullYear(), month.getMonth(), index - offset + 1)), range = scheduleAction.value || {};
            const sunday = new Date(`${value}T00:00:00`).getDay() === 0;
            return { value, number: Number(value.slice(-2)), outside: !year || value < year.start_date || value > year.end_date, sunday, selected: value === range.start_date || value === range.end_date, between: Boolean(range.start_date && range.end_date && value > range.start_date && value < range.end_date) };
        });
    });
    const selectScheduleActionDate = day => {
        if (!day || day.outside || day.sunday || !scheduleAction.value) return;
        const range = scheduleAction.value;
        if (!range.start_date || range.end_date) scheduleAction.value = { ...range, day: null, start_date: day.value, end_date: '' };
        else if (day.value >= range.start_date) scheduleAction.value = { ...range, end_date: day.value };
        else scheduleAction.value = { ...range, start_date: day.value, end_date: range.start_date };
    };
    const previousScheduleActionMonth = () => { const value = scheduleActionMonthDate.value; scheduleActionMonth.value = localDate(new Date(value.getFullYear(), value.getMonth() - 1, 1)).slice(0, 7); };
    const nextScheduleActionMonth = () => { const value = scheduleActionMonthDate.value; scheduleActionMonth.value = localDate(new Date(value.getFullYear(), value.getMonth() + 1, 1)).slice(0, 7); };
    const openScheduleActions = (day, audience) => { const date = day ? localDate(calendarDateForDay(day)) : ''; scheduleActionMonth.value = (date || currentAcademicYear.value?.start_date || '').slice(0, 7); scheduleAction.value = { day, audience, dayPicker: day === null, start_date: date, end_date: date }; };
    const selectScheduleActionDay = day => { if (scheduleAction.value) scheduleAction.value = { ...scheduleAction.value, day }; };
    const downloadScheduleAction = () => {
        const action = scheduleAction.value;
        if (!action) return;
        scheduleAction.value = null;
        if (action.start_date && action.end_date && action.start_date !== action.end_date) {
            const endpoint = action.audience === 'student' ? '/export/schedule.pdf' : '/export/teachers.pdf';
            const params = new URLSearchParams({ week_number: String(weekForDate(action.start_date)), start_date: action.start_date, end_date: action.end_date, academic_year_id: String(selectedAcademicYearId.value) });
            window.open(`${endpoint}?${params}`, '_blank');
            return;
        }
        if (!action.day && action.start_date && action.end_date) {
            const selected = new Date(`${action.start_date}T00:00:00`), selectedDay = selected.getDay() || 7;
            const endpoint = action.audience === 'student' ? '/export/schedule.pdf' : '/export/teachers.pdf';
            const params = new URLSearchParams({ week_number: String(weekForDate(action.start_date)), day: String(selectedDay), schedule_date: action.start_date, academic_year_id: String(selectedAcademicYearId.value) });
            window.open(`${endpoint}?${params}`, '_blank');
            return;
        }
        if (!action.day) { exportWeekPdf(action.audience); return; }
        if (action.audience === 'student') exportPdf(action.day); else exportTeachersPdf(action.day);
    };
    const broadcastScheduleAction = async () => {
        const action = scheduleAction.value;
        if (!action) return;
        scheduleAction.value = null;
        await broadcastSchedule(action.day, action.audience, action.start_date, action.end_date);
    };
    const broadcastSchedule = async (day, audience, startDate = '', endDate = '') => {
        const target = audience === 'student' ? 'студентам' : 'преподавателям';
        const period = startDate && endDate && startDate !== endDate ? `${startDate.split('-').reverse().join('.')} — ${endDate.split('-').reverse().join('.')}` : (day ? `${getDayName(day).toLowerCase()}, ${formatDayDate(day)}` : 'всю неделю');
        if (!confirm(`Отправить PDF расписания ${target} за ${period}?`)) return;
        const params = new URLSearchParams({ week_number: startDate ? weekForDate(startDate) : selectedWeek.value, academic_year_id: selectedAcademicYearId.value, audience });
        if (startDate && endDate && startDate !== endDate) { params.set('start_date', startDate); params.set('end_date', endDate); }
        else if (!day && startDate && endDate) { const selected = new Date(`${startDate}T00:00:00`), selectedDay = selected.getDay() || 7; params.set('day', selectedDay); params.set('schedule_date', startDate); }
        if (day) { params.set('day', day); params.set('schedule_date', localDate(calendarDateForDay(day))); }
        const response = await fetch(`/telegram/broadcast-schedule?${params}`, { method: 'POST' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) { errorMessage.value = data.detail || 'Не удалось отправить PDF в Telegram'; return; }
        if (data.job_id && Number(data.queued) === 0) {
            alert('Рассылка не отправлена: нет подписчиков с выбранной ролью. Попросите пользователей выбрать роль через /role в боте.');
        } else {
            alert(data.job_id ? `PDF поставлен в очередь для ${data.queued} подписчиков. Номер рассылки: ${data.job_id}.` : `Рассылка завершена: отправлено ${data.delivered}, ошибок ${data.failed}.`);
        }
    };
    return { groups, teachers, rooms, plans, schedule, allSchedule, curatorHours, curatorOverrides, daysOff, groupBreakDays, terms, academicYears, selectedAcademicYearId, selectAcademicYear, selectedGroupId, selectedTermId, selectedWeek, selectedDate, isArchived, isArchivedAll, isGenerating, generationProgress, generationCompleted, generationTotal, modalMode, form, selectedPlanId, slotOptionsLoading, activeCardId, curatorEdit, generationScope, generationSettings, scheduleAction, scheduleActionMonthTitle, scheduleActionCalendarDays, scheduleActionMonth, groupPlans, errorMessage, currentGroupTerms, currentTermWeeks, currentTerm, selectTerm, availableTimeSlots, availableTeachers, availableTeachers2, availableRooms, availableRooms2, getDayName, getTeacherName, getTeacherNames, getRoomNames, getEntries, hasActiveEntry, curatorHourAt, hiddenCuratorHourAt, curatorTeacherName, curatorRoomName, curatorCardId, openCuratorEdit, saveCuratorEdit, removeCuratorHourFromGroup, removeCuratorHourForAllGroups, restoreCuratorHourForGroup, onPlanChange, updateRoomFromTeacher, openCreateModal, openEditModal, toggleCardActions, deleteCardEntry, cancelCardEntry, restoreCardEntry, saveEntry, deleteEntry, cancelEntry, restoreEntry, toggleArchive, toggleArchiveAll, currentGroupNumber, currentGroupHasSaturday, currentGroupWeeklyHours, currentWeekActualHours, currentGroupSemesterWeeks, showSaturday, toggleSaturdayForGroup, changeWeeklyHoursPrompt, generationLabel, openGenerationDialog, generateScheduleAll, exportPdf, exportTeachersPdf, openScheduleActions, selectScheduleActionDay, selectScheduleActionDate, previousScheduleActionMonth, nextScheduleActionMonth, downloadScheduleAction, broadcastScheduleAction, broadcastSchedule, formatDayDate, isBeforeTermStart };
} }).component('searchable-select', SearchableSelect).mount('#app');
