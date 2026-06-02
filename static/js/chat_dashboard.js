const alertEl = document.getElementById('chat-alert');

function showAlert(msg, type = 'info') {
    alertEl.textContent = msg;
    alertEl.className = `alert alert-${type} visible`;
}

async function loadDashboard() {
    try {
        const r = await fetch('/api/chats/stats');
        if (!r.ok) throw new Error('获取统计数据失败');
        const data = await r.json();
        
        // Update Hero Stats
        const s = data.stats;
        document.getElementById('stat-total-users').textContent = (s.total_users || 0).toLocaleString();
        document.getElementById('stat-total-messages').textContent = (s.total_messages || 0).toLocaleString();
        document.getElementById('stat-longest-msg').textContent = (s.longest_msg_count || 0).toLocaleString();
        
        // Load User Ranking
        loadUserRanking();
    } catch (e) {
        showAlert(e.message, 'error');
    }
}

function getAvatarHtml(user) {
    const safeUrl = escapeHtml(user.avatar_url || '');
    const safeName = escapeHtml(user.name || '');
    if (user.avatar_url) {
        return `<img src="${safeUrl}" class="user-avatar" loading="lazy" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                <div class="user-avatar-fallback" style="display:none; background:${getAvatarColor(user.name)}">${escapeHtml(getInitials(user.name))}</div>`;
    }
    return `<div class="user-avatar-fallback" style="background:${getAvatarColor(user.name)}">${escapeHtml(getInitials(user.name))}</div>`;
}

function getInitials(name) {
    if (!name) return '?';
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) {
        return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    return name.slice(0, 1).toUpperCase();
}

function getAvatarColor(name) {
    const colors = [
        '#2563eb', '#7c3aed', '#db2777', '#dc2626', '#d97706', '#059669', '#0891b2'
    ];
    if (!name) return colors[0];
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
        hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    return colors[Math.abs(hash) % colors.length];
}

function formatBeijingTime(isoStr) {
    if (!isoStr) return '-';
    // Normalize +0000 to Z for consistent parsing
    const normalized = isoStr.replace(/\+0000$/, 'Z');
    const d = new Date(normalized);
    if (isNaN(d.getTime())) return isoStr;
    return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai' });
}

async function loadUserRanking() {
    const limitInput = document.getElementById('input-ranking-limit');
    const limit = limitInput ? limitInput.value : 100;
    const body = document.getElementById('user-ranking-body');
    if (!body) return;

    try {
        const r = await fetch(`/api/chats/user-ranking?limit=${limit}`);
        if (!r.ok) throw new Error('获取排名数据失败');
        const data = await r.json();

        body.innerHTML = '';
        if (data.length === 0) {
            body.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 40px;">暂无数据</td></tr>';
            return;
        }

        data.forEach((user, index) => {
            const tr = document.createElement('tr');
            const lastActive = user.last_active_time ? formatBeijingTime(user.last_active_time) : '-';
            tr.innerHTML = `
                <td style="color: var(--text-muted); font-size: 13px;">${index + 1}</td>
                <td>
                    <div class="user-info">
                        ${getAvatarHtml(user)}
                        <div style="display: flex; flex-direction: column;">
                            <span style="font-weight: 600;">${escapeHtml(user.name || '未知用户')}</span>
                            <span style="font-size: 11px; color: var(--text-muted); font-family: monospace;">ID: ${escapeHtml(user.user_id)}</span>
                        </div>
                    </div>
                </td>
                <td class="stat-highlight">${user.message_count.toLocaleString()}</td>
                <td>${user.active_days} 天</td>
                <td style="font-size: 13px; color: var(--text-muted);">${lastActive}</td>
            `;
            body.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load user ranking:', e);
    }
}

let chatSyncProgress = null;

function startSync(isFull = false) {
    const btnInc = document.getElementById('btn-sync-chats');
    const btnFull = document.getElementById('btn-full-sync-chats');
    if (btnInc) btnInc.disabled = true;
    if (btnFull) btnFull.disabled = true;

    chatSyncProgress = new TaskProgress({
        taskId: 'chat_sync',
        container: '#sync-progress-wrap',
        bar: '#sync-progress-fill',
        status: '#sync-status',
        percent: null,
        detail: '#sync-detail',
        onComplete: (data) => {
            if (btnInc) btnInc.disabled = false;
            if (btnFull) btnFull.disabled = false;
            const result = data.result || {};
            showAlert(`${isFull ? '全量' : '增量'}同步完成：${result.conversations ?? 0} 个会话，${result.messages ?? 0} 条消息。`, 'success');
            loadDashboard();
        },
        onError: (msg) => {
            if (btnInc) btnInc.disabled = false;
            if (btnFull) btnFull.disabled = false;
            showAlert(msg || '同步失败', 'error');
        },
        onProgress: (data) => {
            const detail = document.getElementById('sync-detail');
            if (detail && data.messages_synced !== undefined) {
                detail.textContent = `累计消息: ${data.messages_synced}`;
            }
        },
    });
    // Trigger sync via POST, then poll for progress
    fetch(`/api/chats/sync?full=${isFull ? 'true' : 'false'}`, { method: 'POST' }).catch(() => {});
    chatSyncProgress.startPolling();
}

document.getElementById('btn-sync-chats')?.addEventListener('click', () => startSync(false));
document.getElementById('btn-full-sync-chats')?.addEventListener('click', () => startSync(true));

async function checkOngoingSync() {
    chatSyncProgress = new TaskProgress({
        taskId: 'chat_sync',
        container: '#sync-progress-wrap',
        bar: '#sync-progress-fill',
        status: '#sync-status',
        percent: null,
        detail: '#sync-detail',
        onComplete: (data) => {
            const btnInc = document.getElementById('btn-sync-chats');
            const btnFull = document.getElementById('btn-full-sync-chats');
            if (btnInc) btnInc.disabled = false;
            if (btnFull) btnFull.disabled = false;
            showAlert('同步已在后台完成', 'success');
            loadDashboard();
        },
        onError: (msg) => {
            const btnInc = document.getElementById('btn-sync-chats');
            const btnFull = document.getElementById('btn-full-sync-chats');
            if (btnInc) btnInc.disabled = false;
            if (btnFull) btnFull.disabled = false;
            showAlert(msg || '同步失败', 'error');
        },
        onProgress: (data) => {
            const detail = document.getElementById('sync-detail');
            if (detail && data.messages_synced !== undefined) {
                detail.textContent = `累计消息: ${data.messages_synced}`;
            }
        },
    });
    const found = await chatSyncProgress.restore();
    if (found) {
        const btnInc = document.getElementById('btn-sync-chats');
        const btnFull = document.getElementById('btn-full-sync-chats');
        if (btnInc) btnInc.disabled = true;
        if (btnFull) btnFull.disabled = true;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    checkOngoingSync();

    document.getElementById('btn-refresh-ranking')?.addEventListener('click', () => {
        loadUserRanking();
    });
});
