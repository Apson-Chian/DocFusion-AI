//个人中心
import { escapeHtml } from '../utils/helpers.js';

/**
 * 渲染右上角个人中心
 * @param {Object} props
 * @param {string} props.avatar
 * @param {string} props.name
 * @param {Function} props.onAvatarClick - 点击头像
 * @param {Function} props.onEditClick - 点击编辑图标
 * @returns {string} HTML字符串
 */
export function renderProfileCorner({ avatar, name, onAvatarClick, onEditClick }) {
    // 注意：事件绑定需要在插入DOM后由父组件附加，此处仅返回HTML
    return `
        <div class="profile-corner" id="profileCorner">
            <img src="${escapeHtml(avatar)}" alt="avatar" class="profile-avatar" id="avatarDisplay" title="点击更改头像">
            <div class="profile-info">
                <div class="profile-name">
                    <span id="profileNameDisplay">${escapeHtml(name)}</span>
                    <span class="profile-edit-icon" id="editProfileBtn">✎</span>
                </div>
                <div class="profile-role">普通用户</div>
            </div>
        </div>
    `;
}