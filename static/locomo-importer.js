// LoCoMo 数据集自动导入功能

const LoCoMoImporter = {
  // 数据集配置
  datasets: {
    locomo: {
      name: 'LoCoMo',
      path: '/path/to/locomo.json',
      description: 'Long Context Memory benchmark',
      format: 'multi-turn'
    },
    longmemeval: {
      name: 'LongMemEval',
      path: '/path/to/longmemeval.json',
      description: 'Long-term memory evaluation',
      format: 'single-turn'
    },
    evolvingevents: {
      name: 'EvolvingEvents',
      path: '/path/to/evolvingevents.json',
      description: 'Dynamic event tracking',
      format: 'temporal'
    }
  },
  
  // 自动导入数据集
  async autoImport(datasetName) {
    const dataset = this.datasets[datasetName];
    if (!dataset) {
      throw new Error(`Unknown dataset: ${datasetName}`);
    }
    
    toast(`正在导入 ${dataset.name}...`);
    
    try {
      // 1. 读取数据集文件
      const data = await this.loadDataset(dataset.path);
      
      // 2. 解析数据集格式
      const parsed = this.parseDataset(data, dataset.format);
      
      // 3. 构建 Context Pack
      const contextPacks = this.buildContextPacks(parsed);
      
      // 4. 验证数据完整性
      const validation = this.validateDataset(contextPacks);
      
      if (!validation.valid) {
        throw new Error(`数据集验证失败: ${validation.errors.join(', ')}`);
      }
      
      toast(`${dataset.name} 导入成功！共 ${contextPacks.length} 个样本`);
      
      return {
        dataset: datasetName,
        samples: contextPacks,
        stats: this.computeStats(contextPacks)
      };
    } catch (e) {
      toast(`导入失败: ${e.message}`);
      throw e;
    }
  },
  
  // 加载数据集文件
  async loadDataset(path) {
    try {
      const response = await fetch(`/api/dataset/load?path=${encodeURIComponent(path)}`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return await response.json();
    } catch (e) {
      throw new Error(`加载数据集失败: ${e.message}`);
    }
  },
  
  // 解析数据集格式
  parseDataset(data, format) {
    switch (format) {
      case 'multi-turn':
        return this.parseMultiTurn(data);
      case 'single-turn':
        return this.parseSingleTurn(data);
      case 'temporal':
        return this.parseTemporal(data);
      default:
        throw new Error(`Unknown format: ${format}`);
    }
  },
  
  // 解析多轮对话格式
  parseMultiTurn(data) {
    const samples = [];
    
    for (const item of data) {
      const sample = {
        id: item.id || `sample_${samples.length}`,
        category: item.category || 'unknown',
        turns: item.turns || [],
        question: item.question,
        answer: item.answer,
        context: item.context || [],
        metadata: item.metadata || {}
      };
      
      samples.push(sample);
    }
    
    return samples;
  },
  
  // 解析单轮格式
  parseSingleTurn(data) {
    const samples = [];
    
    for (const item of data) {
      const sample = {
        id: item.id || `sample_${samples.length}`,
        category: item.category || 'unknown',
        turns: [{
          role: 'user',
          content: item.question
        }],
        question: item.question,
        answer: item.answer,
        context: item.context || [],
        metadata: item.metadata || {}
      };
      
      samples.push(sample);
    }
    
    return samples;
  },
  
  // 解析时序事件格式
  parseTemporal(data) {
    const samples = [];
    
    for (const item of data) {
      const sample = {
        id: item.id || `sample_${samples.length}`,
        category: item.category || 'unknown',
        turns: item.events || [],
        question: item.question,
        answer: item.answer,
        context: item.context || [],
        timeline: item.timeline || [],
        metadata: item.metadata || {}
      };
      
      samples.push(sample);
    }
    
    return samples;
  },
  
  // 构建 Context Pack
  buildContextPacks(samples) {
    return samples.map(sample => {
      const contextPack = {
        id: sample.id,
        category: sample.category,
        question: sample.question,
        expected_answer: sample.answer,
        
        // 参考对话
        reference_conversations: sample.turns.map(turn => ({
          role: turn.role || 'user',
          content: turn.content,
          timestamp: turn.timestamp
        })),
        
        // 上下文工程
        context_engineering: {
          memories: sample.context.filter(c => c.type === 'memory'),
          events: sample.context.filter(c => c.type === 'event'),
          facts: sample.context.filter(c => c.type === 'fact')
        },
        
        // 时间线（如果有）
        timeline: sample.timeline || [],
        
        // 元数据
        metadata: sample.metadata
      };
      
      return contextPack;
    });
  },
  
  // 验证数据集
  validateDataset(contextPacks) {
    const errors = [];
    
    for (const pack of contextPacks) {
      if (!pack.id) {
        errors.push(`样本缺少 ID`);
      }
      if (!pack.question) {
        errors.push(`样本 ${pack.id} 缺少问题`);
      }
      if (!pack.expected_answer) {
        errors.push(`样本 ${pack.id} 缺少预期答案`);
      }
    }
    
    return {
      valid: errors.length === 0,
      errors
    };
  },
  
  // 计算统计信息
  computeStats(contextPacks) {
    const stats = {
      total_samples: contextPacks.length,
      categories: {},
      avg_turns: 0,
      avg_context_length: 0,
      total_memories: 0,
      total_events: 0
    };
    
    let totalTurns = 0;
    let totalContextLength = 0;
    
    for (const pack of contextPacks) {
      // 类别统计
      const cat = pack.category || 'unknown';
      stats.categories[cat] = (stats.categories[cat] || 0) + 1;
      
      // 轮次统计
      totalTurns += pack.reference_conversations.length;
      
      // 上下文长度
      const contextLength = pack.reference_conversations.reduce(
        (sum, conv) => sum + conv.content.length,
        0
      );
      totalContextLength += contextLength;
      
      // 记忆和事件统计
      stats.total_memories += pack.context_engineering.memories.length;
      stats.total_events += pack.context_engineering.events.length;
    }
    
    stats.avg_turns = totalTurns / contextPacks.length;
    stats.avg_context_length = totalContextLength / contextPacks.length;
    
    return stats;
  },
  
  // 渲染导入面板
  renderImportPanel() {
    const container = document.getElementById('locomoImportPanel');
    if (!container) return;
    
    const datasets = Object.entries(this.datasets);
    
    container.innerHTML = `
      <div class="import-panel">
        <div class="import-header">
          <h3>📥 LoCoMo 数据集自动导入</h3>
          <button class="secondary small" onclick="LoCoMoImporter.refreshDatasets()">刷新</button>
        </div>
        
        <div class="dataset-import-grid">
          ${datasets.map(([key, dataset]) => `
            <div class="import-card">
              <div class="import-card-header">
                <div class="import-card-icon">📊</div>
                <div class="import-card-title">${dataset.name}</div>
              </div>
              <div class="import-card-desc">${dataset.description}</div>
              <div class="import-card-path">
                <input 
                  type="text" 
                  id="dataset-path-${key}" 
                  value="${dataset.path}"
                  placeholder="数据集路径"
                  style="width: 100%; padding: 8px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px;"
                />
              </div>
              <div class="import-card-actions">
                <button class="primary small" onclick="LoCoMoImporter.importDataset('${key}')">
                  导入
                </button>
                <button class="secondary small" onclick="LoCoMoImporter.previewDataset('${key}')">
                  预览
                </button>
              </div>
            </div>
          `).join('')}
        </div>
        
        <div id="importProgress" style="margin-top: 20px;"></div>
        <div id="importStats" style="margin-top: 20px;"></div>
      </div>
    `;
  },
  
  // 导入数据集
  async importDataset(datasetKey) {
    const pathInput = document.getElementById(`dataset-path-${datasetKey}`);
    if (pathInput) {
      this.datasets[datasetKey].path = pathInput.value;
    }
    
    try {
      const result = await this.autoImport(datasetKey);
      this.renderImportStats(result);
      
      // 更新全局状态
      state.currentDataset = result;
      
      return result;
    } catch (e) {
      console.error('Import failed:', e);
    }
  },
  
  // 预览数据集
  async previewDataset(datasetKey) {
    const dataset = this.datasets[datasetKey];
    toast(`预览 ${dataset.name}...`);
    
    // TODO: 实现预览功能
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.innerHTML = `
      <div class="modal-content" style="max-width: 800px;">
        <div class="modal-header">
          <h2>${dataset.name} 预览</h2>
          <button class="icon-btn" onclick="this.closest('.modal-overlay').remove()">✕</button>
        </div>
        <div class="modal-body">
          <p><strong>路径:</strong> ${dataset.path}</p>
          <p><strong>格式:</strong> ${dataset.format}</p>
          <p><strong>描述:</strong> ${dataset.description}</p>
          <p style="color: var(--muted); margin-top: 20px;">点击"导入"按钮加载完整数据集</p>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });
  },
  
  // 渲染导入统计
  renderImportStats(result) {
    const container = document.getElementById('importStats');
    if (!container) return;
    
    const stats = result.stats;
    
    container.innerHTML = `
      <div class="import-stats-panel">
        <h4>导入统计</h4>
        <div class="stats-grid" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;">
          <div class="stat-item">
            <div class="stat-value">${stats.total_samples}</div>
            <div class="stat-label">总样本数</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">${stats.avg_turns.toFixed(1)}</div>
            <div class="stat-label">平均轮次</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">${stats.total_memories}</div>
            <div class="stat-label">记忆数</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">${stats.total_events}</div>
            <div class="stat-label">事件数</div>
          </div>
        </div>
        <div style="margin-top: 16px;">
          <strong>类别分布:</strong>
          ${Object.entries(stats.categories).map(([cat, count]) => 
            `<span class="category-chip">C${cat}: ${count}</span>`
          ).join(' ')}
        </div>
      </div>
    `;
  },
  
  // 刷新数据集列表
  refreshDatasets() {
    this.renderImportPanel();
    toast('数据集列表已刷新');
  }
};

// 添加导入面板到页面
function addLoCoMoImportPanel() {
  const datasetsSection = document.getElementById('datasets');
  if (!datasetsSection) return;
  
  const existingPanel = document.getElementById('locomoImportPanel');
  if (existingPanel) return;
  
  const panel = document.createElement('div');
  panel.id = 'locomoImportPanel';
  panel.style.marginTop = '24px';
  
  datasetsSection.appendChild(panel);
  
  LoCoMoImporter.renderImportPanel();
}

// 初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', addLoCoMoImportPanel);
} else {
  addLoCoMoImportPanel();
}

// 导出
window.LoCoMoImporter = LoCoMoImporter;
