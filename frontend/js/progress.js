const { createApp, ref, onMounted, watch } = Vue;
createApp({ setup() {
    const groups = ref([]), selectedGroupId = ref(null), progressList = ref([]);
    const fetchGroups = async () => { const res = await fetch('/groups/'); if (res.ok) groups.value = await res.json(); };
    const fetchProgress = async () => { const params = new URLSearchParams(); const yearId = localStorage.getItem('edusync-academic-year-id'); if (yearId) params.set('academic_year_id', yearId); if (selectedGroupId.value) params.set('group_id', selectedGroupId.value); const query = params.toString(); const res = await fetch(`/plans-progress/${query ? `?${query}` : ''}`); if (res.ok) progressList.value = await res.json(); };
    watch(selectedGroupId, fetchProgress);
    onMounted(async () => { await fetchGroups(); await fetchProgress(); });
    return { groups, selectedGroupId, progressList, fetchProgress };
} }).component('searchable-select', SearchableSelect).mount('#app');
