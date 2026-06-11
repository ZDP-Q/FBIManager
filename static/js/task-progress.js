// TaskProgress — unified task progress tracking component.
//
// Usage:
//   const tp = new TaskProgress({
//     taskId: 'post_sync',
//     container: '#sync-progress-container',
//     bar: '#sync-progress-bar',
//     status: '#progress-status',
//     percent: '#progress-percent',
//     onComplete: (data) => { location.reload(); },
//     onError: (msg) => { showAlert(msg, 'error'); },
//     onProgress: (data) => { /* extra UI updates */ },
//   });
//   tp.startSSE('/api/sync/stream?limit=20');
//   // or: tp.startPolling(2000);
//   // On page load: tp.restore() — checks if task is already running
class TaskProgress {
    constructor({ taskId, container, bar, status, percent, detail, onComplete, onError, onProgress }) {
        this.taskId = taskId;
        this.containerEl = typeof container === 'string' ? document.querySelector(container) : container;
        this.barEl = typeof bar === 'string' ? document.querySelector(bar) : bar;
        this.statusEl = typeof status === 'string' ? document.querySelector(status) : status;
        this.percentEl = typeof percent === 'string' ? document.querySelector(percent) : percent;
        this.detailEl = typeof detail === 'string' ? document.querySelector(detail) : detail;
        this.onComplete = onComplete || (() => {});
        this.onError = onError || (() => {});
        this.onProgress = onProgress || (() => {});
        this._eventSource = null;
        this._pollTimer = null;
        this._done = false;
        this._seenRunning = false;
    }

    _show() {
        if (this.containerEl) this.containerEl.style.display = 'block';
    }

    _hide() {
        if (this.containerEl) this.containerEl.style.display = 'none';
    }

    _updateUI(data) {
        const pct = data.percent ?? data.progress ?? 0;
        const msg = data.msg ?? data.message ?? '';
        if (this.barEl) this.barEl.style.width = `${pct}%`;
        if (this.percentEl) this.percentEl.textContent = `${pct}%`;
        if (this.statusEl && msg) this.statusEl.textContent = msg;
        if (this.detailEl && data.detail) this.detailEl.textContent = data.detail;
        this.onProgress(data);
    }

    _handleDone(data) {
        this._done = true;
        this.stop();
        if (data.error) {
            this.onError(data.msg || data.message || '任务失败');
        } else if (data.canceled) {
            this._updateUI({ percent: data.percent || 0, msg: data.msg || data.message || '已停止' });
            this.onComplete(data);
        } else {
            this._updateUI({ percent: 100, msg: data.msg || data.message || '完成' });
            this.onComplete(data);
        }
    }

    /**
     * Start SSE streaming. Used for real-time progress.
     * @param {string} url - SSE endpoint URL
     * @param {Object} [options]
     * @param {string} [options.eventName] - SSE event name to listen for (default: generic 'message')
     */
    startSSE(url, options = {}) {
        this._show();
        this.stop();

        const es = new EventSource(url);
        this._eventSource = es;

        const handler = (e) => {
            const data = JSON.parse(e.data);
            if (data.done) {
                if (this._seenRunning) {
                    this._handleDone(data);
                }
            } else {
                this._seenRunning = true;
                this._updateUI(data);
            }
        };

        if (options.eventName) {
            es.addEventListener(options.eventName, handler);
        } else {
            es.onmessage = handler;
        }

        es.onerror = () => {
            es.close();
            this._eventSource = null;
            if (!this._done) {
                // Fallback: switch to polling to recover
                this.startPolling(2000);
            }
        };
    }

    /**
     * Start polling. Used for recovery or tasks without SSE.
     * @param {number} [interval=2000] - Poll interval in ms
     */
    startPolling(interval = 2000) {
        this._show();
        this.stop();

        const poll = async () => {
            if (this._done) return;
            try {
                const res = await fetch(`/api/sync/status?task=${encodeURIComponent(this.taskId)}`);
                const data = await res.json();
                if (!data || data.done) {
                    // Only fire _handleDone if we've seen the task running,
                    // otherwise this is a stale "not found" response before the task was created.
                    if (this._seenRunning) {
                        this._handleDone(data || { error: true, msg: '任务不存在' });
                    }
                } else {
                    this._seenRunning = true;
                    this._updateUI(data);
                }
            } catch {
                this.stop();
                this.onError('无法获取任务状态');
            }
        };

        poll();
        this._pollTimer = setInterval(poll, interval);
    }

    /**
     * Check if task is already running (e.g. on page load). Restores UI if so.
     * @returns {Promise<boolean>} true if a running task was found
     */
    async restore() {
        try {
            const res = await fetch(`/api/sync/status?task=${encodeURIComponent(this.taskId)}`);
            const data = await res.json();
            if (data && !data.done) {
                this._show();
                this._updateUI(data);
                this.startPolling(2000);
                return true;
            }
        } catch {
            // ignore
        }
        return false;
    }

    /**
     * Stop all listeners (SSE and polling).
     */
    stop() {
        if (this._eventSource) {
            this._eventSource.close();
            this._eventSource = null;
        }
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    }

    /**
     * Cancel the running task via API.
     */
    async cancel() {
        try {
            await fetch(`/api/tasks/${encodeURIComponent(this.taskId)}/cancel`, { method: 'POST' });
        } catch {
            // fallback to legacy endpoint
            try {
                await fetch('/api/sync/stop', { method: 'POST' });
            } catch {
                // ignore
            }
        }
        this.stop();
        this._hide();
    }
}
