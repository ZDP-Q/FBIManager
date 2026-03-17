(function () {
    const body = document.body;
    const toggleBtn = document.getElementById('sidebar-toggle');
    const accountSelect = document.getElementById('sidebar-account-select');

    const storageKey = 'fbm.sidebar.collapsed';

    function applyCollapsedState() {
        const collapsed = localStorage.getItem(storageKey) === '1';
        body.classList.toggle('sidebar-collapsed', collapsed);
    }

    async function loadAccounts() {
        if (!accountSelect) return;
        try {
            const r = await fetch('/api/settings');
            if (!r.ok) return;
            const data = await r.json();
            const accounts = Array.isArray(data.accounts) ? data.accounts : [];
            const activeId = data.active_account_id;

            if (!accounts.length) {
                accountSelect.innerHTML = '<option value="">未配置账号</option>';
                accountSelect.disabled = true;
                return;
            }

            accountSelect.disabled = false;
            accountSelect.innerHTML = accounts.map((a) => {
                const safeName = String(a.name || `账号 ${a.page_id || a.id}`)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;');
                const selected = Number(a.id) === Number(activeId) ? 'selected' : '';
                return `<option value="${a.id}" ${selected}>${safeName}</option>`;
            }).join('');
        } catch (_) {
            accountSelect.innerHTML = '<option value="">加载失败</option>';
            accountSelect.disabled = true;
        }
    }

    toggleBtn?.addEventListener('click', () => {
        const next = body.classList.contains('sidebar-collapsed') ? '0' : '1';
        localStorage.setItem(storageKey, next);
        applyCollapsedState();
    });

    accountSelect?.addEventListener('change', async () => {
        const accountId = Number(accountSelect.value);
        if (!accountId) return;
        accountSelect.disabled = true;
        try {
            const r = await fetch(`/api/settings/accounts/${accountId}/activate`, { method: 'POST' });
            if (!r.ok) {
                const detail = await r.json().catch(() => ({}));
                throw new Error(detail.detail || '切换账号失败');
            }
            location.reload();
        } catch (err) {
            alert(err.message || '切换账号失败');
            accountSelect.disabled = false;
        }
    });

    applyCollapsedState();
    loadAccounts();
})();
