//指令输入
export function renderCommandInput() {
    return `
        <div class="command-area">
            <input type="text" class="command-input" id="commandInput" placeholder="输入指令 (如: help)" value="ls -la">
            <button class="btn btn-secondary btn-sm" id="executeBtn">执行</button>
        </div>
    `;
}