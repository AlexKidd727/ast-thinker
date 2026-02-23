/**
 * Долговременная память: загрузка списка и добавление записи
 */
(function () {
    var listEl = document.getElementById('memoryList');
    var form = document.getElementById('memoryAddForm');
    var textInput = document.getElementById('memoryText');
    var tagsInput = document.getElementById('memoryTags');

    function render(items) {
        listEl.innerHTML = '';
        if (!items || items.length === 0) {
            listEl.innerHTML = '<p class="journal-desc">Пока нет записей.</p>' +
                '<p class="journal-desc muted">Записи появляются после суммаризации чата (каждые N сообщений в настройках) или добавляются вручную выше.</p>';
            return;
        }
        items.forEach(function (it) {
            var div = document.createElement('div');
            div.className = 'journal-item';
            var timeStr = typeof formatDateTime !== 'undefined' ? formatDateTime(it.timestamp) : (it.timestamp || '');
            var tags = it.tags ? 'Теги: ' + (typeof escapeHtml !== 'undefined' ? escapeHtml(it.tags) : it.tags) : '';
            var bodyHtml = typeof markdownToHtml === 'function' ? markdownToHtml(it.text || '') : (typeof escapeHtml !== 'undefined' ? escapeHtml(it.text || '').replace(/\n/g, '<br>') : (it.text || ''));
            div.innerHTML = '<time>' + (typeof escapeHtml !== 'undefined' ? escapeHtml(timeStr) : timeStr) + '</time>' +
                '<div class="msg-content markdown-content">' + bodyHtml + '</div>' +
                (tags ? '<div class="tags">' + tags + '</div>' : '');
            listEl.appendChild(div);
        });
    }

    function load() {
        fetch('/api/memory')
            .then(function (r) { return r.json(); })
            .then(render)
            .catch(function () {
                listEl.innerHTML = '<p class="error-msg">Не удалось загрузить память.</p>';
            });
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        var text = (textInput.value || '').trim();
        if (!text) return;
        var tags = (tagsInput.value || '').trim();
        fetch('/api/memory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text, tags: tags })
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || 'Ошибка'); });
                return r.json();
            })
            .then(function () {
                textInput.value = '';
                tagsInput.value = '';
                load();
            })
            .catch(function (err) {
                alert(err.message || 'Ошибка');
            });
    });

    load();
})();
