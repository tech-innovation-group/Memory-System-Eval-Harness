// 成本追踪功能

// 渲染成本面板
function renderCostTracking(runData) {
  const container = document.getElementById('costTracking');
  if (!container) return;
  
  const summary = runData.summary || {};
  const summaryJson = summary.summary_json || {};
  
  // 计算成本指标
  const totalTokens = summaryJson.recall_tokens_est_total || 0;
  const avgTokens = summaryJson.recall_tokens_est_avg || 0;
  const totalQuestions = summary.rows || 0;
  const avgMemories = summaryJson.selected_memories_avg || 0;
  
  // 估算成本（假设每 1M tokens = $2）
  const estimatedCost = (totalTokens / 1000000) * 2;
  
  container.innerHTML = `
    <div class="cost-panel">
      <div class="cost-header">
        <h3>💰 成本追踪</h3>
        <button class="secondary small" onclick="exportCostReport()">导出成本报告</button>
      </div>
      <div class="cost-stats">
        <div class="cost-stat">
          <div class="cost-stat-value">$${estimatedCost.toFixed(2)}</div>
          <div class="cost-stat-label">预估成本</div>
        </div>
        <div class="cost-stat">
          <div class="cost-stat-value">${formatNumber(totalTokens)}</div>
          <div class="cost-stat-label">总 Tokens</div>
        </div>
        <div class="cost-stat">
          <div class="cost-stat-value">${formatNumber(avgTokens)}</div>
          <div class="cost-stat-label">平均 Tokens</div>
        </div>
        <div class="cost-stat">
          <div class="cost-stat-value">${avgMemories.toFixed(1)}</div>
          <div class="cost-stat-label">平均记忆数</div>
        </div>
      </div>
      <div class="cost-breakdown">
        <h4>成本明细</h4>
        ${renderCostBreakdown(runData)}
      </div>
    </div>
  `;
}

function renderCostBreakdown(runData) {
  const summary = runData.summary || {};
  const summaryJson = summary.summary_json || {};
  
  const items = [
    {
      label: '召回成本',
      value: `$${((summaryJson.recall_tokens_est_total || 0) / 1000000 * 2).toFixed(2)}`,
      tokens: summaryJson.recall_tokens_est_total || 0
    },
    {
      label: '推理成本',
      value: '$0.00',
      tokens: 0,
      note: '需要后端支持'
    },
    {
      label: 'Judge 成本',
      value: '$0.00',
      tokens: 0,
      note: '需要后端支持'
    },
    {
      label: '总问题数',
      value: summary.rows || 0,
      tokens: null
    },
    {
      label: '平均每题成本',
      value: summary.rows > 0 ? `$${(((summaryJson.recall_tokens_est_total || 0) / 1000000 * 2) / summary.rows).toFixed(4)}` : '-',
      tokens: null
    }
  ];
  
  return items.map(item => `
    <div class="cost-item">
      <div class="cost-item-label">
        ${item.label}
        ${item.note ? `<small style="color: var(--muted); margin-left: 8px;">(${item.note})</small>` : ''}
        ${item.tokens ? `<small style="color: var(--muted); margin-left: 8px;">${formatNumber(item.tokens)} tokens</small>` : ''}
      </div>
      <div class="cost-item-value">${item.value}</div>
    </div>
  `).join('');
}

function formatNumber(num) {
  if (num >= 1000000) {
    return (num / 1000000).toFixed(2) + 'M';
  } else if (num >= 1000) {
    return (num / 1000).toFixed(1) + 'K';
  }
  return num.toString();
}

// 导出成本报告
function exportCostReport() {
  const runs = state.runs || [];
  
  const report = {
    timestamp: new Date().toISOString(),
    summary: {
      total_runs: runs.length,
      total_cost: runs.reduce((sum, run) => {
        const tokens = (run.summary && run.summary.summary_json && run.summary.summary_json.recall_tokens_est_total) || 0;
        return sum + (tokens / 1000000) * 2;
      }, 0),
      total_tokens: runs.reduce((sum, run) => {
        return sum + ((run.summary && run.summary.summary_json && run.summary.summary_json.recall_tokens_est_total) || 0);
      }, 0)
    },
    runs: runs.map(run => {
      const summary = run.summary || {};
      const summaryJson = summary.summary_json || {};
      const tokens = summaryJson.recall_tokens_est_total || 0;
      return {
        name: run.name || run.id,
        created_at: run.created_at,
        tokens: tokens,
        cost: (tokens / 1000000) * 2,
        questions: summary.rows || 0,
        cost_per_question: summary.rows > 0 ? ((tokens / 1000000) * 2) / summary.rows : 0
      };
    })
  };
  
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `cost_report_${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
  
  toast('成本报告已导出');
}

// 添加成本追踪面板
function addCostTrackingPanel() {
  const resultsSection = document.getElementById('results');
  if (!resultsSection) return;
  
  const existingPanel = document.getElementById('costTrackingPanel');
  if (existingPanel) return;
  
  const panel = document.createElement('div');
  panel.id = 'costTrackingPanel';
  panel.innerHTML = '<div id="costTracking"></div>';
  
  const resultKpis = document.getElementById('resultKpis');
  if (resultKpis) {
    resultKpis.parentNode.insertBefore(panel, resultKpis.nextSibling);
  }
}

// 初始化成本追踪
function initCostTracking() {
  addCostTrackingPanel();
  
  // 监听结果更新
  const originalRefreshResults = window.refreshResults;
  if (originalRefreshResults) {
    window.refreshResults = async function() {
      await originalRefreshResults.call(this);
      
      // 渲染成本追踪
      if (state.selectedRun) {
        renderCostTracking(state.selectedRun);
      } else if (state.runs && state.runs.length > 0) {
        renderCostTracking(state.runs[0]);
      }
    };
  }
}

// 页面加载后初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCostTracking);
} else {
  initCostTracking();
}

window.CostTracking = {
  renderCostTracking,
  exportCostReport
};
