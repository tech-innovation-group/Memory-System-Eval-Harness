// 简单的纯 CSS 图表库（无依赖）

// 趋势线图
function renderTrendChart(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container || !data || data.length === 0) return;
  
  const max = Math.max(...data.map(d => d.value));
  const min = Math.min(...data.map(d => d.value));
  const range = max - min || 1;
  
  const points = data.map((d, i) => {
    const x = (i / (data.length - 1)) * 100;
    const y = 100 - ((d.value - min) / range) * 100;
    return `${x},${y}`;
  }).join(' ');
  
  container.innerHTML = `
    <svg viewBox="0 0 100 100" class="trend-chart">
      <polyline points="${points}" fill="none" stroke="var(--accent)" stroke-width="2" />
      ${data.map((d, i) => {
        const x = (i / (data.length - 1)) * 100;
        const y = 100 - ((d.value - min) / range) * 100;
        return `<circle cx="${x}" cy="${y}" r="2" fill="var(--accent)" />`;
      }).join('')}
    </svg>
    <div class="chart-labels">
      <span>${data[0].label}</span>
      <span>${data[data.length - 1].label}</span>
    </div>
  `;
}

// 对比柱状图
function renderComparisonChart(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container || !data || data.length === 0) return;
  
  const max = Math.max(...data.map(d => d.value));
  
  container.innerHTML = data.map(d => {
    const height = (d.value / max) * 100;
    const color = d.delta > 0 ? 'var(--green)' : d.delta < 0 ? 'var(--red)' : 'var(--accent)';
    return `
      <div class="comparison-bar">
        <div class="bar-label">${d.label}</div>
        <div class="bar-container">
          <div class="bar-fill" style="height: ${height}%; background: ${color}"></div>
        </div>
        <div class="bar-value">${(d.value * 100).toFixed(1)}%</div>
        ${d.delta ? `<div class="bar-delta" style="color: ${color}">${d.delta > 0 ? '+' : ''}${(d.delta * 100).toFixed(1)}%</div>` : ''}
      </div>
    `;
  }).join('');
}

// 分布饼图
function renderDistributionChart(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container || !data || data.length === 0) return;
  
  const total = data.reduce((sum, d) => sum + d.value, 0);
  let currentAngle = 0;
  
  const segments = data.map(d => {
    const percentage = d.value / total;
    const angle = percentage * 360;
    const largeArc = angle > 180 ? 1 : 0;
    
    const startX = 50 + 40 * Math.cos((currentAngle - 90) * Math.PI / 180);
    const startY = 50 + 40 * Math.sin((currentAngle - 90) * Math.PI / 180);
    const endX = 50 + 40 * Math.cos((currentAngle + angle - 90) * Math.PI / 180);
    const endY = 50 + 40 * Math.sin((currentAngle + angle - 90) * Math.PI / 180);
    
    currentAngle += angle;
    
    return {
      path: `M 50 50 L ${startX} ${startY} A 40 40 0 ${largeArc} 1 ${endX} ${endY} Z`,
      color: d.color || 'var(--accent)',
      label: d.label,
      value: d.value,
      percentage: (percentage * 100).toFixed(1)
    };
  });
  
  container.innerHTML = `
    <svg viewBox="0 0 100 100" class="pie-chart">
      ${segments.map(s => `<path d="${s.path}" fill="${s.color}" />`).join('')}
    </svg>
    <div class="pie-legend">
      ${segments.map(s => `
        <div class="legend-item">
          <span class="legend-color" style="background: ${s.color}"></span>
          <span class="legend-label">${s.label}</span>
          <span class="legend-value">${s.value} (${s.percentage}%)</span>
        </div>
      `).join('')}
    </div>
  `;
}

// 实时进度条
function renderProgressBar(containerId, progress) {
  const container = document.getElementById(containerId);
  if (!container) return;
  
  const { current, total, status, eta, throughput } = progress;
  const percentage = total > 0 ? (current / total) * 100 : 0;
  
  container.innerHTML = `
    <div class="progress-bar">
      <div class="progress-header">
        <span class="progress-label">${current} / ${total}</span>
        <span class="progress-percentage">${percentage.toFixed(1)}%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" style="width: ${percentage}%"></div>
      </div>
      <div class="progress-meta">
        ${status ? `<span class="progress-status">${status}</span>` : ''}
        ${eta ? `<span class="progress-eta">ETA: ${eta}</span>` : ''}
        ${throughput ? `<span class="progress-throughput">${throughput} q/s</span>` : ''}
      </div>
    </div>
  `;
}

// 导出函数
window.Charts = {
  renderTrendChart,
  renderComparisonChart,
  renderDistributionChart,
  renderProgressBar
};
