const SearchableSelect = {
    inheritAttrs: false,
    props: {
        modelValue: { type: [String, Number], default: null },
        options: { type: Array, default: () => [] },
        optionValue: { type: String, default: 'id' },
        optionLabel: { type: String, default: 'name' },
        placeholder: { type: String, default: 'Выберите значение...' },
        searchPlaceholder: { type: String, default: 'Поиск...' },
        formatOption: { type: Function, default: null }
    },
    emits: ['update:modelValue', 'change'],
    setup(props, { emit }) {
        const search = Vue.ref('');
        const filteredOptions = Vue.computed(() => {
            const query = search.value.trim().toLocaleLowerCase();
            if (!query) return props.options;
            return props.options.filter(option => {
                const label = props.formatOption ? props.formatOption(option) : option[props.optionLabel];
                return String(label ?? '').toLocaleLowerCase().includes(query);
            });
        });
        const optionText = option => props.formatOption ? props.formatOption(option) : option[props.optionLabel];
        const optionKey = option => option[props.optionValue];
        const updateValue = event => {
            const selected = props.options.find(option => String(optionKey(option)) === event.target.value);
            emit('update:modelValue', selected ? optionKey(selected) : null);
            emit('change', event);
        };
        return { search, filteredOptions, optionText, optionKey, updateValue };
    },
    template: `
        <div class="space-y-2">
            <input v-model="search" type="search" :placeholder="searchPlaceholder"
                   class="input-modern w-full px-4 py-2.5 text-slate-700 font-semibold text-sm" autocomplete="off">
            <select v-bind="$attrs" :value="modelValue ?? ''" @change="updateValue">
                <option value="">{{ placeholder }}</option>
                <option v-for="option in filteredOptions" :key="optionKey(option)" :value="optionKey(option)">{{ optionText(option) }}</option>
            </select>
        </div>
    `
};
