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
            tr.innerHTML = `
                <td style="color: var(--text-muted); font-size: 13px;">${index + 1}</td>
                <td>${user.name || '未知用户'}</td>
                <td class="stat-highlight">${user.message_count.toLocaleString()}</td>
                <td>${user.active_days} 天</td>
            `;
            body.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load user ranking:', e);
    }
}

function startSync(isFull = false) {
    const btnInc = document.getElementById('btn-sync-chats');
    const btnFull = document.getElementById('btn-full-sync-chats');
    const wrap = document.getElementById('sync-progress-wrap');
    const status = document.getElementById('sync-status');
    const detail = document.getElementById('sync-detail');
    const fill = document.getElementById('sync-progress-fill');
    
    if (btnInc) btnInc.disabled = true;
    if (btnFull) btnFull.disabled = true;
    
    wrap.style.display = 'block';
    fill.style.width = '5%';
    status.textContent = isFull ? '初始化全量同步...' : '初始化增量同步...';
    
    const url = `/api/chats/sync?full=${isFull ? 'true' : 'false'}`;
    const eventSource = new EventSource(url);
    
    eventSource.addEventListener('progress', (e) => {
        const data = JSON.parse(e.data);
        status.textContent = data.msg;
        if (data.messages_synced !== undefined) {
            detail.textContent = `累计消息: ${data.messages_synced}`;
        }
        
        if (data.done) {
            fill.style.width = '100%';
            eventSource.close();
            if (btnInc) btnInc.disabled = false;
            if (btnFull) btnFull.disabled = false;
            showAlert(`${isFull ? '全量' : '增量'}同步完成：${data.conversations} 个会话，${data.messages} 条消息。`, 'success');
            loadDashboard();
        }
    });
    
    eventSource.addEventListener('error', (e) => {
        console.error('SSE Error:', e);
        eventSource.close();
        if (btnInc) btnInc.disabled = false;
        if (btnFull) btnFull.disabled = false;
        status.textContent = '同步出错';
        showAlert('同步过程中发生错误，请检查网络或日志。', 'error');
    });
}

document.getElementById('btn-sync-chats')?.addEventListener('click', () => startSync(false));
document.getElementById('btn-full-sync-chats')?.addEventListener('click', () => startSync(true));

async function checkOngoingSync() {
    try {
        const res = await fetch('/api/sync/status?task=chat_sync');
        const data = await res.json();
        
        if (data && !data.done) {
            // Restore UI state
            const wrap = document.getElementById('sync-progress-wrap');
            const status = document.getElementById('sync-status');
            const detail = document.getElementById('sync-detail');
            const fill = document.getElementById('sync-progress-fill');
            const btnInc = document.getElementById('btn-sync-chats');
            const btnFull = document.getElementById('btn-full-sync-chats');

            wrap.style.display = 'block';
            status.textContent = data.msg;
            if (data.percent !== undefined) fill.style.width = data.percent + '%';
            if (data.messages_synced !== undefined) detail.textContent = `累计消息: ${data.messages_synced}`;
            
            if (btnInc) btnInc.disabled = true;
            if (btnFull) btnFull.disabled = true;

            // Start polling until done
            const timer = setInterval(async () => {
                const r = await fetch('/api/sync/status?task=chat_sync');
                const d = await r.json();
                if (!d || d.done) {
                    clearInterval(timer);
                    if (btnInc) btnInc.disabled = false;
                    if (btnFull) btnFull.disabled = false;
                    if (d && !d.error) {
                        fill.style.width = '100%';
                        showAlert('同步已在后台完成', 'success');
                        loadDashboard();
                    } else if (d && d.error) {
                        showAlert(d.msg || '同步失败', 'error');
                    }
                } else {
                    status.textContent = d.msg;
                    if (d.percent !== undefined) fill.style.width = d.percent + '%';
                    if (d.messages_synced !== undefined) detail.textContent = `累计消息: ${d.messages_synced}`;
                }
            }, 2000);
        }
    } catch (err) {
        console.error('Failed to check sync status:', err);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    checkOngoingSync();

    document.getElementById('btn-refresh-ranking')?.addEventListener('click', () => {
        loadUserRanking();
    });
});
