const sqlbotAlert = document.getElementById('sqlbot-alert');
const questionEl = document.getElementById('sqlbot-question');
const submitEl = document.getElementById('sqlbot-submit');
const statusEl = document.getElementById('sqlbot-status');
const reportEl = document.getElementById('sqlbot-report');
const rowCountEl = document.getElementById('sqlbot-row-count');
const detailsEl = document.getElementById('sqlbot-details');
const sqlEl = document.getElementById('sqlbot-sql');
const paramsEl = document.getElementById('sqlbot-params');
const tableWrapEl = document.getElementById('sqlbot-table-wrap');
const metaEl = document.getElementById('sqlbot-meta');

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function showAlert(message, type = 'info') {
    if (!sqlbotAlert) return;
    sqlbotAlert.textContent = message;
    sqlbotAlert.className = `alert alert-${type} visible`;
}

function hideAlert() {
    if (!sqlbotAlert) return;
    sqlbotAlert.className = 'alert';
    sqlbotAlert.textContent = '';
}

function setBusy(isBusy) {
    if (submitEl) {
        submitEl.disabled = isBusy;
        submitEl.textContent = isBusy ? '分析中...' : '生成报告';
    }
    if (statusEl) statusEl.textContent = isBusy ? '正在生成 SQL 并查询数据' : '';
}

function renderBadges(data) {
    if (!metaEl) return;
    const badges = [];
    if (data.plan_note) badges.push(data.plan_note);
    badges.push(`返回 ${data.row_count || 0} 行`);
    if (data.truncated) badges.push('结果已截断');
    metaEl.innerHTML = badges
        .map(text => `<span class="badge badge-success">${escapeHtml(text)}</span>`)
        .join('');
    metaEl.style.display = 'flex';
}

function renderTable(columns, rows) {
    if (!tableWrapEl) return;
    if (!columns?.length || !rows?.length) {
        tableWrapEl.innerHTML = '<div style="padding: 16px; color: var(--muted);">暂无结果行</div>';
        return;
    }

    const previewRows = rows.slice(0, 50);
    const head = columns.map(col => `<th>${escapeHtml(col)}</th>`).join('');
    const body = previewRows.map(row => {
        const cells = columns.map(col => `<td title="${escapeHtml(row[col])}">${escapeHtml(row[col])}</td>`).join('');
        return `<tr>${cells}</tr>`;
    }).join('');
    tableWrapEl.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderResult(data) {
    if (reportEl) {
        reportEl.textContent = data.answer || '未生成报告。';
        reportEl.classList.remove('sqlbot-empty');
    }
    if (rowCountEl) {
        rowCountEl.textContent = `${data.row_count || 0} 行`;
    }
    renderBadges(data);
    if (detailsEl) detailsEl.style.display = '';
    if (sqlEl) sqlEl.textContent = data.sql || '';
    if (paramsEl) paramsEl.textContent = `Params:\n${JSON.stringify(data.params || {}, null, 2)}`;
    renderTable(data.columns || [], data.rows || []);
}

async function submitQuestion() {
    const question = (questionEl?.value || '').trim();
    if (!question) {
        showAlert('请输入要分析的问题。', 'warning');
        questionEl?.focus();
        return;
    }

    hideAlert();
    setBusy(true);
    if (reportEl) {
        reportEl.textContent = '正在分析...';
        reportEl.classList.add('sqlbot-empty');
    }
    try {
        const response = await fetch('/api/sqlbot/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || '分析失败');
        }
        renderResult(data);
    } catch (err) {
        showAlert(err.message || '分析失败', 'error');
        if (reportEl) {
            reportEl.textContent = '分析失败。';
            reportEl.classList.add('sqlbot-empty');
        }
    } finally {
        setBusy(false);
    }
}

submitEl?.addEventListener('click', submitQuestion);
questionEl?.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault();
        submitQuestion();
    }
});

document.querySelectorAll('.sqlbot-preset').forEach(button => {
    button.addEventListener('click', () => {
        if (questionEl) questionEl.value = button.dataset.question || '';
        submitQuestion();
    });
});
