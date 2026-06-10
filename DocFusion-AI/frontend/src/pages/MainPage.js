import { getFieldSource, getFields, getRecordSource, getTask, uploadFiles } from '../api/index.js';
import { escapeHtml, formatFileSize } from '../utils/helpers.js';

const FRONTEND_BUILD = '2026-04-17-r7';
const SLOT_OPTIONS = [
    { value: 'category', label: '分类' },
    { value: 'indicator', label: '指标' },
    { value: 'value', label: '数值' },
    { value: 'unit', label: '单位' },
    { value: 'time', label: '时间' },
    { value: 'yoy', label: '同比' }
];

const state = {
    files: [],
    uploading: false,
    dragActive: false,
    status: { text: '先填写表头，再上传文档或表格。', tone: 'info' },
    results: [],
    preview: null,
    trace: null,
    extractFields: [],
    customDraft: { label: '', slot: 'indicator' }
};

const allowedExtensions = ['.txt', '.md', '.docx', '.xlsx'];
const TERMINAL_TASK_STATUSES = new Set(['matched', 'extracted', 'failed', 'parse_failed', 'extract_failed']);
const FAILURE_TASK_STATUSES = new Set(['failed', 'parse_failed', 'extract_failed']);
const MAX_POLL_ERRORS = 20;
const RESULT_CACHE_KEY = 'docfusion-active-results';
const app = document.getElementById('app');
let renderQueued = false;


function createDefaultFields() {
    return [];
}


function captureRenderState() {
    const controlPanel = app.querySelector('.control-panel');
    const activeElement = document.activeElement;
    const activeAction = activeElement?.dataset?.action || '';

    return {
        windowScrollY: window.scrollY,
        controlPanelScrollTop: controlPanel ? controlPanel.scrollTop : 0,
        activeFieldId: activeAction === 'rename-field' ? activeElement.dataset.fieldId : null,
        activeDraft: activeAction === 'draft-label',
        selectionStart: typeof activeElement?.selectionStart === 'number' ? activeElement.selectionStart : null,
        selectionEnd: typeof activeElement?.selectionEnd === 'number' ? activeElement.selectionEnd : null
    };
}


function restoreRenderState(snapshot) {
    if (!snapshot) return;

    const controlPanel = app.querySelector('.control-panel');
    if (controlPanel) {
        controlPanel.scrollTop = snapshot.controlPanelScrollTop || 0;
    }
    window.scrollTo({ top: snapshot.windowScrollY || 0, behavior: 'auto' });

    let target = null;
    if (snapshot.activeFieldId) {
        target = app.querySelector(`[data-action="rename-field"][data-field-id="${snapshot.activeFieldId}"]`);
    } else if (snapshot.activeDraft) {
        target = app.querySelector('[data-action="draft-label"]');
    }
    if (!target) return;

    target.focus({ preventScroll: true });
    if (typeof snapshot.selectionStart === 'number' && typeof target.setSelectionRange === 'function') {
        target.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd ?? snapshot.selectionStart);
    }
}


function queueRender() {
    if (renderQueued) return;
    renderQueued = true;
    window.requestAnimationFrame(() => {
        renderQueued = false;
        render();
    });
}


function readStoredResults() {
    try {
        const raw = window.localStorage.getItem(RESULT_CACHE_KEY);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
        return [];
    }
}


function persistResults() {
    try {
        const payload = state.results
            .filter((item) => item.taskId || item.data?.task_id)
            .slice(-12)
            .map((item) => ({
                taskId: Number(item.taskId || item.data?.task_id),
                fileName: item.fileName || item.data?.file_name || '未知文件',
                pending: Boolean(item.pending),
                success: Boolean(item.success),
                error: item.error || null,
                cached: Boolean(item.cached)
            }));
        window.localStorage.setItem(RESULT_CACHE_KEY, JSON.stringify(payload));
    } catch (error) {
        // Ignore local persistence failures.
    }
}


function restoreStoredResults() {
    const stored = readStoredResults();
    if (!stored.length) return;
    state.results = stored
        .filter((item) => Number.isFinite(Number(item.taskId)))
        .map((item) => ({
            fileName: item.fileName || '未知文件',
            taskId: Number(item.taskId),
            pending: true,
            success: false,
            error: null,
            cached: Boolean(item.cached),
            pollErrors: 0,
            task: {
                task_id: Number(item.taskId),
                status: 'uploaded',
                parse_status: 'pending',
                extract_status: 'pending',
                match_status: 'pending',
                progress: {
                    stage: 'queued',
                    current: 0,
                    total: 1,
                    percent: 2,
                    message: '正在恢复任务状态'
                }
            }
        }));
}


function fieldTypeForSlot(slot) {
    if (slot === 'time') return 'date';
    if (slot === 'value' || slot === 'yoy') return 'numeric';
    return 'string';
}


function visibleFieldCount() {
    return state.extractFields.filter((item) => item.enabled && item.label.trim()).length;
}


function buildExtractConfigPayload() {
    return {
        fields: state.extractFields
            .filter((item) => item.enabled && item.label.trim())
            .map((item) => ({
                field_name: item.label.trim(),
                slot: item.slot,
                type: fieldTypeForSlot(item.slot),
                visible: true
            }))
    };
}


function hasDuplicateFieldLabels(fields) {
    const labels = fields.map((item) => item.field_name.trim());
    return new Set(labels).size !== labels.length;
}


function sanitizeSheetName(name, fallback = 'Sheet') {
    const cleaned = String(name || fallback)
        .replace(/[\\/?*[\]:]/g, ' ')
        .trim()
        .slice(0, 31);
    return cleaned || fallback;
}


function fallbackResultFields(fields = []) {
    return Array.isArray(fields) ? fields.filter(Boolean) : [];
}


function deriveResultFields(resultEntry) {
    const configured = fallbackResultFields(resultEntry?.selected_fields);
    if (configured.length) {
        return configured;
    }

    const firstRecord = Array.isArray(resultEntry?.results) ? resultEntry.results[0] : null;
    if (!firstRecord?.fields) {
        return [];
    }
    return Object.keys(firstRecord.fields).map((fieldName) => ({ field_name: fieldName, slot: 'indicator' }));
}


function fileExtension(name = '') {
    const index = name.lastIndexOf('.');
    return index >= 0 ? name.slice(index).toLowerCase() : '';
}


function fileIcon(name) {
    const ext = fileExtension(name);
    if (ext === '.xlsx') return 'GRID';
    if (ext === '.docx') return 'DOC';
    if (ext === '.md') return 'MD';
    return 'TXT';
}


function isValidFile(file) {
    return allowedExtensions.includes(fileExtension(file.name));
}


function isDuplicate(file) {
    return state.files.some((item) =>
        item.file.name === file.name &&
        item.file.size === file.size &&
        item.file.lastModified === file.lastModified
    );
}


function updateStatus(text, tone = 'info') {
    state.status = { text, tone };
    queueRender();
}


function addFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    let added = 0;
    const invalid = [];
    for (const file of files) {
        if (!isValidFile(file)) {
            invalid.push(file.name);
            continue;
        }
        if (isDuplicate(file)) {
            continue;
        }
        state.files.push({ id: `${Date.now()}-${Math.random()}`, file });
        added += 1;
    }

    state.dragActive = false;
    if (added > 0) {
        updateStatus(`已接入 ${added} 个文件，当前启用 ${visibleFieldCount()} 个提取列。`, 'success');
    } else if (invalid.length) {
        updateStatus(`存在不支持的文件类型：${invalid.slice(0, 3).join('、')}`, 'error');
    } else {
        updateStatus('未新增文件，列表中已有相同文件。', 'warning');
    }
}


function removeFile(index) {
    state.files.splice(index, 1);
    queueRender();
}


function clearFiles() {
    state.files = [];
    updateStatus('上传队列已清空。', 'info');
}


function resetFields() {
    state.extractFields = [];
    state.customDraft = { label: '', slot: 'indicator' };
    updateStatus('表头配置已清空。', 'info');
}


function updateFieldEnabled(fieldId, enabled) {
    state.extractFields = state.extractFields.map((item) =>
        item.id === fieldId ? { ...item, enabled } : item
    );
    queueRender();
}


function updateFieldLabel(fieldId, label) {
    state.extractFields = state.extractFields.map((item) =>
        item.id === fieldId ? { ...item, label } : item
    );
}


function syncFieldInputsFromDOM() {
    const labelInputs = app.querySelectorAll('[data-action="rename-field"]');
    if (labelInputs.length) {
        const labelMap = new Map();
        labelInputs.forEach((input) => {
            labelMap.set(input.dataset.fieldId, input.value);
        });
        state.extractFields = state.extractFields.map((item) =>
            labelMap.has(item.id) ? { ...item, label: labelMap.get(item.id) } : item
        );
    }

    const draftInput = app.querySelector('[data-action="draft-label"]');
    if (draftInput) {
        state.customDraft = { ...state.customDraft, label: draftInput.value };
    }
}


function updateFieldSlot(fieldId, slot) {
    state.extractFields = state.extractFields.map((item) =>
        item.id === fieldId ? { ...item, slot, type: fieldTypeForSlot(slot) } : item
    );
    queueRender();
}


function removeCustomField(fieldId) {
    state.extractFields = state.extractFields.filter((item) => item.id !== fieldId);
    queueRender();
}


function addCustomField() {
    const label = state.customDraft.label.trim();
    if (!label) {
        updateStatus('新增输出列前请先填写列名。', 'warning');
        return;
    }
    if (state.extractFields.some((item) => item.label.trim() === label)) {
        updateStatus(`表头“${label}”已经存在，请避免重复列名。`, 'warning');
        return;
    }

    state.extractFields.push({
        id: `custom-${Date.now()}-${Math.random()}`,
        slot: state.customDraft.slot,
        label,
        hint: '自定义结果列',
        type: fieldTypeForSlot(state.customDraft.slot),
        locked: false,
        enabled: true
    });
    state.customDraft = { label: '', slot: 'indicator' };
    updateStatus(`已新增输出列“${label}”。`, 'success');
    queueRender();
}


function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}


async function pollFields(taskId, maxRetries = 20, interval = 800) {
    for (let attempt = 0; attempt < maxRetries; attempt += 1) {
        const result = await getFields(taskId);
        if (result.success) return result;
        await wait(interval);
    }
    return { success: false, message: '结果获取超时' };
}


function clampProgressPercent(progress) {
    const value = Number(progress?.percent);
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(100, Math.round(value)));
}


function formatTaskUpdatedAt(value) {
    if (!value) return '未刷新';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '未刷新';
    return date.toLocaleTimeString('zh-CN', { hour12: false });
}


function stageLabel(stage) {
    if (stage === 'queued') return '等待处理';
    if (stage === 'parse') return '文件解析';
    if (stage === 'extract') return '字段抽取';
    if (stage === 'match') return '标准匹配';
    if (stage === 'failed') return '处理失败';
    return '处理中';
}


function stageTone(stage) {
    if (stage === 'failed') return 'error';
    if (stage === 'match') return 'success';
    return 'loading';
}


function isTaskFailed(task) {
    return FAILURE_TASK_STATUSES.has(task?.status);
}


function isTaskTerminal(task) {
    return TERMINAL_TASK_STATUSES.has(task?.status);
}


function isTaskReady(task) {
    return task && !isTaskFailed(task) && ['matched', 'extracted'].includes(task.status) && task.extract_status === 'success';
}


function setResultEntry(taskId, patch) {
    state.results = state.results.map((item) => (
        Number(item.taskId || item.data?.task_id) === Number(taskId) ? { ...item, ...patch } : item
    ));
    persistResults();
    queueRender();
}


async function finalizeTaskEntry(taskId) {
    const entry = state.results.find((item) => Number(item.taskId || item.data?.task_id) === Number(taskId));
    if (!entry) return;

    const fieldsResult = await pollFields(taskId);
    if (fieldsResult.success) {
        setResultEntry(taskId, {
            success: true,
            pending: false,
            error: null,
            pollErrors: 0,
            data: fieldsResult.data
        });
        return;
    }

    setResultEntry(taskId, {
        success: false,
        pending: false,
        error: fieldsResult.message || '结果获取失败'
    });
}


async function monitorTaskEntries(taskIds) {
    const pendingIds = new Set(taskIds.map((item) => Number(item)));
    while (pendingIds.size) {
        const activeIds = Array.from(pendingIds);
        updateStatus(`后台处理中：剩余 ${activeIds.length} 个文件。`, 'loading');

        const snapshots = await Promise.all(activeIds.map((taskId) => getTask(taskId)));
        const readyToFinalize = [];

        snapshots.forEach((snapshot, index) => {
            const taskId = activeIds[index];
            const entry = state.results.find((item) => Number(item.taskId || item.data?.task_id) === Number(taskId));
            if (!entry) {
                pendingIds.delete(taskId);
                return;
            }

            if (!snapshot.success) {
                const nextErrors = (entry.pollErrors || 0) + 1;
                if (nextErrors >= MAX_POLL_ERRORS) {
                    setResultEntry(taskId, {
                        pending: false,
                        success: false,
                        error: snapshot.message || '任务状态拉取失败',
                        pollErrors: nextErrors
                    });
                    pendingIds.delete(taskId);
                } else {
                    setResultEntry(taskId, {
                        pollErrors: nextErrors,
                        task: {
                            ...(entry.task || {}),
                            progress: {
                                ...(entry.task?.progress || {}),
                                message: `任务状态拉取失败，正在重试（${nextErrors}/${MAX_POLL_ERRORS}）`
                            }
                        }
                    });
                }
                return;
            }

            const task = snapshot.data;
            setResultEntry(taskId, { task, pollErrors: 0 });

            if (isTaskFailed(task)) {
                setResultEntry(taskId, {
                    pending: false,
                    success: false,
                    error: task.error_message || '任务处理失败'
                });
                pendingIds.delete(taskId);
                return;
            }

            if (isTaskReady(task)) {
                readyToFinalize.push(taskId);
                pendingIds.delete(taskId);
                return;
            }

            if (isTaskTerminal(task)) {
                setResultEntry(taskId, {
                    pending: false,
                    success: false,
                    error: task.error_message || '任务已结束，但没有可展示结果'
                });
                pendingIds.delete(taskId);
            }
        });

        if (readyToFinalize.length) {
            await Promise.all(readyToFinalize.map((taskId) => finalizeTaskEntry(taskId)));
        }

        if (pendingIds.size) {
            await wait(900);
        }
    }
}


async function resumeStoredTaskEntries() {
    if (!state.results.length) return;

    const pendingTaskIds = [];
    for (const entry of [...state.results]) {
        const taskId = Number(entry.taskId || entry.data?.task_id);
        if (!Number.isFinite(taskId)) {
            continue;
        }

        const snapshot = await getTask(taskId);
        if (!snapshot.success) {
            const fieldsResult = await getFields(taskId);
            if (fieldsResult.success) {
                setResultEntry(taskId, {
                    success: true,
                    pending: false,
                    error: null,
                    pollErrors: 0,
                    data: fieldsResult.data
                });
            } else {
                setResultEntry(taskId, {
                    pending: false,
                    success: false,
                    error: fieldsResult.message || snapshot.message || '恢复任务状态失败'
                });
            }
            continue;
        }

        const task = snapshot.data;
        setResultEntry(taskId, { task, pollErrors: 0 });

        if (isTaskFailed(task)) {
            setResultEntry(taskId, {
                pending: false,
                success: false,
                error: task.error_message || '任务处理失败'
            });
            continue;
        }

        if (isTaskReady(task)) {
            await finalizeTaskEntry(taskId);
            continue;
        }

        if (isTaskTerminal(task)) {
            const fieldsResult = await getFields(taskId);
            if (fieldsResult.success) {
                setResultEntry(taskId, {
                    success: true,
                    pending: false,
                    error: null,
                    pollErrors: 0,
                    data: fieldsResult.data
                });
            } else {
                setResultEntry(taskId, {
                    pending: false,
                    success: false,
                    error: task.error_message || fieldsResult.message || '任务已结束，但没有可展示结果'
                });
            }
            continue;
        }

        pendingTaskIds.push(taskId);
    }

    if (pendingTaskIds.length) {
        updateStatus(`已恢复 ${pendingTaskIds.length} 个进行中的任务。`, 'info');
        await monitorTaskEntries(pendingTaskIds);
    }
}


async function buildPreview(file) {
    const ext = fileExtension(file.name);
    if (ext === '.txt' || ext === '.md') {
        const text = await file.text();
        return `<pre class="modal-code">${escapeHtml(text)}</pre>`;
    }

    if (ext === '.docx') {
        const buffer = await file.arrayBuffer();
        const result = await mammoth.convertToHtml({ arrayBuffer: buffer });
        return `<div class="doc-preview">${result.value}</div>`;
    }

    if (ext === '.xlsx') {
        const data = new Uint8Array(await file.arrayBuffer());
        const workbook = XLSX.read(data, { type: 'array' });
        const sheetName = workbook.SheetNames[0];
        const worksheet = workbook.Sheets[sheetName];
        return `<div class="sheet-preview">${XLSX.utils.sheet_to_html(worksheet)}</div>`;
    }

    return `<div class="modal-empty">当前文件暂不支持在线预览。</div>`;
}


async function openPreview(index) {
    const item = state.files[index];
    if (!item) return;

    state.preview = {
        title: item.file.name,
        loading: true,
        content: '<div class="modal-empty">预览生成中...</div>'
    };
    queueRender();

    try {
        state.preview = {
            title: item.file.name,
            loading: false,
            content: await buildPreview(item.file)
        };
    } catch (error) {
        state.preview = {
            title: item.file.name,
            loading: false,
            content: `<div class="modal-empty">预览失败：${escapeHtml(error.message || '未知错误')}</div>`
        };
    }
    queueRender();
}


function closePreview() {
    state.preview = null;
    queueRender();
}


function getResultByTaskId(taskId) {
    return state.results.find((item) => (
        item.data?.task_id === Number(taskId) || Number(item.taskId) === Number(taskId)
    ));
}


function findTableCell(data, tableId, rowIndex, colIndex) {
    const tables = data?.table_views || [];
    const table = tables.find((item) => item.table_id === tableId);
    const row = table?.rows?.find((item) => item.row_index === Number(rowIndex));
    const cell = row?.cells?.find((item) => item.col_index === Number(colIndex));
    return { table, row, cell };
}


function openTraceModal(title, content) {
    state.trace = { title, content };
    queueRender();
}


function closeTraceModal() {
    state.trace = null;
    queueRender();
}


function buildTraceContent(payload) {
    const rows = [
        ['来源文件', payload.source_file || '当前文件'],
        ['来源类型', payload.source_kind || ''],
        ['原始字段', payload.source_key || payload.source_header || ''],
        ['字段值', payload.value || ''],
        ['段落', payload.source_paragraph ?? ''],
        ['表格 ID', payload.source_table_id || ''],
        ['坐标', payload.source_locator || ''],
        ['表头', payload.source_header || ''],
        ['行上下文', payload.source_context || '']
    ];

    return `
        <div class="trace-grid">
            ${rows.map(([label, value]) => `
                <div class="trace-row">
                    <span class="trace-label">${escapeHtml(label)}</span>
                    <span class="trace-value">${escapeHtml(String(value))}</span>
                </div>
            `).join('')}
        </div>
        <div class="trace-block">
            <div class="trace-label">原始证据</div>
            <pre class="modal-code">${escapeHtml(payload.source_text || '暂无原始文本')}</pre>
        </div>
    `;
}


async function openFieldTrace(taskId, fieldName) {
    openTraceModal(`字段溯源 · ${fieldName}`, '<div class="modal-empty">溯源定位中...</div>');
    const result = await getFieldSource(taskId, fieldName);
    if (!result.success) {
        openTraceModal(`字段溯源 · ${fieldName}`, `<div class="modal-empty">获取失败：${escapeHtml(result.message)}</div>`);
        return;
    }
    openTraceModal(`字段溯源 · ${fieldName}`, buildTraceContent(result.data));
}


async function openRecordTrace(taskId, recordIndex) {
    openTraceModal(`记录溯源 · #${recordIndex}`, '<div class="modal-empty">溯源定位中...</div>');
    const result = await getRecordSource(taskId, recordIndex);
    if (!result.success) {
        openTraceModal(`记录溯源 · #${recordIndex}`, `<div class="modal-empty">获取失败：${escapeHtml(result.message)}</div>`);
        return;
    }
    openTraceModal(`记录溯源 · #${recordIndex}`, buildTraceContent(result.data));
}


function openFieldValueTrace(taskId, recordIndex, fieldName) {
    const resultEntry = getResultByTaskId(taskId);
    const record = resultEntry?.data?.results?.[Number(recordIndex)];
    const source = record?.field_sources?.[fieldName];
    if (!source) {
        openTraceModal(`字段溯源 · ${fieldName}`, '<div class="modal-empty">这个值当前没有可展示的来源段落。</div>');
        return;
    }
    openTraceModal(
        `字段溯源 · ${fieldName}`,
        buildTraceContent({
            source_file: resultEntry?.data?.file_name,
            source_key: fieldName,
            value: record?.fields?.[fieldName],
            source_kind: source.source_kind || 'paragraph',
            source_paragraph: source.paragraph_id,
            source_text: source.paragraph_text,
            source_table_id: source.source_table_id,
            source_row: source.source_row,
            source_col: source.source_col,
            source_header: source.source_header,
            source_locator: source.source_locator || (source.paragraph_id != null ? `paragraph:${source.paragraph_id}` : '-'),
            source_context: source.evidence
        })
    );
}


function buildExportRows(entry) {
    const selectedFields = deriveResultFields(entry?.data);
    const headers = selectedFields.map((field) => field.field_name);
    const records = Array.isArray(entry?.data?.results) ? entry.data.results : [];
    return {
        headers,
        rows: records.map((record) => headers.map((header) => record?.fields?.[header] ?? ''))
    };
}


function downloadTaskResult(taskId) {
    const entry = getResultByTaskId(taskId);
    if (!entry?.success || !entry?.data) {
        updateStatus('当前任务没有可下载的结果。', 'warning');
        return;
    }

    const { headers, rows } = buildExportRows(entry);
    if (!headers.length) {
        updateStatus('当前结果没有可导出的表头。', 'warning');
        return;
    }

    const workbook = XLSX.utils.book_new();
    const worksheet = XLSX.utils.aoa_to_sheet([headers, ...rows]);
    XLSX.utils.book_append_sheet(workbook, worksheet, sanitizeSheetName(entry.fileName || entry.data.file_name, 'DocFusion'));
    XLSX.writeFile(workbook, `${(entry.fileName || entry.data.file_name || 'docfusion').replace(/\.[^.]+$/, '')}_result.xlsx`);
}


function downloadAllResults() {
    const successEntries = state.results.filter((item) => item.success && item.data);
    if (!successEntries.length) {
        updateStatus('还没有可下载的结果。', 'warning');
        return;
    }

    const workbook = XLSX.utils.book_new();
    let appended = 0;
    successEntries.forEach((entry, index) => {
        const { headers, rows } = buildExportRows(entry);
        if (!headers.length) {
            return;
        }
        const worksheet = XLSX.utils.aoa_to_sheet([headers, ...rows]);
        XLSX.utils.book_append_sheet(
            workbook,
            worksheet,
            sanitizeSheetName(entry.fileName || entry.data?.file_name, `Result${index + 1}`)
        );
        appended += 1;
    });

    if (!appended) {
        updateStatus('当前结果还没有可导出的表格内容。', 'warning');
        return;
    }

    XLSX.writeFile(workbook, `docfusion_results_${new Date().toISOString().slice(0, 10)}.xlsx`);
}


function openCellTrace(taskId, tableId, rowIndex, colIndex) {
    const resultEntry = getResultByTaskId(taskId);
    if (!resultEntry?.data) return;

    const { table, cell } = findTableCell(resultEntry.data, tableId, rowIndex, colIndex);
    if (!table || !cell) return;

    const traceHits = cell.trace_hits || [];
    const hitHtml = traceHits.length
        ? traceHits.map((hit) => `
            <div class="trace-card">
                <div class="trace-card__title">${escapeHtml(hit.indicator || hit.source_header || '关联字段')}</div>
                <div class="trace-card__meta">值：${escapeHtml(hit.value || '')}</div>
                <div class="trace-card__meta">来源：${escapeHtml(hit.source_text || hit.source_context || '')}</div>
            </div>
        `).join('')
        : '<div class="modal-empty">这个单元格当前没有命中抽取记录，但已保留定位信息。</div>';

    openTraceModal(
        `单元格定位 · ${cell.locator || ''}`,
        `
            <div class="trace-grid">
                <div class="trace-row"><span class="trace-label">表格</span><span class="trace-value">${escapeHtml(table.title || table.table_id)}</span></div>
                <div class="trace-row"><span class="trace-label">位置</span><span class="trace-value">${escapeHtml(cell.locator || '-')}</span></div>
                <div class="trace-row"><span class="trace-label">数值</span><span class="trace-value">${escapeHtml(cell.value || '-')}</span></div>
                <div class="trace-row"><span class="trace-label">关联记录数</span><span class="trace-value">${traceHits.length}</span></div>
            </div>
            <div class="trace-block">
                <div class="trace-label">关联抽取结果</div>
                ${hitHtml}
            </div>
        `
    );
}


async function handleUpload() {
    if (!state.files.length || state.uploading) {
        return;
    }

    syncFieldInputsFromDOM();
    const extractConfig = buildExtractConfigPayload();
    if (!extractConfig.fields.length) {
        updateStatus('请先填写至少一个表头。', 'warning');
        return;
    }
    if (hasDuplicateFieldLabels(extractConfig.fields)) {
        updateStatus('表头名称存在重复，请先修改后再上传。', 'warning');
        return;
    }

    state.uploading = true;
    state.results = [];
    persistResults();
    updateStatus(`上传请求已发出，当前按 ${extractConfig.fields.length} 个提取列执行流水线。`, 'loading');

    const uploadResult = await uploadFiles(state.files.map((item) => item.file), extractConfig);
    if (!uploadResult.success) {
        state.uploading = false;
        updateStatus(`上传失败：${uploadResult.message}`, 'error');
        return;
    }

    state.results = (uploadResult.results || []).map((item) => {
        if (!item?.task_id) {
            return {
                fileName: item?.fileName || '未知文件',
                success: false,
                pending: false,
                error: item?.message || '任务创建失败'
            };
        }
        return {
            fileName: item.fileName || '未知文件',
            taskId: item.task_id,
            pending: !['matched', 'extracted'].includes(item.status),
            success: false,
            error: null,
            cached: Boolean(item.cached),
            pollErrors: 0,
            task: {
                task_id: item.task_id,
                status: item.status,
                parse_status: item.status === 'matched' || item.status === 'extracted' ? 'success' : 'pending',
                extract_status: item.status === 'matched' || item.status === 'extracted' ? 'success' : 'pending',
                match_status: item.status === 'matched' ? 'success' : 'pending',
                progress: item.progress || null
            }
        };
    });
    persistResults();
    queueRender();

    const readyTaskIds = state.results
        .filter((item) => item.taskId && !item.pending)
        .map((item) => item.taskId);
    if (readyTaskIds.length) {
        await Promise.all(readyTaskIds.map((taskId) => finalizeTaskEntry(taskId)));
    }

    const pendingTaskIds = state.results
        .filter((item) => item.taskId && item.pending)
        .map((item) => item.taskId);
    if (pendingTaskIds.length) {
        await monitorTaskEntries(pendingTaskIds);
    }

    state.uploading = false;
    const successCount = state.results.filter((item) => item.success).length;
    const failureCount = state.results.filter((item) => !item.success && !item.pending).length;
    updateStatus(
        `流水线执行完成，共处理 ${state.results.length} 个文件，成功 ${successCount} 个，失败 ${failureCount} 个。`,
        successCount ? 'success' : 'error'
    );
}


function renderFileQueue() {
    if (!state.files.length) {
        return `
            <div class="queue-empty">
                <div class="queue-empty__title">拖入待分析文档</div>
                <div class="queue-empty__desc">支持文本、Markdown、Word、Excel。支持一次性上传多个文件，结果会按文件分开生成表格。</div>
            </div>
        `;
    }

    return `
        <div class="queue-list">
            ${state.files.map((item, index) => `
                <div class="queue-item">
                    <div class="queue-item__badge">${fileIcon(item.file.name)}</div>
                    <div class="queue-item__meta">
                        <div class="queue-item__name">${escapeHtml(item.file.name)}</div>
                        <div class="queue-item__sub">${formatFileSize(item.file.size)} · ${fileExtension(item.file.name).replace('.', '').toUpperCase()}</div>
                    </div>
                    <div class="queue-item__actions">
                        <button class="ghost-btn" data-action="preview-file" data-index="${index}">预览</button>
                        <button class="ghost-btn ghost-btn--danger" data-action="remove-file" data-index="${index}">移除</button>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}


function renderFieldConfigurator() {
    return `
        <section class="config-panel">
            <div class="config-panel__header">
                <div>
                    <h4>提取列配置</h4>
                    <p>表头完全由你手动输入，列顺序就是后端逐列建表的顺序。</p>
                </div>
                <div class="config-panel__meta">${visibleFieldCount()} 列已启用</div>
            </div>
            ${state.extractFields.length ? `
            <div class="config-grid">
                ${state.extractFields.map((field) => `
                    <div class="config-item ${field.enabled ? 'config-item--enabled' : ''}">
                        <div class="config-item__toolbar">
                            <label class="switch">
                                <input type="checkbox" data-action="toggle-field" data-field-id="${field.id}" ${field.enabled ? 'checked' : ''}>
                                <span></span>
                            </label>
                            <select class="config-select" data-action="change-field-slot" data-field-id="${field.id}" ${field.locked ? 'disabled' : ''}>
                                ${SLOT_OPTIONS.map((option) => `
                                    <option value="${option.value}" ${option.value === field.slot ? 'selected' : ''}>${option.label}</option>
                                `).join('')}
                            </select>
                            <button class="ghost-btn ghost-btn--danger ghost-btn--icon" data-action="remove-custom-field" data-field-id="${field.id}">移除</button>
                        </div>
                        <input
                            class="config-input"
                            type="text"
                            value="${escapeHtml(field.label)}"
                            data-action="rename-field"
                            data-field-id="${field.id}"
                            placeholder="结果表头"
                        >
                        <div class="config-item__hint">${escapeHtml(field.hint || '自定义提取字段')}</div>
                    </div>
                `).join('')}
            </div>
            ` : `
            <div class="config-empty">
                <div class="config-empty__title">还没有任何表头</div>
                <div class="config-empty__desc">先在下面添加列名，再上传文件。</div>
            </div>
            `}
            <div class="config-builder">
                <div class="config-builder__label">新增自定义列</div>
                <div class="config-builder__controls">
                    <input
                        class="config-input"
                        type="text"
                        value="${escapeHtml(state.customDraft.label)}"
                        data-action="draft-label"
                        placeholder="输入列名"
                    >
                    <select class="config-select" data-action="draft-slot">
                        ${SLOT_OPTIONS.map((option) => `
                            <option value="${option.value}" ${option.value === state.customDraft.slot ? 'selected' : ''}>${option.label}</option>
                        `).join('')}
                    </select>
                    <button class="primary-btn" data-action="add-custom-field">加入配置</button>
                </div>
                <div class="config-builder__foot">你定义的列名会直接传给后端，槽位只用于帮助抽取和归并。</div>
            </div>
            <div class="config-actions">
                <button class="ghost-btn" data-action="reset-fields">清空表头</button>
            </div>
        </section>
    `;
}


function renderMatchedFields(taskId, matchResult) {
    if (!matchResult) return '';
    if (matchResult.match_status !== 'success') {
        return `
            <section class="module-card">
                <div class="module-card__header">
                    <h4>标准字段匹配</h4>
                    <span class="pill pill--muted">${escapeHtml(matchResult.match_status || 'skipped')}</span>
                </div>
                <div class="module-note">${escapeHtml(matchResult.reason || '当前文件未进入匹配链路')}</div>
            </section>
        `;
    }

    const entries = Object.entries(matchResult.matched_result || {});
    return `
        <section class="module-card">
            <div class="module-card__header">
                <h4>标准字段匹配</h4>
                <span class="pill">MATCH</span>
            </div>
            <div class="field-grid">
                ${entries.map(([key, value]) => `
                    <button class="field-chip" data-action="trace-field" data-task-id="${taskId}" data-field-name="${escapeHtml(key)}">
                        <span class="field-chip__label">${escapeHtml(key)}</span>
                        <span class="field-chip__value">${escapeHtml(String(value))}</span>
                    </button>
                `).join('')}
            </div>
        </section>
    `;
}


function renderExtractedResults(taskId, results, selectedFields = []) {
    if (!results.length) {
        return `
            <section class="module-card">
                <div class="module-card__header">
                    <h4>抽取结果</h4>
                    <span class="pill pill--muted">EMPTY</span>
                </div>
                <div class="module-note">当前文件没有返回可展示的抽取结果。</div>
            </section>
        `;
    }

    const columns = fallbackResultFields(selectedFields);
    return `
        <section class="module-card">
            <div class="module-card__header">
                <h4>抽取结果</h4>
                <span class="pill">${results.length} 条</span>
            </div>
            <div class="metrics-table metrics-table--dynamic" style="--metric-columns:${columns.length}">
                <div class="metrics-table__head metrics-table__head--dynamic">
                    ${columns.map((field) => `<span>${escapeHtml(field.field_name)}</span>`).join('')}
                </div>
                ${results.map((item) => `
                    <div class="metrics-table__row metrics-table__row--dynamic">
                        ${columns.map((field) => `
                            ${item.field_sources?.[field.field_name] ? `
                                <button
                                    class="metric-cell metric-cell--button ${field.slot === 'value' ? 'metric-cell--strong' : ''}"
                                    data-action="trace-field-value"
                                    data-task-id="${taskId}"
                                    data-record-index="${item.record_index}"
                                    data-field-name="${escapeHtml(field.field_name)}"
                                    title="点击查看对应段落"
                                >
                                    ${escapeHtml(String(item.fields?.[field.field_name] ?? ''))}
                                </button>
                            ` : `
                                <span class="metric-cell ${field.slot === 'value' ? 'metric-cell--strong' : ''}">
                                    ${escapeHtml(String(item.fields?.[field.field_name] ?? ''))}
                                </span>
                            `}
                        `).join('')}
                    </div>
                `).join('')}
            </div>
        </section>
    `;
}


function renderPendingResultCard(entry) {
    const task = entry.task || {};
    const progress = task.progress || {};
    const percent = clampProgressPercent(progress);
    const message = progress.message || '等待后端返回阶段进度';
    const refreshedAt = formatTaskUpdatedAt(task.updated_at);

    return `
        <article class="result-card result-card--pending">
            <div class="result-card__header">
                <div>
                    <div class="result-card__title">${escapeHtml(entry.fileName)}</div>
                    <div class="result-card__subtitle">任务 #${task.task_id || entry.taskId || '-'} · ${escapeHtml(stageLabel(progress.stage))} · 最近刷新 ${escapeHtml(refreshedAt)}</div>
                </div>
                <span class="pill pill--muted">${percent}%</span>
            </div>
            <div class="progress-panel">
                <div class="progress-panel__meta">
                    <span class="status-badge status-badge--${stageTone(progress.stage)}">${escapeHtml(message)}</span>
                    <strong>${percent}%</strong>
                </div>
                <div class="progress-bar">
                    <span style="width:${percent}%"></span>
                </div>
            </div>
            <div class="progress-stages">
                <span class="pill pill--muted">parse ${escapeHtml(task.parse_status || 'pending')}</span>
                <span class="pill pill--muted">extract ${escapeHtml(task.extract_status || 'pending')}</span>
                <span class="pill pill--muted">match ${escapeHtml(task.match_status || 'pending')}</span>
            </div>
        </article>
    `;
}


function renderTableViews(taskId, tableViews) {
    if (!tableViews.length) {
        return '';
    }

    return `
        <section class="module-card">
            <div class="module-card__header">
                <h4>解析后表格</h4>
                <span class="pill">${tableViews.length} 张</span>
            </div>
            <div class="table-gallery">
                ${tableViews.map((table) => `
                    <details class="table-card" open>
                        <summary>
                            <span>${escapeHtml(table.title || table.table_id)}</span>
                            <span>${table.row_count} x ${table.column_count}</span>
                        </summary>
                        <div class="table-wrap">
                            <table class="fusion-table">
                                <tbody>
                                    ${table.rows.map((row) => `
                                        <tr>
                                            ${row.cells.map((cell) => `
                                                <td>
                                                    <button
                                                        class="table-cell ${cell.trace_hits?.length ? 'table-cell--hit' : ''}"
                                                        data-action="trace-cell"
                                                        data-task-id="${taskId}"
                                                        data-table-id="${escapeHtml(table.table_id)}"
                                                        data-row="${cell.row_index}"
                                                        data-col="${cell.col_index}"
                                                    >
                                                        ${escapeHtml(cell.value || '-')}
                                                    </button>
                                                </td>
                                            `).join('')}
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        </div>
                    </details>
                `).join('')}
            </div>
        </section>
    `;
}


function renderRawText(rawText) {
    if (!rawText) return '';
    return `
        <section class="module-card">
            <div class="module-card__header">
                <h4>原文预览</h4>
                <span class="pill pill--muted">TEXT</span>
            </div>
            <pre class="raw-text">${escapeHtml(rawText.slice(0, 2000))}${rawText.length > 2000 ? '\n...' : ''}</pre>
        </section>
    `;
}


function renderResultCard(entry) {
    if (entry.pending) {
        return renderPendingResultCard(entry);
    }

    if (!entry.success) {
        return `
            <article class="result-card result-card--error">
                <div class="result-card__header">
                    <div>
                        <div class="result-card__title">${escapeHtml(entry.fileName)}</div>
                        <div class="result-card__subtitle">处理失败</div>
                    </div>
                    <span class="pill pill--danger">FAILED</span>
                </div>
                <div class="module-note">${escapeHtml(entry.error || '未知错误')}</div>
            </article>
        `;
    }

    const { data } = entry;
    const summary = data.parse_result_summary || {};
    const selectedFields = deriveResultFields(data);
    return `
        <article class="result-card">
            <div class="result-card__header">
                <div>
                    <div class="result-card__title">${escapeHtml(entry.fileName)}</div>
                    <div class="result-card__subtitle">任务 #${data.task_id} · ${escapeHtml(data.pipeline_used || 'parse')}</div>
                </div>
                <span class="pill">${escapeHtml((data.pipeline_used || 'parse').toUpperCase())}</span>
            </div>
            <div class="module-actions">
                <button class="ghost-btn" data-action="download-task" data-task-id="${data.task_id}">下载表格</button>
            </div>
            <div class="stats-row">
                <div class="stat-box">
                    <span>段落</span>
                    <strong>${summary.paragraph_count || 0}</strong>
                </div>
                <div class="stat-box">
                    <span>表格</span>
                    <strong>${summary.table_count || 0}</strong>
                </div>
                <div class="stat-box">
                    <span>抽取</span>
                    <strong>${data.total || 0}</strong>
                </div>
            </div>
            <div class="result-fields">
                ${selectedFields.map((field) => `<span class="result-fields__chip">${escapeHtml(field.field_name)}</span>`).join('')}
            </div>
            ${renderMatchedFields(data.task_id, data.match_result)}
            ${data.results_preview_limited ? `<div class="module-note">抽取结果仅预览前 ${data.results.length} 条，完整记录仍保存在后端任务结果中。</div>` : ''}
            ${renderExtractedResults(data.task_id, data.results || [], selectedFields)}
            ${data.table_views_preview_limited ? `<div class="module-note">表格预览已做截断，仅展示前 40 行和前 18 列。</div>` : ''}
            ${renderTableViews(data.task_id, data.table_views || [])}
            ${data.raw_text_preview_limited ? `<div class="module-note">原文仅展示前 4000 个字符。</div>` : ''}
            ${renderRawText(data.raw_text || '')}
        </article>
    `;
}


function renderResultsPanel() {
    if (!state.results.length) {
        return `
            <section class="results-shell">
                <div class="results-empty">
                    <div class="results-empty__title">等待第一批结果</div>
                    <div class="results-empty__desc">上传完成后，这里会按文件分组展示结果表、字段来源、解析表格，并支持直接下载。</div>
                </div>
            </section>
        `;
    }

    return `
        <section class="results-shell">
            <div class="results-shell__header">
                <div>
                    <h3>DocFusion 工作区</h3>
                    <p>每个文件都保留独立结果表。上传后会先显示进度，再分别生成结果和下载表。</p>
                </div>
                <div class="results-shell__stats">
                    <span>${state.results.filter((item) => item.success).length} 成功</span>
                    <span>${state.results.filter((item) => item.pending).length} 处理中</span>
                    <span>${state.results.filter((item) => !item.success && !item.pending).length} 失败</span>
                </div>
                <div class="results-shell__actions">
                    <button class="ghost-btn" data-action="download-all-results">下载全部结果</button>
                </div>
            </div>
            <div class="results-grid">
                ${state.results.map(renderResultCard).join('')}
            </div>
        </section>
    `;
}


function renderModal(modal, type) {
    if (!modal) return '';
    return `
        <div class="modal-layer" data-action="close-${type}" data-modal-type="${type}">
            <div class="modal-card modal-card--${type}" data-modal-card="${type}">
                <div class="modal-card__header">
                    <h3>${escapeHtml(modal.title)}</h3>
                    <button class="modal-close" data-action="close-${type}">×</button>
                </div>
                <div class="modal-card__body">${modal.content}</div>
            </div>
        </div>
    `;
}


function render() {
    const renderState = captureRenderState();
    app.innerHTML = `
        <div class="shell">
            <section class="hero-panel">
                <div class="hero-panel__content">
                    <h1>DocFusion</h1>
                    <p class="hero-caption">一款专为文件处理打造的平台</p>
                    <p>你先定义列名和槽位，后端会先对每段内容做整行结构化识别，再按列顺序用已确认主键逐步归并结果，并把每个值自己的来源段落一并返回。</p>
                    <div class="hero-actions">
                        <a class="hero-link" href="./guide.html" target="_blank" rel="noreferrer">查看新手使用手册</a>
                    </div>
                </div>
                <div class="hero-panel__metrics">
                <div class="hero-metric"><span>队列文件</span><strong>${state.files.length}</strong></div>
                <div class="hero-metric"><span>启用提取列</span><strong>${visibleFieldCount()}</strong></div>
                <div class="hero-metric"><span>支持格式</span><strong>TXT / MD / DOCX / XLSX</strong></div>
                <div class="hero-metric"><span>前端版本</span><strong>${escapeHtml(FRONTEND_BUILD)}</strong></div>
            </div>
        </section>

            <div class="workspace">
                <section class="control-panel">
                    <div class="panel-head">
                        <div>
                            <h3>上传控制台</h3>
                            <p>先定义表头，再选择文件进入统一流水线。</p>
                        </div>
                        <span class="status-badge status-badge--${state.status.tone}">${escapeHtml(state.status.text)}</span>
                    </div>

                    <input type="file" id="fileInput" multiple accept=".txt,.md,.docx,.xlsx" hidden>

                    <div class="drop-zone ${state.dragActive ? 'drop-zone--active' : ''}" id="dropZone">
                        <div class="drop-zone__orb"></div>
                        <div class="drop-zone__title">拖拽文件到这里</div>
                        <div class="drop-zone__desc">或使用按钮加载本地文档与表格</div>
                        <div class="drop-zone__actions">
                            <button class="primary-btn" data-action="select-files">选择文件</button>
                            <button class="ghost-btn" data-action="clear-files">清空队列</button>
                        </div>
                    </div>

                    ${renderFileQueue()}
                    ${renderFieldConfigurator()}

                    <div class="launch-bar">
                        <button class="launch-btn" data-action="upload-files" ${state.uploading ? 'disabled' : ''}>
                            ${state.uploading ? '流水线运行中...' : '启动解析流水线'}
                        </button>
                    </div>
                </section>

                ${renderResultsPanel()}
            </div>
        </div>
        ${renderModal(state.preview, 'preview')}
        ${renderModal(state.trace, 'trace')}
    `;
    restoreRenderState(renderState);
}


app.addEventListener('click', async (event) => {
    const modalCard = event.target.closest('[data-modal-card]');
    const modalLayer = event.target.closest('.modal-layer');
    if (modalLayer && !modalCard) {
        const modalType = modalLayer.dataset.modalType;
        if (modalType === 'preview') {
            closePreview();
            return;
        }
        if (modalType === 'trace') {
            closeTraceModal();
            return;
        }
    }

    const target = event.target.closest('[data-action]');
    if (modalCard && target === modalLayer) {
        return;
    }
    if (!target) return;

    const action = target.dataset.action;
    if (action === 'select-files') {
        document.getElementById('fileInput').click();
    } else if (action === 'clear-files') {
        clearFiles();
    } else if (action === 'remove-file') {
        removeFile(Number(target.dataset.index));
    } else if (action === 'preview-file') {
        await openPreview(Number(target.dataset.index));
    } else if (action === 'upload-files') {
        await handleUpload();
    } else if (action === 'trace-field') {
        await openFieldTrace(target.dataset.taskId, target.dataset.fieldName);
    } else if (action === 'trace-record') {
        await openRecordTrace(target.dataset.taskId, Number(target.dataset.recordIndex));
    } else if (action === 'trace-field-value') {
        openFieldValueTrace(target.dataset.taskId, Number(target.dataset.recordIndex), target.dataset.fieldName);
    } else if (action === 'trace-cell') {
        openCellTrace(target.dataset.taskId, target.dataset.tableId, target.dataset.row, target.dataset.col);
    } else if (action === 'add-custom-field') {
        addCustomField();
    } else if (action === 'remove-custom-field') {
        removeCustomField(target.dataset.fieldId);
    } else if (action === 'reset-fields') {
        resetFields();
    } else if (action === 'download-task') {
        downloadTaskResult(target.dataset.taskId);
    } else if (action === 'download-all-results') {
        downloadAllResults();
    } else if (action === 'close-preview') {
        closePreview();
    } else if (action === 'close-trace') {
        closeTraceModal();
    }
});


app.addEventListener('change', (event) => {
    if (event.target.id === 'fileInput') {
        addFiles(event.target.files);
        event.target.value = '';
        return;
    }

    if (event.target.dataset.action === 'toggle-field') {
        updateFieldEnabled(event.target.dataset.fieldId, event.target.checked);
        return;
    }

    if (event.target.dataset.action === 'change-field-slot') {
        updateFieldSlot(event.target.dataset.fieldId, event.target.value);
        return;
    }

    if (event.target.dataset.action === 'draft-slot') {
        state.customDraft = { ...state.customDraft, slot: event.target.value };
        queueRender();
    }
});


app.addEventListener('input', (event) => {
    if (event.target.dataset.action === 'rename-field') {
        updateFieldLabel(event.target.dataset.fieldId, event.target.value);
        return;
    }

    if (event.target.dataset.action === 'draft-label') {
        state.customDraft = { ...state.customDraft, label: event.target.value };
    }
});


app.addEventListener('dragover', (event) => {
    if (!event.target.closest('#dropZone')) return;
    event.preventDefault();
    if (!state.dragActive) {
        state.dragActive = true;
        queueRender();
    }
});


app.addEventListener('dragleave', (event) => {
    const zone = event.target.closest('#dropZone');
    if (!zone) return;
    if (event.relatedTarget && zone.contains(event.relatedTarget)) return;
    state.dragActive = false;
    queueRender();
});


app.addEventListener('drop', (event) => {
    const zone = event.target.closest('#dropZone');
    if (!zone) return;
    event.preventDefault();
    addFiles(event.dataTransfer?.files || []);
});

restoreStoredResults();
render();
if (state.results.length) {
    updateStatus(`已恢复 ${state.results.length} 个历史任务，正在同步状态。`, 'info');
    resumeStoredTaskEntries();
}
