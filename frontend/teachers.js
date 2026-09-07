const { createApp, ref, computed, onMounted } = Vue;

createApp({
    setup() {
        const rooms = ref([]);
        const teachers = ref([]);
        const weekDays = [
            { id: 1, name: 'Пн' }, { id: 2, name: 'Вт' }, { id: 3, name: 'Ср' },
            { id: 4, name: 'Чт' }, { id: 5, name: 'Пт' }, { id: 6, name: 'Сб' }
        ];
        const defaultWorkingDays = [1, 2, 3, 4, 5];
        const newRoom = ref({ name: '' });
        const newTeacher = ref({ name: '', room_id: null, max_hours_per_week: 30, working_days: defaultWorkingDays });
        const editingTeacherId = ref(null);

        const fetchData = async () => {
            const [rRes, tRes] = await Promise.all([fetch('http://localhost:8000/rooms/'), fetch('http://localhost:8000/teachers/')]);
            if (rRes.ok) rooms.value = await rRes.json();
            if (tRes.ok) teachers.value = await tRes.json();
        };
        onMounted(fetchData);

        const availableRooms = computed(() => {
            const occupiedRoomIds = teachers.value.filter(t => t.id !== editingTeacherId.value).map(t => t.room_id).filter(id => id !== null);
            return rooms.value.filter(r => !occupiedRoomIds.includes(r.id));
        });
        const getRoomName = id => rooms.value.find(r => r.id === id)?.name || '???';
        const getRoomOccupant = roomId => teachers.value.find(t => t.room_id === roomId)?.name || null;
        const normalizeDays = days => String(days || '1,2,3,4,5').split(',').map(Number).filter(Number.isInteger);
        const formatWorkingDays = days => normalizeDays(days).map(day => weekDays.find(item => item.id === day)?.name).filter(Boolean).join(', ') || 'Дни не выбраны';

        const saveRoom = async () => {
            const trimmedName = newRoom.value.name.trim();
            if (!trimmedName) return;
            await fetch('http://localhost:8000/rooms/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: trimmedName }) });
            newRoom.value.name = '';
            fetchData();
        };
        const deleteRoom = async id => {
            if (!confirm('Точно удалить этот кабинет?')) return;
            await fetch(`http://localhost:8000/rooms/${id}`, { method: 'DELETE' });
            fetchData();
        };
        const saveTeacher = async () => {
            const payload = { ...newTeacher.value, working_days: newTeacher.value.working_days.join(',') };
            const url = editingTeacherId.value ? `http://localhost:8000/teachers/${editingTeacherId.value}` : 'http://localhost:8000/teachers/';
            const method = editingTeacherId.value ? 'PUT' : 'POST';
            await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
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
            await fetch(`http://localhost:8000/teachers/${id}`, { method: 'DELETE' });
            fetchData();
        };

        return { rooms, teachers, weekDays, newRoom, newTeacher, editingTeacherId, availableRooms, saveRoom, deleteRoom, saveTeacher, editTeacher, resetTeacherForm, deleteTeacher, getRoomName, getRoomOccupant, formatWorkingDays };
    }
}).component('searchable-select', SearchableSelect).mount('#app');
