// 增强的实验对比功能

// 多实验并排对比
function renderEnhancedComparison(runs) {
  if (!runs || runs.length < 2) return;
  
  const container = document.getElementById('enhancedComparison');
  if (!container) return;
  
  // 选择基线（第一个或标记为 baseline 的）
  const baseline = runs[0];
  
  container.innerHTML = `
    <div class="comparison-header-bar">
      <h3>实验对比分析</h3>
      <div class="comparison-controls">
        <button class="secondary" onclick="exportComparison()">导出对比</button>
        <button class="secondary" onclick="toggleComparisonView()">切换视图</button>
      </div>
    </div>
    <div class="run-comparison-grid">
      ${runs.map((run, idx) => renderComparisonCard(run, baseline, idx === 0)).join('')}
    </div>
    <div class="comparison-details">
      ${renderDetailedDiff(runs)}
    </div>
  `;
}

function renderComparisonCard(run, baseline, isBaseline) {
  const summary = run.summary || {};
  const baselineSummary = baseline.summary || {};
  
  const accuracy = summary.accuracy || (summary.summary_json && summary.summary_json.accuracy_simple) || 0;
  const baselineAccuracy = baselineSummary.accuracy || (baselineSummary.summary_json && baselineSummary.summary_json.accuracy_simple) || 0;
  const delta = accuracy - baselineAccuracy;
  
  const metrics = [
    {
      name: 'Accuracy',
      value: (accuracy * 100).toFixed(1) + '%',
      delta: isBaseline ? null : delta,
      format: 'percent'
    },
    {
      name: 'Correct',
      value: summary.correct || 0,
      delta: isBaseline ? null : (summary.correct || 0) - (baselineSummary.correct || 0),
      format: 'number'
    },
    {
      name: 'Wrong',
      value: summary.wrong || 0,
      delta: isBaseline ? null : (summary.wrong || 0) - (baselineSummary.wrong || 0),
      format: 'number'
    },
    {
      name: 'Avg Time',
      value: summary.avg_time ? summary.avg_time.toFixed(1) + 's' : '-',
      delta: isBaseline ? null : (summary.avg_time || 0) - (baselineSummary.avg_time || 0),
      format: 'time'
    }
  ];
  
  return `
    <div class="run-comparison-card ${isBaseline ? 'baseline' : ''}">
      <div class="comparison-header">
        <div class="comparison-title">${escapeHtml(run.name || run.id)}</div>
        ${isBaseline ? '<span class="comparison-badge">Baseline</span>' : ''}
      </div>
      <div class="comparison-meta">
        <small>${escapeHtml(run.created_at || '')}</small>
        <small>${escapeHtml(run.kind || '')}</small>
      </div>
      <div class="comparison-metrics">
        ${metrics.map(m => renderMetricRow(m)).join('')}
      </div>
      ${renderCategoryBreakdown(summary.categories, baselineSummary.categories, isBaseline)}
    </div>
  `;
}

function renderMetricRow(metric) {
  let deltaHtml = '';
  if (metric.delta !== null && metric.delta !== undefined) {
    const deltaClass = metric.delta > 0 ? 'positive' : metric.delta < 0 ? 'negative' : '';
    const deltaSign = metric.delta > 0 ? '+' : '';
    let deltaValue = '';
    
    if (metric.format === 'percent') {
      deltaValue = `${deltaSign}${(metric.delta * 100).toFixed(1)}%`;
    } else if (metric.format === 'time') {
      deltaValue = `${deltaSign}${metric.delta.toFixed(1)}s`;
    } else {
      deltaValue = `${deltaSign}${metric.delta}`;
    }
    
    deltaHtml = `<span class="metric-delta ${deltaClass}">${deltaValue}</span>`;
  }
  
  return `
    <div class="comparison-metric">
      <span class="metric-name">${metric.name}</span>
      <div>
        <span class="metric-value">${metric.value}</span>
        ${deltaHtml}
      </div>
    </div>
  `;
}

function renderCategoryBreakdown(categories, baselineCategories, isBaseline) {
  if (!categories) return '';
  
  const cats = Object.entries(categories).sort(([a], [b]) => a.localeCompare(b));
  
  return `
    <div class="category-breakdown">
      <div class="breakdown-title">分类表现</div>
      ${cats.map(([cat, counts]) => {
        const correct = counts.CORRECT || 0;
        const wrong = counts.WRONG || 0;
        const total = correct + wrong;
        const accuracy = total > 0 ? correct / total : 0;
        
        let deltaHtml = '';
        if (!isBaseline && baselineCategories && baselineCategories[cat]) {
          const baseCounts = baselineCategories[cat];
          const baseCorrect = baseCounts.CORRECT || 0;
          const baseWrong = baseCounts.WRONG || 0;
          const baseTotal = baseCorrect + baseWrong;
          const baseAccuracy = baseTotal > 0 ? baseCorrect / baseTotal : 0;
          const delta = accuracy - baseAccuracy;
          
          if (delta !== 0) {
            const deltaClass = delta > 0 ? 'positive' : 'negative';
            deltaHtml = `<span class="metric-delta ${deltaClass}">${delta > 0 ? '+' : ''}${(delta * 100).toFixed(1)}%</span>`;
          }
        }
        
        return `
          <div class="breakdown-row">
            <span class="breakdown-label">C${cat}</span>
            <div class="breakdown-bar">
              <div class="breakdown-fill" style="width: ${accuracy * 100}%; background: ${accuracy > 0.7 ? 'var(--green)' : accuracy > 0.4 ? 'var(--amber)' : 'var(--red)'}"></div>
            </div>
            <span class="breakdown-value">${(accuracy * 100).toFixed(0)}%</span>
            ${deltaHtml}
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function renderDetailedDiff(runs) {
  if (runs.length < 2) return '';
  
  return `
    <div class="detailed-diff-panel">
      <h4>详细差异分析</h4>
      <div class="diff-tabs">
        <button class="diff-tab active" onclick="showDiffTab('improved')">改进 ✅</button>
        <button class="diff-tab" onclick="showDiffTab('regressed')">退化 ❌</button>
        <button class="diff-tab" onclick="showDiffTab('changed')">变化 🔄</button>
      </div>
      <div id="diffTabContent" class="diff-tab-content">
        <p style="color: var(--muted); text-align: center; padding: 40px;">
          点击上方标签查看详细差异
        </p>
      </div>
    </div>
  `;
}

// 导出对比报告
function exportComparison() {
  const runs = state.compare || [];
  if (runs.length < 2) {
    toast('需要至少 2 个实验才能导出对比');
    return;
  }
  
  const report = {
    timestamp: new Date().toISOString(),
    baseline: runs[0],
    candidates: runs.slice(1),
    summary: {
      total_runs: runs.length,
      best_accuracy: Math.max(...runs.map(r => r.score || 0)),
      worst_accuracy: Math.min(...runs.map(r => r.score || 0))
    }
  };
  
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `comparison_${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
  
  toast('对比报告已导出');
}

// 切换对比视图
let comparisonViewMode = 'grid';
function toggleComparisonView() {
  comparisonViewMode = comparisonViewMode === 'grid' ? 'table' : 'grid';
  const grid = document.querySelector('.run-comparison-grid');
  if (grid) {
    grid.classList.toggle('table-view', comparisonViewMode === 'table');
  }
  toast(`切换到${comparisonViewMode === 'grid' ? '卡片' : '表格'}视图`);
}

// 显示差异标签
function showDiffTab(type) {
  const tabs = document.querySelectorAll('.diff-tab');
  tabs.forEach(tab => tab.classList.remove('active'));
  event.target.classList.add('active');
  
  const content = document.getElementById('diffTabContent');
  if (!content) return;
  
  // 这里应该从后端获取实际的差异数据
  content.innerHTML = `
    <p style="color: var(--muted); text-align: center; padding: 40px;">
      ${type === 'improved' ? '改进的问题' : type === 'regressed' ? '退化的问题' : '变化的问题'}列表
      <br><small>需要后端 API 支持</small>
    </p>
  `;
}

// 初始化对比功能
function initComparisonEnhancements() {
  // 监听 runs 更新
  const originalRefreshRuns = window.refreshRuns;
  if (originalRefreshRuns) {
    window.refreshRuns = async function() {
      await originalRefreshRuns.call(this);
      renderEnhancedComparison(state.compare || []);
    };
  }
}

// 页面加载后初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initComparisonEnhancements);
} else {
  initComparisonEnhancements();
}

window.ComparisonEnhancements = {
  renderEnhancedComparison,
  exportComparison,
  toggleComparisonView
};
