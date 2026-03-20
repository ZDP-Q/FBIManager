const alertEl = document.getElementById('comments-alert');
const insightsLoaded = new Set();

function showAlert(msg, type = 'info') {
    alertEl.textContent = msg;
    alertEl.className = `alert alert-${type} visible`;
}

const METRIC_LABELS = {
    page_impressions: '主页触达',
    page_media_view: '主页媒体浏览',
    page_total_media_view_unique: '主页媒体独立浏览',
    page_engaged_users: '互动用户数',
    page_views_total: '主页浏览',
    page_actions_post_reactions_total: '发帖被回应数',
    post_impressions: '帖子触达',
    post_media_view: '帖子媒体浏览',
    post_total_media_view_unique: '帖子媒体独立浏览',
    post_engaged_users: '帖子互动用户',
    post_clicks: '点击数',
    post_reactions_like_total: '点赞数',
    total_video_views: '视频播放',
    total_video_view_total_time: '视频总观看时长',
    total_video_complete_views: '视频完整观看',
};

function fmtNum(v) {
    if (typeof v !== 'number') return String(v ?? '-');
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toLocaleString('zh-CN');
}

/* Toggle post expand */
function togglePost(headerEl) {
    const card = headerEl.closest('.post-card');
    card.classList.toggle('expanded');
}

/* Insights toggle */
async function toggleInsights(postId, btn) {
    const panel = document.getElementById(`ins-${postId}`);
    if (panel.classList.contains('visible')) {
        panel.classList.remove('visible');
        return;
    }
    panel.classList.add('visible');
    if (insightsLoaded.has(postId)) return;
    insightsLoaded.add(postId);
    const grid = document.getElementById(`ins-grid-${postId}`);
    try {
        const r = await fetch(`/api/insights/${encodeURIComponent(postId)}`);
        if (!r.ok) throw new Error('获取失败');
        const result = await r.json();
        const metrics = result.data || [];
        if (!metrics.length) {
            grid.innerHTML = '<div class="ins-card"><div class="ins-label">暂无数据</div><div class="ins-value">-</div></div>';
            return;
        }
        grid.innerHTML = metrics.map(m => {
            const latest = Array.isArray(m.values) ? m.values[0] : null;
            const val = latest ? fmtNum(latest.value) : '-';
            const label = METRIC_LABELS[m.name] || m.name;
            return `<div class="ins-card"><div class="ins-label">${label}</div><div class="ins-value">${val}</div></div>`;
        }).join('');
    } catch (e) {
        grid.innerHTML = `<div class="ins-card"><div class="ins-label text-danger">${e.message}</div><div class="ins-value">-</div></div>`;
        insightsLoaded.delete(postId);
    }
}

/* Reply form */
function toggleReplyForm(commentId) {
    const form = document.getElementById(`rf-${commentId}`);
    form.classList.toggle('open');
}

async function sendReply(commentId) {
    const ta = document.getElementById(`rt-${commentId}`);
    const msg = ta.value.trim();
    if (!msg) { showAlert('请输入回复内容', 'warning'); return; }
    showAlert('正在发送回复...', 'info');
    try {
        const r = await fetch(`/api/comments/${commentId}/reply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg }),
        });
        if (!r.ok) throw new Error((await r.json()).detail || '发送失败');
        showAlert('回复发送成功，正在刷新...', 'success');
        setTimeout(() => location.reload(), 600);
    } catch (e) {
        showAlert(e.message, 'error');
    }
}

async function genAiReply(commentId, btn) {
    if (btn.disabled) return;
    const form = document.getElementById(`rf-${commentId}`);
    const ta = document.getElementById(`rt-${commentId}`);
    form.classList.add('open');
    ta.value = '';
    ta.placeholder = 'AI 生成中，请稍候...';
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = '生成中...';
    showAlert('AI 正在生成回复...', 'info');
    try {
        const r = await fetch(`/api/comments/${commentId}/ai-reply`, { method: 'POST' });
        if (!r.ok) throw new Error((await r.json()).detail || '生成失败');
        const res = await r.json();
        ta.value = res.message;
        ta.placeholder = '输入回复内容...';
        showAlert('AI 回复已生成，请确认后发送。', 'success');
        ta.focus();
    } catch (e) {
        ta.placeholder = '输入回复内容...';
        showAlert(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = origText;
    }
}

async function delComment(commentId) {
    if (!confirm('确认删除这条评论？该操作会同步删除 Facebook 上的评论。')) return;
    showAlert('正在删除...', 'info');
    try {
        const r = await fetch(`/api/comments/${commentId}`, { method: 'DELETE' });
        if (!r.ok) throw new Error((await r.json()).detail || '删除失败');
        const el = document.getElementById(`ci-${commentId}`);
        if (el) el.remove();
        showAlert('删除成功。', 'success');
    } catch (e) {
        showAlert(e.message, 'error');
    }
}

async function syncPost(postId, btn) {
    if (btn?.disabled) return;
    const original = btn?.textContent || '同步该帖子';
    if (btn) {
        btn.disabled = true;
        btn.textContent = '同步中...';
    }
    showAlert(`正在同步帖子 ${postId} ...`, 'info');
    try {
        const r = await fetch(`/api/sync/posts/${encodeURIComponent(postId)}`, { method: 'POST' });
        if (!r.ok) throw new Error((await r.json()).detail || '同步失败');
        showAlert('该帖子同步完成，刷新页面...', 'success');
        setTimeout(() => location.reload(), 600);
    } catch (e) {
        showAlert(e.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = original;
        }
    }
}

document.getElementById('btn-sync-comments')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-sync-comments');
    btn.disabled = true; btn.textContent = '同步中...';
    showAlert('正在全量同步所有帖子与评论...', 'info');
    try {
        const r = await fetch('/api/sync?all_posts=true&limit=0', { method: 'POST' });
        if (!r.ok) throw new Error((await r.json()).detail || '同步失败');
        showAlert('全量同步完成，刷新页面...', 'success');
        setTimeout(() => location.reload(), 600);
    } catch (e) {
        showAlert(e.message, 'error');
    } finally {
        btn.disabled = false; btn.textContent = '全量同步所有帖子';
    }
});
