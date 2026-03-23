const alertEl = document.getElementById('home-alert');

let settingsState = {
    accounts: [],
    activeAccountId: null,
    selectedAccountId: null,
};

function showAlert(msg, type = 'info') {
    alertEl.textContent = msg;
    alertEl.className = `alert alert-${type} visible`;
}

function fmtNum(v) {
    if (typeof v !== 'number') return String(v ?? '-');
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toLocaleString('zh-CN');
}

function getAccountFromForm() {
    return {
        name: (document.getElementById('account-name')?.value || '').trim(),
        page_id: (document.getElementById('account-page-id')?.value || '').trim(),
        api_version: (document.getElementById('account-api-version')?.value || '').trim() || 'v25.0',
        page_access_token: (document.getElementById('account-token')?.value || '').trim(),
        verify_token: (document.getElementById('account-verify-token')?.value || '').trim(),
    };
}

function fillAccountForm(account) {
    document.getElementById('account-name').value = account?.name || '';
    document.getElementById('account-page-id').value = account?.page_id || '';
    document.getElementById('account-api-version').value = account?.api_version || 'v25.0';
    document.getElementById('account-token').value = account?.page_access_token || '';
    document.getElementById('account-verify-token').value = account?.verify_token || '';
}

function renderAccountSelect() {
    const select = document.getElementById('account-select');
    if (!select) return;
    const options = settingsState.accounts.map((a) => {
        const activeMark = Number(a.id) === Number(settingsState.activeAccountId) ? ' (当前)' : '';
        return `<option value="${a.id}">${a.name || `账号 ${a.page_id}`}${activeMark}</option>`;
    }).join('');
    select.innerHTML = options || '<option value="">暂无账号</option>';

    if (settingsState.selectedAccountId) {
        select.value = String(settingsState.selectedAccountId);
    } else if (settingsState.activeAccountId) {
        select.value = String(settingsState.activeAccountId);
    }

    if (select.value) {
        const account = settingsState.accounts.find((a) => Number(a.id) === Number(select.value));
        settingsState.selectedAccountId = Number(select.value);
        fillAccountForm(account);
    } else {
        fillAccountForm(null);
    }
}

async function loadSettings() {
    const r = await fetch('/api/settings');
    if (!r.ok) throw new Error('加载配置失败');
    const data = await r.json();

    settingsState.accounts = Array.isArray(data.accounts) ? data.accounts : [];
    settingsState.activeAccountId = data.active_account_id || null;
    if (!settingsState.selectedAccountId && settingsState.activeAccountId) {
        settingsState.selectedAccountId = Number(settingsState.activeAccountId);
    }
    renderAccountSelect();

    const model = data.model || {};
    const baseUrlEl = document.getElementById('model-base-url');
    if (baseUrlEl) baseUrlEl.value = model.ai_api_base_url || '';
    const apiKeyEl = document.getElementById('model-api-key');
    if (apiKeyEl) apiKeyEl.value = model.ai_api_key || '';
    const modelNameEl = document.getElementById('model-name');
    if (modelNameEl) modelNameEl.value = model.ai_model || '';
}

async function saveAccount() {
    const payload = getAccountFromForm();
    if (!payload.page_id || !payload.page_access_token || !payload.verify_token) {
        showAlert('请填写 PAGE_ID、PAGE_ACCESS_TOKEN、VERIFY_TOKEN。', 'warning');
        return;
    }

    const accountId = settingsState.selectedAccountId;
    const isUpdate = Boolean(accountId && settingsState.accounts.some((a) => Number(a.id) === Number(accountId)));
    const url = isUpdate ? `/api/settings/accounts/${accountId}` : '/api/settings/accounts';
    const method = isUpdate ? 'PUT' : 'POST';

    const r = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || '保存账号失败');
    }

    const result = await r.json().catch(() => ({}));
    if (!isUpdate && result.account_id) {
        settingsState.selectedAccountId = Number(result.account_id);
    }
}

async function loadProfile() {
    try {
        const r = await fetch('/api/page-profile');
        if (!r.ok) throw new Error((await r.json()).detail || '获取主页信息失败');
        const p = await r.json();
        const nameEl = document.getElementById('profile-name');
        if (nameEl) nameEl.textContent = p.name || '未命名';
        const catEl = document.getElementById('profile-category');
        if (catEl) catEl.textContent = p.category || '-';
        const userEl = document.getElementById('profile-username');
        if (userEl) userEl.textContent = p.username || '-';
        const fansEl = document.getElementById('profile-fans');
        if (fansEl) fansEl.textContent = p.fan_count ?? '-';
        const link = document.getElementById('profile-link');
        if (link) {
            link.textContent = p.link || '-';
            link.href = p.link || '#';
        }
        const syncEl = document.getElementById('profile-sync-time');
        if (syncEl) syncEl.textContent = `同步于 ${p.synced_at || '-'}`;
    } catch (e) {
        showAlert(e.message, 'warning');
    }
}

document.getElementById('account-select')?.addEventListener('change', (e) => {
    settingsState.selectedAccountId = Number(e.target.value || 0) || null;
    const account = settingsState.accounts.find((a) => Number(a.id) === Number(settingsState.selectedAccountId));
    fillAccountForm(account || null);
});

document.getElementById('btn-account-new')?.addEventListener('click', () => {
    settingsState.selectedAccountId = null;
    const select = document.getElementById('account-select');
    if (select) select.value = '';
    fillAccountForm(null);
});

document.getElementById('btn-account-save')?.addEventListener('click', async () => {
    try {
        await saveAccount();
        await loadSettings();
        showAlert('账号配置已保存。', 'success');
    } catch (e) {
        showAlert(e.message, 'error');
    }
});

document.getElementById('btn-account-activate')?.addEventListener('click', async () => {
    if (!settingsState.selectedAccountId) {
        showAlert('请先选择一个账号。', 'warning');
        return;
    }
    try {
        const r = await fetch(`/api/settings/accounts/${settingsState.selectedAccountId}/activate`, { method: 'POST' });
        if (!r.ok) throw new Error((await r.json()).detail || '切换失败');
        showAlert('账号已切换，页面刷新中...', 'success');
        setTimeout(() => location.reload(), 500);
    } catch (e) {
        showAlert(e.message, 'error');
    }
});

document.getElementById('btn-account-delete')?.addEventListener('click', async () => {
    if (!settingsState.selectedAccountId) {
        showAlert('请先选择一个账号。', 'warning');
        return;
    }
    if (!confirm('确认删除该账号配置？')) return;
    try {
        const r = await fetch(`/api/settings/accounts/${settingsState.selectedAccountId}`, { method: 'DELETE' });
        if (!r.ok) throw new Error((await r.json()).detail || '删除失败');
        settingsState.selectedAccountId = null;
        await loadSettings();
        showAlert('账号已删除。', 'success');
        setTimeout(() => location.reload(), 400);
    } catch (e) {
        showAlert(e.message, 'error');
    }
});

document.getElementById('btn-account-export')?.addEventListener('click', async () => {
    try {
        const r = await fetch('/api/settings/accounts/export');
        if (!r.ok) throw new Error('导出失败');
        const data = await r.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `fb_accounts_export_${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showAlert('导出成功。', 'success');
    } catch (e) {
        showAlert(e.message, 'error');
    }
});

document.getElementById('btn-account-import')?.addEventListener('click', () => {
    document.getElementById('input-account-import').click();
});

document.getElementById('input-account-import')?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    
    const reader = new FileReader();
    reader.onload = async (event) => {
        try {
            const payload = JSON.parse(event.target.result);
            if (!Array.isArray(payload)) throw new Error('JSON 格式错误：应为账号数组');
            
            const r = await fetch('/api/settings/accounts/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!r.ok) throw new Error((await r.json()).detail || '导入失败');
            const res = await r.json();
            showAlert(`成功导入/更新 ${res.count} 个账号。`, 'success');
            await loadSettings();
        } catch (err) {
            showAlert(err.message, 'error');
        } finally {
            e.target.value = ''; // Reset input
        }
    };
    reader.readAsText(file);
});

document.getElementById('btn-model-save')?.addEventListener('click', async () => {
    const payload = {
        ai_api_base_url: (document.getElementById('model-base-url')?.value || '').trim(),
        ai_api_key: (document.getElementById('model-api-key')?.value || '').trim(),
        ai_model: (document.getElementById('model-name')?.value || '').trim(),
    };

    try {
        const r = await fetch('/api/settings/model', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!r.ok) throw new Error((await r.json()).detail || '保存失败');
        showAlert('模型配置已保存。', 'success');
    } catch (e) {
        showAlert(e.message, 'error');
    }
});

document.getElementById('btn-model-test')?.addEventListener('click', async () => {
    const payload = {
        ai_api_base_url: (document.getElementById('model-base-url')?.value || '').trim(),
        ai_api_key: (document.getElementById('model-api-key')?.value || '').trim(),
        ai_model: (document.getElementById('model-name')?.value || '').trim(),
    };

    const btn = document.getElementById('btn-model-test');
    btn.disabled = true;
    btn.textContent = '测试中...';
    showAlert('正在测试 AI 连接，请稍候...', 'info');

    try {
        const r = await fetch('/api/settings/model/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const res = await r.json();
        if (!r.ok) throw new Error(res.detail || '测试失败');
        showAlert(res.message || '连接成功！', 'success');
    } catch (e) {
        showAlert(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '测试配置';
    }
});

document.getElementById('btn-change-password')?.addEventListener('click', async () => {
    const oldPassword = (document.getElementById('admin-old-password')?.value || '').trim();
    const newPassword = (document.getElementById('admin-new-password')?.value || '').trim();

    if (!oldPassword || !newPassword) {
        showAlert('请填写当前密码与新密码。', 'warning');
        return;
    }

    try {
        const r = await fetch('/api/admin/change-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
        });
        if (!r.ok) throw new Error((await r.json()).detail || '密码更新失败');

        showAlert('密码已更新，请重新登录。', 'success');
        setTimeout(() => {
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = '/logout';
            document.body.appendChild(form);
            form.submit();
        }, 500);
    } catch (e) {
        showAlert(e.message, 'error');
    }
});

(async function init() {
    try {
        await loadSettings();
    } catch (e) {
        showAlert(e.message, 'error');
    }
    await loadProfile();
})();
