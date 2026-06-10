// Agent Playground - 真实 LLM 集成

const AgentPlaygroundReal = {
  state: {
    memories: [],
    events: [],
    isLoading: false
  },
  
  // 初始化
  init() {
    this.bindEvents();
    this.renderMemories();
    this.updateStats();
  },
  
  // 绑定事件
  bindEvents() {
    const loadSampleBtn = document.getElementById('loadSampleContext');
    const runAgentBtn = document.getElementById('runAgent');
    const clearAgentBtn = document.getElementById('clearAgent');
    const addMemoryBtn = document.getElementById('addAgentMemory');
    const clearContextBtn = document.getElementById('clearAgentContext');
    
    if (loadSampleBtn) loadSampleBtn.onclick = () => this.loadSampleContext();
    if (runAgentBtn) runAgentBtn.onclick = () => this.runAgent();
    if (clearAgentBtn) clearAgentBtn.onclick = () => this.clearAll();
    if (addMemoryBtn) addMemoryBtn.onclick = () => this.addMemory();
    if (clearContextBtn) clearContextBtn.onclick = () => this.clearContext();
    
    // 回车添加记忆
    const memoryInput = document.getElementById('agentMemoryInput');
    if (memoryInput) {
      memoryInput.onkeydown = (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          this.addMemory();
        }
      };
    }
    
    // 回车运行 Agent
    const questionInput = document.getElementById('agentQuestion');
    if (questionInput) {
      questionInput.onkeydown = (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          this.runAgent();
        }
      };
    }
  },
  
  // 添加记忆
  addMemory() {
    const input = document.getElementById('agentMemoryInput');
    const content = input.value.trim();
    
    if (!content) {
      toast('请输入记忆内容');
      return;
    }
    
    const memory = {
      role: 'user',
      content: content,
      timestamp: new Date().toISOString()
    };
    
    this.state.memories.push(memory);
    input.value = '';
    
    this.renderMemories();
    this.updateStats();
    toast('记忆已添加');
  },
  
  // 渲染记忆列表
  renderMemories() {
    const container = document.getElementById('agentMemoryList');
    if (!container) return;
    
    if (this.state.memories.length === 0) {
      container.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-tertiary);">暂无记忆</div>';
      return;
    }
    
    container.innerHTML = this.state.memories.map((mem, idx) => `
      <div class="agent-memory-item">
        <div class="agent-memory-header">
          <span class="agent-memory-role">${mem.role}</span>
          <span class="agent-memory-timestamp">${new Date(mem.timestamp).toLocaleTimeString()}</span>
        </div>
        <div class="agent-memory-content">${escapeHtml(mem.content)}</div>
      </div>
    `).join('');
  },
  
  // 更新统计
  updateStats() {
    const memoryCountEl = document.getElementById('agentMemoryCount');
    const eventCountEl = document.getElementById('agentEventCount');
    const contextLengthEl = document.getElementById('agentContextLength');
    
    if (memoryCountEl) memoryCountEl.textContent = this.state.memories.length;
    if (eventCountEl) eventCountEl.textContent = this.state.events.length;
    
    const totalLength = this.state.memories.reduce((sum, m) => sum + m.content.length, 0);
    if (contextLengthEl) contextLengthEl.textContent = totalLength;
  },
  
  // 加载示例上下文
  loadSampleContext() {
    this.state.memories = [
      {
        role: 'user',
        content: '端午节假期是 2026 年 5 月 31 日（周六）到 6 月 2 日（周一）',
        timestamp: new Date().toISOString()
      },
      {
        role: 'user',
        content: '从上海出发，不请假意味着只能利用周末时间',
        timestamp: new Date().toISOString()
      },
      {
        role: 'user',
        content: '预算 3000 元以内，往返胡志明市',
        timestamp: new Date().toISOString()
      }
    ];
    
    this.renderMemories();
    this.updateStats();
    toast('示例上下文已加载');
  },
  
  // 清空上下文
  clearContext() {
    this.state.memories = [];
    this.state.events = [];
    this.renderMemories();
    this.updateStats();
    toast('上下文已清空');
  },
  
  // 清空所有
  clearAll() {
    this.clearContext();
    
    const questionEl = document.getElementById('agentQuestion');
    const expectedAnswerEl = document.getElementById('agentExpectedAnswer');
    const resultAnswerEl = document.getElementById('agentResultAnswer');
    
    if (questionEl) questionEl.value = '';
    if (expectedAnswerEl) expectedAnswerEl.value = '';
    if (resultAnswerEl) resultAnswerEl.style.display = 'none';
    
    toast('已清空');
  },
  
  // 运行 Agent
  async runAgent() {
    if (this.state.isLoading) {
      toast('Agent 正在运行中...');
      return;
    }
    
    const questionEl = document.getElementById('agentQuestion');
    const question = questionEl ? questionEl.value.trim() : '';
    
    if (!question) {
      toast('请输入问题');
      return;
    }
    
    // 显示加载状态
    const loadingEl = document.getElementById('agentLoading');
    const resultAnswerEl = document.getElementById('agentResultAnswer');
    
    if (loadingEl) loadingEl.style.display = 'block';
    if (resultAnswerEl) resultAnswerEl.style.display = 'none';
    
    this.state.isLoading = true;
    const startTime = Date.now();
    
    try {
      // 构建 Context Pack
      const contextPack = this.buildContextPack(question);
      
      // 调用真实 LLM API
      const result = await this.callLLM(contextPack);
      
      const endTime = Date.now();
      result.time_cost = endTime - startTime;
      
      // 显示结果
      this.displayResult(result);
      
      toast('Agent 运行完成');
    } catch (e) {
      toast('运行失败: ' + e.message);
      console.error('Agent error:', e);
      if (loadingEl) loadingEl.style.display = 'none';
    } finally {
      this.state.isLoading = false;
    }
  },
  
  // 构建 Context Pack
  buildContextPack(question) {
    const messages = [];
    
    // 系统提示
    messages.push({
      role: 'system',
      content: '你是一个智能助手，可以访问用户提供的历史记忆来回答问题。请根据提供的上下文信息准确回答用户的问题。'
    });
    
    // 注入记忆
    if (this.state.memories.length > 0) {
      const memoryContext = this.state.memories.map(m => 
        `[${new Date(m.timestamp).toLocaleString()}] ${m.content}`
      ).join('\n');
      
      messages.push({
        role: 'system',
        content: `## 历史记忆\n${memoryContext}`
      });
    }
    
    // 当前问题
    messages.push({
      role: 'user',
      content: question
    });
    
    return messages;
  },
  
  // 调用 LLM API
  async callLLM(messages) {
    const modelEl = document.getElementById('agentModel');
    const temperatureEl = document.getElementById('agentTemperature');
    
    const model = modelEl ? modelEl.value : 'gpt-4';
    const temperature = temperatureEl ? parseFloat(temperatureEl.value) : 0.7;
    
    // 调用后端 API
    const response = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        messages: messages,
        model: model,
        temperature: temperature
      })
    });
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || 'API 调用失败');
    }
    
    const data = await response.json();
    
    return {
      answer: data.answer || data.content || '无回答',
      tokens: data.tokens || {
        prompt: 0,
        completion: 0,
        total: 0
      },
      recalled_count: this.state.memories.length,
      context_length: messages.reduce((sum, msg) => sum + msg.content.length, 0),
      model: data.model || model
    };
  },
  
  // 显示结果
  displayResult(result) {
    const loadingEl = document.getElementById('agentLoading');
    const resultAnswerEl = document.getElementById('agentResultAnswer');
    const answerTextEl = document.getElementById('agentAnswerText');
    const tokensUsedEl = document.getElementById('agentTokensUsed');
    const timeCostEl = document.getElementById('agentTimeCost');
    const recalledCountEl = document.getElementById('agentRecalledCount');
    const contextLengthUsedEl = document.getElementById('agentContextLengthUsed');
    
    if (loadingEl) loadingEl.style.display = 'none';
    if (resultAnswerEl) resultAnswerEl.style.display = 'block';
    
    if (answerTextEl) answerTextEl.textContent = result.answer;
    if (tokensUsedEl) tokensUsedEl.textContent = result.tokens.total || '-';
    if (timeCostEl) timeCostEl.textContent = result.time_cost || '-';
    if (recalledCountEl) recalledCountEl.textContent = result.recalled_count;
    if (contextLengthUsedEl) contextLengthUsedEl.textContent = result.context_length;
  }
};

// 替换原有的 AgentPlayground
if (window.AgentPlayground) {
  window.AgentPlayground = AgentPlaygroundReal;
}

// 初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => AgentPlaygroundReal.init());
} else {
  AgentPlaygroundReal.init();
}

// 导出
window.AgentPlaygroundReal = AgentPlaygroundReal;
