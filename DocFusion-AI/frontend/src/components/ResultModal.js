//指令结果
export function renderResultModal() {
    return `
        <div class="result-modal" id="resultModal">
            <div class="modal-content" id="resultModalContent">
                <div class="result-header" id="resultHeader">
                    <div class="result-header-left">
                        <span id="resultTitle">指令执行结果</span>
                        <button class="fullscreen-btn" id="fullscreenResultBtn" title="全屏">⛶</button>
                    </div>
                    <button class="delete-btn" id="closeResultBtn" style="font-size:1.5rem;">&times;</button>
                </div>
                <div class="result-content" id="resultContent"></div>
            </div>
        </div>
    `;
}