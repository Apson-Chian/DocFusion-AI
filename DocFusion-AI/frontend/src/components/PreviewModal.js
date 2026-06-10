//文件预览
export function renderPreviewModal() {
    return `
        <div class="modal preview-modal" id="previewModal">
            <div class="modal-content" id="previewModalContent">
                <div class="preview-header">
                    <div class="preview-header-left">
                        <span id="previewFileName">文件预览</span>
                        <button class="fullscreen-btn" id="fullscreenPreviewBtn" title="全屏">⛶</button>
                    </div>
                    <button class="delete-btn" id="closePreviewBtn" style="font-size:1.5rem;">&times;</button>
                </div>
                <div class="preview-content" id="previewContent">加载中...</div>
                <!-- 新增：Excel发送区域，默认显示，由JS控制显隐 -->
                <div class="preview-footer" id="previewFooter" style="margin-top: 1rem; display: flex; gap: 0.5rem; padding: 0 0.2rem;">
                    <input type="text" id="standardCharInput" class="command-input" placeholder="输入查找内容" style="flex:1;">
                    <button class="btn btn-primary" id="sendTableDataBtn">查找</button>
                </div>
            </div>
        </div>
    `;
}