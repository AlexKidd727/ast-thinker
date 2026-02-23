/**
 * Журнал текущих мыслей: загрузка, отображение и автообновление по таймеру.
 * В режиме «сон» (размышления выключены) журнал не обновляется и не прокручивается вниз.
 */
(function () {
    var listEl = document.getElementById('thoughtsList');
    var metaEl = document.getElementById('thoughtsMeta');
    var clearBtn = document.getElementById('thoughtsClear');
    var loadMoreWrap = document.getElementById('thoughtsLoadMoreWrap');
    var loadMoreBtn = document.getElementById('thoughtsLoadMore');
    var loadMoreInfo = document.getElementById('thoughtsLoadMoreInfo');
    var refreshIntervalMs = 8000;
    var thinkingRunning = false;
    var thoughtsPagination = { page: 1, totalPages: 1, total: 0, perPage: 50, allItems: [] };

    function renderItem(it) {
        var div = document.createElement('div');
        div.className = 'journal-item';
        var numHtml = it.num_display ? '<span class="journal-item-num">' + (typeof escapeHtml !== 'undefined' ? escapeHtml(it.num_display) : it.num_display) + '</span> ' : '';
        var timeStr = typeof formatDateTime !== 'undefined' ? formatDateTime(it.timestamp) : (it.timestamp || '');
        var tags = it.tags ? 'Теги: ' + (typeof escapeHtml !== 'undefined' ? escapeHtml(it.tags) : it.tags) : '';
        var bodyHtml = typeof markdownToHtml === 'function' ? markdownToHtml(it.text || '') : (typeof escapeHtml !== 'undefined' ? escapeHtml(it.text || '').replace(/\n/g, '<br>') : (it.text || ''));
        var mx = it.meta || {};
        var metaLine = (it.model || mx.model || mx.context_limit != null) ?
            '<div class="journal-item-meta">Модель: ' + (it.model || mx.model || '—') +
            ' | Контекст: ' + (mx.context_limit != null ? mx.context_limit : '—') + ' ток.' +
            ' | Саморазмышление: макс. ' + (mx.intro_max_tokens || '—') + ' ток.' +
            ' | temp. ' + (mx.temperature || '—') + '</div>' : '';
        div.innerHTML = '<time>' + numHtml + (typeof escapeHtml !== 'undefined' ? escapeHtml(timeStr) : timeStr) + '</time>' +
            '<div class="msg-content markdown-content">' + bodyHtml + '</div>' +
            (tags ? '<div class="tags">' + tags + '</div>' : '') + metaLine;
        return div;
    }

    function render(data, scrollDown, append) {
        var items = Array.isArray(data) ? data : (data && data.items) ? data.items : [];
        if (metaEl) {
            if (data && data.meta) {
                var m = data.meta;
                metaEl.textContent = 'Модель: ' + (m.model || '—') +
                    ' | Контекст: ' + (m.context_limit || '—') + ' ток.' +
                    ' | Чат: макс. ' + (m.chat_max_tokens || '—') + ' ток.' +
                    ' | Саморазмышление: макс. ' + (m.intro_max_tokens || '—') + ' ток.' +
                    ' | temp. ' + (m.temperature || '—') +
                    ' | Повторения: порог ' + (m.repetition_threshold || '—') +
                    ' | Суммаризация каждые ' + (m.summarize_every_n || '—') + ' сообщ.';
            } else {
                metaEl.textContent = '';
            }
        }
        if (!listEl) return;
        if (!append) {
            listEl.innerHTML = '';
            thoughtsPagination.allItems = items.slice ? items.slice() : items;
        } else {
            thoughtsPagination.allItems = thoughtsPagination.allItems.concat(items);
        }
        if (!thoughtsPagination.allItems.length) {
            listEl.innerHTML = '<p class="journal-desc">Пока нет записей.</p>' +
                '<p class="journal-desc muted">Включите обдумывание в настройках или нажмите «Подумать сейчас» — записи появятся после ответа модели.</p>';
        } else {
            thoughtsPagination.allItems.forEach(function (it) {
                listEl.appendChild(renderItem(it));
            });
        }
        if (data && data.page != null) {
            thoughtsPagination.page = data.page;
            thoughtsPagination.totalPages = data.total_pages || 1;
            thoughtsPagination.total = data.total != null ? data.total : 0;
            thoughtsPagination.perPage = data.per_page || 50;
        }
        if (loadMoreWrap) {
            if (thoughtsPagination.page < thoughtsPagination.totalPages) {
                loadMoreWrap.style.display = 'block';
                if (loadMoreBtn) { loadMoreBtn.style.display = ''; loadMoreBtn.disabled = false; }
                if (loadMoreInfo) loadMoreInfo.textContent = ' Показано ' + thoughtsPagination.allItems.length + ' из ' + thoughtsPagination.total;
            } else {
                loadMoreWrap.style.display = thoughtsPagination.total > thoughtsPagination.perPage ? 'block' : 'none';
                if (loadMoreBtn) loadMoreBtn.style.display = 'none';
                if (loadMoreInfo) loadMoreInfo.textContent = thoughtsPagination.total ? ' Всего ' + thoughtsPagination.total + ' записей.' : '';
            }
        }
        if (scrollDown !== false) {
            listEl.scrollTop = listEl.scrollHeight;
        }
    }

    function loadThoughts(doScroll, appendPage) {
        var page = appendPage ? thoughtsPagination.page + 1 : 1;
        var url = '/api/thoughts?page=' + page + '&per_page=50';
        if (loadMoreBtn && appendPage) loadMoreBtn.disabled = true;
        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (appendPage && data.items && data.items.length) {
                    thoughtsPagination.page = data.page;
                    thoughtsPagination.allItems = thoughtsPagination.allItems.concat(data.items);
                    thoughtsPagination.totalPages = data.total_pages;
                    thoughtsPagination.total = data.total;
                    data.items.forEach(function (it) { listEl.appendChild(renderItem(it)); });
                    if (loadMoreWrap) {
                        if (thoughtsPagination.page >= thoughtsPagination.totalPages) {
                            loadMoreBtn.style.display = 'none';
                            if (loadMoreInfo) loadMoreInfo.textContent = ' Всего ' + thoughtsPagination.total + ' записей.';
                        } else {
                            if (loadMoreInfo) loadMoreInfo.textContent = ' Показано ' + thoughtsPagination.allItems.length + ' из ' + thoughtsPagination.total;
                            loadMoreBtn.disabled = false;
                        }
                    }
                    if (doScroll !== false) listEl.scrollTop = listEl.scrollHeight;
                } else {
                    render(data, doScroll, false);
                }
            })
            .catch(function () {
                if (listEl) listEl.innerHTML = '<p class="error-msg">Не удалось загрузить журнал.</p>';
                if (loadMoreBtn) loadMoreBtn.disabled = false;
            });
    }

    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', function () {
            loadThoughts(true, true);
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            if (!confirm('Удалить все записи журнала мыслей?')) return;
            clearBtn.disabled = true;
            fetch('/api/thoughts', { method: 'DELETE' })
                .then(function (r) { return r.json(); })
                .then(function () { loadThoughts(); })
                .catch(function () { alert('Не удалось очистить журнал.'); })
                .finally(function () { clearBtn.disabled = false; });
        });
    }

    var summarizeBtn = document.getElementById('thoughtsSummarize');
    var progressWrap = document.getElementById('thoughtsSummarizeProgressWrap');
    var progressStatus = document.getElementById('thoughtsSummarizeStatus');
    var progressBar = document.getElementById('thoughtsSummarizeBar');

    function setSummarizeProgress(percent, text) {
        if (progressBar) progressBar.style.width = (percent || 0) + '%';
        if (progressStatus) progressStatus.textContent = text || '';
    }

    function hideSummarizeProgress() {
        if (progressWrap) progressWrap.style.display = 'none';
        setSummarizeProgress(0, '');
    }

    if (summarizeBtn) {
        summarizeBtn.addEventListener('click', function () {
            if (!confirm('Обобщить весь журнал мыслей и сохранить итог в долговременную память? Процесс может занять время.')) return;
            summarizeBtn.disabled = true;
            var origText = summarizeBtn.textContent;
            summarizeBtn.textContent = 'Идёт обобщение...';
            if (progressWrap) progressWrap.style.display = 'inline';
            setSummarizeProgress(0, 'Подготовка...');

            fetch('/api/thoughts/summarize/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
                .then(function (response) {
                    if (!response.ok) {
                        return response.json().then(function (j) { throw new Error(j.error || 'Ошибка'); });
                    }
                    var reader = response.body.getReader();
                    var decoder = new TextDecoder();
                    var totalChunks = 0;
                    var buf = '';
                    function processChunk() {
                        return reader.read().then(function (result) {
                            if (result.done) {
                                return Promise.resolve();
                            }
                            buf += decoder.decode(result.value, { stream: true });
                            var parts = buf.split('\n\n');
                            buf = parts.pop() || '';
                            for (var p = 0; p < parts.length; p++) {
                                var block = parts[p];
                                var lastEvent = '';
                                var dataStr = '';
                                block.split('\n').forEach(function (line) {
                                    if (line.indexOf('event:') === 0) lastEvent = line.replace(/^event:\s*/, '').trim();
                                    if (line.indexOf('data:') === 0) dataStr = line.replace(/^data:\s*/, '');
                                });
                                if (!lastEvent || !dataStr) return;
                                var data = {};
                                try { data = JSON.parse(dataStr); } catch (e) { }
                                if (lastEvent === 'start') {
                                    var n = data.total_entries || 0;
                                    var cs = data.chunk_size || 10;
                                    totalChunks = cs > 0 ? Math.ceil(n / cs) : 0;
                                    setSummarizeProgress(0, 'Обобщение блоков: 0 из ' + totalChunks + '...');
                                } else if (lastEvent === 'progress') {
                                    var phase = data.phase;
                                    var cur = data.current || 0;
                                    var tot = data.total || 1;
                                    if (phase === 'chunk') {
                                        var pct = totalChunks > 0 ? Math.round((cur / totalChunks) * 90) : 0;
                                        setSummarizeProgress(pct, 'Блок ' + cur + ' из ' + tot + '...');
                                    } else if (phase === 'final') {
                                        setSummarizeProgress(95, 'Финальное обобщение...');
                                    }
                                } else if (lastEvent === 'done') {
                                    setSummarizeProgress(100, 'Готово.');
                                    alert('Готово. Итог сохранён в долговременную память (раздел «Память»).');
                                    return Promise.resolve();
                                } else if (lastEvent === 'error') {
                                    throw new Error(data.message || 'Ошибка обобщения');
                                }
                            }
                            return processChunk();
                        });
                    }
                    return processChunk().then(function () {
                        if (progressBar && progressBar.style.width !== '100%') {
                            setSummarizeProgress(100, 'Готово.');
                            alert('Готово. Итог сохранён в долговременную память (раздел «Память»).');
                        }
                    });
                })
                .catch(function (err) {
                    hideSummarizeProgress();
                    alert(err.message || 'Ошибка обобщения');
                })
                .finally(function () {
                    summarizeBtn.disabled = false;
                    summarizeBtn.textContent = origText;
                    setTimeout(hideSummarizeProgress, 1500);
                });
        });
    }

    var archiveBtn = document.getElementById('thoughtsArchive');
    if (archiveBtn) {
        archiveBtn.addEventListener('click', function () {
            if (!confirm('Архивировать текущий журнал и очистить его? Архив появится в разделе «История».')) return;
            archiveBtn.disabled = true;
            fetch('/api/thoughts/archive', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
                .then(function (r) {
                    if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || 'Ошибка'); });
                    return r.json();
                })
                .then(function () {
                    loadThoughts();
                    alert('Журнал архивирован. Перейти в «История»?');
                    window.location.href = '/history';
                })
                .catch(function (err) { alert(err.message || 'Ошибка архивации'); })
                .finally(function () { archiveBtn.disabled = false; });
        });
    }

    function startRefreshTimer() {
        setInterval(function () { loadThoughts(thinkingRunning); }, refreshIntervalMs);
    }

    fetch('/api/thinking/status')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            thinkingRunning = !!data.running;
            loadThoughts(thinkingRunning);
            if (thinkingRunning) {
                startRefreshTimer();
            }
        })
        .catch(function () {
            loadThoughts(true);
            startRefreshTimer();
        });
})();
