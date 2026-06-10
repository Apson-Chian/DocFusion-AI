//个人资料编辑
export function renderProfileModal() {
    return `
        <div class="modal" id="profileModal">
            <div class="modal-content">
                <h3>编辑个人资料</h3>
                <div class="avatar-edit-area">
                    <img src="" alt="头像预览" class="current-avatar-preview" id="avatarPreview">
                    <div>
                        <input type="file" id="avatarUpload" accept="image/png, image/jpeg, image/jpg, image/gif, image/webp" style="display: none;">
                        <button class="avatar-upload-btn" id="uploadAvatarBtn">上传新头像</button>
                        <p style="font-size:0.75rem; color:#64748b; margin-top:6px;">JPG/PNG/GIF</p>
                    </div>
                </div>
                <div class="name-edit-field">
                    <label>姓名</label>
                    <input type="text" id="profileNameInput" placeholder="输入名字" value="旅行者">
                </div>
                <div class="modal-actions">
                    <button class="btn" id="closeProfileModal">取消</button>
                    <button class="btn btn-primary" id="saveProfileBtn">保存</button>
                </div>
            </div>
        </div>
    `;
}