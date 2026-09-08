const { createApp, ref, onMounted, watch } = Vue;
createApp({ setup() {
    const groups = ref([]), selectedGroupId = ref(null), progressList = ref([]);
    const fetchGroups = async () => { const res = await fetch('/groups/'); if (res.ok) groups.value = await res.json(); };
    const fetchProgress = async () => { let url = '/plans-progress/'; if (selectedGroupId.value) url += `?group_id=${selectedGroupId.value}`; const res = await fetch(url); if (res.ok) progressList.value = await res.json(); };
    watch(selectedGroupId, fetchProgress);
    onMounted(async () => { await fetchGroups(); await fetchProgress(); });
    return { groups, selectedGroupId, progressList, fetchProgress };
} }).component('searchable-select', SearchableSelect).mount('#app');
