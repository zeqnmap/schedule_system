const { createApp, ref, computed, onMounted, watch } = Vue;
createApp({ setup() {
    const groups = ref([]), terms = ref([]), selectedGroupId = ref(null), selectedTermId = ref(null), progressList = ref([]);
    const fetchGroups = async () => { const res = await fetch('/groups/'); if (res.ok) groups.value = await res.json(); };
    const fetchProgress = async () => { const params = new URLSearchParams(); const yearId = localStorage.getItem('edusync-academic-year-id'); if (yearId) params.set('academic_year_id', yearId); if (selectedGroupId.value) params.set('group_id', selectedGroupId.value); if (selectedTermId.value) params.set('term_id', selectedTermId.value); const query = params.toString(); const res = await fetch(`/plans-progress/${query ? `?${query}` : ''}`); if (res.ok) progressList.value = await res.json(); };
    const loadTerms = async () => { const yearId = localStorage.getItem('edusync-academic-year-id'); const res = await fetch(`/group-terms/${yearId ? `?academic_year_id=${yearId}` : ''}`); if (res.ok) terms.value = await res.json(); };
    const availableTerms = computed(() => selectedGroupId.value ? terms.value.filter(term => Number(term.group_id) === Number(selectedGroupId.value)) : []);
    watch(selectedGroupId, () => { selectedTermId.value = null; fetchProgress(); }); watch(selectedTermId, fetchProgress);
    onMounted(async () => { await fetchGroups(); await loadTerms(); await fetchProgress(); });
    return { groups, terms, availableTerms, selectedGroupId, selectedTermId, progressList, fetchProgress };
} }).component('searchable-select', SearchableSelect).mount('#app');
