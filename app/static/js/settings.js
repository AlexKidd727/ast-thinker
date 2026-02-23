/**
 * Настройки: провайдер LLM (LM Studio / OpenRouter), модель, токен OpenRouter, загрузка моделей, остальные параметры
 */
(function () {
    var form = document.getElementById('settingsForm');
    var llmProviderEl = document.getElementById('llmProvider');
    var openrouterApiKeyEl = document.getElementById('openrouterApiKey');
    var openrouterKeyLabel = document.getElementById('openrouterKeyLabel');
    var modelNameEl = document.getElementById('modelName');
    var loadModelsBtn = document.getElementById('loadModelsBtn');
    var idleMinutes = document.getElementById('idleMinutes');
    var introInterval = document.getElementById('introInterval');
    var introMaxTokens = document.getElementById('introMaxTokens');
    var chatMaxTokens = document.getElementById('chatMaxTokens');
    var temperatureEl = document.getElementById('temperature');
    var summarizeEveryN = document.getElementById('summarizeEveryN');
    var modelThinkingDisabled = document.getElementById('modelThinkingDisabled');
    var modelThinkingMaxTokens = document.getElementById('modelThinkingMaxTokens');
    var repetitionThreshold = document.getElementById('repetitionThreshold');
    var introPrompt = document.getElementById('introPrompt');
    var noteEl = document.getElementById('settingsNote');

    function setOpenrouterVisibility() {
        if (openrouterKeyLabel) openrouterKeyLabel.style.display = (llmProviderEl && llmProviderEl.value === 'openrouter') ? '' : 'none';
    }

    function modelsUrl(provider, key) {
        if (provider === 'openrouter' && key) return '/api/models?provider=openrouter&openrouter_api_key=' + encodeURIComponent(key);
        return '/api/models';
    }

    function fillModelSelect(list, savedModelName) {
        if (!modelNameEl) return;
        modelNameEl.innerHTML = '';
        var opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '(авто / первая в списке)';
        modelNameEl.appendChild(opt);
        (list || []).forEach(function (id) {
            var o = document.createElement('option');
            o.value = id;
            o.textContent = id;
            modelNameEl.appendChild(o);
        });
        modelNameEl.value = (savedModelName && list && list.indexOf(savedModelName) !== -1) ? savedModelName : (savedModelName || '');
        if (savedModelName && modelNameEl.value !== savedModelName && list && list.indexOf(savedModelName) === -1) {
            var customOpt = document.createElement('option');
            customOpt.value = savedModelName;
            customOpt.textContent = savedModelName + ' (сохранён)';
            modelNameEl.appendChild(customOpt);
            modelNameEl.value = savedModelName;
        }
    }

    function loadModelsAndSettings() {
        fetch('/api/settings').then(function (r) { return r.json(); })
            .then(function (settingsData) {
                var provider = (settingsData.llm_provider || 'lm_studio').trim();
                var key = (settingsData.openrouter_api_key || '').trim();
                if (llmProviderEl) llmProviderEl.value = (provider === 'openrouter') ? 'openrouter' : 'lm_studio';
                if (openrouterApiKeyEl) openrouterApiKeyEl.value = key;
                setOpenrouterVisibility();

                introInterval.value = settingsData.intro_interval || '';
                introMaxTokens.value = settingsData.intro_max_tokens || '';
                if (chatMaxTokens) chatMaxTokens.value = settingsData.chat_max_tokens || '';
                if (temperatureEl) temperatureEl.value = settingsData.temperature || '0.7';
                if (summarizeEveryN !== null) summarizeEveryN.value = settingsData.summarize_every_n || '20';
                introPrompt.value = settingsData.intro_prompt || '';
                if (idleMinutes !== null) idleMinutes.value = settingsData.idle_minutes !== undefined ? settingsData.idle_minutes : '5';
                if (modelThinkingDisabled !== null) modelThinkingDisabled.checked = !!settingsData.model_thinking_disabled;
                if (modelThinkingMaxTokens !== null) modelThinkingMaxTokens.value = settingsData.model_thinking_max_tokens !== undefined ? settingsData.model_thinking_max_tokens : '0';
                if (repetitionThreshold !== null) repetitionThreshold.value = settingsData.repetition_threshold !== undefined ? settingsData.repetition_threshold : '0.7';

                var url = modelsUrl(provider, key);
                return fetch(url).then(function (r) { return r.json(); }).then(function (modelsData) {
                    var list = modelsData.models || [];
                    var err = modelsData.error;
                    var savedModelName = (settingsData.model_name || '').trim();
                    if (modelNameEl) fillModelSelect(list, savedModelName);
                    if (err && noteEl) {
                        noteEl.textContent = 'Список моделей: ' + (list.length ? 'загружено ' + list.length : 'ошибка — ' + err);
                        noteEl.className = 'settings-note ' + (list.length ? '' : 'error-msg');
                    }
                });
            })
            .catch(function (e) {
                if (noteEl) {
                    noteEl.textContent = 'Не удалось загрузить настройки или список моделей.';
                    noteEl.className = 'settings-note error-msg';
                }
                if (modelNameEl) modelNameEl.innerHTML = '<option value="">Не удалось загрузить список</option>';
            });
    }

    function loadModelsOnly() {
        var provider = (llmProviderEl && llmProviderEl.value) ? llmProviderEl.value : 'lm_studio';
        var key = (openrouterApiKeyEl && openrouterApiKeyEl.value) ? openrouterApiKeyEl.value.trim() : '';
        var url = modelsUrl(provider, key);
        if (noteEl) { noteEl.textContent = 'Загрузка списка моделей...'; noteEl.className = 'settings-note'; }
        fetch(url).then(function (r) { return r.json(); })
            .then(function (data) {
                var list = data.models || [];
                var err = data.error;
                var savedModelName = (modelNameEl && modelNameEl.value) ? modelNameEl.value : '';
                if (modelNameEl) fillModelSelect(list, savedModelName);
                if (noteEl) {
                    noteEl.textContent = list.length ? 'Загружено моделей: ' + list.length : 'Ошибка: ' + (err || 'нет данных');
                    noteEl.className = 'settings-note ' + (list.length ? '' : 'error-msg');
                }
            })
            .catch(function () {
                if (noteEl) { noteEl.textContent = 'Не удалось загрузить список моделей.'; noteEl.className = 'settings-note error-msg'; }
            });
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        noteEl.textContent = '';
        noteEl.className = 'settings-note';
        var payload = {
            intro_interval: introInterval.value,
            intro_max_tokens: introMaxTokens.value,
            chat_max_tokens: chatMaxTokens ? chatMaxTokens.value : '',
            intro_prompt: introPrompt.value
        };
        if (temperatureEl) payload.temperature = temperatureEl.value;
        if (summarizeEveryN !== null) payload.summarize_every_n = summarizeEveryN.value;
        if (idleMinutes !== null) payload.idle_minutes = idleMinutes.value;
        if (modelNameEl) payload.model_name = modelNameEl.value;
        if (llmProviderEl) payload.llm_provider = llmProviderEl.value;
        if (openrouterApiKeyEl) payload.openrouter_api_key = openrouterApiKeyEl.value;
        if (modelThinkingDisabled !== null) payload.model_thinking_disabled = modelThinkingDisabled.checked;
        if (modelThinkingMaxTokens !== null) payload.model_thinking_max_tokens = modelThinkingMaxTokens.value;
        if (repetitionThreshold !== null) payload.repetition_threshold = repetitionThreshold.value;
        fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
            .then(function (r) {
                if (!r.ok) throw new Error('Ошибка сохранения');
                return r.json();
            })
            .then(function () {
                noteEl.textContent = 'Настройки сохранены.';
                noteEl.className = 'settings-note';
            })
            .catch(function () {
                noteEl.textContent = 'Не удалось сохранить.';
                noteEl.className = 'settings-note error-msg';
            });
    });

    if (llmProviderEl) llmProviderEl.addEventListener('change', setOpenrouterVisibility);
    if (loadModelsBtn) loadModelsBtn.addEventListener('click', loadModelsOnly);

    loadModelsAndSettings();
})();
