/**
 * AST-Thinker: общие утилиты для веб-интерфейса
 */

function formatDateTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    return d.toLocaleString('ru-RU');
}

function escapeHtml(s) {
    if (!s) return '';
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

/**
 * Конвертация Markdown в HTML для вывода ответов LLM в чате и журнале.
 * Сначала экранируем <>&, чтобы сырой HTML/скрипты не выполнялись, затем парсим markdown.
 */
function markdownToHtml(text) {
    if (text == null) return '';
    var s = String(text);
    if (!s) return '';
    s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    if (typeof marked !== 'undefined') {
        if (typeof marked.parse === 'function') return marked.parse(s);
        if (typeof marked === 'function') return marked(s);
    }
    return escapeHtml(text).replace(/\n/g, '<br>');
}
