/**
 * Task Center (任务中心) — 前端逻辑
 *
 * 功能：
 * - 按类型（同步/分析/监控）筛选任务
 * - 按状态（进行中/已完成/失败/已取消）筛选任务
 * - 自动刷新进行中的任务
 * - 取消运行中的任务
 * - 清理历史任务
 */
(function () {
    'use strict';

    // ── State ──
    let currentType = '';
    let currentStatus = '';
    let currentPage = 0;
    const PAGE_SIZE = 30;
    let totalTasks = 0;
    let autoRefreshTimer = null;
    let summary = {};

    // ── DOM refs ──
    const $list = document.getElementById('task-list');
    const $empty = document.getElementById('task-empty');
    const $alert = document.getElementById('task-alert');
    const $pagination = document.getElementById('task-pagination');
    const $pageInfo = document.getElementById('page-info');
    const $btnPrev = document.getElementById('btn-prev');
    const $btnNext = document.getElementById('btn-next');

    // ── Status labels ──
    const STATUS_LABELS = {
        pending: '等待中',
        running: '进行中',
        success: '已完成',
        failed: '失败',
        canceled: '已取消',
    };
    const TYPE_LABELS = {
        sync: '同步',
        analysis: '分析',
        monitor: '监控',
    };

    // ── Helpers ──
    function showAlert(msg, type) {
        if (!msg) { $alert.style.display = 'none'; $alert.textContent = ''; return; }
        $alert.textContent = msg;
        $alert.className = 'alert alert-' + (type || 'info');
        $alert.style.display = 'block';
        if (type !== 'error') setTimeout(() => { $alert.style.display = 'none'; }, 3000);
    }

    // Parse ISO timestamp from server as UTC
    function parseUTC(iso) {
        if (!iso) return null;
        // Already has timezone info (Z or +HH:MM) — parse as-is
        if (iso.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(iso) || /[+-]\d{4}$/.test(iso)) {
            return new Date(iso);
        }
        // Bare timestamp without timezone — treat as UTC
        return new Date(iso + 'Z');
    }

    function formatTime(iso) {
        if (!iso) return '-';
        try {
            const d = parseUTC(iso);
            if (!d || isNaN(d.getTime())) return iso;
            const pad = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        } catch { return iso; }
    }

    function formatDuration(start, end) {
        if (!start) return '-';
        const s = parseUTC(start);
        const e = end ? parseUTC(end) : new Date();
        if (!s) return '-';
        const diff = Math.floor((e - s) / 1000);
        if (diff < 0) return '0秒';
        if (diff < 60) return `${diff}秒`;
        if (diff < 3600) return `${Math.floor(diff/60)}分${diff%60}秒`;
        return `${Math.floor(diff/3600)}时${Math.floor((diff%3600)/60)}分`;
    }

    // ── Fetch summary ──
    async function fetchSummary() {
        try {
            const resp = await fetch('/api/tasks/summary');
            if (!resp.ok) return;
            summary = await resp.json();
            updateSummaryCounts();
        } catch (e) {
            console.error('[tasks] fetchSummary failed:', e);
        }
    }

    function updateSummaryCounts() {
        let totalAll = 0;
        let totalRunning = 0, totalSuccess = 0, totalFailed = 0, totalCanceled = 0;

        for (const type of ['sync', 'analysis', 'monitor', 'other']) {
            const s = summary[type] || {};
            const typeTotal = (s.running || 0) + (s.success || 0) + (s.failed || 0) + (s.canceled || 0) + (s.pending || 0);
            const el = document.getElementById('count-' + type);
            if (el) el.textContent = typeTotal;
            totalAll += typeTotal;
            totalRunning += s.running || 0;
            totalSuccess += s.success || 0;
            totalFailed += s.failed || 0;
            totalCanceled += s.canceled || 0;
        }

        const elAll = document.getElementById('count-all');
        if (elAll) elAll.textContent = totalAll;

        const $fcountAll = document.getElementById('fcount-all');
        const $fcountRunning = document.getElementById('fcount-running');
        const $fcountSuccess = document.getElementById('fcount-success');
        const $fcountFailed = document.getElementById('fcount-failed');
        const $fcountCanceled = document.getElementById('fcount-canceled');
        if ($fcountAll) $fcountAll.textContent = totalAll;
        if ($fcountRunning) $fcountRunning.textContent = totalRunning;
        if ($fcountSuccess) $fcountSuccess.textContent = totalSuccess;
        if ($fcountFailed) $fcountFailed.textContent = totalFailed;
        if ($fcountCanceled) $fcountCanceled.textContent = totalCanceled;
    }

    // ── Fetch tasks ──
    async function fetchTasks() {
        const params = new URLSearchParams();
        if (currentType) params.set('task_type', currentType);
        if (currentStatus) params.set('status', currentStatus);
        params.set('limit', PAGE_SIZE);
        params.set('offset', currentPage * PAGE_SIZE);

        try {
            const resp = await fetch('/api/tasks?' + params.toString());
            if (!resp.ok) { showAlert('加载任务列表失败', 'error'); return; }
            const data = await resp.json();
            totalTasks = data.total;
            renderTasks(data.tasks);
            renderPagination();
        } catch (e) {
            console.error('[tasks] fetchTasks failed:', e);
            showAlert('网络错误，请稍后重试', 'error');
        }
    }

    // ── Render tasks ──
    function renderTasks(tasks) {
        // Clear existing task cards (keep empty placeholder)
        const cards = $list.querySelectorAll('.task-card');
        cards.forEach(c => c.remove());

        if (!tasks || tasks.length === 0) {
            $empty.style.display = 'flex';
            $pagination.style.display = 'none';
            return;
        }

        $empty.style.display = 'none';

        for (const task of tasks) {
            $list.appendChild(createTaskCard(task));
        }
    }

    // Use global escapeHtml from base.js (handles quotes for attribute safety)
    var escapeHtml = window.escapeHtml || function(s) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(s));
        return div.innerHTML;
    };

    function createTaskCard(task) {
        const div = document.createElement('div');
        div.className = 'task-card';
        div.dataset.id = task.id;

        const isRunning = task.status === 'running';
        const statusClass = 'task-status-' + task.status;
        const typeLabel = escapeHtml(TYPE_LABELS[task.task_type] || task.task_type || '其他');
        const statusLabel = escapeHtml(STATUS_LABELS[task.status] || task.status);

        let progressHtml = '';
        if (isRunning && task.progress > 0) {
            progressHtml = `
                <div class="task-progress-bar">
                    <div class="task-progress-fill" style="width: ${task.progress}%"></div>
                </div>
                <span class="task-progress-text">${task.progress}%</span>
            `;
        }

        let errorHtml = '';
        if (task.status === 'failed' && task.error) {
            errorHtml = `<div class="task-error">${escapeHtml(task.error)}</div>`;
        }

        let actionsHtml = '';
        if (isRunning) {
            actionsHtml = `<button class="btn btn-outline btn-sm task-cancel-btn" data-id="${escapeHtml(String(task.id))}" title="取消任务">取消</button>`;
        }

        div.innerHTML = `
            <div class="task-card-header">
                <div class="task-card-left">
                    <span class="task-type-badge task-type-${task.task_type || 'other'}">${typeLabel}</span>
                    <span class="task-name">${escapeHtml(task.name)}</span>
                </div>
                <div class="task-card-right">
                    <span class="task-status-badge ${statusClass}">${statusLabel}</span>
                    ${actionsHtml}
                </div>
            </div>
            ${task.message ? `<div class="task-message">${escapeHtml(task.message)}</div>` : ''}
            ${errorHtml}
            ${progressHtml}
            <div class="task-card-footer">
                <span class="task-time">创建: ${formatTime(task.created_at)}</span>
                ${task.started_at ? `<span class="task-time">开始: ${formatTime(task.started_at)}</span>` : ''}
                ${task.ended_at ? `<span class="task-time">结束: ${formatTime(task.ended_at)}</span>` : ''}
                ${task.started_at ? `<span class="task-duration">耗时: ${formatDuration(task.started_at, task.ended_at)}</span>` : ''}
            </div>
        `;

        return div;
    }

    // ── Pagination ──
    function renderPagination() {
        if (totalTasks <= PAGE_SIZE) {
            $pagination.style.display = 'none';
            return;
        }
        $pagination.style.display = 'flex';
        const totalPages = Math.ceil(totalTasks / PAGE_SIZE);
        $pageInfo.textContent = `第 ${currentPage + 1} / ${totalPages} 页 (共 ${totalTasks} 条)`;
        $btnPrev.disabled = currentPage <= 0;
        $btnNext.disabled = currentPage >= totalPages - 1;
    }

    // ── Auto-refresh (always poll to keep data fresh) ──
    function startAutoRefresh() {
        stopAutoRefresh();
        autoRefreshTimer = setInterval(() => {
            fetchSummary();
            fetchTasks();
        }, 5000);
    }

    function stopAutoRefresh() {
        if (autoRefreshTimer) {
            clearInterval(autoRefreshTimer);
            autoRefreshTimer = null;
        }
    }

    // ── Event handlers ──
    function init() {
        // Type tabs
        document.querySelectorAll('.task-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.task-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                currentType = tab.dataset.type;
                currentPage = 0;
                fetchTasks();
            });
        });

        // Status filters
        document.querySelectorAll('.task-filter').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.task-filter').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentStatus = btn.dataset.status;
                currentPage = 0;
                fetchTasks();
            });
        });

        // Pagination
        $btnPrev.addEventListener('click', () => {
            if (currentPage > 0) { currentPage--; fetchTasks(); }
        });
        $btnNext.addEventListener('click', () => {
            currentPage++;
            fetchTasks();
        });

        // Refresh button
        document.getElementById('btn-refresh').addEventListener('click', () => {
            fetchSummary();
            fetchTasks();
            showAlert('已刷新', 'info');
        });

        // Cleanup button
        document.getElementById('btn-cleanup').addEventListener('click', async () => {
            if (!confirm('确定要清理超过 24 小时的已完成任务吗？')) return;
            try {
                const resp = await fetch('/api/tasks/cleanup', { method: 'POST' });
                if (resp.ok) {
                    const data = await resp.json();
                    showAlert(`已清理 ${data.deleted || 0} 条历史任务`, 'success');
                    fetchSummary();
                    fetchTasks();
                } else {
                    showAlert('清理失败', 'error');
                }
            } catch (e) {
                showAlert('网络错误', 'error');
            }
        });

        // Cancel buttons (event delegation)
        $list.addEventListener('click', async (e) => {
            const btn = e.target.closest('.task-cancel-btn');
            if (!btn) return;
            const taskId = btn.dataset.id;
            if (!confirm(`确定要取消任务 "${taskId}" 吗？`)) return;
            try {
                const resp = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' });
                if (resp.ok) {
                    showAlert('任务取消请求已发送', 'success');
                    fetchSummary();
                    fetchTasks();
                } else {
                    const data = await resp.json();
                    showAlert(data.detail || '取消失败', 'error');
                }
            } catch (e) {
                showAlert('网络错误', 'error');
            }
        });

        // Initial load + start auto-refresh
        fetchSummary();
        fetchTasks();
        startAutoRefresh();
    }

    // ── Start ──
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
