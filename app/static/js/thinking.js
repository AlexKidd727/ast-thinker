/**
 * Кнопки запуска/остановки процесса мышления и отображение статуса (запущен / сон)
 */
(function () {
    var statusEl = document.getElementById('thinkingStatus');
    var startBtn = document.getElementById('thinkingStart');
    var stopBtn = document.getElementById('thinkingStop');
    if (!statusEl || (!startBtn && !stopBtn)) return;

    function applyState(running) {
        statusEl.textContent = running ? 'запущен' : 'сон';
        if (startBtn) startBtn.disabled = running;
        if (stopBtn) stopBtn.disabled = !running;
    }

    function updateStatus() {
        fetch('/api/thinking/status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                applyState(data.running);
            })
            .catch(function () {
                statusEl.textContent = '?';
            });
    }

    if (startBtn) {
        startBtn.addEventListener('click', function () {
            if (startBtn.disabled) return;
            startBtn.disabled = true;
            fetch('/api/thinking/start', { method: 'POST' })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    applyState(data.running);
                })
                .catch(function () {
                    updateStatus();
                    startBtn.disabled = false;
                });
        });
    }
    if (stopBtn) {
        stopBtn.addEventListener('click', function () {
            if (stopBtn.disabled) return;
            stopBtn.disabled = true;
            fetch('/api/thinking/stop', { method: 'POST' })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    applyState(data.running);
                })
                .catch(function () {
                    updateStatus();
                    stopBtn.disabled = false;
                });
        });
    }
    updateStatus();
})();
