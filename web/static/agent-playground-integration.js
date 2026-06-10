// Agent Playground 集成脚本

const AgentPlayground = {
  state: {
    memories: [],
    events: []
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
      container.innerHTML = '';
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
        content: 'Caroline went to the LGBTQ support group on 7 May 2023.',
        timestamp: new Date('2023-05-07').toISOString()
      },
      {
        role: 'user',
        content: 'Melanie painted a sunrise in 2022.',
        timestamp: new Date('2022-01-01').toISOString()
      },
      {
        role: 'user',
        content: 'Caroline is interested in psychology and counseling certification.',
        timestamp: new Date('2023-05-10').toISOString()
      },
      {
        role: 'user',
        content: 'Caroline researched adoption agencies.',
        timestamp: new Date('2023-05-15').toISOString()
      },
      {
        role: 'user',
        content: 'Caroline is a transgender woman.',
        timestamp: new Date('2023-05-01').toISOString()
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
    
    try {
      // 构建 Context Pack
      const contextPack = this.buildContextPack(question);
      
      // 模拟 LLM 调用
      const result = await this.simulateLLM(contextPack);
      
      // 显示结果
      this.displayResult(result);
      
      toast('Agent 运行完成');
    } catch (e) {
      toast('运行失败: ' + e.message);
      if (loadingEl) loadingEl.style.display = 'none';
    }
  },
  
  // 构建 Context Pack
  buildContextPack(question) {
    const contextParts = [];
    
    // 系统提示
    contextParts.push({
      role: 'system',
      content: '你是一个具有长期记忆能力的 AI 助手。你可以访问历史对话、事件和记忆来回答问题。'
    });
    
    // 注入记忆
    if (this.state.memories.length > 0) {
      const memoryContext = this.state.memories.map(m => 
        `[${new Date(m.timestamp).toLocaleDateString()}] ${m.role}: ${m.content}`
      ).join('\n');
      
      contextParts.push({
        role: 'system',
        content: `## 历史记录\n${memoryContext}`
      });
    }
    
    // 当前问题
    contextParts.push({
      role: 'user',
      content: question
    });
    
    return contextParts;
  },
  
  // 模拟 LLM 调用
  async simulateLLM(contextPack) {
    // 模拟延迟
    await new Promise(resolve => setTimeout(resolve, 1500));
    
    // 模拟回答
    const answer = '根据历史记录，Caroline 在 2023 年 5 月 7 日参加了 LGBTQ 支持小组。';
    
    return {
      answer: answer,
      tokens: {
        prompt: 150,
        completion: 30,
        total: 180
      },
      recalled_count: this.state.memories.length,
      context_length: contextPack.reduce((sum, msg) => sum + msg.content.length, 0),
      time_cost: 1523
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
    if (tokensUsedEl) tokensUsedEl.textContent = result.tokens.total;
    if (timeCostEl) timeCostEl.textContent = result.time_cost;
    if (recalledCountEl) recalledCountEl.textContent = result.recalled_count;
    if (contextLengthUsedEl) contextLengthUsedEl.textContent = result.context_length;
  }
};

// 初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => AgentPlayground.init());
} else {
  AgentPlayground.init();
}

// 导出
window.AgentPlayground = AgentPlayground;
