// UI 增强功能

// 添加实时进度监控
function enhanceTaskMonitoring() {
  const taskList = document.getElementById('taskList');
  if (!taskList) return;
  
  // 为每个运行中的任务添加进度条
  const runningTasks = state.tasks.filter(t => t.status === 'running');
  
  runningTasks.forEach(task => {
    if (task.progress) {
      const progressId = `progress-${task.id}`;
      let progressContainer = document.getElementById(progressId);
      
      if (!progressContainer) {
        const taskEl = document.querySelector(`[data-task="${task.id}"]`);
        if (taskEl) {
          progressContainer = document.createElement('div');
          progressContainer.id = progressId;
          progressContainer.style.marginTop = '12px';
          taskEl.appendChild(progressContainer);
        }
      }
      
      if (progressContainer && window.Charts) {
        window.Charts.renderProgressBar(progressId, task.progress);
      }
    }
  });
}

// 添加对比图表
function enhanceRunComparison() {
  const compareRuns = state.compare || [];
  if (compareRuns.length < 2) return;
  
  const comparisonData = compareRuns.map(run => ({
    label: run.name || run.id,
    value: run.score || 0,
    delta: run.delta_vs_first || 0
  }));
  
  const comparisonContainer = document.getElementById('runComparisonChart');
  if (comparisonContainer && window.Charts) {
    window.Charts.renderComparisonChart('runComparisonChart', comparisonData);
  }
}

// 添加分类分布图
function enhanceCategoryDistribution() {
  const summary = state.lastResultSummary;
  if (!summary || !summary.categories) return;
  
  const categories = Object.entries(summary.categories).map(([cat, counts]) => {
    const correct = counts.CORRECT || 0;
    const wrong = counts.WRONG || 0;
    const total = correct + wrong;
    return {
      label: `C${cat}`,
      value: total,
      color: total > 0 ? (correct / total > 0.7 ? 'var(--green)' : correct / total > 0.4 ? 'var(--amber)' : 'var(--red)') : 'var(--muted)'
    };
  });
  
  const distContainer = document.getElementById('categoryDistribution');
  if (distContainer && window.Charts && categories.length > 0) {
    window.Charts.renderDistributionChart('categoryDistribution', categories);
  }
}

// 添加快速操作面板
function addQuickActionsPanel() {
  const overview = document.getElementById('overview');
  if (!overview) return;
  
  const existingPanel = document.getElementById('quickActionsPanel');
  if (existingPanel) return;
  
  const panel = document.createElement('section');
  panel.id = 'quickActionsPanel';
  panel.className = 'panel';
  panel.innerHTML = `
    <h3 style="margin-bottom: 16px;">快速操作</h3>
    <div class="quick-actions">
      <div class="quick-action" onclick="document.getElementById('smokeRun').click()">
        <div class="quick-action-icon">⚡️</div>
        <div class="quick-action-label">快速测试</div>
        <div class="quick-action-desc">5 题 Smoke</div>
      </div>
      <div class="quick-action" onclick="document.getElementById('probeServer').click()">
        <div class="quick-action-icon">🔌</div>
        <div class="quick-action-label">测试连接</div>
        <div class="quick-action-desc">验证 OV 可达</div>
      </div>
      <div class="quick-action" onclick="document.getElementById('refreshResults').click()">
        <div class="quick-action-icon">📊</div>
        <div class="quick-action-label">刷新结果</div>
        <div class="quick-action-desc">更新统计</div>
      </div>
      <div class="quick-action" onclick="document.getElementById('refreshRuns').click()">
        <div class="quick-action-icon">🔄</div>
        <div class="quick-action-label">刷新 Runs</div>
        <div class="quick-action-desc">加载历史</div>
      </div>
    </div>
  `;
  
  overview.parentNode.insertBefore(panel, overview.nextSibling);
}

// 添加实时监控面板
function addLiveMonitorPanel() {
  const logsSection = document.getElementById('logs');
  if (!logsSection) return;
  
  const existingMonitor = document.getElementById('liveMonitor');
  if (existingMonitor) {
    updateLiveMonitor();
    return;
  }
  
  const monitor = document.createElement('div');
  monitor.id = 'liveMonitor';
  monitor.className = 'live-monitor';
  monitor.innerHTML = `
    <div class="monitor-header">
      <div class="monitor-title">实时监控</div>
      <div class="monitor-status">
        <span class="status-dot"></span>
        <span id="monitorStatusText">运行中</span>
      </div>
    </div>
    <div id="liveProgress"></div>
    <div class="monitor-stats">
      <div class="monitor-stat">
        <div class="monitor-stat-value" id="statCompleted">0</div>
        <div class="monitor-stat-label">已完成</div>
      </div>
      <div class="monitor-stat">
        <div class="monitor-stat-value" id="statRunning">0</div>
        <div class="monitor-stat-label">运行中</div>
      </div>
      <div class="monitor-stat">
        <div class="monitor-stat-value" id="statFailed">0</div>
        <div class="monitor-stat-label">失败</div>
      </div>
      <div class="monitor-stat">
        <div class="monitor-stat-value" id="statTotal">0</div>
        <div class="monitor-stat-label">总任务</div>
      </div>
    </div>
  `;
  
  logsSection.insertBefore(monitor, logsSection.firstChild);
  updateLiveMonitor();
}

function updateLiveMonitor() {
  const tasks = state.tasks || [];
  const completed = tasks.filter(t => t.status === 'succeeded').length;
  const running = tasks.filter(t => t.status === 'running').length;
  const failed = tasks.filter(t => t.status === 'failed').length;
  const total = tasks.length;
  
  const statCompleted = document.getElementById('statCompleted');
  const statRunning = document.getElementById('statRunning');
  const statFailed = document.getElementById('statFailed');
  const statTotal = document.getElementById('statTotal');
  const monitorStatusText = document.getElementById('monitorStatusText');
  
  if (statCompleted) statCompleted.textContent = completed;
  if (statRunning) statRunning.textContent = running;
  if (statFailed) statFailed.textContent = failed;
  if (statTotal) statTotal.textContent = total;
  if (monitorStatusText) {
    monitorStatusText.textContent = running > 0 ? '运行中' : '空闲';
  }
  
  // 如果有运行中的任务，显示进度
  const runningTask = tasks.find(t => t.status === 'running');
  if (runningTask && runningTask.progress) {
    const progressContainer = document.getElementById('liveProgress');
    if (progressContainer && window.Charts) {
      window.Charts.renderProgressBar('liveProgress', runningTask.progress);
    }
  }
}

// 添加对比图表容器
function addComparisonChartContainer() {
  const runsSection = document.getElementById('runs');
  if (!runsSection) return;
  
  const existingChart = document.getElementById('runComparisonPanel');
  if (existingChart) return;
  
  const compareStrip = document.getElementById('compareStrip');
  if (!compareStrip) return;
  
  const chartPanel = document.createElement('div');
  chartPanel.id = 'runComparisonPanel';
  chartPanel.className = 'panel';
  chartPanel.style.marginTop = '20px';
  chartPanel.innerHTML = `
    <h3 style="margin-bottom: 16px;">实验对比</h3>
    <div id="runComparisonChart" style="min-height: 200px;"></div>
  `;
  
  compareStrip.parentNode.insertBefore(chartPanel, compareStrip.nextSibling);
}

// 初始化所有增强功能
function initEnhancements() {
  addQuickActionsPanel();
  addLiveMonitorPanel();
  addComparisonChartContainer();
  
  // 定期更新
  setInterval(() => {
    enhanceTaskMonitoring();
    updateLiveMonitor();
    enhanceRunComparison();
    enhanceCategoryDistribution();
  }, 2000);
}

// 页面加载完成后初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initEnhancements);
} else {
  initEnhancements();
}

// 导出函数供外部调用
window.UIEnhancements = {
  enhanceTaskMonitoring,
  enhanceRunComparison,
  enhanceCategoryDistribution,
  updateLiveMonitor
};
