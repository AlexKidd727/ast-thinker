/**
 * Чат: загрузка сообщений, отправка текста и файлов
 */
(function () {
    var messagesEl = document.getElementById('chatMessages');
    var summaryInfoEl = document.getElementById('chatSummaryInfo');
    var metaEl = document.getElementById('chatMeta');
    var form = document.getElementById('chatForm');
    var textArea = document.getElementById('chatText');
    var fileInput = document.getElementById('chatFiles');
    var filesListEl = document.getElementById('chatFilesList');
    var sendBtn = document.getElementById('chatSend');
    var thinkBtn = document.getElementById('chatThinkNow');
    var clearBtn = document.getElementById('chatClear');
    var loadMoreWrap = document.getElementById('chatLoadMoreWrap');
    var loadMoreBtn = document.getElementById('chatLoadMore');
    var loadMoreInfo = document.getElementById('chatLoadMoreInfo');
    var chatPagination = { page: 1, totalPages: 1, total: 0, perPage: 50, allMessages: [] };

    function renderMessage(m) {
        var div = document.createElement('div');
        div.className = 'chat-msg ' + m.role;
        var timeStr = typeof formatDateTime !== 'undefined' ? formatDateTime(m.created_at) : (m.created_at || '');
        var att = (m.attachments || []).length ? ' <span class="chat-att">(' + m.attachments.length + ' файл.)</span>' : '';
        var content;
        if (m.role === 'assistant' && typeof markdownToHtml === 'function') {
            content = '<div class="msg-content markdown-content">' + markdownToHtml(m.text || '') + '</div>';
        } else {
            content = '<span class="white-space-pre">' + (typeof escapeHtml !== 'undefined' ? escapeHtml(m.text || '').replace(/\n/g, '<br>') : (m.text || '')) + '</span>';
        }
        var metaLine = '';
        if (m.model || (m.meta && (m.meta.model || m.meta.context_limit))) {
            var mx = m.meta || {};
            metaLine = '<div class="chat-msg-meta">Модель: ' + (m.model || mx.model || '—') +
                ' | Контекст: ' + (mx.context_limit != null ? mx.context_limit : '—') + ' ток.' +
                ' | Чат: макс. ' + (mx.chat_max_tokens || '—') + ' ток.' +
                ' | temp. ' + (mx.temperature || '—') + '</div>';
        }
        div.innerHTML = '<time>' + (typeof escapeHtml !== 'undefined' ? escapeHtml(timeStr) : timeStr) + '</time>' + content + att + metaLine;
        return div;
    }

    function renderMessages(data, append) {
        var list = (data && data.messages) ? data.messages : (Array.isArray(data) ? data : []);
        var summaryInfo = (data && data.summary_info) ? data.summary_info : '';
        if (summaryInfoEl) summaryInfoEl.textContent = summaryInfo;
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
        if (!append) {
            chatPagination.allMessages = list.slice();
            messagesEl.innerHTML = '';
        } else {
            chatPagination.allMessages = chatPagination.allMessages.concat(list);
        }
        chatPagination.allMessages.forEach(function (m) {
            messagesEl.appendChild(renderMessage(m));
        });
        if (!append) {
            chatPagination.page = (data && data.page) ? data.page : 1;
            chatPagination.totalPages = (data && data.total_pages) ? data.total_pages : 1;
            chatPagination.total = (data && data.total) != null ? data.total : chatPagination.allMessages.length;
            chatPagination.perPage = (data && data.per_page) ? data.per_page : 50;
        }
        if (loadMoreWrap) {
            if (chatPagination.page < chatPagination.totalPages) {
                loadMoreWrap.style.display = 'block';
                if (loadMoreInfo) loadMoreInfo.textContent = ' Показано ' + chatPagination.allMessages.length + ' из ' + chatPagination.total;
                if (loadMoreBtn) loadMoreBtn.disabled = false;
            } else {
                loadMoreWrap.style.display = chatPagination.total > chatPagination.perPage ? 'block' : 'none';
                if (loadMoreInfo) loadMoreInfo.textContent = chatPagination.total ? ' Всего ' + chatPagination.total + ' сообщ.' : '';
                if (loadMoreBtn) loadMoreBtn.style.display = 'none';
            }
        }
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function loadMessages(appendPage) {
        var url = '/api/chat/messages?page=' + (appendPage ? chatPagination.page + 1 : 1) + '&per_page=50';
        if (loadMoreBtn && appendPage) loadMoreBtn.disabled = true;
        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (appendPage && data.messages && data.messages.length) {
                    chatPagination.page = data.page;
                    chatPagination.allMessages = chatPagination.allMessages.concat(data.messages);
                    messagesEl.innerHTML = '';
                    chatPagination.allMessages.forEach(function (m) {
                        messagesEl.appendChild(renderMessage(m));
                    });
                    chatPagination.totalPages = data.total_pages;
                    chatPagination.total = data.total;
                    if (loadMoreWrap) {
                        if (chatPagination.page >= chatPagination.totalPages) {
                            loadMoreBtn.style.display = 'none';
                            if (loadMoreInfo) loadMoreInfo.textContent = ' Всего ' + chatPagination.total + ' сообщ.';
                        } else {
                            if (loadMoreInfo) loadMoreInfo.textContent = ' Показано ' + chatPagination.allMessages.length + ' из ' + chatPagination.total;
                            loadMoreBtn.disabled = false;
                        }
                    }
                    messagesEl.scrollTop = messagesEl.scrollHeight;
                } else {
                    renderMessages(data, false);
                }
            })
            .catch(function () {
                messagesEl.innerHTML = '<p class="error-msg">Не удалось загрузить сообщения.</p>';
                if (loadMoreBtn) loadMoreBtn.disabled = false;
            });
    }

    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', function () {
            loadMessages(true);
        });
    }

    function updateFilesList() {
        var files = fileInput.files;
        if (!files || files.length === 0) {
            filesListEl.textContent = '';
            return;
        }
        var names = [];
        for (var i = 0; i < files.length; i++) names.push(files[i].name);
        filesListEl.textContent = 'Выбрано: ' + names.join(', ');
    }

    fileInput.addEventListener('change', updateFilesList);

    if (thinkBtn) {
        thinkBtn.addEventListener('click', function () {
            thinkBtn.disabled = true;
            var text = (textArea.value || '').trim();
            var opts = {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(text ? { text: text } : {})
            };
            fetch('/api/thinking/think_now', opts)
                .then(function (r) { return r.json(); })
                .then(function () {
                    if (text) textArea.value = '';
                    thinkBtn.disabled = false;
                    window.location.href = '/thoughts';
                })
                .catch(function () {
                    thinkBtn.disabled = false;
                });
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            if (!confirm('Удалить все сообщения чата?')) return;
            clearBtn.disabled = true;
            fetch('/api/chat/messages', { method: 'DELETE' })
                .then(function (r) { return r.json(); })
                .then(function () {
                    loadMessages();
                })
                .catch(function () {
                    alert('Не удалось очистить чат.');
                })
                .finally(function () {
                    clearBtn.disabled = false;
                });
        });
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        var text = (textArea.value || '').trim();
        var files = fileInput.files;
        if (!text && (!files || files.length === 0)) return;

        sendBtn.disabled = true;
        var formData = new FormData();
        formData.append('text', text);
        for (var i = 0; i < (files ? files.length : 0); i++) {
            formData.append('files', files[i]);
        }

        fetch('/api/chat/send', {
            method: 'POST',
            body: formData
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || 'Ошибка'); });
                return r.json();
            })
            .then(function (data) {
                textArea.value = '';
                fileInput.value = '';
                updateFilesList();
                loadMessages();
            })
            .catch(function (err) {
                alert(err.message || 'Ошибка отправки');
            })
            .finally(function () {
                sendBtn.disabled = false;
            });
    });

    loadMessages();
})();
