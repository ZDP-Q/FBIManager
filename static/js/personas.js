const alertEl = document.getElementById('personas-alert');
const tbody = document.getElementById('personas-body');

let promptsData = [];

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
        tbody.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center;">未找到任何 .j2 模板文件，请前往服务器 prompts/ 目录添加。</td></tr>';
        return;
    }
    
    tbody.innerHTML = promptsData.map((p, idx) => `
        <tr>
            <td>
                <div style="font-weight:600;">${p.filename}</div>
            </td>
            <td>
                ${p.is_active 
                    ? '<span class="badge badge-success">当前使用</span>' 
                    : '<span class="badge badge-secondary">未激活</span>'}
            </td>
            <td>
                <div class="actions">
                    <button class="btn btn-outline btn-sm" onclick="previewPrompt(${idx})">预览</button>
                    ${!p.is_active 
                        ? `<button class="btn btn-primary btn-sm" onclick="activatePrompt('${p.filename}')">使用此人设</button>` 
                        : `<button class="btn btn-primary btn-sm" disabled>使用此人设</button>`}
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

async function activatePrompt(filename) {
    try {
        const r = await fetch('/api/prompts/activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename })
        });
        if (!r.ok) throw new Error('切换人设失败');
        showAlert('已成功切换回复人设！', 'success');
        loadPrompts();
    } catch (e) {
        showAlert(e.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', loadPrompts);
