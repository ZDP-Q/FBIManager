document.addEventListener('DOMContentLoaded', function () {
    const alertEl = document.getElementById('schedule-alert');
    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressStatus = document.getElementById('progress-status');
    const progressPercent = document.getElementById('progress-percent');
    const batchAnalyzeBtn = document.getElementById('btn-batch-analyze');
    const batchPushBtn = document.getElementById('btn-batch-push');
    const pushCountEl = document.getElementById('push-count');
    const selectAllCb = document.getElementById('select-all');

    function showAlert(msg, type) {
        if (!alertEl) return;
        alertEl.className = 'alert alert-' + (type || 'info');
        alertEl.textContent = msg;
        alertEl.style.display = 'block';
        if (type === 'success') setTimeout(() => { alertEl.style.display = 'none'; }, 3000);
    }

    function hideAlert() {
        if (alertEl) alertEl.style.display = 'none';
    }

    function updateProgress(pct, msg) {
        if (progressContainer) progressContainer.style.display = 'block';
        if (progressBar) progressBar.style.width = pct + '%';
        if (progressPercent) progressPercent.textContent = pct + '%';
        if (progressStatus && msg) progressStatus.textContent = msg;
    }

    function hideProgress() {
        if (progressContainer) progressContainer.style.display = 'none';
        if (progressBar) progressBar.style.width = '0%';
    }

    // Update batch push button visibility
    function updatePushCount() {
        const checked = document.querySelectorAll('.push-checkbox:checked');
        if (pushCountEl) pushCountEl.textContent = checked.length;
        if (batchPushBtn) batchPushBtn.style.display = checked.length > 0 ? '' : 'none';
    }

    // Select all checkbox
    if (selectAllCb) {
        selectAllCb.addEventListener('change', function () {
            document.querySelectorAll('.push-checkbox').forEach(cb => {
                cb.checked = selectAllCb.checked;
            });
            updatePushCount();
        });
    }

    // Individual checkboxes
    document.querySelectorAll('.push-checkbox').forEach(cb => {
        cb.addEventListener('change', updatePushCount);
    });

    // Batch analyze
    if (batchAnalyzeBtn) {
        batchAnalyzeBtn.addEventListener('click', async function () {
            if (batchAnalyzeBtn.disabled) return;
            batchAnalyzeBtn.disabled = true;
            batchAnalyzeBtn.textContent = '分析中...';
            hideAlert();
            updateProgress(0, '正在启动批量分析...');

            try {
                const r = await fetch('/api/video/batch-analyze', { method: 'POST' });
                if (!r.ok) throw new Error((await r.json()).detail || '批量分析失败');
                const data = await r.json();

                if (data.total === 0) {
                    showAlert('没有需要分析的视频。', 'info');
                    hideProgress();
                    batchAnalyzeBtn.disabled = false;
                    batchAnalyzeBtn.textContent = '批量分析未识别视频';
                    return;
                }

                const bp = new TaskProgress({
                    taskId: 'batch_video_analysis',
                    container: '#progress-container',
                    bar: '#progress-bar',
                    status: '#progress-status',
                    percent: '#progress-percent',
                    onComplete: (d) => {
                        hideProgress();
                        const msg = (d.result || {}).msg || d.message || '批量分析完成。';
                        showAlert(msg, 'success');
                        setTimeout(() => location.reload(), 1500);
                    },
                    onError: (msg) => {
                        hideProgress();
                        showAlert(msg || '批量分析失败', 'error');
                    },
                    onProgress: (d) => {
                        updateProgress(d.percent ?? 0, d.msg ?? '正在分析...');
                    },
                });
                bp.startPolling(2000);
            } catch (e) {
                hideProgress();
                showAlert(e.message, 'error');
                batchAnalyzeBtn.disabled = false;
                batchAnalyzeBtn.textContent = '批量分析未识别视频';
            }
        });
    }

    // Push single post
    document.querySelectorAll('.btn-push').forEach(btn => {
        btn.addEventListener('click', async function () {
            const postId = btn.getAttribute('data-post-id');
            if (!postId || btn.disabled) return;
            btn.disabled = true;
            btn.textContent = '推送中...';

            try {
                const r = await fetch(`/api/video/push/${postId}`, { method: 'POST' });
                if (!r.ok) throw new Error((await r.json()).detail || '推送失败');
                showAlert('推送成功，刷新页面...', 'success');
                setTimeout(() => location.reload(), 600);
            } catch (e) {
                showAlert(e.message, 'error');
                btn.textContent = '推送';
                btn.disabled = false;
            }
        });
    });

    // Batch push
    if (batchPushBtn) {
        batchPushBtn.addEventListener('click', async function () {
            const checkboxes = document.querySelectorAll('.push-checkbox:checked');
            if (checkboxes.length === 0) return;

            const postIds = Array.from(checkboxes).map(cb => cb.getAttribute('data-post-id'));
            batchPushBtn.disabled = true;
            batchPushBtn.textContent = '推送中...';
            hideAlert();

            let success = 0, failed = 0;
            for (const postId of postIds) {
                try {
                    const r = await fetch(`/api/video/push/${postId}`, { method: 'POST' });
                    if (!r.ok) throw new Error('推送失败');
                    success++;
                    // Update row
                    const row = document.getElementById('row-' + postId);
                    if (row) {
                        const statusCell = row.querySelector('td:nth-child(7)');
                        if (statusCell) statusCell.innerHTML = '<span class="badge badge-success">已推送</span>';
                        const cbCell = row.querySelector('td:first-child');
                        if (cbCell) cbCell.innerHTML = '';
                    }
                } catch {
                    failed++;
                }
            }

            showAlert(`推送完成：成功 ${success}，失败 ${failed}`, failed > 0 ? 'error' : 'success');
            batchPushBtn.disabled = false;
            batchPushBtn.textContent = '批量推送';
            updatePushCount();
        });
    }
});
