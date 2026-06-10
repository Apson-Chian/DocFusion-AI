import { escapeHtml, formatFileSize } from '../utils/helpers.js';

/**
 * 渲染文件列表
 * @param {Array<{file: File, id: string|number}>} fileArray
 * @returns {string}
 */
export function renderFileList(fileArray) {
    if (fileArray.length === 0) {
        return '<div class="empty-message">📭 暂无已选文件</div>';
    }

    let html = '';
    fileArray.forEach((item, index) => {
        const file = item.file;
        const fileName = file.name;
        const fileSize = formatFileSize(file.size);
        let icon = '📄';
        const ext = fileName.slice(fileName.lastIndexOf('.')).toLowerCase();
        if (ext === '.txt') icon = '📃';
        else if (ext === '.md') icon = '📝';
        else if (ext === '.doc' || ext === '.docx') icon = '📘';
        else if (ext === '.xls' || ext === '.xlsx') icon = '📊';

        html += `
            <div class="file-item" data-index="${index}">
                <div class="file-info">
                    <span class="file-icon">${icon}</span>
                    <div class="file-details">
                        <span class="file-name">${escapeHtml(fileName)} <span class="file-size">${fileSize}</span></span>
                    </div>
                </div>
                <div class="file-actions-btn">
                    <button class="preview-text-btn" data-index="${index}" title="预览文件">预览</button>
                    <button class="delete-btn" data-index="${index}" title="移除">✕</button>
                </div>
            </div>
        `;
    });
    return html;
}