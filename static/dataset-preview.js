// 数据集预览和统计功能

// 渲染数据集预览
function renderDatasetPreview(dataset) {
  if (!dataset) return;
  
  const container = document.getElementById('datasetPreview');
  if (!container) return;
  
  container.innerHTML = `
    <div class="dataset-preview-panel">
      <div class="preview-header">
        <h3>📊 数据集预览</h3>
        <div class="preview-actions">
          <button class="secondary small" onclick="refreshDatasetStats()">刷新统计</button>
          <button class="secondary small" onclick="exportDatasetReport()">导出报告</button>
        </div>
      </div>
      
      <div class="dataset-info-grid">
        <div class="dataset-info-card">
          <div class="info-icon">📝</div>
          <div class="info-content">
            <div class="info-label">数据集名称</div>
            <div class="info-value">${escapeHtml(dataset.name || '-')}</div>
          </div>
        </div>
        
        <div class="dataset-info-card">
          <div class="info-icon">📦</div>
          <div class="info-content">
            <div class="info-label">总样本数</div>
            <div class="info-value">${dataset.total_samples || 0}</div>
          </div>
        </div>
        
        <div class="dataset-info-card">
          <div class="info-icon">❓</div>
          <div class="info-content">
            <div class="info-label">总问题数</div>
            <div class="info-value">${dataset.total_questions || 0}</div>
          </div>
        </div>
        
        <div class="dataset-info-card">
          <div class="info-icon">📂</div>
          <div class="info-content">
            <div class="info-label">类别数量</div>
            <div class="info-value">${dataset.categories ? Object.keys(dataset.categories).length : 0}</div>
          </div>
        </div>
      </div>
      
      ${renderCategoryDistribution(dataset.categories)}
      ${renderDifficultyAnalysis(dataset.difficulty)}
      ${renderSamplePreview(dataset.samples)}
    </div>
  `;
}

function renderCategoryDistribution(categories) {
  if (!categories || Object.keys(categories).length === 0) {
    return '<div class="empty-state"><p>暂无类别统计</p></div>';
  }
  
  const total = Object.values(categories).reduce((sum, count) => sum + count, 0);
  const sortedCategories = Object.entries(categories).sort((a, b) => b[1] - a[1]);
  
  return `
    <div class="category-distribution-section">
      <h4>类别分布</h4>
      <div class="category-bars">
        ${sortedCategories.map(([cat, count]) => {
          const percentage = (count / total) * 100;
          return `
            <div class="category-bar-row">
              <div class="category-bar-label">
                <span class="category-name">C${escapeHtml(cat)}</span>
                <span class="category-count">${count} 题 (${percentage.toFixed(1)}%)</span>
              </div>
              <div class="category-bar-track">
                <div class="category-bar-fill" style="width: ${percentage}%"></div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function renderDifficultyAnalysis(difficulty) {
  if (!difficulty) {
    return `
      <div class="difficulty-section">
        <h4>难度分析</h4>
        <div class="empty-state"><p>需要运行评测后才能分析难度</p></div>
      </div>
    `;
  }
  
  const levels = [
    { label: '简单', key: 'easy', color: 'var(--green)', icon: '😊' },
    { label: '中等', key: 'medium', color: 'var(--amber)', icon: '😐' },
    { label: '困难', key: 'hard', color: 'var(--red)', icon: '😰' }
  ];
  
  const total = levels.reduce((sum, level) => sum + (difficulty[level.key] || 0), 0);
  
  return `
    <div class="difficulty-section">
      <h4>难度分析</h4>
      <div class="difficulty-grid">
        ${levels.map(level => {
          const count = difficulty[level.key] || 0;
          const percentage = total > 0 ? (count / total) * 100 : 0;
          return `
            <div class="difficulty-card">
              <div class="difficulty-icon">${level.icon}</div>
              <div class="difficulty-label">${level.label}</div>
              <div class="difficulty-value" style="color: ${level.color}">${count}</div>
              <div class="difficulty-percentage">${percentage.toFixed(1)}%</div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function renderSamplePreview(samples) {
  if (!samples || samples.length === 0) {
    return '<div class="empty-state"><p>暂无样本预览</p></div>';
  }
  
  return `
    <div class="sample-preview-section">
      <h4>样本预览 (前 ${Math.min(samples.length, 5)} 个)</h4>
      <div class="sample-cards">
        ${samples.slice(0, 5).map((sample, idx) => `
          <div class="sample-card">
            <div class="sample-header">
              <span class="sample-id">#${idx + 1} ${escapeHtml(sample.id || '-')}</span>
              <span class="sample-category">C${escapeHtml(sample.category || '-')}</span>
            </div>
            <div class="sample-question">
              ${escapeHtml((sample.question || '').slice(0, 150))}${(sample.question || '').length > 150 ? '...' : ''}
            </div>
            <div class="sample-meta">
              <span>类型: ${escapeHtml(sample.type || '-')}</span>
              ${sample.difficulty ? `<span>难度: ${escapeHtml(sample.difficulty)}</span>` : ''}
            </div>
          </div>
        `).join('')}
      </div>
      ${samples.length > 5 ? `<p style="text-align: center; color: var(--muted); margin-top: 16px;">还有 ${samples.length - 5} 个样本...</p>` : ''}
    </div>
  `;
}

// 刷新数据集统计
async function refreshDatasetStats() {
  const dataPath = $("data").value.trim();
  if (!dataPath) {
    toast('请先选择数据集');
    return;
  }
  
  try {
    toast('正在加载数据集统计...');
    const stats = await api(`/api/dataset/stats?path=${encodeURIComponent(dataPath)}`);
    state.currentDatasetStats = stats;
    renderDatasetPreview(stats);
    toast('数据集统计已更新');
  } catch (e) {
    toast('加载数据集统计失败: ' + e.message);
  }
}

// 导出数据集报告
function exportDatasetReport() {
  const stats = state.currentDatasetStats;
  if (!stats) {
    toast('暂无数据集统计');
    return;
  }
  
  const report = {
    timestamp: new Date().toISOString(),
    dataset: stats.name,
    summary: {
      total_samples: stats.total_samples,
      total_questions: stats.total_questions,
      categories: Object.keys(stats.categories || {}).length
    },
    categories: stats.categories,
    difficulty: stats.difficulty,
    samples: stats.samples
  };
  
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `dataset_report_${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
  
  toast('数据集报告已导出');
}

// 添加数据集预览面板
function addDatasetPreviewPanel() {
  const runSection = document.getElementById('run');
  if (!runSection) return;
  
  const existingPanel = document.getElementById('datasetPreviewPanel');
  if (existingPanel) return;
  
  const panel = document.createElement('section');
  panel.id = 'datasetPreviewPanel';
  panel.className = 'panel';
  panel.style.marginTop = '24px';
  panel.innerHTML = '<div id="datasetPreview"></div>';
  
  const formGrid = document.querySelector('.form-grid');
  if (formGrid) {
    formGrid.parentNode.insertBefore(panel, formGrid.nextSibling);
  }
}

// 监听数据集选择变化
function watchDatasetSelection() {
  const dataInput = document.getElementById('data');
  if (!dataInput) return;
  
  dataInput.addEventListener('change', () => {
    const dataPath = dataInput.value.trim();
    if (dataPath) {
      // 自动加载数据集统计
      setTimeout(refreshDatasetStats, 500);
    }
  });
}

// 初始化数据集预览
function initDatasetPreview() {
  addDatasetPreviewPanel();
  watchDatasetSelection();
}

// 页面加载后初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDatasetPreview);
} else {
  initDatasetPreview();
}

window.DatasetPreview = {
  renderDatasetPreview,
  refreshDatasetStats,
  exportDatasetReport
};
