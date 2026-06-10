const BASE_URL = window.location.origin;

async function request(path, options = {}) {
    const { timeoutMs = 15000, ...restOptions } = options;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    const requestOptions = {
        cache: 'no-store',
        signal: controller.signal,
        ...restOptions
    };
    try {
        const response = await fetch(`${BASE_URL}${path}`, requestOptions);
        const text = await response.text();
        let data = null;
        try {
            data = text ? JSON.parse(text) : null;
        } catch (error) {
            throw new Error(text || `请求失败: ${response.status}`);
        }
        if (!response.ok) {
            throw new Error(data?.detail || data?.message || `请求失败: ${response.status}`);
        }
        return data;
    } catch (error) {
        if (error?.name === 'AbortError') {
            throw new Error(`请求超时: ${path}`);
        }
        throw error;
    } finally {
        window.clearTimeout(timeoutId);
    }
}

export async function uploadFiles(files, extractConfig = null) {
    try {
        const formData = new FormData();
        for (const file of files) {
            formData.append('files', file);
        }
        if (extractConfig) {
            formData.append('extract_config', JSON.stringify(extractConfig));
        }
        const data = await request('/upload/batch', {
            method: 'POST',
            body: formData,
            timeoutMs: 180000
        });
        return {
            success: true,
            results: data.results || [],
            message: data.message || '上传成功'
        };
    } catch (error) {
        return { success: false, message: error.message || '上传失败' };
    }
}

export async function getTask(taskId) {
    try {
        return { success: true, data: await request(`/tasks/${taskId}/progress?_=${Date.now()}`, { timeoutMs: 8000 }) };
    } catch (error) {
        return { success: false, message: error.message || '查询任务失败' };
    }
}

export async function getFields(taskId) {
    try {
        return { success: true, data: await request(`/fields/${taskId}?_=${Date.now()}`, { timeoutMs: 20000 }) };
    } catch (error) {
        return { success: false, message: error.message || '查询字段失败' };
    }
}

export async function getFieldSource(taskId, fieldName) {
    try {
        return { success: true, data: await request(`/fields/${taskId}/source/${encodeURIComponent(fieldName)}?_=${Date.now()}`, { timeoutMs: 10000 }) };
    } catch (error) {
        return { success: false, message: error.message || '查询溯源失败' };
    }
}

export async function getRecordSource(taskId, recordIndex) {
    try {
        return { success: true, data: await request(`/fields/${taskId}/records/${recordIndex}/source?_=${Date.now()}`, { timeoutMs: 10000 }) };
    } catch (error) {
        return { success: false, message: error.message || '查询记录溯源失败' };
    }
}
