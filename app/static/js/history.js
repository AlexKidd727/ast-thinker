/**
 * История: список архивов веток размышлений, восстановление, экспорт в MD, удаление
 */
(function () {
    var listEl = document.getElementById('historyList');

    function loadArchives() {
        fetch('/api/thoughts/archives')
            .then(function (r) { return r.json(); })
            .then(function (items) {
                if (!listEl) return;
                listEl.innerHTML = '';
                if (!items || items.length === 0) {
                    listEl.innerHTML = '<p class="journal-desc">Пока нет архивов.</p>';
                    return;
                }
                items.forEach(function (a) {
                    var div = document.createElement('div');
                    div.className = 'history-item';
                    var timeStr = typeof formatDateTime !== 'undefined' ? formatDateTime(a.created_at) : (a.created_at || '');
                    var name = (a.name || '').trim() || ('Архив #' + a.id);
                    var count = a.entries_count || 0;
                    div.innerHTML =
                        '<div class="history-item-head">' +
                        '<span class="history-item-name">' + (typeof escapeHtml !== 'undefined' ? escapeHtml(name) : name) + '</span>' +
                        '<span class="history-item-meta">' + (typeof escapeHtml !== 'undefined' ? escapeHtml(timeStr) : timeStr) + ', записей: ' + count + '</span>' +
                        '</div>' +
                        '<div class="history-item-actions">' +
                        '<button type="button" class="history-btn history-btn-restore" data-id="' + a.id + '">Восстановить</button>' +
                        '<a href="/api/thoughts/archives/' + a.id + '/export" class="history-btn history-btn-export" download>Экспорт MD</a>' +
                        '<button type="button" class="history-btn history-btn-delete" data-id="' + a.id + '">Удалить</button>' +
                        '</div>';
                    listEl.appendChild(div);
                });
                listEl.querySelectorAll('.history-btn-restore').forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        var id = parseInt(btn.getAttribute('data-id'), 10);
                        if (!confirm('Восстановить этот архив? Текущий журнал мыслей будет заменён.')) return;
                        btn.disabled = true;
                        fetch('/api/thoughts/archives/' + id + '/restore', { method: 'POST' })
                            .then(function (r) {
                                if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || 'Ошибка'); });
                                return r.json();
                            })
                            .then(function () {
                                window.location.href = '/thoughts';
                            })
                            .catch(function (err) {
                                alert(err.message || 'Ошибка восстановления');
                            })
                            .finally(function () { btn.disabled = false; });
                    });
                });
                listEl.querySelectorAll('.history-btn-delete').forEach(function (btn) {
                    btn.addEventListener('click', function () {
                        var id = parseInt(btn.getAttribute('data-id'), 10);
                        if (!confirm('Удалить этот архив безвозвратно?')) return;
                        btn.disabled = true;
                        fetch('/api/thoughts/archives/' + id, { method: 'DELETE' })
                            .then(function (r) {
                                if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || 'Ошибка'); });
                                return r.json();
                            })
                            .then(function () { loadArchives(); })
                            .catch(function (err) { alert(err.message || 'Ошибка удаления'); })
                            .finally(function () { btn.disabled = false; });
                    });
                });
            })
            .catch(function () {
                if (listEl) listEl.innerHTML = '<p class="error-msg">Не удалось загрузить историю.</p>';
            });
    }

    loadArchives();
})();
