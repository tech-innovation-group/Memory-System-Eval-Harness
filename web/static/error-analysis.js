// 错题深度分析功能

// 错题聚类分析
function renderErrorClustering(analysis) {
  if (!analysis || !analysis.failure_clusters) return;
  
  const clusters = analysis.failure_clusters.clusters || [];
  const container = document.getElementById('errorClustering');
  if (!container) return;
  
  container.innerHTML = `
    <div class="analysis-header">
      <h3>错题聚类分析</h3>
      <div class="analysis-meta">
        <span>${clusters.length} 个失败模式</span>
        <span>${analysis.failure_clusters.total_failures || 0} 个错题</span>
      </div>
    </div>
    <div class="cluster-grid">
      ${clusters.map((cluster, idx) => renderClusterCard(cluster, idx)).join('')}
    </div>
  `;
}

function renderClusterCard(cluster, idx) {
  const examples = cluster.examples || [];
  const topSamples = cluster.top_samples || [];
  const severity = cluster.count > 10 ? 'high' : cluster.count > 5 ? 'medium' : 'low';
  
  return `
    <div class="cluster-card severity-${severity}">
      <div class="cluster-header">
        <div class="cluster-badge">Cluster ${idx + 1}</div>
        <div class="cluster-count">${cluster.count} 题</div>
      </div>
      <div class="cluster-label">${escapeHtml(cluster.label || '未分类')}</div>
      <div class="cluster-examples">
        ${examples.slice(0, 2).map(ex => `
          <div class="cluster-example">
            <div class="example-question">${escapeHtml((ex.question || '').slice(0, 100))}...</div>
            <div class="example-meta">
              <span>样本: ${escapeHtml(ex.sample_id || '-')}</span>
              <span>类别: C${escapeHtml(ex.category || '-')}</span>
            </div>
          </div>
        `).join('')}
      </div>
      ${topSamples.length > 0 ? `
        <div class="cluster-samples">
          <strong>高频样本:</strong>
          ${topSamples.slice(0, 3).map(([id, count]) => 
            `<span class="sample-chip">${escapeHtml(id)} (${count})</span>`
          ).join('')}
        </div>
      ` : ''}
      <button class="secondary small" onclick="viewClusterDetails(${idx})">查看详情</button>
    </div>
  `;
}

// 失败模式分析
function renderFailureModes(analysis) {
  if (!analysis || !analysis.modes) return;
  
  const modes = Object.entries(analysis.modes).sort((a, b) => b[1] - a[1]);
  const container = document.getElementById('failureModes');
  if (!container) return;
  
  const total = modes.reduce((sum, [_, count]) => sum + count, 0);
  
  container.innerHTML = `
    <div class="analysis-header">
      <h3>失败模式分布</h3>
      <div class="analysis-meta">
        <span>${modes.length} 种模式</span>
        <span>${total} 个错题</span>
      </div>
    </div>
    <div class="failure-modes-chart">
      ${modes.map(([mode, count]) => {
        const percentage = (count / total) * 100;
        const examples = (analysis.examples && analysis.examples[mode]) || [];
        return `
          <div class="failure-mode-row">
            <div class="mode-info">
              <div class="mode-name">${escapeHtml(mode)}</div>
              <div class="mode-count">${count} 题 (${percentage.toFixed(1)}%)</div>
            </div>
            <div class="mode-bar">
              <div class="mode-fill" style="width: ${percentage}%"></div>
            </div>
            ${examples.length > 0 ? `
              <div class="mode-example">
                <small>${escapeHtml((examples[0].question || '').slice(0, 80))}...</small>
              </div>
            ` : ''}
          </div>
        `;
      }).join('')}
    </div>
  `;
}

// 根因分析
function renderRootCauseAnalysis(wrongRows) {
  if (!wrongRows || wrongRows.length === 0) return;
  
  const container = document.getElementById('rootCauseAnalysis');
  if (!container) return;
  
  // 分析根因
  const rootCauses = analyzeRootCauses(wrongRows);
  
  container.innerHTML = `
    <div class="analysis-header">
      <h3>根因分析</h3>
      <div class="analysis-meta">
        <span>${rootCauses.length} 个潜在根因</span>
      </div>
    </div>
    <div class="root-cause-list">
      ${rootCauses.map(cause => `
        <div class="root-cause-card">
          <div class="cause-header">
            <div class="cause-icon">${getCauseIcon(cause.type)}</div>
            <div class="cause-title">${escapeHtml(cause.title)}</div>
            <div class="cause-impact">${cause.count} 题</div>
          </div>
          <div class="cause-description">${escapeHtml(cause.description)}</div>
          <div class="cause-suggestions">
            <strong>建议:</strong>
            <ul>
              ${cause.suggestions.map(s => `<li>${escapeHtml(s)}</li>`).join('')}
            </ul>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

function analyzeRootCauses(wrongRows) {
  const causes = [];
  
  // 分析记忆召回问题
  const lowRecallRows = wrongRows.filter(r => 
    (r.selected_memories || 0) < 3 || (r.recall_wrapper_tokens_est || 0) < 100
  );
  if (lowRecallRows.length > 0) {
    causes.push({
      type: 'recall',
      title: '记忆召回不足',
      count: lowRecallRows.length,
      description: '部分问题的记忆召回数量或质量不足，导致缺少必要上下文',
      suggestions: [
        '检查记忆索引是否完整',
        '优化召回策略和相似度阈值',
        '增加记忆库的覆盖范围'
      ]
    });
  }
  
  // 分析超时问题
  const timeoutRows = wrongRows.filter(r => (r.time_cost || 0) > 150);
  if (timeoutRows.length > 0) {
    causes.push({
      type: 'timeout',
      title: '响应超时',
      count: timeoutRows.length,
      description: '部分问题处理时间过长，可能触发超时或影响质量',
      suggestions: [
        '优化推理流程，减少不必要步骤',
        '增加超时时间限制',
        '检查是否有死循环或重复计算'
      ]
    });
  }
  
  // 分析类别集中问题
  const categoryCount = {};
  wrongRows.forEach(r => {
    const cat = r.category || 'unknown';
    categoryCount[cat] = (categoryCount[cat] || 0) + 1;
  });
  const dominantCategory = Object.entries(categoryCount).sort((a, b) => b[1] - a[1])[0];
  if (dominantCategory && dominantCategory[1] > wrongRows.length * 0.3) {
    causes.push({
      type: 'category',
      title: `类别 C${dominantCategory[0]} 表现差`,
      count: dominantCategory[1],
      description: `超过 30% 的错题集中在类别 C${dominantCategory[0]}，说明该类别存在系统性问题`,
      suggestions: [
        `针对 C${dominantCategory[0]} 类别增加训练数据`,
        '分析该类别的特殊性，调整策略',
        '检查该类别的记忆覆盖情况'
      ]
    });
  }
  
  // 分析样本集中问题
  const sampleCount = {};
  wrongRows.forEach(r => {
    const sample = r.sample_id || 'unknown';
    sampleCount[sample] = (sampleCount[sample] || 0) + 1;
  });
  const problematicSamples = Object.entries(sampleCount).filter(([_, count]) => count > 3);
  if (problematicSamples.length > 0) {
    causes.push({
      type: 'sample',
      title: '特定样本表现差',
      count: problematicSamples.reduce((sum, [_, count]) => sum + count, 0),
      description: `${problematicSamples.length} 个样本的错误率异常高，可能存在数据质量问题`,
      suggestions: [
        '检查这些样本的数据质量',
        '分析样本的共同特征',
        '考虑为这些样本增加专门的处理逻辑'
      ]
    });
  }
  
  return causes;
}

function getCauseIcon(type) {
  const icons = {
    recall: '🧠',
    timeout: '⏱️',
    category: '📊',
    sample: '📝',
    default: '⚠️'
  };
  return icons[type] || icons.default;
}

// 错题详情查看
function viewClusterDetails(clusterIdx) {
  // 这里应该打开一个模态框显示详细信息
  toast(`查看 Cluster ${clusterIdx + 1} 详情`);
  
  // TODO: 实现详细视图
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="modal-content" style="max-width: 800px;">
      <div class="modal-header">
        <h2>Cluster ${clusterIdx + 1} 详情</h2>
        <button class="icon-btn" onclick="this.closest('.modal-overlay').remove()">✕</button>
      </div>
      <div class="modal-body">
        <p>详细的错题列表和分析...</p>
        <p style="color: var(--muted);">需要后端 API 支持</p>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });
}

// 导出错题分析报告
function exportErrorAnalysis() {
  const analysis = state.errorAnalysis;
  if (!analysis) {
    toast('暂无错题分析数据');
    return;
  }
  
  const report = {
    timestamp: new Date().toISOString(),
    summary: {
      total_errors: analysis.total_errors || 0,
      failure_modes: Object.keys(analysis.modes || {}).length,
      clusters: (analysis.failure_clusters && analysis.failure_clusters.clusters || []).length
    },
    failure_modes: analysis.modes,
    clusters: analysis.failure_clusters,
    root_causes: analyzeRootCauses(analysis.wrong_rows || [])
  };
  
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `error_analysis_${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
  
  toast('错题分析报告已导出');
}

// 初始化错题分析
function initErrorAnalysis() {
  // 监听结果更新
  const originalRefreshResults = window.refreshResults;
  if (originalRefreshResults) {
    window.refreshResults = async function() {
      await originalRefreshResults.call(this);
      
      // 获取错题数据并分析
      const input = $("judgeInput").value.trim() || state.lastResult;
      if (input) {
        try {
          const analysis = await api(`/api/analyze?input=${encodeURIComponent(input)}`);
          state.errorAnalysis = analysis;
          
          renderErrorClustering(analysis);
          renderFailureModes(analysis);
          renderRootCauseAnalysis(analysis.wrong_rows || []);
        } catch (e) {
          console.error('Error analysis failed:', e);
        }
      }
    };
  }
}

// 添加错题分析面板到结果页面
function addErrorAnalysisPanels() {
  const resultsSection = document.getElementById('results');
  if (!resultsSection) return;
  
  const existingPanel = document.getElementById('errorAnalysisPanel');
  if (existingPanel) return;
  
  const panel = document.createElement('section');
  panel.id = 'errorAnalysisPanel';
  panel.className = 'panel';
  panel.style.marginTop = '24px';
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <h2>错题深度分析</h2>
        <p>基于失败模式、聚类和根因的智能分析</p>
      </div>
      <button class="secondary" onclick="exportErrorAnalysis()">导出分析</button>
    </div>
    <div id="errorClustering" class="analysis-section"></div>
    <div id="failureModes" class="analysis-section"></div>
    <div id="rootCauseAnalysis" class="analysis-section"></div>
  `;
  
  const reviewDeck = document.getElementById('reviewDeck');
  if (reviewDeck) {
    reviewDeck.parentNode.insertBefore(panel, reviewDeck);
  }
}

// 页面加载后初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initErrorAnalysis();
    addErrorAnalysisPanels();
  });
} else {
  initErrorAnalysis();
  addErrorAnalysisPanels();
}

window.ErrorAnalysis = {
  renderErrorClustering,
  renderFailureModes,
  renderRootCauseAnalysis,
  exportErrorAnalysis,
  viewClusterDetails
};
