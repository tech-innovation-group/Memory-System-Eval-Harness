// LoCoMo 数据集自动导入功能 v2
// 支持真实的 LoCoMo 数据集格式

const LoCoMoImporter = {
  // 数据集配置
  datasets: {
    locomo: {
      name: 'LoCoMo',
      path: '/path/to/locomo10.json',
      description: 'Long Context Memory benchmark - 10 samples',
      format: 'locomo'
    },
    longmemeval: {
      name: 'LongMemEval',
      path: '/path/to/longmemeval.json',
      description: 'Long-term memory evaluation',
      format: 'longmemeval'
    },
    evolvingevents: {
      name: 'EvolvingEvents',
      path: '/path/to/evolvingevents.json',
      description: 'Dynamic event tracking',
      format: 'evolvingevents'
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
      const data = await this.loadDatasetFile(dataset.path);
      
      // 2. 解析数据集格式
      const parsed = this.parseLoCoMoFormat(data);
      
      // 3. 构建 Context Pack
      const contextPacks = this.buildContextPacks(parsed);
      
      // 4. 验证数据完整性
      const validation = this.validateDataset(contextPacks);
      
      if (!validation.valid) {
        throw new Error(`数据集验证失败: ${validation.errors.join(', ')}`);
      }
      
      toast(`${dataset.name} 导入成功！共 ${contextPacks.length} 个样本，${parsed.totalQuestions} 个问题`);
      
      return {
        dataset: datasetName,
        samples: contextPacks,
        stats: this.computeStats(contextPacks, parsed)
      };
    } catch (e) {
      toast(`导入失败: ${e.message}`);
      throw e;
    }
  },
  
  // 加载数据集文件
  async loadDatasetFile(path) {
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
  
  // 解析 LoCoMo 格式
  parseLoCoMoFormat(data) {
    const samples = [];
    let totalQuestions = 0;
    
    for (const item of data) {
      const sample = {
        id: item.sample_id || `sample_${samples.length}`,
        
        // QA 对
        qa: item.qa || [],
        
        // 对话信息
        speakers: {
          a: item.conversation?.speaker_a || 'Speaker A',
          b: item.conversation?.speaker_b || 'Speaker B'
        },
        
        // 会话（按 session 组织）
        sessions: this.extractSessions(item),
        
        // 事件摘要
        events: this.extractEvents(item.event_summary || {}),
        
        // 观察记录
        observations: this.extractObservations(item.observation || {}),
        
        // 会话摘要
        summaries: this.extractSummaries(item.session_summary || {})
      };
      
      totalQuestions += sample.qa.length;
      samples.push(sample);
    }
    
    return {
      samples,
      totalQuestions
    };
  },
  
  // 提取会话
  extractSessions(item) {
    const sessions = [];
    const conv = item.conversation || {};
    
    let sessionNum = 1;
    while (conv[`session_${sessionNum}`]) {
      const sessionData = conv[`session_${sessionNum}`];
      const dateTime = conv[`session_${sessionNum}_date_time`];
      
      sessions.push({
        session_id: sessionNum,
        date_time: dateTime,
        turns: Array.isArray(sessionData) ? sessionData : [],
        speaker_a: conv.speaker_a,
        speaker_b: conv.speaker_b
      });
      
      sessionNum++;
    }
    
    return sessions;
  },
  
  // 提取事件
  extractEvents(eventSummary) {
    const events = [];
    
    for (const [key, value] of Object.entries(eventSummary)) {
      if (key.startsWith('events_session_')) {
        const sessionNum = parseInt(key.replace('events_session_', ''));
        events.push({
          session: sessionNum,
          events: Array.isArray(value) ? value : []
        });
      }
    }
    
    return events;
  },
  
  // 提取观察
  extractObservations(observation) {
    const observations = [];
    
    for (const [key, value] of Object.entries(observation)) {
      if (key.startsWith('session_') && key.endsWith('_observation')) {
        const sessionNum = parseInt(key.replace('session_', '').replace('_observation', ''));
        observations.push({
          session: sessionNum,
          observation: value
        });
      }
    }
    
    return observations;
  },
  
  // 提取摘要
  extractSummaries(sessionSummary) {
    const summaries = [];
    
    for (const [key, value] of Object.entries(sessionSummary)) {
      if (key.startsWith('session_') && key.endsWith('_summary')) {
        const sessionNum = parseInt(key.replace('session_', '').replace('_summary', ''));
        summaries.push({
          session: sessionNum,
          summary: value
        });
      }
    }
    
    return summaries;
  },
  
  // 构建 Context Pack
  buildContextPacks(parsed) {
    const contextPacks = [];
    
    for (const sample of parsed.samples) {
      // 为每个 QA 对创建一个 Context Pack
      for (const qa of sample.qa) {
        const contextPack = {
          id: `${sample.id}_q${sample.qa.indexOf(qa)}`,
          sample_id: sample.id,
          category: `C${qa.category || 0}`,
          
          // 问题和答案
          question: qa.question,
          expected_answer: qa.answer,
          evidence: qa.evidence || [],
          
          // 参考对话（所有 sessions）
          reference_conversations: this.buildConversationContext(sample.sessions),
          
          // 上下文工程
          context_engineering: {
            events: sample.events,
            observations: sample.observations,
            summaries: sample.summaries
          },
          
          // 说话人信息
          speakers: sample.speakers,
          
          // 元数据
          metadata: {
            total_sessions: sample.sessions.length,
            total_turns: sample.sessions.reduce((sum, s) => sum + s.turns.length, 0),
            qa_index: sample.qa.indexOf(qa),
            total_qa: sample.qa.length
          }
        };
        
        contextPacks.push(contextPack);
      }
    }
    
    return contextPacks;
  },
  
  // 构建对话上下文
  buildConversationContext(sessions) {
    const conversations = [];
    
    for (const session of sessions) {
      for (const turn of session.turns) {
        conversations.push({
          session: session.session_id,
          date_time: session.date_time,
          speaker: turn.speaker || 'unknown',
          content: turn.content || turn.text || '',
          turn_id: turn.turn_id
        });
      }
    }
    
    return conversations;
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
  computeStats(contextPacks, parsed) {
    const stats = {
      total_samples: parsed.samples.length,
      total_questions: parsed.totalQuestions,
      total_context_packs: contextPacks.length,
      categories: {},
      avg_sessions: 0,
      avg_turns: 0,
      avg_qa_per_sample: 0
    };
    
    let totalSessions = 0;
    let totalTurns = 0;
    
    for (const pack of contextPacks) {
      // 类别统计
      const cat = pack.category || 'unknown';
      stats.categories[cat] = (stats.categories[cat] || 0) + 1;
      
      // 会话和轮次统计
      totalSessions += pack.metadata.total_sessions;
      totalTurns += pack.metadata.total_turns;
    }
    
    stats.avg_sessions = totalSessions / contextPacks.length;
    stats.avg_turns = totalTurns / contextPacks.length;
    stats.avg_qa_per_sample = parsed.totalQuestions / parsed.samples.length;
    
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
        <h4>✅ 导入成功</h4>
        <div class="stats-grid" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 16px;">
          <div class="stat-item">
            <div class="stat-value">${stats.total_samples}</div>
            <div class="stat-label">样本数</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">${stats.total_questions}</div>
            <div class="stat-label">问题数</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">${stats.avg_sessions.toFixed(1)}</div>
            <div class="stat-label">平均会话数</div>
          </div>
          <div class="stat-item">
            <div class="stat-value">${stats.avg_turns.toFixed(0)}</div>
            <div class="stat-label">平均轮次</div>
          </div>
        </div>
        <div style="margin-top: 16px;">
          <strong>类别分布:</strong>
          ${Object.entries(stats.categories).map(([cat, count]) => 
            `<span class="category-chip">${cat}: ${count}</span>`
          ).join(' ')}
        </div>
        <div style="margin-top: 16px;">
          <strong>平均每样本问题数:</strong> ${stats.avg_qa_per_sample.toFixed(1)}
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
