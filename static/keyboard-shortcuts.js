// 键盘快捷键支持

const shortcuts = {
  'k': { action: 'focusSearch', desc: '快速搜索' },
  'r': { action: 'refreshResults', desc: '刷新结果' },
  'p': { action: 'probeConnection', desc: '测试连接' },
  't': { action: 'runSmokeTest', desc: '快速测试' },
  'l': { action: 'viewLogs', desc: '查看日志' },
  'h': { action: 'showHelp', desc: '显示帮助' },
  '?': { action: 'showShortcuts', desc: '快捷键列表' },
  'Escape': { action: 'closeModal', desc: '关闭弹窗' }
};

let shortcutsHintTimeout;

function handleKeyboardShortcut(e) {
  // 忽略输入框中的按键
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
    return;
  }
  
  const key = e.key.toLowerCase();
  const isCmdOrCtrl = e.metaKey || e.ctrlKey;
  
  // Cmd/Ctrl + 快捷键
  if (isCmdOrCtrl) {
    switch(key) {
      case 'k':
        e.preventDefault();
        focusSearch();
        break;
      case 'r':
        e.preventDefault();
        refreshResults();
        break;
      case 'p':
        e.preventDefault();
        probeConnection();
        break;
    }
    return;
  }
  
  // 单键快捷键
  const shortcut = shortcuts[key];
  if (shortcut) {
    e.preventDefault();
    executeShortcut(shortcut.action);
    showShortcutHint(shortcut.desc);
  }
}

function executeShortcut(action) {
  switch(action) {
    case 'focusSearch':
      const searchInput = document.getElementById('runSearch');
      if (searchInput) searchInput.focus();
      break;
    case 'refreshResults':
      const refreshBtn = document.getElementById('refreshResults');
      if (refreshBtn) refreshBtn.click();
      break;
    case 'probeConnection':
      const probeBtn = document.getElementById('probeServer');
      if (probeBtn) probeBtn.click();
      break;
    case 'runSmokeTest':
      const smokeBtn = document.getElementById('smokeRun');
      if (smokeBtn) smokeBtn.click();
      break;
    case 'viewLogs':
      const logsSection = document.getElementById('logs');
      if (logsSection) logsSection.scrollIntoView({ behavior: 'smooth' });
      break;
    case 'showHelp':
      showHelpModal();
      break;
    case 'showShortcuts':
      showShortcutsModal();
      break;
    case 'closeModal':
      closeAllModals();
      break;
  }
}

function showShortcutHint(desc) {
  let hint = document.getElementById('shortcutsHint');
  if (!hint) {
    hint = document.createElement('div');
    hint.id = 'shortcutsHint';
    hint.className = 'shortcuts-hint';
    document.body.appendChild(hint);
  }
  
  hint.textContent = desc;
  hint.classList.add('show');
  
  clearTimeout(shortcutsHintTimeout);
  shortcutsHintTimeout = setTimeout(() => {
    hint.classList.remove('show');
  }, 2000);
}

function showShortcutsModal() {
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="modal-content">
      <div class="modal-header">
        <h2>键盘快捷键</h2>
        <button class="icon-btn" onclick="this.closest('.modal-overlay').remove()">✕</button>
      </div>
      <div class="modal-body">
        <div class="shortcuts-list">
          ${Object.entries(shortcuts).map(([key, info]) => `
            <div class="shortcut-row">
              <kbd>${key === 'Escape' ? 'Esc' : key.toUpperCase()}</kbd>
              <span>${info.desc}</span>
            </div>
          `).join('')}
          <div class="shortcut-row">
            <kbd>Cmd/Ctrl</kbd> + <kbd>K</kbd>
            <span>快速搜索</span>
          </div>
          <div class="shortcut-row">
            <kbd>Cmd/Ctrl</kbd> + <kbd>R</kbd>
            <span>刷新结果</span>
          </div>
          <div class="shortcut-row">
            <kbd>Cmd/Ctrl</kbd> + <kbd>P</kbd>
            <span>测试连接</span>
          </div>
        </div>
      </div>
    </div>
  `;
  
  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });
}

function showHelpModal() {
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="modal-content">
      <div class="modal-header">
        <h2>使用帮助</h2>
        <button class="icon-btn" onclick="this.closest('.modal-overlay').remove()">✕</button>
      </div>
      <div class="modal-body">
        <h3>快速开始</h3>
        <ol>
          <li>配置数据集、运行工作区和 Judge 信息</li>
          <li>选择数据集</li>
          <li>点击"Run Smoke"快速测试</li>
          <li>查看结果和分析</li>
        </ol>
        
        <h3>常见问题</h3>
        <p><strong>Q: 如何对比多个实验？</strong><br>
        A: 在"最近 Runs"中点击多个实验，系统会自动生成对比分析。</p>
        
        <p><strong>Q: 如何查看详细日志？</strong><br>
        A: 点击任务列表中的任务，下方会显示完整日志。</p>
        
        <p><strong>Q: 如何导出结果？</strong><br>
        A: 在结果页面点击"导出报告"按钮。</p>
      </div>
    </div>
  `;
  
  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });
}

function closeAllModals() {
  document.querySelectorAll('.modal-overlay').forEach(modal => modal.remove());
}

// 初始化
document.addEventListener('keydown', handleKeyboardShortcut);

// 添加帮助按钮到顶部栏
function addHelpButton() {
  const topbar = document.querySelector('.topbar');
  if (!topbar) return;
  
  const existingHelp = document.getElementById('helpButton');
  if (existingHelp) return;
  
  const helpBtn = document.createElement('button');
  helpBtn.id = 'helpButton';
  helpBtn.className = 'icon-btn tooltip';
  helpBtn.setAttribute('data-tooltip', '帮助 (?)');
  helpBtn.textContent = '?';
  helpBtn.onclick = () => showShortcutsModal();
  
  const statusPill = topbar.querySelector('.status-pill');
  if (statusPill) {
    statusPill.parentNode.insertBefore(helpBtn, statusPill);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', addHelpButton);
} else {
  addHelpButton();
}

window.KeyboardShortcuts = {
  showShortcutsModal,
  showHelpModal
};
