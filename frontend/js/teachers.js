const { createApp, ref, computed, onMounted } = Vue;

createApp({
    setup() {
        const rooms = ref([]);
        const teachers = ref([]);
        const workloads = ref([]);
        const errorMessage = ref('');
        const weekDays = [
            { id: 1, name: 'Пн' }, { id: 2, name: 'Вт' }, { id: 3, name: 'Ср' },
            { id: 4, name: 'Чт' }, { id: 5, name: 'Пт' }, { id: 6, name: 'Сб' }
        ];
        const defaultWorkingDays = [1, 2, 3, 4, 5];
        const newRoom = ref({ name: '' });
        const newTeacher = ref({ name: '', room_id: null, max_hours_per_week: 30, working_days: defaultWorkingDays });
        const editingTeacherId = ref(null);
        const vacationEditorId = ref(null);
        const vacations = ref([]);
        const vacationForm = ref({ start_date: '', end_date: '' });
        const activeYear = ref(null);

        const fetchData = async () => {
            const [rRes, tRes, wRes, yRes] = await Promise.all([fetch('/rooms/'), fetch('/teachers/'), fetch('/teachers-workload/'), fetch('/academic-years/')]);
            if (rRes.ok) rooms.value = await rRes.json();
            if (tRes.ok) teachers.value = await tRes.json();
            if (wRes.ok) workloads.value = await wRes.json();
            if (yRes.ok) { const years = await yRes.json(); activeYear.value = years.find(year => year.is_active) || null; }
            if (activeYear.value) { const vRes = await fetch(`/teacher-vacations/?academic_year_id=${activeYear.value.id}`); if (vRes.ok) vacations.value = await vRes.json(); }
        };
        onMounted(fetchData);

        const availableRooms = computed(() => {
            const occupiedRoomIds = teachers.value.filter(t => t.id !== editingTeacherId.value).map(t => t.room_id).filter(id => id !== null);
            return rooms.value.filter(r => !occupiedRoomIds.includes(r.id));
        });
        const getRoomName = id => rooms.value.find(r => r.id === id)?.name || '???';
        const getRoomOccupant = roomId => teachers.value.find(t => t.room_id === roomId)?.name || null;
        const workload = teacher => workloads.value.find(item => item.id === teacher.id) || { assigned_weekly_hours: 0, max_hours_per_week: teacher.max_hours_per_week };
        const normalizeDays = days => String(days || '1,2,3,4,5').split(',').map(Number).filter(Number.isInteger);
        const formatWorkingDays = days => normalizeDays(days).map(day => weekDays.find(item => item.id === day)?.name).filter(Boolean).join(', ') || 'Дни не выбраны';

        const saveRoom = async () => {
            const trimmedName = newRoom.value.name.trim();
            if (!trimmedName) return;
            const response = await fetch('/rooms/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: trimmedName }) });
            if (!response.ok) { errorMessage.value = (await response.json().catch(() => ({}))).detail || 'Не удалось сохранить кабинет'; return; }
            newRoom.value.name = '';
            fetchData();
        };
        const deleteRoom = async id => {
            if (!confirm('Точно удалить этот кабинет?')) return;
            await fetch(`/rooms/${id}`, { method: 'DELETE' });
            fetchData();
        };
        const saveTeacher = async () => {
            const payload = { ...newTeacher.value, working_days: newTeacher.value.working_days.join(',') };
            const url = editingTeacherId.value ? `/teachers/${editingTeacherId.value}` : '/teachers/';
            const method = editingTeacherId.value ? 'PUT' : 'POST';
            const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) { errorMessage.value = (await response.json().catch(() => ({}))).detail || 'Не удалось сохранить преподавателя'; return; }
            resetTeacherForm();
            fetchData();
        };
        const editTeacher = t => {
            editingTeacherId.value = t.id;
            newTeacher.value = { name: t.name, room_id: t.room_id, max_hours_per_week: t.max_hours_per_week, working_days: normalizeDays(t.working_days) };
        };
        const resetTeacherForm = () => {
            editingTeacherId.value = null;
            newTeacher.value = { name: '', room_id: null, max_hours_per_week: 30, working_days: [...defaultWorkingDays] };
        };
        const deleteTeacher = async id => {
            if (!confirm('Точно удалить преподавателя?')) return;
            await fetch(`/teachers/${id}`, { method: 'DELETE' });
            fetchData();
        };
        const openVacationEditor = teacher => {
            vacationEditorId.value = teacher.id;
            vacationForm.value = { start_date: '', end_date: '' };
        };
        const closeVacationEditor = () => { vacationEditorId.value = null; vacationForm.value = { start_date: '', end_date: '' }; };
        const teacherVacations = teacher => vacations.value.filter(vacation => vacation.teacher_id === teacher.id);
        const saveVacation = async teacher => {
            if (!activeYear.value || !vacationForm.value.start_date || !vacationForm.value.end_date) return;
            const response = await fetch('/teacher-vacations/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ teacher_id: teacher.id, academic_year_id: activeYear.value.id, ...vacationForm.value }) });
            if (!response.ok) { errorMessage.value = (await response.json().catch(() => ({}))).detail || 'Не удалось сохранить отпуск'; return; }
            closeVacationEditor(); await fetchData();
        };
        const deleteVacation = async id => { const response = await fetch(`/teacher-vacations/${id}`, { method: 'DELETE' }); if (response.ok) await fetchData(); };

        return { rooms, teachers, weekDays, newRoom, newTeacher, editingTeacherId, vacationEditorId, vacationForm, activeYear, availableRooms, saveRoom, deleteRoom, saveTeacher, editTeacher, resetTeacherForm, deleteTeacher, openVacationEditor, closeVacationEditor, saveVacation, deleteVacation, teacherVacations, getRoomName, getRoomOccupant, formatWorkingDays, workload, errorMessage };
    }
}).component('searchable-select', SearchableSelect).mount('#app');
