const alertEl = document.getElementById('personas-alert');
const tbody = document.getElementById('personas-body');

let promptsData = [];
let editingFilename = '';
let isNewPrompt = false;

// Template variables reference
const TEMPLATE_VARS = [
    { name: 'page_name', desc: '主页名称', example: 'Elio Silvestri' },
    { name: 'post_message', desc: '帖子正文内容', example: '今天天气真好...' },
    { name: 'comment_message', desc: '当前评论内容', example: '太帅了！' },
    { name: 'author_name', desc: '评论用户名称', example: '张三' },
    { name: 'parent_comment_message', desc: '被回复的原评论（回复时才有）', example: '我觉得...' },
    { name: 'video_analysis', desc: '视频内容分析（视频帖才有）', example: '拍摄地点：巴黎...' },
    { name: 'previous_replies', desc: '本帖下已回复列表（数组）', example: '[{author_name, comment_message, reply_message}]' },
];

const STARTER_TEMPLATE = `# 人设名称
你是一个友善、专业的 AI 助手。

# 回复风格
- 保持自然、简洁
- 使用与评论者相同的语言
- 每次回复 1-3 句话

# 上下文信息

主页名称: {{ page_name or '未提供' }}
帖子内容: {{ post_message or '未提供' }}
{% if video_analysis %}视频分析: {{ video_analysis }}
{% endif %}评论用户: {{ author_name or '匿名用户' }}
评论内容: {{ comment_message or '（空）' }}
{% if parent_comment_message -%}
被回复的原评论: {{ parent_comment_message }}
{% endif -%}
{% if previous_replies %}
# 已回复过的内容（避免重复）
{% for pr in previous_replies %}
- 对 {{ pr.author_name }} 回复了："{{ pr.reply_message[:80] }}"
{% endfor %}
{% endif %}

# 任务
根据以上信息，回复这条评论。直接输出回复内容，不要加任何前缀或分析。
`;

function showAlert(msg, type = 'info') {
    alertEl.textContent = msg;
    alertEl.className = `alert alert-${type} visible`;
    setTimeout(() => { alertEl.className = 'alert'; }, 5000);
}

function closeModal(id) {
    document.getElementById(id).classList.remove('open');
}

function openModal(id) {
    document.getElementById(id).classList.add('open');
}

function renderVarReference() {
    const container = document.getElementById('var-list');
    if (!container) return;
    container.innerHTML = TEMPLATE_VARS.map(v => `
        <div style="margin-bottom: 10px; cursor: pointer; padding: 6px 8px; border-radius: 6px; transition: background 0.15s;"
             onmouseenter="this.style.background='var(--surface-3)'"
             onmouseleave="this.style.background='transparent'"
             onclick="insertVariable('{{ ${v.name} }}')">
            <div style="font-family: monospace; font-size: 12px; font-weight: 600; color: var(--accent);">{{ ${v.name} }}</div>
            <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">${v.desc}</div>
        </div>
    `).join('');
}

function insertVariable(varText) {
    const ta = document.getElementById('edit-content');
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    ta.value = ta.value.substring(0, start) + varText + ta.value.substring(end);
    ta.selectionStart = ta.selectionEnd = start + varText.length;
    ta.focus();
}

async function loadPrompts() {
    try {
        const r = await fetch('/api/prompts');
        if (!r.ok) throw new Error('获取人设列表失败');
        const result = await r.json();
        promptsData = result.data || [];
        renderTable();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="3" class="text-danger" style="text-align:center;">${e.message}</td></tr>`;
    }
}

function renderTable() {
    if (promptsData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center;">暂无人设模板，点击"新建人设"创建。</td></tr>';
        return;
    }

    tbody.innerHTML = promptsData.map((p, idx) => `
        <tr class="${p.is_active ? 'active-row' : ''}">
            <td>
                <div style="font-weight:600;">${p.filename}</div>
            </td>
            <td>
                ${p.is_active
                    ? '<span class="badge badge-success">正在使用</span>'
                    : '<span class="badge badge-neutral">备选</span>'}
            </td>
            <td>
                <div class="actions">
                    <button class="btn btn-outline btn-sm" onclick="previewPrompt(${idx})">预览</button>
                    <button class="btn btn-outline btn-sm" onclick="editPrompt(${idx})">编辑</button>
                    ${!p.is_active
                        ? `<button class="btn btn-primary btn-sm" id="btn-act-${idx}" onclick="activatePrompt('${p.filename}', ${idx})">使用此人设</button>`
                        : `<button class="btn btn-primary btn-sm" disabled style="opacity:0.6;cursor:not-allowed;">使用中</button>`}
                </div>
            </td>
        </tr>
    `).join('');
}

function previewPrompt(idx) {
    const p = promptsData[idx];
    if (!p) return;
    document.getElementById('preview-filename').textContent = p.filename;
    document.getElementById('preview-content').textContent = p.content;
    openModal('modal-preview');
}

function editPrompt(idx) {
    const p = promptsData[idx];
    if (!p) return;
    isNewPrompt = false;
    editingFilename = p.filename;
    document.getElementById('edit-modal-title').innerHTML = '编辑模板：<span id="edit-filename">' + p.filename + '</span>';
    document.getElementById('edit-content').value = p.content;
    renderVarReference();
    openModal('modal-edit');
}

function createPrompt() {
    const filename = prompt('请输入新模板文件名（需以 .j2 结尾）：', 'new_persona.j2');
    if (!filename) return;
    if (!filename.endsWith('.j2')) {
        showAlert('文件名必须以 .j2 结尾', 'warning');
        return;
    }
    if (promptsData.some(p => p.filename === filename)) {
        showAlert('该文件名已存在，请使用其他名称', 'warning');
        return;
    }
    isNewPrompt = true;
    editingFilename = filename;
    document.getElementById('edit-modal-title').innerHTML = '新建模板：<span id="edit-filename">' + filename + '</span>';
    document.getElementById('edit-content').value = STARTER_TEMPLATE;
    renderVarReference();
    openModal('modal-edit');
}

async function savePrompt() {
    const btn = document.getElementById('btn-save-prompt');
    btn.disabled = true;
    btn.textContent = '保存中...';
    try {
        const content = document.getElementById('edit-content').value;
        const method = isNewPrompt ? 'POST' : 'PUT';
        const r = await fetch(`/api/prompts/${encodeURIComponent(editingFilename)}`, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: editingFilename, content })
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail || '保存失败');
        }
        showAlert(isNewPrompt ? '人设已创建！' : '模板已保存！', 'success');
        closeModal('modal-edit');
        isNewPrompt = false;
        await loadPrompts();
    } catch (e) {
        showAlert(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '保存';
    }
}

async function activatePrompt(filename, idx) {
    const btn = document.getElementById(`btn-act-${idx}`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = '切换中...';
    }
    try {
        const r = await fetch('/api/prompts/activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename })
        });
        if (!r.ok) throw new Error('切换人设失败');
        showAlert('已成功切换回复人设！AI 将以此身份进行自动回复。', 'success');
        await loadPrompts();
    } catch (e) {
        showAlert(e.message, 'error');
        if (btn) {
            btn.disabled = false;
            btn.textContent = '使用此人设';
        }
    }
}

// Tab key support in editor textarea
document.getElementById('edit-content')?.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
        e.preventDefault();
        const ta = e.target;
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        ta.value = ta.value.substring(0, start) + '    ' + ta.value.substring(end);
        ta.selectionStart = ta.selectionEnd = start + 4;
    }
    // Ctrl+S / Cmd+S to save
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        savePrompt();
    }
});

document.addEventListener('DOMContentLoaded', loadPrompts);
