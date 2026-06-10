// LoCoMo Memory Importer - 从 LoCoMo 数据集导入记忆

const LoCoMoMemoryImporter = {
  // 导入 LoCoMo 记忆
  async importLoCoMoMemory() {
    try {
      // 获取数据集路径
      const datasetPath = document.getElementById('data')?.value || '/path/to/locomo10.json';
      
      // 加载数据集
      const response = await fetch(`/api/dataset/load?path=${encodeURIComponent(datasetPath)}`);
      if (!response.ok) {
        throw new Error('数据集加载失败');
      }
      
      const dataset = await response.json();
      
      if (!dataset || dataset.length === 0) {
        toast('数据集为空');
        return;
      }
      
      // 显示样本选择对话框
      this.showSampleSelector(dataset);
      
    } catch (e) {
      toast('导入失败: ' + e.message);
      console.error('Import error:', e);
    }
  },
  
  // 显示样本选择器
  showSampleSelector(dataset) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
      <div class="modal-content" style="max-width: 800px;">
        <div class="modal-header">
          <h2>选择 LoCoMo 样本</h2>
          <button class="icon-btn" onclick="this.closest('.modal').remove()">✕</button>
        </div>
        <div class="modal-body">
          <p style="margin-bottom: 16px; color: var(--text-secondary);">
            从 LoCoMo 数据集中选择一个样本，将其对话历史和事件导入为记忆
          </p>
          <div class="sample-list" style="max-height: 400px; overflow-y: auto;">
            ${dataset.map((sample, idx) => this.renderSampleCard(sample, idx)).join('')}
          </div>
        </div>
      </div>
    `;
    
    document.body.appendChild(modal);
    
    // 绑定点击事件
    modal.querySelectorAll('.sample-card').forEach((card, idx) => {
      card.onclick = () => {
        this.importSample(dataset[idx]);
        modal.remove();
      };
    });
  },
  
  // 渲染样本卡片
  renderSampleCard(sample, idx) {
    const sampleId = sample.sample_id || `sample_${idx}`;
    const qaCount = sample.qa ? sample.qa.length : 0;
    const eventCount = sample.event_summary ? Object.keys(sample.event_summary).length : 0;
    
    // 获取第一个问题作为预览
    const firstQuestion = sample.qa && sample.qa[0] ? sample.qa[0].question : '无问题';
    
    return `
      <div class="sample-card" style="
        padding: 16px;
        margin-bottom: 12px;
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 8px;
        cursor: pointer;
        transition: all 150ms;
      " onmouseover="this.style.borderColor='var(--accent)'" onmouseout="this.style.borderColor='var(--border)'">
        <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
          <strong style="font-size: 14px;">${sampleId}</strong>
          <div style="display: flex; gap: 8px;">
            <span class="badge">${qaCount} 问题</span>
            <span class="badge">${eventCount} 事件</span>
          </div>
        </div>
        <p style="font-size: 13px; color: var(--text-tertiary); margin: 0;">
          ${escapeHtml(firstQuestion.substring(0, 100))}${firstQuestion.length > 100 ? '...' : ''}
        </p>
      </div>
    `;
  },
  
  // 导入样本
  importSample(sample) {
    const memories = [];
    
    // 1. 导入对话历史
    if (sample.conversation && sample.conversation.history) {
      const history = sample.conversation.history;
      for (let i = 0; i < Math.min(history.length, 10); i++) {
        const msg = history[i];
        if (msg.content) {
          memories.push({
            role: msg.role || 'user',
            content: msg.content.substring(0, 500), // 限制长度
            timestamp: new Date().toISOString()
          });
        }
      }
    }
    
    // 2. 导入事件摘要
    if (sample.event_summary) {
      const events = sample.event_summary;
      let eventCount = 0;
      
      for (const [sessionKey, sessionEvents] of Object.entries(events)) {
        if (eventCount >= 5) break; // 最多导入5个事件
        
        if (typeof sessionEvents === 'object') {
          for (const [person, actions] of Object.entries(sessionEvents)) {
            if (Array.isArray(actions)) {
              for (const action of actions.slice(0, 2)) {
                memories.push({
                  role: 'system',
                  content: `[${person}] ${action}`,
                  timestamp: new Date().toISOString()
                });
                eventCount++;
                if (eventCount >= 5) break;
              }
            }
          }
        }
      }
    }
    
    // 3. 导入观察信息
    if (sample.observation && memories.length < 15) {
      const obs = sample.observation;
      if (typeof obs === 'object') {
        for (const [key, value] of Object.entries(obs).slice(0, 3)) {
          memories.push({
            role: 'system',
            content: `观察 - ${key}: ${JSON.stringify(value).substring(0, 200)}`,
            timestamp: new Date().toISOString()
          });
        }
      }
    }
    
    // 更新 AgentPlaygroundReal 的状态
    if (window.AgentPlaygroundReal) {
      window.AgentPlaygroundReal.state.memories = memories;
      window.AgentPlaygroundReal.renderMemories();
      window.AgentPlaygroundReal.updateStats();
    }
    
    toast(`已导入 ${memories.length} 条记忆`);
  }
};

// 绑定按钮事件
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('loadLocomoMemory');
  if (btn) {
    btn.onclick = () => LoCoMoMemoryImporter.importLoCoMoMemory();
  }
});

// 导出
window.LoCoMoMemoryImporter = LoCoMoMemoryImporter;
